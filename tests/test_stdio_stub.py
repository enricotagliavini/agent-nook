"""Tests for the standalone stdio_stub.py (error paths and pump behavior).

Bridge-side round trips are covered in test_stdio_bridge.py; here the stub
is exercised against a minimal in-thread unix socket server.
"""

import contextlib
import os
import select
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


SRC = Path(__file__).resolve().parent.parent / "src"
STUB = SRC / "agent_nook" / "stdio_stub.py"


def start_stub(sock_addr: str) -> subprocess.Popen:
    """Start stdio_stub.py connected to ``sock_addr``."""
    return subprocess.Popen(
        [sys.executable, str(STUB), "--socket", sock_addr],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


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


def test_stub_connect_refused_unix(tmp_path):
    """Connecting to a missing unix socket exits 1 with a stderr message."""
    sock = tmp_path / "nope.sock"
    stub = start_stub(str(sock))
    assert stub.wait(timeout=5) == 1
    err = stub.stderr.read()
    close_streams(stub)
    assert b"cannot connect" in err


def test_stub_invalid_address():
    """A malformed address exits 1 with a stderr message."""
    stub = start_stub("tcp:127.0.0.1:99999")
    assert stub.wait(timeout=5) == 1
    err = stub.stderr.read()
    close_streams(stub)
    assert b"port" in err


def test_stub_roundtrip_with_manual_server(tmp_path):
    """stdin is forwarded to the socket and the reply is written to stdout."""
    sock = tmp_path / "srv.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock))
    server.listen(1)

    def serve() -> None:
        conn, _ = server.accept()
        data = conn.recv(65536)
        conn.sendall(b"ECHO:" + data)
        conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        stub = start_stub(str(sock))
        try:
            stub.stdin.write(b"abc")
            stub.stdin.flush()
            read_until(stub, b"ECHO:abc")
            stub.stdin.close()
            assert stub.wait(timeout=5) == 0
        finally:
            close_streams(stub)
    finally:
        thread.join(timeout=5)
        server.close()


def test_stub_reads_socket_from_env(tmp_path, monkeypatch):
    """Without --socket, the socket path comes from STDIO_SOCKET_PATH."""
    sock = tmp_path / "nope.sock"  # nonexistent → connect fails
    monkeypatch.setenv("STDIO_SOCKET_PATH", str(sock))
    proc = subprocess.run([sys.executable, str(STUB)], capture_output=True, timeout=5)
    assert proc.returncode == 1
    assert b"cannot connect" in proc.stderr


def test_stub_requires_address_or_env(tmp_path, monkeypatch):
    """With neither --socket nor STDIO_SOCKET_PATH set, it exits 1."""
    monkeypatch.delenv("STDIO_SOCKET_PATH", raising=False)
    proc = subprocess.run([sys.executable, str(STUB)], capture_output=True, timeout=5)
    assert proc.returncode == 1
    assert b"no socket address" in proc.stderr


def test_stub_socket_flag_precedes_env(tmp_path, monkeypatch):
    """--socket takes precedence: a stale STDIO_SOCKET_PATH is ignored."""
    sock = tmp_path / "srv.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock))
    server.listen(1)

    def serve() -> None:
        conn, _ = server.accept()
        data = conn.recv(65536)
        conn.sendall(b"ECHO:" + data)
        conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        env = dict(os.environ)
        env["STDIO_SOCKET_PATH"] = str(tmp_path / "stale.sock")  # nonexistent
        proc = subprocess.Popen(
            [sys.executable, str(STUB), "--socket", str(sock)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            proc.stdin.write(b"abc")
            proc.stdin.flush()
            read_until(proc, b"ECHO:abc")
            proc.stdin.close()
            assert proc.wait(timeout=5) == 0
        finally:
            close_streams(proc)
    finally:
        thread.join(timeout=5)
        server.close()
