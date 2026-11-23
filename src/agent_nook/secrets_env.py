#!/usr/bin/env python3
"""secrets_env — export sandbox secrets as environment variables, then exec.

Standalone tool (Python standard library only; no ``agent_nook`` imports).
Intended to run INSIDE the sandbox. agent-nook bind-mounts each secret value
to ``/run/secrets/<name>`` and writes a manifest (``secrets.env``) mapping
every opt-in env var to its in-sandbox path. This wrapper reads that manifest,
exports each variable, and then ``exec``s the target command with the
augmented environment.

Using ``os.execvpe`` (rather than spawning) makes the sandboxed command a
direct replacement of this process, so its exit code and the signals sent to
the wrapper pass straight through.

Usage:
    secrets_env.py COMMAND [ARGS...]

The manifest is read from ``/run/agent_nook_secrets.env`` — the path
agent-nook bind-mounts it to inside the sandbox. That path can be overridden
with the ``AGENT_NOOK_SECRETS_MANIFEST`` environment variable (a test hook;
agent-nook never sets it in production).

Exit codes:
    0    the exec'd command exited 0 (a successful exec replaces this process,
         so the command's exit code becomes this process's exit code)
    1    the manifest could not be read, or a referenced secret was unreadable
    127  the COMMAND could not be executed (e.g. not found on PATH)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


__all__ = ["load_secret_env"]

# The path agent-nook bind-mounts the manifest to inside the sandbox. It can be
# overridden with AGENT_NOOK_SECRETS_MANIFEST (a test hook; agent-nook never
# sets it in production, where the fixed path is used).
DEFAULT_MANIFEST_PATH = "/run/agent_nook_secrets.env"
_MANIFEST_ENV_VAR = "AGENT_NOOK_SECRETS_MANIFEST"


def load_secret_env(manifest_path: str) -> dict[str, str]:
    """Read a secret manifest into an ``{ENV_VAR: <value>}`` mapping.

    Each non-empty line is ``ENV_VAR=/run/secrets/<name>``; the value is the
    current text contents of the file at ``<path>`` (read from the
    bind-mounted file, so the value reflects what is actually mounted).

    Args:
        manifest_path: Path to the manifest file.

    Returns:
        A dict mapping each env var name to its secret file's text contents.

    Raises:
        OSError: If the manifest or a referenced secret file cannot be read.
        ValueError: If a manifest line is not shaped like ``VAR=path``.
    """
    env: dict[str, str] = {}
    text = Path(manifest_path).read_text(encoding="utf-8", errors="surrogateescape")
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        var, sep, value_path = line.partition("=")
        if not sep:
            raise ValueError(f"manifest {manifest_path!r} line {lineno}: expected 'VAR=path', got {line!r}")
        var = var.strip()
        value_path = value_path.strip()
        if not var:
            raise ValueError(f"manifest {manifest_path!r} line {lineno}: empty env var name in {line!r}")
        env[var] = Path(value_path).read_text(encoding="utf-8", errors="surrogateescape")
    return env


def main() -> int:
    """Entry point: load the manifest, export the vars, exec the command."""
    manifest_path = os.environ.get(_MANIFEST_ENV_VAR, DEFAULT_MANIFEST_PATH)
    command = sys.argv[1:]
    if not command:
        print("secrets_env: error: no command to exec", file=sys.stderr)
        return 1

    try:
        env = load_secret_env(manifest_path)
    except OSError as e:
        print(f"secrets_env: error: cannot read manifest {manifest_path!r}: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"secrets_env: error: {e}", file=sys.stderr)
        return 1

    os.environ.update(env)

    program = command[0]
    try:
        os.execvpe(program, command, os.environ)
    except OSError as e:
        print(f"secrets_env: error: cannot exec {program!r}: {e}", file=sys.stderr)
        return 127
    # Unreachable: a successful os.execvpe replaces this process image.
    return 0


if __name__ == "__main__":
    sys.exit(main())
