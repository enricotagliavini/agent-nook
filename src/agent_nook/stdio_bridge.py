#!/usr/bin/env python3
"""stdio_bridge — bridge a listening socket to the stdio of a child process.

Standalone tool (Python standard library only; no ``agent_nook`` imports).
Intended to run OUTSIDE the sandbox — on the host or inside a container.
It listens on one socket and, for each incoming connection, spawns the
configured command and forwards bytes between the socket and the child's
stdin/stdout. One connection is handled at a time; after a session ends the
bridge returns to listening and serves the next connection.

Socket address format (shared contract with ``stdio_stub.py`` and
agent-nook's ``orchestration:`` config section):

    /path/to/socket    Unix stream socket
    tcp:host:port      TCP stream socket

(udp: addresses are rejected: a datagram transport has no clean
session-end semantics for a stdio-style tool such as an MCP server.)

The child's stderr is inherited, so it lands in the host/container log.

The child is terminated when the bridge dies: on the normal paths the
bridge does this itself (SIGTERM, then SIGKILL after 5 s); on Linux the
child is additionally given PR_SET_PDEATHSIG=SIGKILL, so the kernel kills
it if the bridge dies without being able to clean up (SIGKILL, OOM, crash).

Exit codes:
    0  normal operation (including clean SIGTERM/SIGINT shutdown)
    2  invalid arguments, bad socket address, or command not found

Example:
    stdio_bridge.py --listen /tmp/mcp.sock -- node dist/index.js
    stdio_bridge.py --listen tcp:127.0.0.1:9000 -- node dist/index.js
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import os
import select
import shutil
import signal
import socket
import subprocess
import sys


CHUNK_SIZE = 65536
_SELECT_TIMEOUT = 0.5  # seconds; keeps every wait interruptible for shutdown
_CHILD_TERMINATE_TIMEOUT = 5.0  # seconds of grace after SIGTERM before SIGKILL

# PR_SET_PDEATHSIG: prctl(2) operation that sets the signal delivered when the
# parent dies. No library exposes this constant at runtime (it is a kernel
# UAPI #define in <linux/prctl.h>, not a libc symbol), so it is named here;
# the value 1 is ABI-stable (UAPI constants never change; unchanged since
# Linux 2.1.57). test_pdeathsig_terminates_child_when_bridge_killed pins the
# value behaviorally: a wrong value makes prctl fail, the child is orphaned,
# and the test fails.
_PR_SET_PDEATHSIG = 1

_shutdown = False


def log(message: str) -> None:
    """Write a status line to stdout (the bridge's own stdout is never bridged)."""
    print(f"stdio_bridge: {message}", flush=True)


def _handle_shutdown(signum, frame) -> None:
    """Signal handler: only set the flag; all waits poll it on a timeout."""
    global _shutdown
    _shutdown = True


def parse_address(address: str) -> tuple[str, str, int]:
    """Parse a socket address into ``(kind, host, port)``.

    Accepted formats:
        /path/to/socket      Unix stream socket (any value without a scheme)
        tcp:host:port        TCP stream socket

    ``udp:`` addresses are rejected: a datagram transport has no clean
    session-end semantics for a stdio-style tool.

    For the ``unix`` kind, ``host`` is the socket path and ``port`` is 0.

    Args:
        address: The address string from --listen.

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


def parse_mode(mode: str) -> int:
    """Parse an octal permission mode string (e.g. "666") into an int.

    Raises:
        ValueError: If the mode is not valid octal or out of range.
    """
    try:
        value = int(mode, 8)
    except ValueError:
        raise ValueError(f"invalid --chmod mode {mode!r}: expected octal like 666") from None
    if not 0 <= value <= 0o777:
        raise ValueError(f"invalid --chmod mode {mode!r}: out of range")
    return value


def create_listener(kind: str, host: str, port: int, mode: int | None) -> socket.socket:
    """Create, bind and (where applicable) listen on the socket.

    For unix sockets, a stale socket file left by an ungraceful previous
    run is removed first. The socket file's permissions are whatever the
    process umask dictates, unless ``mode`` is given, in which case the
    file is chmod'd to it.

    Args:
        kind: One of "unix", "tcp".
        host: Socket path (unix) or host (tcp).
        port: Port (tcp); ignored for unix.
        mode: Permission mode for the unix socket file, or None to leave
            the umask-decided mode in place.

    Returns:
        The bound listening socket.

    Raises:
        OSError: If bind/listen fails.
        RuntimeError: If a stale socket file cannot be removed.
    """
    if kind == "unix":
        path = host
        try:
            if os.path.exists(path) or os.path.islink(path):
                os.unlink(path)
                log(f"removed stale socket file {path}")
        except OSError as e:
            raise RuntimeError(f"cannot remove stale socket file {path}: {e}") from e
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(path)
            if mode is not None:
                os.chmod(path, mode)
        except OSError:
            server.close()
            raise
        server.listen(1)
        return server

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(1)
    return server


def _validate_command(command: list[str]) -> None:
    """Fail fast when the command to spawn is not available.

    Args:
        command: The command list (program plus arguments).

    Raises:
        ValueError: If the program cannot be found.
    """
    program = command[0]
    if os.path.dirname(program):
        if not os.path.exists(program):
            raise ValueError(f"command not found: {program}")
    elif shutil.which(program) is None:
        raise ValueError(f"command not found: {program}")


def _set_pdeathsig() -> None:
    """preexec_fn: ask the kernel to SIGKILL the child when the bridge dies.

    Safety net for uncontrolled bridge death (SIGKILL, OOM, crash) where the
    bridge cannot run its normal SIGTERM -> wait -> SIGKILL cleanup: the
    kernel kills the child instead of leaving it orphaned. SIGKILL (not
    SIGTERM) because this path has no one left to escalate to — a
    SIGTERM-ignoring child would survive SIGTERM forever, and the child has
    already lost its only client (the bridge's socket is dead), so there is
    no shutdown worth making graceful. The guarantee (the child never
    outlives the bridge) holds unconditionally.

    PR_SET_PDEATHSIG is preserved across execve, so it applies to the
    exec'd command. Best effort: a prctl failure (e.g. unsupported kernel)
    must not fail the spawn. Safe to use here because the bridge is
    single-threaded (preexec_fn is only safe in a single-threaded process).
    """
    with contextlib.suppress(OSError):
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)


def spawn_command(command: list[str]) -> subprocess.Popen | None:
    """Spawn the bridged child process with piped stdio.

    The child's stderr is inherited so it lands in the host/container log.
    On Linux the child is given PR_SET_PDEATHSIG=SIGKILL (see
    _set_pdeathsig) as an orphan-avoidance safety net.

    Returns:
        The Popen instance, or None if the spawn failed (already logged).
    """
    try:
        return subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            # The bridge is single-threaded, so preexec_fn is safe (PLW1509
            # is a threads-only hazard; see _set_pdeathsig).
            preexec_fn=_set_pdeathsig if sys.platform == "linux" else None,  # noqa: PLW1509
        )
    except (OSError, ValueError) as e:
        log(f"failed to start command {' '.join(command)}: {e}")
        return None


def write_all(fd: int, data: bytes) -> None:
    """Write all bytes to a raw file descriptor, looping over partial writes.

    Raises:
        OSError: On error (e.g. broken pipe, reset connection).
    """
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def terminate_process(proc: subprocess.Popen) -> None:
    """Terminate the child: SIGTERM, wait up to 5 s, then SIGKILL.

    Also closes any stdio pipes so no file descriptors leak (processes
    spawned without pipes are unaffected).
    """
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=_CHILD_TERMINATE_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    for pipe in (proc.stdin, proc.stdout):
        if pipe is not None:
            with contextlib.suppress(OSError):
                pipe.close()


def graceful_close(conn: socket.socket) -> None:
    """Close a stream connection without resetting the peer.

    Closing a stream socket with unread peer data still in the receive
    buffer makes the kernel send RST, which turns the peer's clean EOF into
    a connection-reset error. Drain what is already buffered first (a
    non-blocking recv returns EAGAIN as soon as the buffer is empty — the
    peer may legitimately keep the connection open and silent), then shut
    down.
    """
    conn.setblocking(False)
    while True:
        try:
            data = conn.recv(CHUNK_SIZE)
        except (BlockingIOError, InterruptedError):
            break  # receive buffer is empty
        except OSError:
            break
        if not data:
            break
    with contextlib.suppress(OSError):
        conn.shutdown(socket.SHUT_RDWR)
    with contextlib.suppress(OSError):
        conn.close()


def _bridge_stream_session(conn: socket.socket, proc: subprocess.Popen) -> None:
    """Pump bytes between one stream connection and the child's stdio.

    Ends (and cleans up) when either side reaches EOF or the session errors:
      - client EOF → the child is terminated
      - child stdout EOF → EOF is sent to the client

    Args:
        conn: The accepted connection (unix or tcp).
        proc: The child process for this session.
    """
    conn_fd = conn.fileno()
    child_in_fd = proc.stdin.fileno()
    child_out_fd = proc.stdout.fileno()
    log(f"started child pid={proc.pid}")
    try:
        while not _shutdown:
            r, _, _ = select.select([conn_fd, child_out_fd], [], [], _SELECT_TIMEOUT)
            if _shutdown:
                log("shutdown requested; ending session")
                break
            if conn_fd in r:
                data = os.read(conn_fd, CHUNK_SIZE)
                if not data:
                    log("client closed the connection; terminating child")
                    break
                write_all(child_in_fd, data)
            if child_out_fd in r:
                data = os.read(child_out_fd, CHUNK_SIZE)
                if not data:
                    log("child stdout closed; sending EOF to client")
                    conn.shutdown(socket.SHUT_WR)
                    break
                write_all(conn_fd, data)
    except OSError as e:
        log(f"session error: {e}")
    finally:
        terminate_process(proc)
        graceful_close(conn)


def _serve_stream(server: socket.socket, command: list[str]) -> None:
    """Accept loop for unix/tcp: one session at a time, until shutdown."""
    server_fd = server.fileno()
    while not _shutdown:
        try:
            r, _, _ = select.select([server_fd], [], [], _SELECT_TIMEOUT)
        except OSError:
            break
        if _shutdown or not r:
            continue
        try:
            conn, addr = server.accept()
        except OSError:
            break
        log(f"connection from {addr}")
        proc = spawn_command(command)
        if proc is None:
            graceful_close(conn)
            continue
        _bridge_stream_session(conn, proc)


def _drain_backlog(server: socket.socket) -> None:
    """Gracefully close any connections pending in the listen backlog.

    On shutdown, connections the kernel queued but we never accept()ed
    would be orphaned by closing the listener, which resets the peer
    (ECONNRESET) instead of ending its session cleanly. Accept and
    gracefully close each so the peer sees a clean EOF.
    """
    server.setblocking(False)
    while True:
        try:
            conn, _ = server.accept()
        except (BlockingIOError, InterruptedError):
            break
        except OSError:
            break
        graceful_close(conn)


def _cleanup_listener(server: socket.socket, kind: str, host: str) -> None:
    """Close the listening socket and remove the unix socket file if present."""
    with contextlib.suppress(OSError):
        server.close()
    if kind == "unix":
        with contextlib.suppress(OSError):
            os.unlink(host)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="stdio_bridge.py",
        description="Bridge a listening socket to the stdin/stdout of a child process (one connection at a time, keeps listening).",
    )
    parser.add_argument(
        "--listen",
        required=True,
        metavar="ADDR",
        help="Socket to listen on: /path (unix) or tcp:host:port",
    )
    parser.add_argument(
        "--chmod",
        default=None,
        metavar="MODE",
        help="Octal permission mode for the unix socket file (default: none — the file keeps the mode the process umask dictates)",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        metavar="COMMAND [ARGS...]",
        help="Command to start per connection (use -- before the command)",
    )
    return parser.parse_args(argv)


def main() -> int:
    """Entry point."""
    args = _parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("stdio_bridge: error: no command specified (usage: --listen ADDR -- COMMAND [ARGS...])", file=sys.stderr)
        return 2
    try:
        kind, host, port = parse_address(args.listen)
        mode = parse_mode(args.chmod) if args.chmod is not None else None
        _validate_command(command)
    except ValueError as e:
        print(f"stdio_bridge: error: {e}", file=sys.stderr)
        return 2
    try:
        server = create_listener(kind, host, port, mode)
    except (OSError, RuntimeError) as e:
        print(f"stdio_bridge: error: {e}", file=sys.stderr)
        return 2

    log(f"listening on {args.listen}")
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)
    try:
        _serve_stream(server, command)
    except BaseException as e:
        # A shutdown signal can interrupt any in-flight syscall (spawn, I/O,
        # wait); once shutdown is requested, treat the resulting error as a
        # clean exit instead of a crash.
        if _shutdown:
            log(f"shutdown in progress ({type(e).__name__}); exiting")
        elif not isinstance(e, KeyboardInterrupt):
            print(f"stdio_bridge: error: {e}", file=sys.stderr)
            return 1
    finally:
        _drain_backlog(server)
        _cleanup_listener(server, kind, host)
    log("bridge exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
