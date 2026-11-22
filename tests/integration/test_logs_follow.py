"""Integration tests for the `agent-nook logs` command.

The logs command delegates to coreutils `tail`:
- one-shot: `tail -n N <logfile>` (raw lines, file order)
- follow:   `tail -n N -F <logfile>` (live stream, rotation-aware)
"""

from __future__ import annotations

import fcntl
import os
import re
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.fixture
def logs_env(tmp_path: Path) -> tuple[Path, dict]:
    """Temporary XDG dirs with a seeded log file and a matching env snapshot."""
    config_dir = tmp_path / "config"
    state_dir = tmp_path / "state"
    config_dir.mkdir()
    state_dir.mkdir()

    old_config = os.environ.get("XDG_CONFIG_HOME")
    old_state = os.environ.get("XDG_STATE_HOME")
    os.environ["XDG_CONFIG_HOME"] = str(config_dir)
    os.environ["XDG_STATE_HOME"] = str(state_dir)

    from agent_nook.utils.logger import get_log_directory

    log_dir = Path(get_log_directory())
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "agent-nook.log"
    log_file.write_text("2026-01-01 00:00:00 - agent_nook - INFO - line one\n2026-01-01 00:00:01 - agent_nook - INFO - line two\n")

    yield log_file, {**os.environ}

    for var, old in (("XDG_CONFIG_HOME", old_config), ("XDG_STATE_HOME", old_state)):
        if old is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = old


def _start_logs_cli(env: dict, *args: str) -> subprocess.Popen:
    # start_new_session=True puts agent-nook and its `tail` child in one process
    # group, so _stop_follow can signal or SIGKILL the whole tree in a single
    # call. Signalling agent-nook's PID alone would leave the following `tail`
    # orphaned on the group, so signalling the group is what makes teardown
    # leak-free.
    return subprocess.Popen(
        [sys.executable, "-m", "agent_nook", "logs", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )


class _LineReader:
    """Reads newline-terminated lines from a pipe with a timeout.

    Reads raw bytes and is the sole consumer of the file descriptor.
    The text-mode wrapper must not be mixed in: its internal decoded
    buffer desynchronizes from fd-level availability checks (select,
    peek), which can block a read forever.
    """

    def __init__(self, proc: subprocess.Popen) -> None:
        self._fd = proc.stdout.fileno()
        flags = fcntl.fcntl(self._fd, fcntl.F_GETFL)
        fcntl.fcntl(self._fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self._buf = b""

    def readline(self, timeout: float = 10.0) -> str:
        """Return one line without its trailing newline.

        Fails the test if the pipe stays silent for `timeout` seconds.
        """
        deadline = time.monotonic() + timeout
        while b"\n" not in self._buf:
            try:
                chunk = os.read(self._fd, 4096)
            except BlockingIOError:
                chunk = None  # no data yet
            if chunk is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    pytest.fail(f"timed out after {timeout}s waiting for a line on stdout")
                select.select([self._fd], [], [], min(0.05, remaining))
                continue
            if not chunk:  # b"" -> EOF
                pytest.fail("stdout closed before a complete line was produced")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line.decode("utf-8", "replace")


def _assert_no_orphan_tail(log_file: Path) -> None:
    """No tail process following our log file must survive the SIGINT."""
    pattern = "tail -n [0-9]+ -F " + re.escape(str(log_file))
    result = subprocess.run(["pgrep", "-f", pattern], capture_output=True, check=False)
    assert result.returncode != 0, f"orphaned tail process still running: {result.stdout!r}"


# SIGINT should make the follow process exit promptly (code 130), but under
# heavy load its main thread can be starved before it reaches that handler.
# Wait a generous grace period, then fall back to SIGKILL the whole process
# group — which also guarantees the following `tail` is never orphaned even if
# agent-nook itself never handled the signal.
_STOP_GRACEFUL_TIMEOUT: float = 20.0


def _stop_follow(proc: subprocess.Popen, log_file: Path) -> None:
    """Stop the follow process and confirm a clean, leak-free shutdown.

    Signals the follow process's process group (agent-nook and the `tail` it
    spawned) with SIGINT. Under normal load both exit 130 quickly. Under heavy
    parallel-CI load the backgrounded process may be descheduled before its
    handler runs, so if it has not exited within ``_STOP_GRACEFUL_TIMEOUT`` we
    SIGKILL the group instead. The orphan check below is the real leak guard and
    always runs.
    """
    group = os.getpgid(proc.pid)
    os.killpg(group, signal.SIGINT)
    exited_gracefully = True
    try:
        proc.wait(timeout=_STOP_GRACEFUL_TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(group, signal.SIGKILL)
        proc.wait(timeout=5)
        exited_gracefully = False
    if exited_gracefully:
        assert proc.returncode == 130, f"expected exit 130 on SIGINT, got {proc.returncode!r}"
    _assert_no_orphan_tail(log_file)


@pytest.mark.integration
def test_logs_one_shot_shows_raw_last_lines(logs_env) -> None:
    """One-shot mode prints the last N lines raw, in file order."""
    _, env = logs_env
    result = subprocess.run(
        [sys.executable, "-m", "agent_nook", "logs", "-n", "1"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "2026-01-01 00:00:01 - agent_nook - INFO - line two"


@pytest.mark.integration
def test_logs_one_shot_without_log_file(logs_env) -> None:
    """Exits cleanly with no output when no log lines exist yet.

    The command's own logger init recreates the (empty) log file before
    tail runs, so the observable result is a successful, empty run.
    """
    log_file, env = logs_env
    log_file.unlink()
    result = subprocess.run(
        [sys.executable, "-m", "agent_nook", "logs"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


@pytest.mark.integration
def test_logs_follow_streams_new_lines(logs_env) -> None:
    """Follow mode prints existing lines, then streams appended ones."""
    log_file, env = logs_env
    proc = _start_logs_cli(env, "-f", "-n", "10")
    reader = _LineReader(proc)
    try:
        assert "line one" in reader.readline()
        assert "line two" in reader.readline()
        with log_file.open("a", encoding="utf-8") as f:
            f.write("2026-01-01 00:00:02 - agent_nook - INFO - line three\n")
        assert "line three" in reader.readline()
    finally:
        _stop_follow(proc, log_file)


@pytest.mark.integration
def test_logs_follow_flag_aliases(logs_env) -> None:
    """-f, -F and --follow are all accepted as follow flags."""
    log_file, env = logs_env
    for flag in ("-f", "-F", "--follow"):
        proc = _start_logs_cli(env, flag, "-n", "1")
        reader = _LineReader(proc)
        try:
            assert "line two" in reader.readline()
        finally:
            _stop_follow(proc, log_file)
