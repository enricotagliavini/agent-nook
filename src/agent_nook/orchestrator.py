"""Host-side command orchestration around sandbox runs.

For every ``orchestration:`` config entry the orchestrator:

1. starts the command on the host (outside the sandbox) with inherited
   stdio, so its output lands in agent-nook's log;
2. waits — up to the entry's ``wait-timeout`` — until the entry's socket
   is ready: unix — the socket file exists and is a socket; tcp — a
   connection succeeds;
3. after the sandbox exits (success, non-zero exit, timeout, or
   exception), stops every entry that is not ``keep: true`` with
   SIGTERM → wait 5 s → SIGKILL.

``keep: true`` entries are left running (documented opt-out: the user
owns their lifecycle from that point on).

Socket address parsing and process termination are shared with the
standalone bridge script (``stdio_bridge.parse_address`` /
``stdio_bridge.terminate_process``) — the shared address contract and the
graceful-then-forceful termination sequence each exist in exactly one
place.
"""

import os
import socket
import stat
import subprocess
import time


try:
    from agent_nook.config.config import OrchestrationEntry
    from agent_nook.stdio_bridge import parse_address
    from agent_nook.stdio_bridge import terminate_process
    from agent_nook.utils.logger import main_logger
except ImportError:
    from .config.config import OrchestrationEntry
    from .stdio_bridge import parse_address
    from .stdio_bridge import terminate_process
    from .utils.logger import main_logger


_logger = main_logger(__name__)

_POLL_INTERVAL = 0.1  # seconds between socket readiness checks
_TCP_CONNECT_TIMEOUT = 1.0  # seconds per tcp readiness probe


class Orchestrator:
    """Starts, waits for, and stops the orchestration commands of a run.

    One instance per sandbox run (created by ``BwrapSandbox.run``); the
    lifecycle is ``start()`` → ``wait_ready()`` → ... sandbox ... →
    ``stop()``. All three methods are safe to call on an empty
    orchestrator (no-op) and ``stop()`` is idempotent.
    """

    def __init__(self, entries: list[OrchestrationEntry]) -> None:
        """Create an orchestrator for the given (already validated) entries.

        Args:
            entries: The ``orchestration:`` entries from the sandbox config.
        """
        self._entries = entries
        self._procs: list[tuple[OrchestrationEntry, subprocess.Popen]] = []
        self._stopped = False

    def start(self) -> None:
        """Start every entry's command (in parallel).

        Stdio is inherited so command output lands in agent-nook's log.

        Raises:
            RuntimeError: If a command cannot be spawned; all commands
                started so far are stopped before re-raising.
        """
        for entry in self._entries:
            try:
                proc = subprocess.Popen(entry.command)
            except (OSError, ValueError) as e:
                self.stop()
                raise RuntimeError(f"failed to start orchestration command {' '.join(entry.command)}: {e}") from e
            self._procs.append((entry, proc))
            _logger.info("orchestration: started pid=%s socket=%s command=%s", proc.pid, entry.socket, " ".join(entry.command))

    def wait_ready(self) -> None:
        """Wait until every entry's socket is ready.

        The clock starts once all commands are spawned; each entry is
        given its own ``wait_timeout``. On timeout — or on a socket
        address the structural config validation cannot catch (e.g.
        ``tcp:host:99999999``) — every started command is stopped and a
        RuntimeError is raised, so the sandbox never starts.

        Raises:
            RuntimeError: If a socket is not ready in time, or an
                address is malformed.
        """
        # Fail fast on malformed addresses (the loader only checks the
        # shape; full address syntax is the shared script-side contract).
        for entry in self._entries:
            try:
                parse_address(entry.socket)
            except ValueError as e:
                self.stop()
                raise RuntimeError(f"orchestration entry socket {entry.socket!r}: {e}") from e

        pending: list[tuple[OrchestrationEntry, float]] = [(entry, time.monotonic() + entry.wait_timeout) for entry in self._entries]
        while pending:
            time.sleep(_POLL_INTERVAL)
            now = time.monotonic()
            still_pending: list[tuple[OrchestrationEntry, float]] = []
            for entry, deadline in pending:
                if self._is_ready(entry):
                    _logger.info("orchestration: socket %s ready", entry.socket)
                elif now >= deadline:
                    self.stop()
                    raise RuntimeError(
                        f"orchestration socket {entry.socket!r} not ready after {entry.wait_timeout}s "
                        f"(command: {' '.join(entry.command)})"
                    )
                else:
                    still_pending.append((entry, deadline))
            pending = still_pending

    def stop(self) -> None:
        """Stop every started command that is not ``keep: true``.

        SIGTERM → wait 5 s → SIGKILL (shared with the bridge's
        ``terminate_process``). ``keep: true`` commands are left running.
        Idempotent: a second call is a no-op.
        """
        if self._stopped:
            return
        self._stopped = True
        for entry, proc in self._procs:
            if entry.keep:
                _logger.info("orchestration: leaving pid=%s running (keep: true)", proc.pid)
                continue
            _logger.info("orchestration: stopping pid=%s", proc.pid)
            terminate_process(proc)

    def _is_ready(self, entry: OrchestrationEntry) -> bool:
        """Check whether the entry's socket is ready to accept sessions."""
        kind, host, port = parse_address(entry.socket)
        if kind == "unix":
            try:
                mode = os.stat(host).st_mode
            except OSError:
                return False
            return stat.S_ISSOCK(mode)
        try:
            with socket.create_connection((host, port), timeout=_TCP_CONNECT_TIMEOUT):
                return True
        except OSError:
            return False


__all__ = ["Orchestrator"]
