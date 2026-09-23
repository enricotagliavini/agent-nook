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
from agent_nook.sandbox.builder import BwrapBuilder
from agent_nook.utils.logger import main_logger


# Module-level logger — uses centralized main_logger
_logger = main_logger(__name__)


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
      3. Builds the bwrap command line via BwrapBuilder
      4. Executes the command
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

    def build(self, command: list[str] | None = None) -> list[str]:
        """Build the bwrap command line.

        Args:
            command: Optional command and arguments to append. When
                omitted, the returned list contains only the sandbox
                setup (useful for inspection or dry runs).

        Returns:
            The complete bwrap command line.
        """
        return self._builder.build(command or [])

    def run(self, command: list[str]) -> SandboxResult:
        """Run a command inside the sandbox.

        Args:
            command: The command and arguments to execute.

        Returns:
            SandboxResult with execution details.

        Raises:
            RuntimeError: If a mount source that must be created
                (create-source: true) cannot be created.

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

        bwrap_cmd = self.build(command)

        _logger.info("Full bwrap command: %s", " ".join(bwrap_cmd), extra={"command_only": True})

        # Run without capturing output - stdout/stderr go directly to console
        result = subprocess.run(
            bwrap_cmd,
            timeout=self._config.timeout,
            check=False,
        )

        return SandboxResult(
            success=result.returncode == 0,
            return_code=result.returncode,
        )


__all__ = ["BwrapSandbox", "SandboxConfig", "SandboxResult"]
