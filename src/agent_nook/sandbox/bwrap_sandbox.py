"""Unified sandbox executor combining config, builder, and execution.

Usage:
    from agent_nook.sandbox import BwrapSandbox, BwrapBuilder, SandboxConfig

    # Run a command in a sandbox
    result = BwrapSandbox(config).run(["python3", "agent.py"])
    if result.success:
        _logger.info("Command executed successfully")

    # Build the bwrap command line without running it
    cmd = BwrapSandbox(config).build(["python3", "agent.py"])

    # Manual builder
    cmd = BwrapBuilder(config).build(["python3", "agent.py"])
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from agent_nook.config.config import Mount
from agent_nook.config.config import SandboxConfig
from agent_nook.orchestrator import Orchestrator
from agent_nook.sandbox.builder import BwrapBuilder
from agent_nook.sandbox.secret import SecretsBundle
from agent_nook.sandbox.secret import prepare_secrets
from agent_nook.utils.logger import main_logger


# Module-level logger — uses centralized main_logger
_logger = main_logger(__name__)


def _stdio_stub_host_path() -> str:
    """Absolute host path to the bundled sandbox-side stdio stub.

    The stub ships alongside this module in the installed package. It is
    bind-mounted into the sandbox at ``/run/stdio_stub.py``, so the
    sandboxed command can run it without knowing where agent-nook was
    installed — and without importing anything from agent-nook (which the
    sandbox environment never has).
    """
    return str(Path(__file__).resolve().parent.parent / "stdio_stub.py")


def _ensure_mount_sources(mounts: list[Mount]) -> None:
    """Create missing mount source paths before the sandbox starts.

    For each mount with ``create_source=True`` and a non-empty source,
    ensures the host source path exists: a directory (including missing
    parents) by default, or an empty file when ``create_as="file"``
    (parent directories are created as needed). Existing paths are left
    untouched. Mounts without a source are skipped, so the options are a
    harmless no-op for tmpfs/proc/dev/dir mounts.

    Args:
        mounts: The mounts from the sandbox configuration.

    Raises:
        RuntimeError: If a source path cannot be created (e.g. permission
            denied, or a directory exists where a file is required).
    """
    for mount in mounts:
        if not mount.create_source or not mount.source:
            continue
        source = Path(mount.source)
        as_file = mount.create_as == "file"
        try:
            if as_file:
                if source.is_dir():
                    raise FileExistsError(f"'{source}' is a directory, cannot create it as a file")
                source.parent.mkdir(parents=True, exist_ok=True)
                if not source.exists():
                    source.touch()
            else:
                source.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            _logger.exception("Failed to create mount source %r for %s mount -> %r", mount.source, mount.type, mount.target)
            raise RuntimeError(f"Failed to create mount source '{mount.source}': {e}") from e
        _logger.debug("Ensured mount source %s exists (%s)", mount.source, "file" if as_file else "directory")


def _prepare_overlays(mounts: list[Mount]) -> None:
    """Prepare host paths for overlay mounts before the sandbox starts.

    For each ``overlay`` mount, ensure the workdir exists as a directory
    (created when missing) and lives on the same filesystem as the
    read-write source — the kernel rejects the mount otherwise. Emptiness
    is deliberately NOT enforced: the kernel leaves internal state (a
    ``work`` subdirectory) in the workdir after every run, and reusing the
    same workdir across runs must keep working.

    Also rejects layer paths that do not exist, are duplicated, or where one
    layer is an ancestor of another after resolving symlinks — within each
    overlay mount (overlayfs behavior there is undefined and some kernels
    do not detect the problem). The same lower directory MAY be shared
    between different overlay mounts, but a workdir may not.

    Mounts missing required fields (e.g. a programmatically created
    ``overlay`` mount without source/workdir) are skipped here and are
    rejected with a clear error by ``Mount.build()`` instead.

    Args:
        mounts: The mounts from the sandbox configuration.

    Raises:
        RuntimeError: If a workdir cannot be created, is on a different
            filesystem than the source, is shared between two overlay
            mounts, or layer paths overlap.
    """
    overlay_mounts = [m for m in mounts if m.type.lower().strip() in ("overlay", "ro-overlay", "tmp-overlay")]
    if not overlay_mounts:
        return

    # Workdir preparation (overlay mounts only).
    for mount in overlay_mounts:
        if mount.type.lower().strip() != "overlay" or mount.source is None or mount.workdir is None:
            continue
        source = Path(mount.source)
        workdir = Path(mount.workdir)
        if not source.exists():
            raise RuntimeError(f"Overlay source (RWSRC) '{source}' does not exist")
        try:
            workdir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            _logger.exception("Failed to create overlay workdir %r for mount %r -> %r", workdir, source, mount.target)
            raise RuntimeError(f"Failed to create overlay workdir '{workdir}': {e}") from e
        if not workdir.is_dir():
            raise RuntimeError(f"Overlay workdir '{workdir}' is not a directory")
        try:
            dev_workdir = workdir.resolve().stat().st_dev
            dev_source = source.resolve().stat().st_dev
        except OSError as e:
            _logger.exception("Failed to stat overlay source %r or workdir %r", source, workdir)
            raise RuntimeError(f"Cannot stat overlay source '{source}' or workdir '{workdir}': {e}") from e
        if dev_workdir != dev_source:
            raise RuntimeError(f"Overlay workdir '{workdir}' must be on the same filesystem as source '{source}'")
        _logger.debug("Ensured overlay workdir %s exists (same filesystem as %s)", workdir, source)

    # Workdirs must not be shared between two overlay mounts.
    seen_workdirs: dict[str, str] = {}
    for mount in overlay_mounts:
        if mount.type.lower().strip() != "overlay" or mount.workdir is None:
            continue
        workdir = str(Path(mount.workdir).resolve())
        if workdir in seen_workdirs:
            raise RuntimeError(
                f"Overlay workdir '{workdir}' is used by more than one overlay mount ('{seen_workdirs[workdir]}' and '{mount.target}')"
            )
        seen_workdirs[workdir] = mount.target or ""

    # Layer checks, per overlay mount: layers must exist and must not be
    # duplicated or nested (undefined overlayfs behavior).
    for mount in overlay_mounts:
        layers = list(mount.overlay_src)
        if mount.type.lower().strip() == "overlay" and mount.source is not None:
            layers.append(mount.source)
        real_paths: list[tuple[str, str]] = []
        for layer in layers:
            if not Path(layer).exists():
                raise RuntimeError(f"Overlay layer '{layer}' does not exist (mount type '{mount.type}', target '{mount.target}')")
            real_paths.append((str(Path(layer).resolve()), layer))

        for i, (real_a, raw_a) in enumerate(real_paths):
            for real_b, raw_b in real_paths[i + 1 :]:
                if real_a == real_b:
                    raise RuntimeError(f"Overlay layers must be unique, found duplicate: '{raw_a}'")
                if real_b.startswith(real_a + "/") or real_a.startswith(real_b + "/"):
                    raise RuntimeError(f"Overlay layers must not be nested: '{raw_a}' and '{raw_b}'")


@dataclass
class SandboxResult:
    """Result of a sandboxed command execution.

    Attributes:
        success: Whether the command exited with code 0.
        return_code: The exit code of the command.
    """

    success: bool
    return_code: int


class BwrapSandbox:
    """Unified sandbox executor combining config, builder, and execution.

    A single object that holds a SandboxConfig and can build or run
    commands in a bubblewrap sandbox.

    Usage:
        result = BwrapSandbox(config).run(["python3", "agent.py"])
        if result.success:
            _logger.info("Command executed successfully")

        # Build the command line without running it
        cmd = BwrapSandbox(config).build(["python3", "agent.py"])

        # Manual builder (for advanced use)
        cmd = BwrapBuilder(config).build(["python3", "agent.py"])
        subprocess.run(cmd)

    The BwrapSandbox class encapsulates the full lifecycle:
      1. Holds the SandboxConfig
      2. Ensures mount sources exist (create-source option)
      3. Prepares overlay mounts (workdir, layer checks)
      4. Starts orchestration commands (host side) and waits for their
         sockets (no-op when the config has none)
      5. Builds the bwrap command line via BwrapBuilder
      6. Executes the command
      7. Stops the orchestration commands (unless keep: true)
    """

    def __init__(
        self,
        config: SandboxConfig | None = None,
    ) -> None:
        """Initialize BwrapSandbox with a config.

        Args:
            config: REQUIRED sandbox configuration. Cannot be None.

        Example:
            sandbox = BwrapSandbox(config)
            result = sandbox.run(["echo", "hello"])
        """
        if config is None:
            raise ValueError("config is required and cannot be None")
        self._config = config
        self._builder = BwrapBuilder(config)

    @property
    def config(self) -> SandboxConfig:
        """Return the sandbox configuration."""
        return self._config

    def build(
        self,
        command: list[str] | None = None,
        socket_mounts: object = None,
        stdio_stub_path: str | None = None,
        secrets: SecretsBundle | None = None,
    ) -> list[str]:
        """Build the bwrap command line.

        Args:
            command: Optional command and arguments to append. When
                omitted, the returned list contains only the sandbox
                setup (useful for inspection or dry runs).
            socket_mounts: Runtime socket wiring (the list of
                ``SocketMount`` objects from
                :meth:`Orchestrator.socket_mounts`). When provided, each
                mount is bind-mounted into ``/run`` and its ``STDIO_SOCKET_PATH``
                env var set.
            stdio_stub_path: Absolute host path to the bundled stdio stub.
                When provided, it is bind-mounted into ``/run/stdio_stub.py``
                so the sandboxed command can run it.
            secrets: A :class:`agent_nook.sandbox.secret.SecretsBundle`
                returned by :func:`agent_nook.sandbox.secret.prepare_secrets`,
                or ``None`` (no secrets). When provided, secrets are read-only
                bind-mounted into ``/run/secrets``; env-exposed secrets are
                exported by the ``secrets_env.py`` wrapper before the command.

        Returns:
            The complete bwrap command line.
        """
        return self._builder.build(
            command or [],
            socket_mounts=socket_mounts,
            stdio_stub_path=stdio_stub_path,
            secrets=secrets,
        )

    def run(self, command: list[str]) -> SandboxResult:
        """Run a command inside the sandbox.

        Args:
            command: The command and arguments to execute.

        Returns:
            SandboxResult with execution details.

        Raises:
            RuntimeError: If a mount source that must be created
                (create-source: true) cannot be created, if an overlay
                workdir cannot be prepared (missing source, different
                filesystem, overlapping layers), or if an orchestration
                command cannot be started, has a malformed socket
                address, or its socket is not ready within
                wait-timeout (started commands are stopped; the sandbox
                never starts).

        Example:
            result = BwrapSandbox(config).run(["/bin/echo", "hello"])
            if result.success:
                _logger.info("Command executed successfully")
        """
        _logger.debug(
            "Running sandbox with config: name=%s, command=%s",
            self._config.name,
            " ".join(command),
        )

        # Ensure mount sources that must be created exist before bwrap runs
        _ensure_mount_sources(self._config.mounts)
        _prepare_overlays(self._config.mounts)

        # Start the orchestration commands (host side, outside the sandbox)
        # and wait for their sockets before the sandbox starts. They are
        # stopped after the sandbox exits (unless keep: true), on success,
        # non-zero exit, timeout, and exception alike.
        orchestrator = Orchestrator(self._config.orchestration, name=self._config.name)
        orchestrator.start()
        orchestrator.wait_ready()

        # Materialize secrets (private temp files) before building the bwrap
        # command. prepare_secrets removes its own partial temp dir on failure,
        # so a materialize error leaves nothing behind.
        secrets_bundle = prepare_secrets(self._config.secrets)

        try:
            # Build the bwrap command with the runtime socket mounts (bind into
            # /run, set STDIO_SOCKET_PATH), the stdio stub (bind into
            # /run/stdio_stub.py so the sandboxed command can run it), and the
            # secrets (read-only bind into /run/secrets; env-exposed secrets are
            # exported by the wrapper before the real command runs).
            bwrap_cmd = self.build(
                command,
                socket_mounts=orchestrator.socket_mounts(),
                stdio_stub_path=_stdio_stub_host_path(),
                secrets=secrets_bundle,
            )

            _logger.debug("Full bwrap command: %s", " ".join(bwrap_cmd), extra={"command_only": True})

            # Run without capturing output - stdout/stderr go directly to console
            result = subprocess.run(
                bwrap_cmd,
                timeout=self._config.timeout,
                check=False,
            )
        finally:
            secrets_bundle.cleanup()
            orchestrator.stop()

        return SandboxResult(
            success=result.returncode == 0,
            return_code=result.returncode,
        )


__all__ = ["BwrapSandbox", "SandboxConfig", "SandboxResult"]
