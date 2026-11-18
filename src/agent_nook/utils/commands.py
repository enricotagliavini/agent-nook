"""Helpers for formatting commands in log output.

Provides :func:`short_command`, which renders a command (a ``list[str]`` argv)
as a compact, log-friendly fragment. It is intentionally generic — it takes the
raw command tokens, not a domain object — so any module that needs a short
command in its log line (the orchestrator, the CLI startup banner, ...) can
reuse the same implementation.
"""

from __future__ import annotations


__all__ = ["short_command"]

_MAX_COMMAND_LENGTH = 20


def short_command(command: list[str]) -> str:
    """Return a short, log-friendly form of a command.

    The command tokens are joined with spaces. If the result exceeds
    ``_MAX_COMMAND_LENGTH`` characters it is truncated with an ellipsis; shorter
    commands are shown in full. The fragment is wrapped in single quotes so it
    reads as a command in the log line.
    """
    cmd = " ".join(command)
    return f"'{cmd[:_MAX_COMMAND_LENGTH]}...'" if len(cmd) > _MAX_COMMAND_LENGTH else f"'{cmd}'"
