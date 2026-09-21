"""Tests for the BwrapSandbox executor (build and run).

Complements test_builder.py, which covers BwrapBuilder in isolation:
these tests exercise BwrapSandbox.build() delegation, command
appending, and BwrapSandbox.run() execution end-to-end.
"""

import os
import subprocess
import sys
import tempfile

import pytest


sys.path.insert(0, "src")

from agent_nook.config.loader import ConfigLoader
from agent_nook.sandbox import BwrapSandbox


def test_build_command_with_ro_bind():
    """Test that ro-bind mounts produce --ro-bind arguments."""
    config = ConfigLoader().set(
        {
            "name": "test",
            "chdir": "/tmp",
            "mounts": [
                {"source": "/readonly", "target": "/readonly", "type": "ro-bind"},
                {"source": "/writable", "target": "/writable", "type": "bind"},
            ],
            "capabilities": {"drop": ["ALL"]},
            "unshare": {"pid": True},
        }
    )
    cmd = BwrapSandbox(config).build()
    assert "--ro-bind" in cmd
    assert "/readonly" in cmd
    assert "--bind" in cmd
    assert "/writable" in cmd


def test_build_command_with_bad_size():
    """Test that invalid mount sizes raise ValueError in BwrapSandbox.build()."""
    config = ConfigLoader().set(
        {
            "name": "test",
            "chdir": "/tmp",
            "mounts": [{"target": "/tmp", "type": "tmpfs", "size": "100AM"}],
            "capabilities": {"drop": ["ALL"]},
            "unshare": {"pid": True},
        }
    )
    with pytest.raises(ValueError, match="Invalid size suffix"):
        BwrapSandbox(config).build()


def test_build_command_no_command():
    """Test BwrapSandbox.build() without a command argument."""
    config = ConfigLoader().set(
        {
            "name": "test",
            "mounts": [{"source": "/", "target": "/", "type": "bind"}],
            "capabilities": {"drop": ["ALL"]},
        }
    )
    cmd = BwrapSandbox(config).build()
    assert cmd[0] == "bwrap"
    assert "--die-with-parent" in cmd
    # Should NOT have any command at the end
    assert "--cap-drop" in cmd
    assert "ALL" in cmd


def test_build_command_with_command():
    """Test BwrapSandbox.build() with a command argument."""
    config = ConfigLoader().set(
        {
            "name": "test",
            "mounts": [{"source": "/", "target": "/", "type": "bind"}],
            "capabilities": {"drop": ["ALL"]},
        }
    )
    cmd = BwrapSandbox(config).build(["echo", "hello"])
    assert cmd[0] == "bwrap"
    assert "echo" in cmd
    assert "hello" in cmd


def test_bwrap_sandbox_run_basic():
    """Test basic sandbox execution via BwrapSandbox.run()."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(
            """name: test-sandbox
chdir: "/tmp"
mounts:
  - source: /
    target: /
    type: bind
  - target: /tmp
    type: tmpfs
die_with_parent: true
new_session: true
"""
        )
        path = f.name

    try:
        config = ConfigLoader().load(path)
        result = BwrapSandbox(config).run(["echo", "hello"])
        assert result.success is True
        assert result.return_code == 0
    finally:
        os.unlink(path)


def test_bwrap_sandbox_run_with_hostname():
    """Test BwrapSandbox.run() with custom hostname."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(
            """name: test-sandbox
chdir: "/tmp"
mounts:
  - source: /
    target: /
    type: bind
  - target: /tmp
    type: tmpfs
unshare:
  uts: true
hostname: myhost
die_with_parent: true
new_session: true
"""
        )
        path = f.name

    try:
        config = ConfigLoader().load(path)
        result = BwrapSandbox(config).run(["bash", "-c", "cat /proc/sys/kernel/hostname"])
        assert result.success is True
    finally:
        os.unlink(path)


def test_bwrap_sandbox_run_with_env_vars():
    """Test BwrapSandbox.run() with environment variables."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(
            """name: test-sandbox
chdir: "/tmp"
mounts:
  - source: /
    target: /
    type: bind
  - target: /tmp
    type: tmpfs
env_vars:
  MY_VAR: hello
die_with_parent: true
new_session: true
"""
        )
        path = f.name

    try:
        config = ConfigLoader().load(path)
        result = BwrapSandbox(config).run(["echo", "hello"])
        assert result.success is True
    finally:
        os.unlink(path)


def test_bwrap_sandbox_run_unshare_namespace():
    """Test BwrapSandbox.run() with unshare namespace."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(
            """name: test-sandbox
chdir: "/tmp"
mounts:
  - source: /
    target: /
    type: bind
  - target: /tmp
    type: tmpfs
unshare:
  pid: true
die_with_parent: true
new_session: true
"""
        )
        path = f.name

    try:
        config = ConfigLoader().load(path)
        result = BwrapSandbox(config).run(["bash", "-c", "echo sandbox"])
        assert result.success is True
        assert result.return_code == 0
    finally:
        os.unlink(path)


def test_bwrap_sandbox_run_path_error():
    """Test BwrapSandbox.run() returns non-zero return code for non-existent bind source.

    bwrap errors propagate directly from subprocess and produce a non-zero return code.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(
            """name: test-sandbox
chdir: "/tmp"
mounts:
  - source: /nonexistent/path
    target: /sandbox
    type: bind
  - target: /tmp
    type: tmpfs
die_with_parent: true
new_session: true
"""
        )
        path = f.name

    try:
        config = ConfigLoader().load(path)
        result = BwrapSandbox(config).run(["echo", "hello"])
        assert result.success is False
        assert result.return_code != 0
    finally:
        os.unlink(path)


def test_bwrap_sandbox_run_command_not_found():
    """Test BwrapSandbox.run() returns non-zero return code for non-existent command."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(
            """name: test-sandbox
chdir: "/tmp"
mounts:
  - source: /
    target: /
    type: bind
  - target: /tmp
    type: tmpfs
die_with_parent: true
new_session: true
"""
        )
        path = f.name

    try:
        config = ConfigLoader().load(path)
        result = BwrapSandbox(config).run(["nonexistent-cmd-xyz"])
        assert result.success is False
        assert result.return_code != 0
    finally:
        os.unlink(path)


def test_bwrap_sandbox_run_timeout():
    """Test BwrapSandbox.run() with a timeout configured in config."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(
            """name: test-sandbox
chdir: "/tmp"
mounts:
  - source: /
    target: /
    type: bind
  - target: /tmp
    type: tmpfs
timeout: 0.1
die_with_parent: true
new_session: true
"""
        )
        path = f.name

    try:
        config = ConfigLoader().load(path)
        with pytest.raises(subprocess.TimeoutExpired, match="timed out"):
            BwrapSandbox(config).run(["bash", "-c", "sleep 5"])
    finally:
        os.unlink(path)


def test_bwrap_sandbox_run_die_with_parent_false():
    """Test BwrapSandbox.run() with die_with_parent=False."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(
            """name: test-sandbox
chdir: "/tmp"
mounts:
  - source: /
    target: /
    type: bind
  - target: /tmp
    type: tmpfs
die_with_parent: false
new_session: true
"""
        )
        path = f.name

    try:
        config = ConfigLoader().load(path)
        result = BwrapSandbox(config).run(["echo", "hello"])
        assert result.success is True
    finally:
        os.unlink(path)


def test_cli_command_building():
    """Test that BwrapSandbox.build() builds the correct bwrap command.

    This test verifies that the BwrapSandbox API produces correct bwrap
    commands with all options.
    """
    config = ConfigLoader().set(
        {
            "name": "cli-test",
            "chdir": "/tmp",
            "mounts": [
                {"source": "/", "target": "/", "type": "bind"},
                {"source": "/etc/resolv.conf", "target": "/etc/resolv.conf", "type": "ro-bind"},
                {"target": "/tmp", "type": "tmpfs"},
            ],
            "capabilities": {"drop": ["ALL"]},
            "unshare": {"pid": True, "uts": True},
        }
    )
    cmd = BwrapSandbox(config).build()

    # First command should start with bwrap
    assert cmd[0] == "bwrap"
    assert "--die-with-parent" in cmd
    assert "--new-session" in cmd
    assert "--cap-drop" in cmd
    assert "ALL" in cmd
    assert "--unshare-pid" in cmd
    assert "--unshare-uts" in cmd
    # readonly: True normalizes to type='bind', so it becomes --bind
    assert "--bind" in cmd
    assert "/etc/resolv.conf" in cmd
    assert "--tmpfs" in cmd
    # Command should NOT start with "bwrap" (no duplication)
    assert cmd[0] == "bwrap", "Command should start with 'bwrap', not duplicated"
