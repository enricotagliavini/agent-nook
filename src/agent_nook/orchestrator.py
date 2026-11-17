"""Host-side command orchestration around sandbox runs.

For every ``orchestration:`` config entry the orchestrator:

1. starts the command on the host (outside the sandbox) with its stdio
   redirected to the log file (stdin to DEVNULL, stdout and stderr merged),
   so its output lands in agent-nook's log and never reaches the terminal;
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
    from agent_nook.utils.logger import log_file_handle
    from agent_nook.utils.logger import main_logger
except ImportError:
    from .config.config import OrchestrationEntry
    from .stdio_bridge import parse_address
    from .stdio_bridge import terminate_process
    from .utils.logger import log_file_handle
    from .utils.logger import main_logger


_logger = main_logger(__name__)

_POLL_INTERVAL = 0.1  # seconds between socket readiness checks
_TCP_CONNECT_TIMEOUT = 1.0  # seconds per tcp readiness probe
_PROGRESS_LOG_INTERVAL = 3.0  # seconds between "still waiting for socket" logs


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
        self._last_progress_logged: dict = {}  # id(entry) -> time of last "still waiting" log (OrchestrationEntry is unhashable)

    def start(self) -> None:
        """Start every entry's command (in parallel).

        Each command runs with its stdio redirected to the log file (stdin
        to DEVNULL, stdout and stderr merged), so its output lands in
        agent-nook's log and never reaches the terminal.

        Raises:
            RuntimeError: If a command cannot be spawned; all commands
                started so far are stopped before re-raising.
        """
        with log_file_handle() as log_fh:
            for entry in self._entries:
                try:
                    proc = subprocess.Popen(
                        entry.command,
                        stdin=subprocess.DEVNULL,
                        stdout=log_fh,
                        stderr=subprocess.STDOUT,
                    )
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

        If a command has already exited without its socket becoming
        ready (e.g. ``podman run -d`` returns immediately but the
        container never binds), a periodic "still waiting for socket"
        message is logged so the wait is not silent until the timeout.

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

        pending: list[tuple[OrchestrationEntry, float, subprocess.Popen]] = [
            (entry, time.monotonic() + entry.wait_timeout, proc) for entry, proc in self._procs
        ]
        while pending:
            time.sleep(_POLL_INTERVAL)
            now = time.monotonic()
            still_pending: list[tuple[OrchestrationEntry, float, subprocess.Popen]] = []
            for entry, deadline, proc in pending:
                if self._is_ready(entry):
                    _logger.info("orchestration: socket %s ready", entry.socket)
                elif now >= deadline:
                    self.stop()
                    raise RuntimeError(
                        f"orchestration socket {entry.socket!r} not ready after {entry.wait_timeout}s "
                        f"(command: {' '.join(entry.command)})"
                    )
                else:
                    if proc.poll() is not None:
                        self._log_progress(entry, now)
                    still_pending.append((entry, deadline, proc))
            pending = still_pending

    def _log_progress(self, entry: OrchestrationEntry, now: float) -> None:
        """Log a periodic "still waiting for socket" message for a command that has already exited.

        For commands that return immediately (e.g. ``podman run -d``) but
        never expose their socket, the wait would otherwise be silent until
        the timeout. Log once when the process is first observed to have
        exited, then every ``_PROGRESS_LOG_INTERVAL`` seconds.
        """
        last = self._last_progress_logged.get(id(entry))
        if last is None or now - last >= _PROGRESS_LOG_INTERVAL:
            _logger.info("orchestration: still waiting for socket from command %s", self._short_command(entry))
            self._last_progress_logged[id(entry)] = now

    @staticmethod
    def _short_command(entry: OrchestrationEntry) -> str:
        """Return a short, log-friendly form of the command (first 20 chars + '...').

        Commands longer than 20 chars are truncated with an ellipsis; shorter
        commands are shown in full. Wrapped in single quotes so the fragment
        reads as a command in the log line.
        """
        cmd = " ".join(entry.command)
        return f"'{cmd[:20]}...'" if len(cmd) > 20 else f"'{cmd}'"

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
