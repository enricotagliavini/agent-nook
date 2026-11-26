"""Tests for the XDG logging setup (SandboxFormatter, set_sandbox_name)."""

import logging
import os
import re
import sys
from pathlib import Path

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_nook.utils.logger import SandboxFormatter
from agent_nook.utils.logger import get_log_directory
from agent_nook.utils.logger import get_log_file_path
from agent_nook.utils.logger import main_logger
from agent_nook.utils.logger import set_sandbox_name


LINE_RE = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"


def _record(name: str = "agent_nook", message: str = "hello") -> logging.LogRecord:
    return logging.LogRecord(name, logging.INFO, __file__, 1, message, None, None)


@pytest.fixture
def isolated_agent_nook_logger(monkeypatch, tmp_path):
    """Point the "agent_nook" logger at a temporary state directory.

    Resets any handlers the logger already has (importing agent_nook
    configures it as a side effect) and leaves it clean afterwards.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    logger = logging.getLogger("agent_nook")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    yield logger
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def test_formatter_without_sandbox_name_matches_legacy_format():
    """Before a sandbox name is set, output is exactly the legacy format."""
    formatter = SandboxFormatter()
    line = formatter.format(_record())

    assert re.fullmatch(rf"{LINE_RE} - agent_nook - INFO - hello", line)


def test_formatter_with_sandbox_name_tags_logger_name():
    """After a sandbox name is set, the logger name renders as name[sandbox]."""
    formatter = SandboxFormatter(sandbox_name="mybox")
    line = formatter.format(_record())

    assert re.fullmatch(rf"{LINE_RE} - agent_nook\[mybox\] - INFO - hello", line)


def test_formatter_keeps_dotted_child_logger_names():
    """Child loggers keep their dotted names, with the sandbox suffix appended."""
    formatter = SandboxFormatter(sandbox_name="mybox")
    line = formatter.format(_record(name="agent_nook.sandbox.bwrap_sandbox"))

    assert re.fullmatch(rf"{LINE_RE} - agent_nook\.sandbox\.bwrap_sandbox\[mybox\] - INFO - hello", line)


def test_format_is_stable_when_record_formatted_twice():
    """A LogRecord is shared across handlers; reformatting must be idempotent.

    Guards against mutating record.name, which would double-suffix the name
    (agent_nook[box][box]) when a second handler formats the same record.
    """
    formatter = SandboxFormatter(sandbox_name="mybox")
    record = _record()

    first = formatter.format(record)
    second = formatter.format(record)

    assert first == second
    assert re.search(r" - agent_nook\[mybox\] - INFO - hello$", first)


def test_set_sandbox_name_tags_lines_and_keeps_log_file(isolated_agent_nook_logger, tmp_path):
    """Lines after set_sandbox_name are tagged; the log file is not changed."""
    logger = main_logger("agent_nook")

    logger.info("before sandbox")
    set_sandbox_name("mybox")
    logger.info("after sandbox")

    log_file = tmp_path / "agent-nook" / "agent-nook.log"
    assert log_file.exists()
    assert not (tmp_path / "agent-nook" / "nook-mybox.log").exists()

    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert any(re.search(rf"^{LINE_RE} - agent_nook - INFO - before sandbox$", line) for line in lines)
    assert any(re.search(rf"^{LINE_RE} - agent_nook\[mybox\] - INFO - after sandbox$", line) for line in lines)


def test_logger_falls_back_to_tmp_and_getters_agree(isolated_agent_nook_logger, tmp_path, monkeypatch):
    """When the XDG state dir is not writable, the logger falls back to /tmp,
    and get_log_file_path()/get_log_directory() report that /tmp path — the
    single source of truth — so they agree with where the handler writes.

    Guards against the log_file_handle()/logger and the getters diverging when
    the /tmp fallback is taken.
    """
    if os.geteuid() == 0:
        pytest.skip("cannot make a dir unwritable while running as root")

    # A 0o555 dir refuses writes for non-root, simulating an unwritable XDG
    # state dir and forcing the /tmp fallback.
    unreadable = tmp_path / "unreadable"
    unreadable.mkdir()
    unreadable.chmod(0o555)
    monkeypatch.setenv("XDG_STATE_HOME", str(unreadable))

    logger = main_logger("agent_nook")
    logger.info("fallback-marker")

    log_path = get_log_file_path()
    assert log_path.startswith("/tmp/agent-nook-logs/"), log_path
    assert get_log_directory() == "/tmp/agent-nook-logs", get_log_directory()
    # The handler actually wrote there: the marker is in the /tmp log, not a
    # file under the (unwritable) XDG state dir.
    assert Path(log_path).read_text(encoding="utf-8").find("fallback-marker") != -1
