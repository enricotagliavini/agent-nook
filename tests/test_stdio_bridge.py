"""Tests for the standalone stdio_bridge.py / stdio_stub.py tools.

The scripts are launched as plain subprocesses with no PYTHONPATH and no
package imports — exactly how a container or sandbox would run them.
They are run directly from the source tree.
"""

import contextlib
import os
import re
import select
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parent.parent / "src"
BRIDGE = SRC / "agent_nook" / "stdio_bridge.py"
STUB = SRC / "agent_nook" / "stdio_stub.py"

# Child that echoes every chunk of stdin prefixed with b"ECHO:" until EOF.
# Uses raw os.read/os.write: buffered read(n) on a pipe blocks until n bytes
# or EOF, which would deadlock with a chunked client.
ECHO_CHILD = "import os\nwhile True:\n    d = os.read(0, 4096)\n    if not d:\n        break\n    os.write(1, b'ECHO:' + d)\n"

# Child that writes b"BYE" and exits immediately.
BYE_CHILD = "import sys; sys.stdout.buffer.write(b'BYE'); sys.stdout.buffer.flush()"

# Child that never produces output (stays alive until killed).
SLEEP_CHILD = "import time; time.sleep(30)"


def start_bridge(listen: str, command: list[str]) -> subprocess.Popen:
    """Start stdio_bridge.py listening on ``listen`` bridging to ``command``."""
    return subprocess.Popen(
        [sys.executable, str(BRIDGE), "--listen", listen, "--", *command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def start_stub(sock_addr: str) -> subprocess.Popen:
    """Start stdio_stub.py connected to ``sock_addr``."""
    return subprocess.Popen(
        [sys.executable, str(STUB), "--socket", sock_addr],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def wait_for_unix_socket(path: Path, timeout: float = 5.0) -> None:
    """Wait until the unix socket file exists."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_socket():
            return
        time.sleep(0.02)
    raise TimeoutError(f"unix socket {path} did not appear")


def wait_for_tcp(host_port: tuple[str, int], timeout: float = 5.0) -> None:
    """Wait until a TCP port accepts connections."""
    host, port = host_port
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.02)
    raise TimeoutError(f"tcp {host}:{port} is not listening")


def read_until(proc: subprocess.Popen, expected: bytes, timeout: float = 5.0) -> bytes:
    """Read proc.stdout until it contains ``expected`` (or the pipe closes)."""
    buf = b""
    deadline = time.monotonic() + timeout
    while expected not in buf and time.monotonic() < deadline:
        r, _, _ = select.select([proc.stdout], [], [], 0.1)
        if not r:
            continue
        chunk = os.read(proc.stdout.fileno(), 65536)
        if not chunk:
            break
        buf += chunk
    assert expected in buf, f"expected {expected!r} in output, got {buf!r}"
    return buf


def close_streams(proc: subprocess.Popen) -> None:
    """Close any still-open stdin/stdout/stderr pipes of a subprocess."""
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None:
            with contextlib.suppress(OSError):
                stream.close()


def wait_for_bridge_log(bridge: subprocess.Popen, needle: bytes, timeout: float = 5.0) -> bytes:
    """Wait until the bridge's stdout log contains ``needle``; return the log.

    The bridge's own log lines (e.g. "listening on", "started child pid=")
    are the reliable readiness signals — used e.g. to confirm a session is
    active before sending SIGTERM in a test.
    """
    buf = b""
    deadline = time.monotonic() + timeout
    while needle not in buf and time.monotonic() < deadline:
        r, _, _ = select.select([bridge.stdout], [], [], 0.05)
        if r:
            chunk = os.read(bridge.stdout.fileno(), 65536)
            if not chunk:
                break
            buf += chunk
    assert needle in buf, f"bridge log {needle!r} not found in {buf!r}"
    return buf


def test_unix_roundtrip_and_clean_exit(tmp_path):
    """Data flows both ways; closing stdin ends the session with exit 0."""
    sock = tmp_path / "mcp.sock"
    bridge = start_bridge(str(sock), [sys.executable, "-c", ECHO_CHILD])
    try:
        wait_for_unix_socket(sock)
        stub = start_stub(str(sock))
        try:
            stub.stdin.write(b"hello")
            stub.stdin.flush()
            read_until(stub, b"ECHO:hello")
            stub.stdin.close()  # EOF toward the bridge
            assert stub.wait(timeout=5) == 0
        finally:
            close_streams(stub)
    finally:
        bridge.terminate()
        bridge.wait(timeout=5)
        close_streams(bridge)


def test_child_exit_sends_eof_to_client(tmp_path):
    """When the child exits, the client sees EOF and the stub exits 0."""
    sock = tmp_path / "mcp.sock"
    bridge = start_bridge(str(sock), [sys.executable, "-c", BYE_CHILD])
    try:
        wait_for_unix_socket(sock)
        stub = start_stub(str(sock))
        try:
            read_until(stub, b"BYE")
            assert stub.wait(timeout=5) == 0
        finally:
            close_streams(stub)
    finally:
        bridge.terminate()
        bridge.wait(timeout=5)
        close_streams(bridge)


def test_client_eof_terminates_child_and_keeps_listening(tmp_path):
    """Client disconnect kills a stuck child; the bridge serves the next connection."""
    sock = tmp_path / "mcp.sock"
    bridge = start_bridge(str(sock), [sys.executable, "-c", SLEEP_CHILD])
    try:
        wait_for_unix_socket(sock)
        for _ in range(2):
            stub = start_stub(str(sock))
            try:
                stub.stdin.close()  # immediate EOF; the sleeping child must be killed
                assert stub.wait(timeout=5) == 0
            finally:
                close_streams(stub)
    finally:
        bridge.terminate()
        bridge.wait(timeout=5)
        close_streams(bridge)


def test_bridge_keeps_listening_across_sessions(tmp_path):
    """The bridge serves multiple sequential sessions on the same socket."""
    sock = tmp_path / "mcp.sock"
    bridge = start_bridge(str(sock), [sys.executable, "-c", ECHO_CHILD])
    try:
        wait_for_unix_socket(sock)
        for i in range(3):
            stub = start_stub(str(sock))
            try:
                stub.stdin.write(f"ping{i}".encode())
                stub.stdin.flush()
                read_until(stub, f"ECHO:ping{i}".encode())
                stub.stdin.close()
                assert stub.wait(timeout=5) == 0
            finally:
                close_streams(stub)
    finally:
        bridge.terminate()
        bridge.wait(timeout=5)
        close_streams(bridge)


def test_sigterm_removes_socket_and_exits_clean(tmp_path):
    """SIGTERM exits 0 and unlinks the unix socket file."""
    sock = tmp_path / "mcp.sock"
    bridge = start_bridge(str(sock), [sys.executable, "-c", ECHO_CHILD])
    try:
        wait_for_unix_socket(sock)
        bridge.send_signal(signal.SIGTERM)
        assert bridge.wait(timeout=5) == 0
        assert not sock.exists()
    finally:
        close_streams(bridge)


def test_sigterm_during_active_session(tmp_path):
    """SIGTERM mid-session ends the session, kills the child, closes the client."""
    sock = tmp_path / "mcp.sock"
    bridge = start_bridge(str(sock), [sys.executable, "-c", SLEEP_CHILD])
    stub = None
    try:
        wait_for_unix_socket(sock)
        stub = start_stub(str(sock))
        stub.stdin.write(b"hi")
        stub.stdin.flush()
        wait_for_bridge_log(bridge, b"started child")  # session is active
        bridge.send_signal(signal.SIGTERM)
        assert bridge.wait(timeout=5) == 0
        assert not sock.exists()
        assert stub.wait(timeout=5) == 0  # stub saw the socket close
    finally:
        if stub is not None:
            close_streams(stub)
        close_streams(bridge)


def test_invalid_tcp_address_exits_2():
    """An out-of-range port is rejected with exit 2 and a stderr message."""
    proc = start_bridge("tcp:127.0.0.1:99999", [sys.executable, "-c", "pass"])
    assert proc.wait(timeout=5) == 2
    err = proc.stderr.read()
    close_streams(proc)
    assert "port" in err


def test_missing_command_exits_2(tmp_path):
    """A command that cannot be found is rejected up front with exit 2."""
    sock = tmp_path / "mcp.sock"
    proc = start_bridge(str(sock), ["definitely-not-a-real-command-xyz"])
    assert proc.wait(timeout=5) == 2
    err = proc.stderr.read()
    close_streams(proc)
    assert "command not found" in err


def test_tcp_roundtrip():
    """Round trip over a tcp: listening socket works."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    host_port = ("127.0.0.1", port)
    bridge = start_bridge(f"tcp:{host_port[0]}:{host_port[1]}", [sys.executable, "-c", ECHO_CHILD])
    try:
        wait_for_tcp(host_port)
        stub = start_stub(f"tcp:{host_port[0]}:{host_port[1]}")
        try:
            stub.stdin.write(b"hello-tcp")
            stub.stdin.flush()
            read_until(stub, b"ECHO:hello-tcp")
            stub.stdin.close()
            assert stub.wait(timeout=5) == 0
        finally:
            close_streams(stub)
    finally:
        bridge.terminate()
        bridge.wait(timeout=5)
        close_streams(bridge)


def test_udp_address_rejected():
    """udp: addresses are rejected by both tools with a clear error."""
    bridge = start_bridge("udp:127.0.0.1:9999", [sys.executable, "-c", "pass"])
    assert bridge.wait(timeout=5) == 2
    err = bridge.stderr.read()
    close_streams(bridge)
    assert "not supported" in err

    stub = start_stub("udp:127.0.0.1:9999")
    assert stub.wait(timeout=5) == 1
    err = stub.stderr.read()
    close_streams(stub)
    assert b"not supported" in err


def start_bridge_umask77(sock: str, chmod: str | None = None) -> subprocess.Popen:
    """Start the bridge with umask 0o077 (via a wrapper), optionally --chmod."""
    extra = f", '--chmod', {chmod!r}" if chmod else ""
    wrapper = (
        "import os, runpy, sys\n"
        "os.umask(0o077)\n"
        f"sys.argv = ['stdio_bridge.py', '--listen', {sock!r}{extra}, '--', {sys.executable!r}, '-c', 'pass']\n"
        f"runpy.run_path({str(BRIDGE)!r}, run_name='__main__')\n"
    )
    return subprocess.Popen([sys.executable, "-c", wrapper], stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_unix_socket_mode_umask_and_chmod(tmp_path):
    """Socket file mode: umask-decided by default, --chmod overrides."""
    sock_a = tmp_path / "umask.sock"
    bridge_a = start_bridge_umask77(str(sock_a))
    try:
        wait_for_unix_socket(sock_a)
        mode = stat.S_IMODE(os.stat(sock_a).st_mode)
        # default bind() mode is 0o777, masked by the umask 0o077
        assert mode == 0o700, f"expected umask-decided mode 0o700, got {oct(mode)}"
    finally:
        bridge_a.terminate()
        bridge_a.wait(timeout=5)
        close_streams(bridge_a)

    sock_b = tmp_path / "chmod.sock"
    bridge_b = start_bridge_umask77(str(sock_b), chmod="660")
    try:
        wait_for_unix_socket(sock_b)
        mode = stat.S_IMODE(os.stat(sock_b).st_mode)
        assert mode == 0o660, f"expected chmod mode 0o660, got {oct(mode)}"
    finally:
        bridge_b.terminate()
        bridge_b.wait(timeout=5)
        close_streams(bridge_b)


@pytest.mark.skipif(sys.platform != "linux", reason="PR_SET_PDEATHSIG is Linux-specific")
def test_pdeathsig_terminates_child_when_bridge_killed(tmp_path):
    """If the bridge is SIGKILLed, the kernel SIGKILLs the child (no orphan)."""
    sock = tmp_path / "mcp.sock"
    bridge = start_bridge(str(sock), [sys.executable, "-c", SLEEP_CHILD])
    stub = None
    try:
        wait_for_unix_socket(sock)
        stub = start_stub(str(sock))
        stub.stdin.write(b"hi")
        stub.stdin.flush()
        out = wait_for_bridge_log(bridge, b"started child pid=")
        match = re.search(rb"started child pid=(\d+)", out)
        assert match, f"child pid not found in bridge log: {out!r}"
        child_pid = int(match.group(1))
        os.kill(child_pid, 0)  # child is alive
        bridge.kill()  # uncontrolled death: SIGKILL, no cleanup possible
        bridge.wait(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                return  # child is gone — the safety net worked
            except PermissionError:
                return  # reaped (zombie) — gone enough
            time.sleep(0.1)
        pytest.fail(f"child {child_pid} survived the bridge's SIGKILL")
    finally:
        if stub is not None:
            close_streams(stub)
        close_streams(bridge)
