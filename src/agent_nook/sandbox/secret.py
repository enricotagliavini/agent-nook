"""Materialize secrets for the sandbox.

Each secret is read on the host (from a file, or from the stdout of a command
run with no shell) and written to a private temp file (mode ``0600``) inside a
per-run private directory (mode ``0700``) under ``$XDG_RUNTIME_DIR/agent-nook``
(or ``/tmp``). The temp file is then read-only bind-mounted into the sandbox at
``/run/secrets/<name>``.

Secret values are never passed on the command line (``--setenv``) or exported
as host environment variables, so they do not appear in ``ps`` or the logs.

For a secret with an ``env_var`` set, the value is also exported inside the
sandbox: the wrapper (``secrets_env.py``) reads the value from the bind-mounted
file at start. The manifest (``secrets.env``) maps each env variable name to
its in-sandbox target path; it is only written when at least one secret opt-in
exposes an env var.

This module runs in the executor (``BwrapSandbox.run``), just before the bwrap
command is built, and returns a :class:`SecretsBundle` the caller binds and
later cleans up.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from agent_nook.config.config import Secret


__all__ = ["SecretMount", "SecretsBundle", "prepare_secrets"]

_MANIFEST_NAME = "secrets.env"
_SECRETS_DIR = "/run/secrets"


def _secrets_env_host_path() -> str:
    """Absolute host path to the bundled sandbox-side secrets wrapper.

    The wrapper ships alongside this module in the installed package. It is
    bind-mounted into the sandbox at ``/run/secrets_env.py`` so the sandboxed
    command can run it without knowing where agent-nook was installed — and
    without importing anything from agent-nook (which the sandbox environment
    never has).
    """
    return str(Path(__file__).resolve().parent.parent / "secrets_env.py")


def _base_dir() -> str:
    """Return the per-run temp directory base, created mode ``0700`` on demand.

    Mirrors ``socket_factory.generate``: ``$XDG_RUNTIME_DIR/agent-nook`` when
    set, else ``/tmp``. The directory is force-chmod'd to ``0700`` because
    ``os.makedirs`` honours ``umask`` (e.g. ``umask 022`` → ``0755``).
    """
    xdg_runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    base = os.path.join(xdg_runtime_dir, "agent-nook") if xdg_runtime_dir else "/tmp"
    os.makedirs(base, mode=0o700, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(base, 0o700)
    return base


def _read_content(secret: Secret) -> bytes:
    """Read the raw secret value as bytes (file or command source).

    ``file`` is read verbatim (preserving trailing newlines and binary
    content). ``command`` was already tokenized into an argv list by the
    loader (``shlex``, no shell); it runs directly via ``subprocess``, whose
    stdout is captured as bytes — preserving trailing newlines the way
    ``$(cat file)`` would not.
    """
    if secret.source_file is not None:
        return Path(secret.source_file).read_bytes()
    if secret.source_command is None:
        raise ValueError(f"secret {secret.name!r} has neither a file nor a command source")
    return subprocess.run(secret.source_command, check=True, capture_output=True).stdout


def _write_file(path: str, content: bytes, mode: int) -> None:
    """Write ``content`` to ``path`` and force ``mode`` (umask-independent)."""
    Path(path).write_bytes(content)
    os.chmod(path, mode)


def _chmod_remove(dirpath: str) -> None:
    """Recursively remove ``dirpath``, chmod-ing each dir before descending.

    Overlayfs creates its internal overlay work subdirectories with mode ``000``
    (no read/write/execute). A plain ``shutil.rmtree`` cannot traverse such a
    dir and silently stops, leaving garbage behind. chmod-ing each directory to
    ``0o700`` before listing it lets rmtree remove everything.
    """
    try:
        os.chmod(dirpath, 0o700)
    except OSError:
        return
    try:
        entries = list(os.scandir(dirpath))
    except OSError:
        return
    for entry in entries:
        _chmod_remove(Path(entry.path))
    shutil.rmtree(dirpath, ignore_errors=True)


@dataclass(frozen=True)
class SecretMount:
    """Runtime wiring for one secret.

    The value is materialized to a private host temp file (``host_path``),
    then read-only bind-mounted into the sandbox at ``target``
    (``/run/secrets/<name>``). When ``env_var`` is set, the wrapper also exports
    that variable inside the sandbox, reading the value from ``target`` at start.

    Attributes:
        name: The filename-safe secret name.
        host_path: Absolute host path to the materialized secret temp file.
        target: Path the secret is bind-mounted to inside the sandbox
            (``/run/secrets/<name>``).
        env_var: Optional env var to export inside the sandbox (reads from
            ``target``), or ``None`` for a file-only secret.
    """

    name: str
    host_path: str
    target: str
    env_var: str | None = None


@dataclass(frozen=True)
class SecretsBundle:
    """Per-run collection of materialized secrets and their runtime wiring.

    Attributes:
        temp_dir: Absolute path to the per-run private temp directory (mode
            ``0700``), or ``None`` when there are no secrets.
        mounts: One :class:`SecretMount` per secret.
        manifest_host_path: Absolute host path to the manifest (``secrets.env``)
            mapping env vars to their in-sandbox targets, or ``None`` when no
            secret opts in to env exposure.
        wrapper_host_path: Absolute host path to the bundled wrapper
            (``secrets_env.py``), or ``None`` when no secret opts in to env
            exposure.
    """

    temp_dir: str | None
    mounts: list[SecretMount]
    manifest_host_path: str | None
    wrapper_host_path: str | None

    @property
    def needs_env(self) -> bool:
        """Whether any secret opts in to env exposure (needs the wrapper)."""
        return any(m.env_var is not None for m in self.mounts)

    def cleanup(self) -> None:
        """Remove the per-run temp dir (and its secret files) on teardown."""
        if self.temp_dir is None:
            return
        _chmod_remove(self.temp_dir)


def prepare_secrets(config_secrets: list[Secret]) -> SecretsBundle:
    """Materialize each secret to a private temp file and build the bundle.

    Each secret is read (file or command) and written to ``<temp_dir>/<name>``
    mode ``0600``; env-exposed secrets are additionally recorded in the manifest
    ``<temp_dir>/secrets.env`` (mode ``0600``). The temp dir lives under
    ``$XDG_RUNTIME_DIR/agent-nook`` (or ``/tmp``).

    Args:
        config_secrets: The validated secrets from ``SandboxConfig.secrets``.

    Returns:
        A :class:`SecretsBundle` to bind into the sandbox and later clean up.
        With no secrets, ``temp_dir`` is ``None`` (no temp dir is created).

    Raises:
        OSError: If the temp dir or a secret file cannot be created.
        subprocess.CalledProcessError: If a command-source secret's command
            exits non-zero.
    """
    if not config_secrets:
        return SecretsBundle(temp_dir=None, mounts=[], manifest_host_path=None, wrapper_host_path=None)

    temp_dir = tempfile.mkdtemp(prefix="agent-nook-secrets-", dir=_base_dir())
    with contextlib.suppress(OSError):
        os.chmod(temp_dir, 0o700)

    try:
        mounts: list[SecretMount] = []
        manifest_lines: list[str] = []
        for secret in config_secrets:
            host_path = os.path.join(temp_dir, secret.name)
            _write_file(host_path, _read_content(secret), 0o600)
            mounts.append(
                SecretMount(
                    name=secret.name,
                    host_path=host_path,
                    target=f"{_SECRETS_DIR}/{secret.name}",
                    env_var=secret.env_var,
                )
            )
            if secret.env_var is not None:
                manifest_lines.append(f"{secret.env_var}={_SECRETS_DIR}/{secret.name}")

        manifest_host_path = None
        wrapper_host_path = None
        if manifest_lines:
            manifest_host_path = os.path.join(temp_dir, _MANIFEST_NAME)
            _write_file(manifest_host_path, ("\n".join(manifest_lines) + "\n").encode(), 0o600)
            wrapper_host_path = _secrets_env_host_path()
    except BaseException:
        # Clean up the partial temp dir before re-raising, so a failed
        # materialize never leaves secret bytes on disk.
        _chmod_remove(temp_dir)
        raise

    return SecretsBundle(temp_dir=temp_dir, mounts=mounts, manifest_host_path=manifest_host_path, wrapper_host_path=wrapper_host_path)
