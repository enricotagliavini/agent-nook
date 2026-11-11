#!/usr/bin/env python3
"""stdio_stub — bridge this process's stdin/stdout to a socket.

Standalone tool (Python standard library only; no ``agent_nook`` imports).
Intended to run INSIDE the sandbox. It connects to a socket created by
``stdio_bridge.py`` (or any other listener) and forwards bytes in both
directions: stdin → socket and socket → stdout. This lets a sandboxed
process talk to an external application (e.g. an MCP server in a container)
over plain stdio.

Exit when:
  - stdin is exhausted → EOF is sent to the socket (shutdown of the write
    side), the socket is then drained until it closes, and the stub exits 0
  - the socket reaches EOF → the stub exits 0

Socket address format (shared contract with ``stdio_bridge.py`` and
agent-nook's ``orchestration:`` config section):

    /path/to/socket    Unix stream socket
    tcp:host:port      TCP stream socket

(udp: addresses are rejected: a datagram transport has no clean
session-end semantics for a stdio-style tool such as an MCP server.)

Exit codes:
    0  clean session end (stdin EOF or socket EOF)
    1  invalid address or connect/IO failure

Example:
    stdio_stub.py --socket /tmp/mcp.sock
"""

from __future__ import annotations

import argparse
import contextlib
import os
import select
import socket
import sys


CHUNK_SIZE = 65536


def parse_address(address: str) -> tuple[str, str, int]:
    """Parse a socket address into ``(kind, host, port)``.

    Accepted formats:
        /path/to/socket      Unix stream socket (any value without a scheme)
        tcp:host:port        TCP stream socket

    ``udp:`` addresses are rejected: a datagram transport has no clean
    session-end semantics for a stdio-style tool.

    For the ``unix`` kind, ``host`` is the socket path and ``port`` is 0.

    Args:
        address: The address string from --socket.

    Returns:
        A tuple of (kind, host, port).

    Raises:
        ValueError: If the address is empty, unsupported, malformed, or the
            port is out of range.
    """
    if not address:
        raise ValueError("socket address must not be empty")
    if address.startswith("udp:"):
        raise ValueError("udp: addresses are not supported (use a unix path or tcp:host:port)")
    if address.startswith("tcp:"):
        kind, _, rest = address.partition(":")
        host, _, port_str = rest.rpartition(":")
        if not host or not port_str:
            raise ValueError(f"malformed {kind} address {address!r}: expected {kind}:host:port")
        try:
            port = int(port_str)
        except ValueError:
            raise ValueError(f"malformed port {port_str!r} in {address!r}") from None
        if not 1 <= port <= 65535:
            raise ValueError(f"port out of range (1-65535): {port}")
        return kind, host, port
    return "unix", address, 0


def connect(kind: str, host: str, port: int) -> socket.socket:
    """Connect to the socket.

    Args:
        kind: One of "unix", "tcp".
        host: Socket path (unix) or host (tcp).
        port: Port (tcp); ignored for unix.

    Returns:
        The connected socket.

    Raises:
        OSError: If the connection fails (e.g. no such unix socket, refused).
    """
    if kind == "unix":
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(host)
        return s
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((host, port))
    return s


def write_all(fd: int, data: bytes) -> None:
    """Write all bytes to a raw file descriptor, looping over partial writes.

    Raises:
        OSError: On error (e.g. broken pipe).
    """
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _pump_stream(conn: socket.socket) -> None:
    """Pump bytes between stdin and a stream socket until either side EOFs.

    When stdin reaches EOF, the write side of the socket is shut down so the
    peer sees EOF; the socket is then drained until it closes.

    Args:
        conn: The connected unix or tcp socket.

    Raises:
        OSError: On I/O failure (e.g. connection reset).
    """
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    sock_fd = conn.fileno()
    stdin_ended = False
    while True:
        watch = [sock_fd]
        if not stdin_ended:
            watch.append(stdin_fd)
        r, _, _ = select.select(watch, [], [])
        if stdin_fd in r and not stdin_ended:
            data = os.read(stdin_fd, CHUNK_SIZE)
            if not data:
                stdin_ended = True
                conn.shutdown(socket.SHUT_WR)
            else:
                write_all(sock_fd, data)
        if sock_fd in r:
            data = os.read(sock_fd, CHUNK_SIZE)
            if not data:
                return
            write_all(stdout_fd, data)


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(
        prog="stdio_stub.py",
        description="Bridge this process's stdin/stdout to a socket.",
    )
    parser.add_argument(
        "--socket",
        required=True,
        metavar="ADDR",
        help="Socket to connect to: /path (unix) or tcp:host:port",
    )
    args = parser.parse_args()

    try:
        kind, host, port = parse_address(args.socket)
    except ValueError as e:
        print(f"stdio_stub: error: {e}", file=sys.stderr)
        return 1
    try:
        conn = connect(kind, host, port)
    except OSError as e:
        print(f"stdio_stub: error: cannot connect to {args.socket}: {e}", file=sys.stderr)
        return 1

    try:
        _pump_stream(conn)
    except OSError as e:
        print(f"stdio_stub: error: {e}", file=sys.stderr)
        return 1
    finally:
        with contextlib.suppress(OSError):
            conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
