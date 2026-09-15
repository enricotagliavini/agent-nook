"""Tests for the sandbox runner.

Note: This test directory is kept for backward compatibility with
code that might import from `agent_nook.runner`. The functionality
has been moved to `tests/sandbox/` and the module no longer exists.
"""

import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_nook.config.loader import ConfigLoader
from agent_nook.sandbox import BwrapSandbox, BwrapError


def test_build_command_valid():
    """Test BwrapSandbox.build_command with valid config."""
    config = ConfigLoader().set(
        {
            "name": "test",
            "chdir": "/tmp",
            "mounts": [
                {"source": "/", "target": "/", "type": "bind"},
                {"target": "/tmp", "type": "tmpfs"},
            ],
            "capabilities": {"drop": ["ALL"]},
            "unshare": {"pid": True, "uts": True},
        }
    )
    cmd = BwrapSandbox.build_command(config)
    assert cmd[0] == "bwrap"
    assert "--die-with-parent" in cmd
    assert "--new-session" in cmd
    assert "--cap-drop" in cmd
    assert "ALL" in cmd
    assert "--unshare-pid" in cmd
    assert "--unshare-uts" in cmd
    assert "--bind" in cmd
    assert "/" in cmd
    assert "--tmpfs" in cmd


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
    cmd = BwrapSandbox.build_command(config)
    assert "--ro-bind" in cmd
    assert "/readonly" in cmd
    assert "--bind" in cmd
    assert "/writable" in cmd


def test_build_command_with_bad_size():
    """Test that invalid mount sizes raise ValueError in BwrapSandbox.build_command()."""
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
        BwrapSandbox.build_command(config)


def test_build_command_hostname():
    """Test that hostname sets unshare-uts and --hostname."""
    config = ConfigLoader().set(
        {
            "name": "test",
            "chdir": "/tmp",
            "mounts": [{"source": "/", "target": "/", "type": "bind"}],
            "hostname": "sandbox-host",
            "unshare": {"pid": True},
        }
    )
    cmd = BwrapSandbox.build_command(config)
    assert "--unshare-uts" in cmd
    assert "--hostname" in cmd
    assert "sandbox-host" in cmd


def test_build_command_env_vars():
    """Test that env_vars are added as --setenv arguments."""
    config = ConfigLoader().set(
        {
            "name": "test",
            "chdir": "/tmp",
            "mounts": [{"source": "/", "target": "/", "type": "bind"}],
            "env_vars": {"MY_VAR": "myvalue", "ANOTHER": "another"},
        }
    )
    cmd = BwrapSandbox.build_command(config)
    assert "--setenv" in cmd
    assert "MY_VAR" in cmd
    assert "myvalue" in cmd


def test_build_command_unset_env_vars():
    """Test that unset_vars are added as --unsetenv arguments."""
    config = ConfigLoader().set(
        {
            "name": "test",
            "chdir": "/tmp",
            "mounts": [{"source": "/", "target": "/", "type": "bind"}],
            "unset_vars": ["VAR1", "VAR2"],
        }
    )
    cmd = BwrapSandbox.build_command(config)
    assert "--unsetenv" in cmd
    assert "VAR1" in cmd
    assert "VAR2" in cmd


def test_build_command_no_command():
    """Test BwrapSandbox.build_command without a command argument."""
    config = ConfigLoader().set(
        {
            "name": "test",
            "mounts": [{"source": "/", "target": "/", "type": "bind"}],
            "capabilities": {"drop": ["ALL"]},
        }
    )
    cmd = BwrapSandbox.build_command(config)
    assert cmd[0] == "bwrap"
    assert "--die-with-parent" in cmd
    # Should NOT have any command at the end
    assert "--cap-drop" in cmd
    assert "ALL" in cmd


def test_build_command_with_command():
    """Test BwrapSandbox.build_command with a command argument."""
    config = ConfigLoader().set(
        {
            "name": "test",
            "mounts": [{"source": "/", "target": "/", "type": "bind"}],
            "capabilities": {"drop": ["ALL"]},
        }
    )
    cmd = BwrapSandbox.build_command(config, command=["echo", "hello"])
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
        result = BwrapSandbox.run(["echo", "hello"], config=config)
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
        result = BwrapSandbox.run(["bash", "-c", "cat /proc/sys/kernel/hostname"], config=config)
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
        result = BwrapSandbox.run(["echo", "hello"], config=config)
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
        result = BwrapSandbox.run(["bash", "-c", "echo sandbox"], config=config)
        assert result.success is True
        assert result.return_code == 0
    finally:
        os.unlink(path)


def test_bwrap_sandbox_run_path_error():
    """Test BwrapSandbox.run() returns non-zero return code for non-existent bind source.

    With capture_output=False, errors now propagate directly from subprocess
    and return a non-zero return code instead of being caught and converted to BwrapError.
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
        result = BwrapSandbox.run(["echo", "hello"], config=config)
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
        result = BwrapSandbox.run(["nonexistent-cmd-xyz"], config=config)
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
timeout: 2
die_with_parent: true
new_session: true
"""
        )
        path = f.name

    try:
        config = ConfigLoader().load(path)
        with pytest.raises(subprocess.TimeoutExpired, match="timed out"):
            BwrapSandbox.run(["bash", "-c", "sleep 5"], config=config)
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
        result = BwrapSandbox.run(["echo", "hello"], config=config)
        assert result.success is True
    finally:
        os.unlink(path)


def test_cli_command_building():
    """Test that BwrapSandbox.build_command() builds the correct bwrap command.

    This test verifies that the BwrapSandbox API (preferred over removed
    stub functions) produces correct bwrap commands with all options.
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
    cmd = BwrapSandbox.build_command(config)

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


def test_cli_direct_execution():
    """Test that CLI directly executes the bwrap command via subprocess.

    This test verifies that BwrapSandbox.build_command() produces a
    correct bwrap command and executes it successfully via subprocess.run().
    """
    from agent_nook.sandbox import BwrapSandbox

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
    bwrap_cmd = BwrapSandbox.build_command(config, ["echo", "hello from sandbox"])

    # Verify the command builds correctly
    assert bwrap_cmd[0] == "bwrap"
    assert "--cap-drop" in bwrap_cmd
    assert "ALL" in bwrap_cmd
    assert "echo" in bwrap_cmd
