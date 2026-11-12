"""Tests for the host-side command orchestrator (orchestration: runtime).

The orchestrator is exercised with real subprocesses (fake "bridge"
commands that bind a socket and sleep) for start/wait_ready/stop, and
with BwrapSandbox.run() for the hook — where bwrap itself is mocked the
same way tests/sandbox/test_bwrap_sandbox.py does (the real bwrap
end-to-end path is covered by the S4 integration test).
"""

import os
import signal
import socket
import subprocess
import sys
import time

import pytest


sys.path.insert(0, "src")

from agent_nook.config.config import OrchestrationEntry
from agent_nook.config.loader import ConfigLoader
from agent_nook.orchestrator import Orchestrator
from agent_nook.sandbox import BwrapSandbox


# Fake "bridge" that writes its own pid, binds a unix socket immediately,
# and stays alive (30 s) until killed.
PID_UNIX_BRIDGE = (
    "import os, socket, sys, time\n"
    "open(sys.argv[2], 'w').write(str(os.getpid()))\n"
    "s = socket.socket(socket.AF_UNIX)\n"
    "s.bind(sys.argv[1])\n"
    "s.listen(1)\n"
    "time.sleep(30)\n"
)

# Same, but the socket only appears ~0.5 s after start (late readiness).
LATE_UNIX_BRIDGE = (
    "import os, socket, sys, time\n"
    "open(sys.argv[2], 'w').write(str(os.getpid()))\n"
    "time.sleep(0.5)\n"
    "s = socket.socket(socket.AF_UNIX)\n"
    "s.bind(sys.argv[1])\n"
    "s.listen(1)\n"
    "time.sleep(30)\n"
)

# Fake bridge for a tcp socket on 127.0.0.1.
TCP_BRIDGE = (
    "import os, socket, sys, time\n"
    "open(sys.argv[2], 'w').write(str(os.getpid()))\n"
    "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
    "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
    "s.bind(('127.0.0.1', int(sys.argv[1])))\n"
    "s.listen(1)\n"
    "time.sleep(30)\n"
)

# Never creates a socket (for the timeout test).
NO_SOCKET = "import os, sys, time\nopen(sys.argv[1], 'w').write(str(os.getpid()))\ntime.sleep(30)\n"

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


def _entry(command: list[str], sock: str, wait_timeout: float = 5.0, keep: bool = False) -> OrchestrationEntry:
    return OrchestrationEntry(command=command, socket=sock, wait_timeout=wait_timeout, keep=keep)


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


def _free_tcp_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --- start / wait_ready / stop ---


def test_wait_ready_unix_immediate(tmp_path):
    """An already-listening unix socket passes wait_ready; stop() kills the command."""
    sock, pidfile = tmp_path / "a.sock", tmp_path / "a.pid"
    orch = Orchestrator([_entry([sys.executable, "-c", PID_UNIX_BRIDGE, str(sock), str(pidfile)], str(sock))])
    try:
        orch.start()
        orch.wait_ready()
        assert sock.is_socket()
    finally:
        orch.stop()
    _wait_dead(_read_pid(pidfile))


def test_wait_ready_late_unix(tmp_path):
    """A socket that appears ~0.5 s late is picked up by polling."""
    sock, pidfile = tmp_path / "late.sock", tmp_path / "late.pid"
    orch = Orchestrator([_entry([sys.executable, "-c", LATE_UNIX_BRIDGE, str(sock), str(pidfile)], str(sock))])
    try:
        orch.start()
        t0 = time.monotonic()
        orch.wait_ready()
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.4, f"socket was reported ready too early ({elapsed:.2f}s)"
        assert sock.is_socket()
    finally:
        orch.stop()
    _wait_dead(_read_pid(pidfile))


def test_wait_ready_tcp(tmp_path):
    """A listening tcp socket passes wait_ready; stop() kills the command."""
    port = _free_tcp_port()
    pidfile = tmp_path / "tcp.pid"
    orch = Orchestrator([_entry([sys.executable, "-c", TCP_BRIDGE, str(port), str(pidfile)], f"tcp:127.0.0.1:{port}")])
    try:
        orch.start()
        orch.wait_ready()
    finally:
        orch.stop()
    _wait_dead(_read_pid(pidfile))


def test_parallel_entries(tmp_path):
    """Two entries start in parallel; wait_ready returns only when both are ready."""
    sock_a, sock_b = tmp_path / "a.sock", tmp_path / "b.sock"
    pid_a, pid_b = tmp_path / "a.pid", tmp_path / "b.pid"
    orch = Orchestrator(
        [
            _entry([sys.executable, "-c", PID_UNIX_BRIDGE, str(sock_a), str(pid_a)], str(sock_a)),
            _entry([sys.executable, "-c", LATE_UNIX_BRIDGE, str(sock_b), str(pid_b)], str(sock_b)),
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


def test_timeout_stops_commands_and_raises(tmp_path):
    """A socket that never appears: RuntimeError after wait_timeout, command stopped."""
    sock, pidfile = tmp_path / "never.sock", tmp_path / "never.pid"
    orch = Orchestrator([_entry([sys.executable, "-c", NO_SOCKET, str(pidfile)], str(sock), wait_timeout=1)])
    orch.start()
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="not ready after 1s"):
        orch.wait_ready()
    elapsed = time.monotonic() - t0
    assert 0.9 <= elapsed < 5, f"timeout did not fire at the configured time (elapsed {elapsed:.2f}s)"
    _wait_dead(_read_pid(pidfile))  # wait_ready() stopped the command itself


def test_keep_entry_survives_stop(tmp_path):
    """keep: true commands are left running by stop(); others are stopped."""
    sock_norm, sock_keep = tmp_path / "n.sock", tmp_path / "k.sock"
    pid_norm, pid_keep = tmp_path / "n.pid", tmp_path / "k.pid"
    orch = Orchestrator(
        [
            _entry([sys.executable, "-c", PID_UNIX_BRIDGE, str(sock_norm), str(pid_norm)], str(sock_norm)),
            _entry([sys.executable, "-c", KEEP_BRIDGE, str(sock_keep), str(pid_keep)], str(sock_keep), keep=True),
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


def test_spawn_failure_stops_started_commands(tmp_path):
    """If a later entry fails to spawn, already-started entries are stopped."""
    sock, pidfile = tmp_path / "a.sock", tmp_path / "a.pid"
    orch = Orchestrator(
        [
            _entry([sys.executable, "-c", PID_UNIX_BRIDGE, str(sock), str(pidfile)], str(sock)),
            _entry(["/nonexistent/command-xyz"], str(sock)),
        ]
    )
    with pytest.raises(RuntimeError, match="failed to start orchestration command"):
        orch.start()
    _wait_gone(str(sock))  # the started entry was stopped (no pid file: SIGTERM raced start-up)


def test_malformed_address_fails_fast_and_stops(tmp_path):
    """An address the loader cannot catch (out-of-range port) fails before waiting."""
    pidfile = tmp_path / "x.pid"
    orch = Orchestrator([_entry([sys.executable, "-c", NO_SOCKET, str(pidfile)], "tcp:127.0.0.1:99999999", wait_timeout=30)])
    orch.start()
    with pytest.raises(RuntimeError, match="orchestration entry socket"):
        orch.wait_ready()
    _wait_gone(str(pidfile))  # stop() raced the child's start-up; no pid file


def test_empty_orchestrator_is_noop():
    """No entries: start/wait_ready/stop are no-ops; stop() is idempotent."""
    orch = Orchestrator([])
    orch.start()
    orch.wait_ready()
    orch.stop()
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
        [{"command": [sys.executable, "-c", PID_UNIX_BRIDGE, str(sock), str(pidfile)], "socket": str(sock)}],
    )
    result = BwrapSandbox(config).run(["echo", "hi"])
    assert result.success
    assert len(calls) == 1
    _wait_dead(_read_pid(pidfile))


def test_run_stops_orchestration_when_sandbox_fails(tmp_path, monkeypatch):
    """A non-zero sandbox exit still stops the orchestration commands."""
    sock, pidfile = tmp_path / "m.sock", tmp_path / "m.pid"
    _mock_bwrap(monkeypatch, returncode=3)
    config = _hook_config(
        tmp_path,
        [{"command": [sys.executable, "-c", PID_UNIX_BRIDGE, str(sock), str(pidfile)], "socket": str(sock)}],
    )
    result = BwrapSandbox(config).run(["echo", "hi"])
    assert result.return_code == 3 and not result.success
    _wait_dead(_read_pid(pidfile))


def test_run_stops_orchestration_on_bwrap_timeout(tmp_path, monkeypatch):
    """A bwrap timeout (subprocess.TimeoutExpired) still stops the orchestration commands."""
    sock, pidfile = tmp_path / "m.sock", tmp_path / "m.pid"

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

    monkeypatch.setattr("agent_nook.sandbox.bwrap_sandbox.subprocess.run", fake_run)
    config = _hook_config(
        tmp_path,
        [{"command": [sys.executable, "-c", PID_UNIX_BRIDGE, str(sock), str(pidfile)], "socket": str(sock)}],
    )
    with pytest.raises(subprocess.TimeoutExpired):
        BwrapSandbox(config).run(["sleep", "10"])
    _wait_dead(_read_pid(pidfile))


def test_run_never_starts_sandbox_when_socket_never_ready(tmp_path, monkeypatch):
    """If the socket never becomes ready, bwrap is never invoked and the command is stopped."""
    sock, pidfile = tmp_path / "never.sock", tmp_path / "never.pid"
    calls = _mock_bwrap(monkeypatch)
    config = _hook_config(
        tmp_path,
        [
            {
                "command": [sys.executable, "-c", NO_SOCKET, str(pidfile)],
                "socket": str(sock),
                "wait-timeout": 1,
            }
        ],
    )
    with pytest.raises(RuntimeError, match="not ready after"):
        BwrapSandbox(config).run(["echo", "hi"])
    assert calls == [], "bwrap must not start when an orchestration socket never becomes ready"
    _wait_dead(_read_pid(pidfile))


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
