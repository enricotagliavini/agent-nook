"""Generate unique, collision-proof socket names for tracked orchestration entries.

For every ``track-socket: true`` orchestration entry the orchestrator generates
a distinct unix socket name of the form ``{name}-{pid}-{counter}.sock``. The
name is unique across parallel ``agent-nook`` runs (``{name}`` + the process
``pid``) and across entries within a single run (the ``counter``), so multiple
bridges never collide on a shared path.

The base directory is ``$XDG_RUNTIME_DIR/agent-nook`` when ``XDG_RUNTIME_DIR``
is set, else ``/tmp``. The directory is created (mode 0700) on demand.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from pathlib import Path


__all__ = ["SocketMount", "generate"]

_SUFFIX = ".sock"


def generate(name: str, counter: int, *, pid: int = os.getpid(), base_dir: str | None = None) -> str:
    """Return the path of a fresh, unique socket name and ensure its dir exists.

    The returned path is ``{base_dir}/{safe_name}-{pid}-{counter}.sock``. The
    directory ``base_dir`` is created with mode ``0700`` when missing. The file
    itself is **not** created — only its name is reserved; the caller binds it.

    Args:
        name: The sandbox name used as the socket's base name. Directory
            components are stripped (``Path(name).name``) so a hostile name
            cannot escape the base directory.
        counter: Zero-based per-process index that disambiguates entries in the
            same run; start at 0 and increment per tracked entry.
        pid: Process id baked into the name for cross-run uniqueness. Defaults
            to the caller's pid.
        base_dir: Directory the socket lives in. When ``None``, defaults to
            ``$XDG_RUNTIME_DIR/agent-nook`` (or ``/tmp`` when that env var is
            unset).

    Returns:
        The absolute path to the reserved socket name.

    Raises:
        OSError: If ``base_dir`` cannot be created.
    """
    if base_dir is None:
        xdg_runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        base_dir = os.path.join(xdg_runtime_dir, "agent-nook") if xdg_runtime_dir else "/tmp"

    safe_name = Path(name).name
    socket_path = os.path.join(base_dir, f"{safe_name}-{pid}-{counter}{_SUFFIX}")

    os.makedirs(base_dir, mode=0o700, exist_ok=True)
    # makedirs honors umask, so force 0700 explicitly (e.g. umask 022 -> 0755).
    with contextlib.suppress(OSError):
        os.chmod(base_dir, 0o700)
    return socket_path


@dataclass(frozen=True)
class SocketMount:
    """Runtime socket wiring for one ``track-socket`` orchestration entry.

    The orchestrator generates a unique ``host_path``, then this record pins
    how the sandbox consumes it: the sandbox bind-mounts ``host_path`` to
    ``run_path`` and points ``env_var`` at ``run_path``.

    Attributes:
        host_path: Absolute host path to the generated socket (e.g.
            ``/tmp/agent-nook/mybox-1234-0.sock``).
        run_path: Path the socket is bind-mounted to inside the sandbox
            (``/run/<name>.sock``); the sandbox-side value of ``env_var``.
        env_var: Environment variable the sandbox sets (``STDIO_SOCKET_PATH``
            for a single entry, ``STDIO_SOCKET_PATH1``/``2``/... for several).
    """

    host_path: str
    run_path: str
    env_var: str
