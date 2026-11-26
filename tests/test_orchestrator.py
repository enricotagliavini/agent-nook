"""Tests for the host-side command orchestrator (orchestration: runtime).

The orchestrator is exercised with real subprocesses (fake "bridge"
commands that bind a generated unix socket and sleep) for start/
wait_ready/stop, with a direct unit test for placeholder substitution,
and via BwrapSandbox.run() for the hook — where bwrap itself is mocked the
same way tests/sandbox/test_bwrap_sandbox.py does (the real bwrap
end-to-end path is covered by the S4 integration test).

The orchestrator generates each tracked entry's socket path itself (via
:func:`agent_nook.socket_factory.generate`) and substitutes the
``${STDIO_SOCKET_PATH}`` / ``${STDIO_SOCKET_NAME}`` / ``${STDIO_SOCKET_DIR}``
placeholders into the already-tokenized argv before starting the command. Tests patch that
generation to return deterministic paths under ``tmp_path`` so they can
assert on the socket without depending on the real XDG base dir.
"""

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


sys.path.insert(0, "src")

from agent_nook.config.config import OrchestrationEntry
from agent_nook.config.loader import ConfigLoader
from agent_nook.orchestrator import Orchestrator
from agent_nook.orchestrator import substitute_placeholders
from agent_nook.sandbox import BwrapSandbox
from agent_nook.utils.logger import get_log_file_path
from agent_nook.utils.logger import main_logger


# Fake "bridge" that writes its own pid, binds a unix socket immediately, and
# stays alive (30 s) until killed. Invoked as
#   python -c PID_UNIX_BRIDGE "${STDIO_SOCKET_PATH}" <pidfile>
# so inside the code sys.argv = ['-c', socket_path, pidfile].
PID_UNIX_BRIDGE = (
    "import os, socket, sys, time\n"
    "open(sys.argv[2], 'w').write(str(os.getpid()))\n"
    "s = socket.socket(socket.AF_UNIX)\n"
    "s.bind(sys.argv[1])\n"
    "s.listen(1)\n"
    "time.sleep(30)\n"
)

# Same, but the socket only appears ~0.2 s after start (late readiness).
LATE_UNIX_BRIDGE = (
    "import os, socket, sys, time\n"
    "open(sys.argv[2], 'w').write(str(os.getpid()))\n"
    "time.sleep(0.2)\n"
    "s = socket.socket(socket.AF_UNIX)\n"
    "s.bind(sys.argv[1])\n"
    "s.listen(1)\n"
    "time.sleep(30)\n"
)

# Never creates a socket (for the timeout test).
NO_SOCKET = "import os, sys, time\nopen(sys.argv[2], 'w').write(str(os.getpid()))\ntime.sleep(30)\n"

# Exits immediately without creating a socket (simulates a background command
# like 'podman run -d' that returns right away and never exposes its socket).
EXIT_NO_SOCKET = "import os, sys\nopen(sys.argv[2], 'w').write(str(os.getpid()))\nos._exit(0)\n"

# Writes its argv (socket path, socket dir, pidfile) to the pidfile line by line,
# then binds a unix socket immediately and stays alive — lets the test assert the
# substituted socket path/dir actually reached the spawned command.
ARGS_UNIX_BRIDGE = (
    "import os, socket, sys, time\n"
    "sock_path, dir_path, pidfile = sys.argv[1], sys.argv[2], sys.argv[3]\n"
    "open(pidfile, 'w').write(dir_path + '\\n' + sock_path)\n"
    "s = socket.socket(socket.AF_UNIX)\n"
    "s.bind(sock_path)\n"
    "s.listen(1)\n"
    "time.sleep(30)\n"
)

# keep: true fixture: daemonizes (double fork) so the worker is reaped by
# init after the test kills it — the orchestrator deliberately leaves
# keep entries running, and the test process is not their parent then.
KEEP_BRIDGE = (
    "import os, socket, sys, time\n"
    "sock_path, pidfile = sys.argv[1], sys.argv[2]\n"
    "if os.fork() == 0:\n"
    "    os.setsid()\n"
    "    if os.fork() == 0:\n"
    "        open(pidfile, 'w').write(str(os.getpid()))\n"
    "        s = socket.socket(socket.AF_UNIX)\n"
    "        s.bind(sock_path)\n"
    "        s.listen(1)\n"
    "        time.sleep(30)\n"
    "    os._exit(0)\n"
    "os._exit(0)\n"
)


# --- helpers ---


@pytest.fixture
def gen_dir(tmp_path, monkeypatch):
    """Patch socket generation to return deterministic tmp_path paths and
    expose the temp dir. Generated paths are ``tmp_path/gen{counter}.sock``."""

    def fake_generate(name, counter, *, pid=os.getpid(), base_dir=None):
        return str(tmp_path / f"gen{counter}.sock")

    monkeypatch.setattr("agent_nook.orchestrator.generate", fake_generate)
    return tmp_path


@pytest.fixture
def isolated_logger_path(tmp_path, xdg_state_dir, monkeypatch):
    """Point the logger's resolved log path at tmp_path for the test, then
    restore it to the session state dir afterwards.

    main_logger caches the resolved path (read from the file handler), so a
    bare XDG override is not enough: log_file_handle() and get_log_file_path()
    would keep returning the import-time path. Reset the logger against
    tmp_path before the test, and against the session state dir afterwards, so
    the test is fully isolated and later tests inherit a clean path.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    logger = logging.getLogger("agent_nook")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    main_logger("agent_nook")  # _log_file_path -> tmp_path
    yield
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg_state_dir))
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    main_logger("agent_nook")  # _log_file_path -> session state dir


def _entry(command: list[str], *, wait_timeout: float = 5.0, keep: bool = False, track_socket: bool = True) -> OrchestrationEntry:
    return OrchestrationEntry(
        command=list(command),
        track_socket=track_socket,
        wait_timeout=wait_timeout,
        keep=keep,
    )


def _wait_dead(pid: int, timeout: float = 10.0) -> None:
    """Poll until the pid no longer exists."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.05)
    pytest.fail(f"process {pid} is still alive")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _wait_gone(needle: str) -> None:
    """Poll until no process with ``needle`` in its cmdline exists.

    For paths where the stopped command may die before it could write its
    pid file (stop() races the child's start-up).
    """
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        r = subprocess.run(["pgrep", "-f", needle], check=False, capture_output=True)
        if r.returncode != 0:
            return
        time.sleep(0.05)
    pytest.fail(f"a process matching {needle!r} is still alive")


def _read_pid(pidfile) -> int:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if pidfile.exists():
            return int(pidfile.read_text())
        time.sleep(0.02)
    pytest.fail(f"pid file {pidfile} was never written")


# Reads one byte from stdin (b'' when stdin is detached) then writes a marker
# to stdout via raw os.write() calls, which bypass Python's stdio buffering so
# the output is visible in the log as soon as the child runs.
_IO_BRIDGE = "import os\nif os.read(0, 1) == b'':\n    os.write(1, b'STDIN_EOF')\nos.write(1, b'OUTPUT-GOES-TO-LOG')\n"


def _wait_for_log(needle: str, timeout: float = 5.0) -> None:
    """Poll the log file until ``needle`` appears (or time out)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if needle in Path(get_log_file_path()).read_text(encoding="utf-8"):
            return
        time.sleep(0.05)
    pytest.fail(f"{needle!r} not found in log file within {timeout}s")


# The socket path placeholder the loader keeps literal (and the orchestrator
# substitutes at runtime). Kept as a plain string so it survives f-strings
# unchanged; interpolated with {{ }} only if used inline.
SOCKET_PATH_PLACEHOLDER = "${STDIO_SOCKET_PATH}"


# --- substitute_placeholders (direct unit test) ---


def test_substitute_placeholders():
    """Both placeholders are filled from the host path without re-tokenizing."""
    out = substitute_placeholders(
        ["echo", "${STDIO_SOCKET_PATH}", "${STDIO_SOCKET_NAME}", "end"],
        "/run/agent-nook/mybox-42-0.sock",
    )
    assert out == ["echo", "/run/agent-nook/mybox-42-0.sock", "mybox-42-0.sock", "end"]


def test_substitute_placeholders_leaves_unmatched_tokens():
    """Tokens without a placeholder are returned unchanged."""
    out = substitute_placeholders(["python", "app.py", "--flag"], "/tmp/x.sock")
    assert out == ["python", "app.py", "--flag"]


def test_substitute_placeholders_dir_placeholder():
    """${STDIO_SOCKET_DIR} fills in the socket's base directory (dirname)."""
    out = substitute_placeholders(
        ["podman", "run", "-v", "${STDIO_SOCKET_DIR}:/app:z", "--name", "${STDIO_SOCKET_NAME}"],
        "/run/agent-nook/mybox-42-0.sock",
    )
    assert out == ["podman", "run", "-v", "/run/agent-nook:/app:z", "--name", "mybox-42-0.sock"]


def test_substitute_placeholders_all_three_placeholders():
    """Path, name and dir placeholders coexist without clobbering one another."""
    out = substitute_placeholders(
        ["echo", "${STDIO_SOCKET_PATH}", "${STDIO_SOCKET_NAME}", "${STDIO_SOCKET_DIR}"],
        "/run/agent-nook/mybox-42-0.sock",
    )
    assert out == ["echo", "/run/agent-nook/mybox-42-0.sock", "mybox-42-0.sock", "/run/agent-nook"]


# --- start / wait_ready / stop ---


def test_start_substitutes_socket_dir_placeholder(gen_dir):
    """start() substitutes ${STDIO_SOCKET_DIR} into the argv; the child receives
    the base dir the orchestrator generated before launching, plus the socket path."""
    sock, pidfile = gen_dir / "gen0.sock", gen_dir / "args.pid"
    orch = Orchestrator(
        [_entry([sys.executable, "-c", ARGS_UNIX_BRIDGE, "${STDIO_SOCKET_PATH}", "${STDIO_SOCKET_DIR}", str(pidfile)])]
    )
    try:
        orch.start()
        orch.wait_ready()  # binds at the substituted path, proving PATH substitution too
    finally:
        orch.stop()
    assert pidfile.read_text().splitlines() == [str(gen_dir), str(sock)]
    _wait_gone(str(sock))  # orch.stop() terminated the spawned command


def test_orchestration_output_goes_to_log_not_terminal(capsys, isolated_logger_path):
    """A command's stdout/stderr land in the log file, never the terminal, and
    its stdin is detached from the terminal too."""
    orch = Orchestrator([_entry([sys.executable, "-c", _IO_BRIDGE], track_socket=False)])
    orch.start()
    _wait_for_log("OUTPUT-GOES-TO-LOG")
    orch.stop()

    captured = capsys.readouterr()
    assert "OUTPUT-GOES-TO-LOG" not in captured.out, "child output must not reach the terminal"
    assert "STDIN_EOF" not in captured.out, "stdin must be detached from the terminal"
    log_text = Path(get_log_file_path()).read_text(encoding="utf-8")
    assert "OUTPUT-GOES-TO-LOG" in log_text, "child output must be appended to the log file"


def test_wait_ready_unix_immediate(gen_dir):
    """An already-listening unix socket passes wait_ready; stop() kills the command."""
    sock, pidfile = gen_dir / "gen0.sock", gen_dir / "a.pid"
    orch = Orchestrator([_entry([sys.executable, "-c", PID_UNIX_BRIDGE, "${STDIO_SOCKET_PATH}", str(pidfile)])])
    try:
        orch.start()
        orch.wait_ready()
        assert sock.is_socket()
    finally:
        orch.stop()
    _wait_dead(_read_pid(pidfile))


def test_wait_ready_late_unix(gen_dir):
    """A socket that appears ~0.2 s late is picked up by polling."""
    sock, pidfile = gen_dir / "gen0.sock", gen_dir / "late.pid"
    orch = Orchestrator([_entry([sys.executable, "-c", LATE_UNIX_BRIDGE, "${STDIO_SOCKET_PATH}", str(pidfile)])])
    try:
        orch.start()
        t0 = time.monotonic()
        orch.wait_ready()
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.15, f"socket was reported ready too early ({elapsed:.2f}s)"
        assert sock.is_socket()
    finally:
        orch.stop()
    _wait_dead(_read_pid(pidfile))


def test_parallel_entries(gen_dir):
    """Two entries start in parallel; wait_ready returns only when both are ready."""
    sock_a, sock_b = gen_dir / "gen0.sock", gen_dir / "gen1.sock"
    pid_a, pid_b = gen_dir / "a.pid", gen_dir / "b.pid"
    orch = Orchestrator(
        [
            _entry([sys.executable, "-c", PID_UNIX_BRIDGE, "${STDIO_SOCKET_PATH}", str(pid_a)]),
            _entry([sys.executable, "-c", LATE_UNIX_BRIDGE, "${STDIO_SOCKET_PATH}", str(pid_b)]),
        ]
    )
    try:
        orch.start()
        orch.wait_ready()
        assert sock_a.is_socket() and sock_b.is_socket()
    finally:
        orch.stop()
    _wait_dead(_read_pid(pid_a))
    _wait_dead(_read_pid(pid_b))


def test_timeout_stops_commands_and_raises(gen_dir):
    """A socket that never appears: RuntimeError after wait_timeout, command stopped."""
    sock, pidfile = gen_dir / "gen0.sock", gen_dir / "never.pid"
    orch = Orchestrator([_entry([sys.executable, "-c", NO_SOCKET, "${STDIO_SOCKET_PATH}", str(pidfile)], wait_timeout=0.01)])
    orch.start()
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match=r"not ready after 0.01s"):
        orch.wait_ready()
    elapsed = time.monotonic() - t0
    assert 0.05 <= elapsed < 1, f"timeout did not fire at the configured time (elapsed {elapsed:.2f}s)"
    _wait_dead(_read_pid(pidfile))  # wait_ready() stopped the command itself


def test_wait_ready_logs_when_command_exits_without_socket(gen_dir, caplog):
    """A command that exits without creating its socket logs progress, then times out."""
    sock, pidfile = gen_dir / "gen0.sock", gen_dir / "no.pid"
    orch = Orchestrator([_entry([sys.executable, "-c", EXIT_NO_SOCKET, "${STDIO_SOCKET_PATH}", str(pidfile)], wait_timeout=0.3)])
    orch.start()
    with pytest.raises(RuntimeError, match=r"not ready after 0.3s"):
        orch.wait_ready()
    _wait_dead(_read_pid(pidfile))
    caplog.set_level(logging.INFO)
    assert any("still waiting for socket" in r.message for r in caplog.records), "no progress log while waiting for a dead command"


def test_keep_entry_survives_stop(gen_dir):
    """keep: true commands are left running by stop(); others are stopped."""
    sock_norm, sock_keep = gen_dir / "gen0.sock", gen_dir / "gen1.sock"
    pid_norm, pid_keep = gen_dir / "n.pid", gen_dir / "k.pid"
    orch = Orchestrator(
        [
            _entry([sys.executable, "-c", PID_UNIX_BRIDGE, "${STDIO_SOCKET_PATH}", str(pid_norm)]),
            _entry([sys.executable, "-c", KEEP_BRIDGE, "${STDIO_SOCKET_PATH}", str(pid_keep)], keep=True),
        ]
    )
    try:
        orch.start()
        orch.wait_ready()
        orch.stop()
    finally:
        orch.stop()  # idempotent
    _wait_dead(_read_pid(pid_norm))
    keep_pid = _read_pid(pid_keep)
    try:
        assert _alive(keep_pid), "keep: true command was stopped"
    finally:
        os.kill(keep_pid, signal.SIGTERM)
        _wait_dead(keep_pid)


def test_spawn_failure_stops_started_commands(gen_dir):
    """If a later entry fails to spawn, already-started entries are stopped."""
    sock, pidfile = gen_dir / "gen0.sock", gen_dir / "a.pid"
    orch = Orchestrator(
        [
            _entry([sys.executable, "-c", PID_UNIX_BRIDGE, "${STDIO_SOCKET_PATH}", str(pidfile)]),
            _entry(["/nonexistent/command-xyz"], track_socket=False),
        ]
    )
    with pytest.raises(RuntimeError, match="failed to start orchestration command"):
        orch.start()
    _wait_gone(str(sock))  # the started entry was stopped (no pid file: SIGTERM raced start-up)


def test_empty_orchestrator_is_noop():
    """No entries: start/wait_ready/stop are no-ops; stop() is idempotent."""
    orch = Orchestrator([])
    orch.start()
    orch.wait_ready()
    orch.stop()
    orch.stop()


def test_socket_mounts_reports_only_tracked_entries(gen_dir):
    """socket_mounts returns one SocketMount per tracked entry, in config order."""
    orch = Orchestrator(
        [
            _entry([sys.executable, "-c", PID_UNIX_BRIDGE, "${STDIO_SOCKET_PATH}", str(gen_dir / "a.pid")]),
            _entry(["echo", "noop"], track_socket=False),
            _entry([sys.executable, "-c", PID_UNIX_BRIDGE, "${STDIO_SOCKET_PATH}", str(gen_dir / "b.pid")]),
        ]
    )
    orch.start()
    try:
        orch.wait_ready()
        mounts = orch.socket_mounts()
        assert [m.host_path for m in mounts] == [str(gen_dir / "gen0.sock"), str(gen_dir / "gen1.sock")]
        assert all(m.run_path.startswith("/run/") for m in mounts)
    finally:
        orch.stop()


# --- BwrapSandbox.run() hook (bwrap itself is mocked) ---


def _mock_bwrap(monkeypatch, returncode: int = 0):
    """Replace subprocess.run inside the executor; returns captured invocations."""
    calls: list = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class _Result:
            pass

        r = _Result()
        r.returncode = returncode
        return r

    monkeypatch.setattr("agent_nook.sandbox.bwrap_sandbox.subprocess.run", fake_run)
    return calls


def _setenv_map(cmd: list[str]) -> dict[str, str]:
    """Return the env-var bindings (``--setenv VAR value``) from a bwrap command."""
    env: dict[str, str] = {}
    for i, tok in enumerate(cmd):
        if tok == "--setenv" and i + 2 < len(cmd):
            env[cmd[i + 1]] = cmd[i + 2]
    return env


def _bind_map(cmd: list[str]) -> dict[str, str]:
    """Return the host-path bindings (``--bind SRC DST``) from a bwrap command."""
    binds: dict[str, str] = {}
    for i, tok in enumerate(cmd):
        if tok == "--bind" and i + 2 < len(cmd):
            binds[cmd[i + 1]] = cmd[i + 2]
    return binds


def _hook_config(tmp_path, orchestration: list[dict]):
    return ConfigLoader().set(
        {
            "name": "orch-hook",
            "mounts": [{"target": "/proc", "type": "proc"}],
            "orchestration": orchestration,
        }
    )


def test_run_starts_and_stops_orchestration(tmp_path, monkeypatch):
    """On success the orchestration command is stopped after the sandbox exits."""
    sock, pidfile = tmp_path / "m.sock", tmp_path / "m.pid"
    calls = _mock_bwrap(monkeypatch)
    config = _hook_config(
        tmp_path,
        [{"command": f'{sys.executable} -c "{PID_UNIX_BRIDGE}" {SOCKET_PATH_PLACEHOLDER} {pidfile}', "track-socket": True}],
    )
    result = BwrapSandbox(config).run(["echo", "hi"])
    assert result.success
    assert len(calls) == 1
    _wait_dead(_read_pid(pidfile))


def test_run_starts_and_waits_for_substituted_socket(tmp_path, monkeypatch):
    """The generated socket path is substituted into the argv and the bridge binds it."""
    _mock_bwrap(monkeypatch)
    config = _hook_config(
        tmp_path,
        [{"command": f'{sys.executable} -c "{PID_UNIX_BRIDGE}" {SOCKET_PATH_PLACEHOLDER} {tmp_path / "m.pid"}', "track-socket": True}],
    )
    # Should not raise: wait_ready must see the substituted path become ready.
    result = BwrapSandbox(config).run(["echo", "hi"])
    assert result.success
    _wait_dead(_read_pid(tmp_path / "m.pid"))


def test_run_stops_orchestration_when_sandbox_fails(tmp_path, monkeypatch):
    """A non-zero sandbox exit still stops the orchestration commands."""
    _mock_bwrap(monkeypatch, returncode=3)
    config = _hook_config(
        tmp_path,
        [{"command": f'{sys.executable} -c "{PID_UNIX_BRIDGE}" {SOCKET_PATH_PLACEHOLDER} {tmp_path / "m.pid"}', "track-socket": True}],
    )
    result = BwrapSandbox(config).run(["echo", "hi"])
    assert result.return_code == 3 and not result.success
    _wait_dead(_read_pid(tmp_path / "m.pid"))


def test_run_stops_orchestration_on_bwrap_timeout(tmp_path, monkeypatch):
    """A bwrap timeout (subprocess.TimeoutExpired) still stops the orchestration commands."""

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

    monkeypatch.setattr("agent_nook.sandbox.bwrap_sandbox.subprocess.run", fake_run)
    config = _hook_config(
        tmp_path,
        [{"command": f'{sys.executable} -c "{PID_UNIX_BRIDGE}" {SOCKET_PATH_PLACEHOLDER} {tmp_path / "m.pid"}', "track-socket": True}],
    )
    with pytest.raises(subprocess.TimeoutExpired):
        BwrapSandbox(config).run(["sleep", "10"])
    _wait_dead(_read_pid(tmp_path / "m.pid"))


def test_run_never_starts_sandbox_when_socket_never_ready(tmp_path, monkeypatch):
    """If the socket never becomes ready, bwrap is never invoked and the command is stopped."""
    calls = _mock_bwrap(monkeypatch)
    config = _hook_config(
        tmp_path,
        [
            {
                "command": f'{sys.executable} -c "{NO_SOCKET}" {SOCKET_PATH_PLACEHOLDER} {tmp_path / "never.pid"}',
                "track-socket": True,
                "wait-timeout": 0.01,
            }
        ],
    )
    with pytest.raises(RuntimeError, match="not ready after"):
        BwrapSandbox(config).run(["echo", "hi"])
    assert calls == [], "bwrap must not start when an orchestration socket never becomes ready"
    _wait_dead(_read_pid(tmp_path / "never.pid"))


def test_run_without_orchestration_is_unchanged(tmp_path, monkeypatch):
    """Zero orchestration entries: the hook is a no-op, bwrap runs exactly once."""
    calls = _mock_bwrap(monkeypatch)
    config = ConfigLoader().set(
        {
            "name": "no-orch",
            "mounts": [{"target": "/proc", "type": "proc"}],
        }
    )
    result = BwrapSandbox(config).run(["echo", "hi"])
    assert result.success
    assert len(calls) == 1


def test_run_wire_single_socket_mount(tmp_path, monkeypatch):
    """One tracked entry: bwrap gets --setenv STDIO_SOCKET_PATH and --bind to
    the generated host socket's /run path (single-entry env name)."""
    sock = tmp_path / "orch-hook-123-0.sock"

    def fake_generate(name, counter, *, pid=os.getpid(), base_dir=None):
        return str(sock)

    monkeypatch.setattr("agent_nook.orchestrator.generate", fake_generate)
    calls = _mock_bwrap(monkeypatch)
    config = _hook_config(
        tmp_path,
        [{"command": f'{sys.executable} -c "{PID_UNIX_BRIDGE}" {SOCKET_PATH_PLACEHOLDER} {tmp_path / "m.pid"}', "track-socket": True}],
    )
    result = BwrapSandbox(config).run(["echo", "hi"])
    assert result.success
    assert len(calls) == 1
    env = _setenv_map(calls[0])
    binds = _bind_map(calls[0])
    assert env["STDIO_SOCKET_PATH"] == f"/run/{sock.name}"
    assert binds[str(sock)] == f"/run/{sock.name}"
    _wait_dead(_read_pid(tmp_path / "m.pid"))


def test_run_wire_numbered_socket_mounts(tmp_path, monkeypatch):
    """Multiple tracked entries get STDIO_SOCKET_PATH1/2 in config order, each
    bound to its generated socket's /run path."""
    socks = [tmp_path / "orch-hook-123-0.sock", tmp_path / "orch-hook-123-1.sock"]

    def fake_generate(name, counter, *, pid=os.getpid(), base_dir=None):
        return str(socks[counter])

    monkeypatch.setattr("agent_nook.orchestrator.generate", fake_generate)
    calls = _mock_bwrap(monkeypatch)
    config = _hook_config(
        tmp_path,
        [
            {
                "command": f'{sys.executable} -c "{PID_UNIX_BRIDGE}" {SOCKET_PATH_PLACEHOLDER} {tmp_path / "a.pid"}',
                "track-socket": True,
            },
            {
                "command": f'{sys.executable} -c "{PID_UNIX_BRIDGE}" {SOCKET_PATH_PLACEHOLDER} {tmp_path / "b.pid"}',
                "track-socket": True,
            },
        ],
    )
    result = BwrapSandbox(config).run(["echo", "hi"])
    assert result.success
    env = _setenv_map(calls[0])
    binds = _bind_map(calls[0])
    assert env["STDIO_SOCKET_PATH1"] == f"/run/{socks[0].name}"
    assert env["STDIO_SOCKET_PATH2"] == f"/run/{socks[1].name}"
    assert binds[str(socks[0])] == f"/run/{socks[0].name}"
    assert binds[str(socks[1])] == f"/run/{socks[1].name}"
    _wait_dead(_read_pid(tmp_path / "a.pid"))
    _wait_dead(_read_pid(tmp_path / "b.pid"))
