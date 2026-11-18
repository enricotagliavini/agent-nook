"""Host-side command orchestration around sandbox runs.

For every ``orchestration:`` config entry the orchestrator:

1. (for ``track-socket: true`` entries) generates a unique unix socket path
   via :func:`agent_nook.socket_factory.generate` and substitutes the
   ``${STDIO_SOCKET_PATH}`` (full path), ``${STDIO_SOCKET_NAME}`` (basename) and
   ``${STDIO_SOCKET_DIR}`` (containing directory) placeholders into the
   already-tokenized argv, then starts the command on the host with its stdio
   redirected to the log file (stdin to DEVNULL, stdout and stderr merged), so
   its output lands in agent-nook's log and never reaches the terminal;
2. waits — up to the entry's ``wait-timeout`` — until the generated socket is
   ready: the socket file exists and is a socket;
3. after the sandbox exits (success, non-zero exit, timeout, or exception),
   stops every entry that is not ``keep: true`` (SIGTERM → wait 5 s → SIGKILL),
   then unlinks the generated socket for non-keep entries (cleanup covers
   SIGKILL/crash where the bridge never unlinks it).

``keep: true`` entries are left running (documented opt-out: the user owns their
lifecycle from that point on).

The generated socket name and its sandbox wiring are returned per entry as
``SocketMount`` objects (see :meth:`Orchestrator.socket_mounts`) so the sandbox
can bind-mount them into ``/run`` and set ``STDIO_SOCKET_PATH``.
"""

import contextlib
import os
import stat
import subprocess
import time


try:
    from agent_nook.config.config import OrchestrationEntry
    from agent_nook.socket_factory import SocketMount
    from agent_nook.socket_factory import generate
    from agent_nook.stdio_bridge import terminate_process
    from agent_nook.utils.commands import short_command
    from agent_nook.utils.logger import log_file_handle
    from agent_nook.utils.logger import main_logger
except ImportError:
    from .config.config import OrchestrationEntry
    from .socket_factory import SocketMount
    from .socket_factory import generate
    from .stdio_bridge import terminate_process
    from .utils.commands import short_command
    from .utils.logger import log_file_handle
    from .utils.logger import main_logger


__all__ = ["Orchestrator", "substitute_placeholders"]

_logger = main_logger(__name__)

_POLL_INTERVAL = 0.1  # seconds between socket readiness checks
_PROGRESS_LOG_INTERVAL = 3.0  # seconds between "still waiting for socket" logs
_SOCKET_CLEANUP_WAIT = 5.0  # seconds to wait for sockets to disappear after stop

# Placeholders substituted into the (already-tokenized) host argv. Never
# re-tokenize: operate on the list[str] produced at config parse time. The
# directory placeholder lets a command bind-mount the *directory* containing
# the socket (e.g. ``podman run -v ${STDIO_SOCKET_DIR}:/app:z``) rather than
# the socket file, which does not exist yet when the command starts.
_PATH_PLACEHOLDER = "${STDIO_SOCKET_PATH}"
_NAME_PLACEHOLDER = "${STDIO_SOCKET_NAME}"
_DIR_PLACEHOLDER = "${STDIO_SOCKET_DIR}"


def substitute_placeholders(argv: list[str], host_path: str) -> list[str]:
    """Return ``argv`` with socket placeholders filled in, without re-tokenizing.

    Args:
        argv: The already-tokenized host command (a ``list[str]`` produced at
            config parse time).
        host_path: The generated socket's absolute host path.

    Returns:
        A new list with every ``${STDIO_SOCKET_PATH}`` replaced by ``host_path``,
        every ``${STDIO_SOCKET_NAME}`` (the socket basename) replaced by
        ``os.path.basename(host_path)``, and every ``${STDIO_SOCKET_DIR}``
        replaced by ``os.path.dirname(host_path)`` (the directory the socket is
        reserved in — which ``generate`` creates before the command starts, so
        it can be bind-mounted even though the socket file does not exist yet).
    """
    name = os.path.basename(host_path)
    directory = os.path.dirname(host_path)
    return [
        elem.replace(_PATH_PLACEHOLDER, host_path).replace(_NAME_PLACEHOLDER, name).replace(_DIR_PLACEHOLDER, directory)
        for elem in argv
    ]


class Orchestrator:
    """Starts, waits for, and stops the orchestration commands of a run.

    One instance per sandbox run (created by ``BwrapSandbox.run``); the
    lifecycle is ``start()`` → ``wait_ready()`` → ... sandbox ... → ``stop()``.
    All three methods are safe to call on an empty orchestrator (no-op) and
    ``stop()`` is idempotent.
    """

    def __init__(self, entries: list[OrchestrationEntry], name: str = "agent-nook") -> None:
        """Create an orchestrator for the given (already validated) entries.

        Args:
            entries: The ``orchestration:`` entries from the sandbox config.
            name: The sandbox name, used as the socket's base name.
        """
        self._entries = entries
        self._name = name
        self._procs: list[tuple[OrchestrationEntry, subprocess.Popen]] = []
        self._mounts: dict[int, SocketMount] = {}  # id(entry) -> SocketMount
        self._tracked: list[OrchestrationEntry] = [e for e in entries if e.track_socket]
        self._stopped = False
        self._last_progress_logged: dict = {}  # id(entry) -> time of last "still waiting" log (OrchestrationEntry is unhashable)

    @property
    def name(self) -> str:
        """The sandbox name (used for socket generation)."""
        return self._name

    def start(self) -> None:
        """Start every entry's command (in parallel).

        For ``track-socket: true`` entries the orchestrator generates a socket
        path, substitutes the placeholders into the argv, and records the
        resulting ``SocketMount``. Each command runs with its stdio redirected to
        the log file (stdin to DEVNULL, stdout and stderr merged), so its output
        lands in agent-nook's log and never reaches the terminal.

        Raises:
            RuntimeError: If a command cannot be spawned; all commands
                started so far are stopped before re-raising.
        """
        with log_file_handle() as log_fh:
            counter = 0
            for entry in self._entries:
                if entry.track_socket:
                    host_path = generate(self._name, counter)
                    counter += 1
                    argv = substitute_placeholders(entry.command, host_path)
                    self._mounts[id(entry)] = self._make_mount(entry, host_path)
                else:
                    argv = entry.command
                try:
                    proc = subprocess.Popen(
                        argv,
                        stdin=subprocess.DEVNULL,
                        stdout=log_fh,
                        stderr=subprocess.STDOUT,
                    )
                except (OSError, ValueError) as e:
                    self.stop()
                    raise RuntimeError(f"failed to start orchestration command {' '.join(argv)}: {e}") from e
                self._procs.append((entry, proc))
                _logger.debug(
                    "orchestration: started pid=%s socket=%s command=%s",
                    proc.pid,
                    self._mounts.get(id(entry), SocketMount("<none>", "<none>", "<none>")),
                    " ".join(argv),
                )

    def wait_ready(self) -> None:
        """Wait until every ``track-socket`` entry's socket is ready.

        Non-``track-socket`` entries are started but not waited on (they expose
        no tracked socket). The clock starts once all commands are spawned; each
        tracked entry is given its own ``wait_timeout``. On timeout the started
        commands are stopped and a RuntimeError is raised, so the sandbox never
        starts.

        If a command has already exited without its socket becoming ready (e.g.
        ``podman run -d`` returns immediately but the container never binds), a
        periodic "still waiting for socket" message is logged so the wait is not
        silent until the timeout.

        Raises:
            RuntimeError: If a socket is not ready in time.
        """
        pending: list[tuple[OrchestrationEntry, float, subprocess.Popen, SocketMount]] = []
        for entry, proc in self._procs:
            mount = self._mounts.get(id(entry))
            if mount is not None:
                pending.append((entry, time.monotonic() + entry.wait_timeout, proc, mount))
        while pending:
            time.sleep(_POLL_INTERVAL)
            now = time.monotonic()
            still_pending: list[tuple[OrchestrationEntry, float, subprocess.Popen, SocketMount]] = []
            for entry, deadline, proc, mount in pending:
                if self._is_ready(mount.host_path):
                    _logger.debug("orchestration: socket %s ready", mount.host_path)
                elif now >= deadline:
                    self.stop()
                    raise RuntimeError(
                        f"orchestration socket {mount.host_path!r} not ready after {entry.wait_timeout}s "
                        f"(command: {' '.join(entry.command)})"
                    )
                else:
                    if proc.poll() is not None:
                        self._log_progress(entry, now)
                    still_pending.append((entry, deadline, proc, mount))
            pending = still_pending

    def _log_progress(self, entry: OrchestrationEntry, now: float) -> None:
        """Log a periodic "still waiting for socket" message for a command that has already exited.

        For commands that return immediately (e.g. ``podman run -d``) but never
        expose their socket, the wait would otherwise be silent until the
        timeout. Log once when the process is first observed to have exited,
        then every ``_PROGRESS_LOG_INTERVAL`` seconds.
        """
        last = self._last_progress_logged.get(id(entry))
        if last is None or now - last >= _PROGRESS_LOG_INTERVAL:
            _logger.info("orchestration: still waiting for socket from command %s", short_command(entry.command))
            self._last_progress_logged[id(entry)] = now

    def stop(self) -> None:
        """Stop every started command that is not ``keep: true``, then unlink its socket.

        SIGTERM → wait 5 s → SIGKILL (shared with the bridge's
        ``terminate_process``). ``keep: true`` commands are left running. Each
        non-keep entry's generated socket is then unlinked (best-effort — the
        bridge may have already unlinked it, covering SIGKILL/crash leaks).
        Idempotent: a second call is a no-op.
        """
        if self._stopped:
            return
        self._stopped = True
        for entry, proc in self._procs:
            if entry.keep:
                _logger.debug("orchestration: leaving pid=%s running (keep: true)", proc.pid)
                continue
            _logger.debug("orchestration: stopping pid=%s", proc.pid)
            terminate_process(proc)
        for entry, _ in self._procs:
            if entry.keep:
                continue
            mount = self._mounts.get(id(entry))
            if mount is not None:
                with contextlib.suppress(OSError):
                    os.unlink(mount.host_path)
        self._wait_for_socket_gone()

    def _wait_for_socket_gone(self) -> None:
        """Wait briefly for sockets to disappear after unlink (covers bind races)."""
        deadline = time.monotonic() + _SOCKET_CLEANUP_WAIT
        while time.monotonic() < deadline:
            still_present = [
                self._mounts[id(entry)].host_path
                for entry, _ in self._procs
                if not entry.keep
                and entry in self._tracked
                and self._mounts.get(id(entry)) is not None
                and os.path.exists(self._mounts[id(entry)].host_path)
            ]
            if not still_present:
                return
            time.sleep(_POLL_INTERVAL)

    def _is_ready(self, host_path: str) -> bool:
        """Check whether the generated unix socket is ready to accept sessions."""
        try:
            mode = os.stat(host_path).st_mode
        except OSError:
            return False
        return stat.S_ISSOCK(mode)

    def socket_mounts(self) -> list[SocketMount]:
        """Return the runtime socket mounts, in configuration order (for the sandbox builder).

        Returns:
            One :class:`~agent_nook.socket_factory.SocketMount` per
            ``track-socket`` entry, in configuration order.
        """
        return [self._mounts[id(e)] for e in self._tracked if id(e) in self._mounts]

    def _make_mount(self, entry: OrchestrationEntry, host_path: str) -> SocketMount:
        """Build the :class:`SocketMount` for a tracked entry.

        ``run_path`` is ``/run/<basename>`` (the sandbox bind-mount target).
        ``env_var`` is ``STDIO_SOCKET_PATH`` when this is the only tracked entry,
        else ``STDIO_SOCKET_PATH`` numbered by this entry's position among tracked
        entries.
        """
        run_path = os.path.join("/run", os.path.basename(host_path))
        env_var = "STDIO_SOCKET_PATH" if len(self._tracked) == 1 else f"STDIO_SOCKET_PATH{self._tracked.index(entry) + 1}"
        return SocketMount(host_path=host_path, run_path=run_path, env_var=env_var)


__all__ = ["Orchestrator", "substitute_placeholders"]
