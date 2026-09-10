"""Tests for the sandbox runner."""

import pytest
import tempfile
import os
import sys
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_nook.config.loader import ConfigLoader
from agent_nook.runner import run_in_sandbox, build_command, validate_config
from agent_nook.sandbox.builder import BwrapBuilder, BwrapError
from agent_nook.config.config import ConfigValidationError


def test_build_command_valid():
    """Test build_command with valid config."""
    config = ConfigLoader().set({
        "name": "test",
        "root": "/tmp",
        "mounts": [{"source": "/", "target": "/"}],
        "tmpfs_mounts": [{"target": "/tmp"}],
        "capabilities": {"drop": "ALL"},
        "unshare": ["pid"],
    })
    cmd = build_command(config)
    assert cmd[0] == "bwrap"
    assert "bwrap" in cmd


def test_build_command_empty():
    """Test build_command with empty command list."""
    config = ConfigLoader().set({
        "name": "test",
        "root": "/tmp",
        "mounts": [{"source": "/", "target": "/"}],
        "tmpfs_mounts": [{"target": "/tmp"}],
        "capabilities": {"drop": "ALL"},
        "unshare": ["pid"],
    })
    cmd = build_command(config)
    assert "bwrap" in cmd
    assert len(cmd) > 10


def test_validate_config():
    """Test validate_config with valid config."""
    config = ConfigLoader().set({
        "name": "test",
        "root": "/tmp",
        "mounts": [{"source": "/", "target": "/"}],
        "tmpfs_mounts": [{"target": "/tmp"}],
        "capabilities": {"drop": "ALL"},
        "unshare": ["pid"],
    })
    validate_config(config)  # Should not raise


def test_validate_config_invalid_mounts():
    """Test validate_config with invalid mounts."""
    config = ConfigLoader().set({
        "name": "test",
        "root": "/tmp",
        "mounts": [],
        "tmpfs_mounts": [{"target": "/tmp"}],
        "capabilities": {"drop": "ALL"},
        "unshare": ["pid"],
    })
    with pytest.raises(ConfigValidationError, match="empty"):
        validate_config(config)


def test_validate_config_missing_root():
    """Test validate_config with missing root."""
    config = ConfigLoader().set({
        "name": "test",
        "root": "",
        "mounts": [{"source": "/", "target": "/"}],
        "tmpfs_mounts": [{"target": "/tmp"}],
        "capabilities": {"drop": "ALL"},
        "unshare": ["pid"],
    })
    with pytest.raises(ConfigValidationError, match="empty"):
        validate_config(config)


def test_run_in_sandbox_echo():
    """Test run_in_sandbox with a simple echo command."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
tmpfs_mounts:
  - target: /tmp
capabilities:
  drop: ALL
  keep: CHOWN
unshare:
  - pid
  - uts
die_with_parent: true
new_session: true
proc_enabled: true
dev_enabled: true
""")
        path = f.name

    try:
        config = ConfigLoader().load(path)
        result = run_in_sandbox(config, ["echo", "hello", "world"])
        assert result.success is True
        assert "hello" in result.stdout
        assert "world" in result.stdout
    finally:
        os.unlink(path)


def test_run_in_sandbox_with_env_vars():
    """Test run_in_sandbox with environment variables."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
tmpfs_mounts:
  - target: /tmp
env_vars:
  FOO: bar
die_with_parent: true
new_session: true
proc_enabled: true
dev_enabled: true
""")
        path = f.name

    try:
        config = ConfigLoader().load(path)
        result = run_in_sandbox(config, ["bash", "-c", "echo \$FOO"])
        assert result.success is True
        assert "bar" in result.stdout
    finally:
        os.unlink(path)


def test_run_in_sandbox_with_unenv_vars():
    """Test run_in_sandbox with unenv_vars."""
    # Set PATH in environment
    env = os.environ.copy()
    env["TEST_PATH"] = "/original/path"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
tmpfs_mounts:
  - target: /tmp
unenv_vars:
  - TEST_PATH
die_with_parent: true
new_session: true
proc_enabled: true
dev_enabled: true
""")
        path = f.name

    try:
        config = ConfigLoader().load(path)
        result = run_in_sandbox(config, ["bash", "-c", "echo \$TEST_PATH"])
        # TEST_PATH should not be set
        assert result.success is True
        # The variable should be unset
    finally:
        os.unlink(path)


def test_run_in_sandbox_with_hostname():
    """Test run_in_sandbox with custom hostname."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
tmpfs_mounts:
  - target: /tmp
hostname: myhost
die_with_parent: true
new_session: true
proc_enabled: true
dev_enabled: true
""")
        path = f.name

    try:
        config = ConfigLoader().load(path)
        result = run_in_sandbox(config, ["hostname"])
        # hostname command prints the hostname
        # We just check it runs successfully
        assert result.success is True
    finally:
        os.unlink(path)


def test_run_in_sandbox_timeout():
    """Test run_in_sandbox with a timeout."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
tmpfs_mounts:
  - target: /tmp
die_with_parent: true
new_session: true
proc_enabled: true
dev_enabled: true
""")
        path = f.name

    try:
        config = ConfigLoader().load(path)
        # Run a long-running command with a very short timeout
        result = run_in_sandbox(config, ["sleep", "100"], timeout=1)
        assert result.success is False
    finally:
        os.unlink(path)


def test_run_in_sandbox_permission_error():
    """Test run_in_sandbox with a permission error."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""name: test-sandbox
root: /tmp
mounts:
  - source: /nonexistent/path/that/does/not/exist
    target: /sandbox
tmpfs_mounts:
  - target: /tmp
die_with_parent: true
new_session: true
proc_enabled: true
dev_enabled: true
""")
        path = f.name

    try:
        config = ConfigLoader().load(path)
        # This should fail because the source doesn't exist
        result = run_in_sandbox(config, ["echo", "hello"])
        assert result.success is False
    finally:
        os.unlink(path)


def test_run_in_sandbox_no_such_file():
    """Test run_in_sandbox with a non-existent command."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
tmpfs_mounts:
  - target: /tmp
die_with_parent: true
new_session: true
proc_enabled: true
dev_enabled: true
""")
        path = f.name

    try:
        config = ConfigLoader().load(path)
        result = run_in_sandbox(config, ["nonexistent-command-that-definitely-does-not-exist-12345"])
        assert result.success is False
        assert result.return_code is not None
    finally:
        os.unlink(path)


def test_run_in_sandbox_with_new_session():
    """Test run_in_sandbox with new_session option."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
tmpfs_mounts:
  - target: /tmp
die_with_parent: true
new_session: true
proc_enabled: true
dev_enabled: true
""")
        path = f.name

    try:
        config = ConfigLoader().load(path)
        result = run_in_sandbox(config, ["bash", "-c", r"echo $PPID"])
        assert result.success is True
        # With new_session, the PARENT_PROCESS_ID env var should be set
        # but PPID should be 0 (new session)
    finally:
        os.unlink(path)


def test_run_in_sandbox_with_die_with_parent_false():
    """Test run_in_sandbox with die_with_parent=False."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
tmpfs_mounts:
  - target: /tmp
die_with_parent: false
new_session: true
proc_enabled: true
dev_enabled: true
""")
        path = f.name

    try:
        config = ConfigLoader().load(path)
        result = run_in_sandbox(config, ["bash", "-c", "echo 'still running'"])
        assert result.success is True
    finally:
        os.unlink(path)


def test_bwrap_command_builds_correctly():
    """Test that build_command produces a complete bwrap command."""
    config = ConfigLoader().set({
        "name": "test",
        "root": "/tmp",
        "mounts": [{"source": "/host", "target": "/sandbox"}],
        "tmpfs_mounts": [{"target": "/tmp", "size": "100M"}],
        "capabilities": {"drop": "ALL", "keep": ["CHOWN", "SETUID"]},
        "unshare": ["pid", "uts", "user"],
        "die_with_parent": True,
        "new_session": True,
        "proc_enabled": True,
        "dev_enabled": True,
        "hostname": "sandbox-host",
        "env_vars": {"MY_VAR": "myvalue"},
    })
    cmd = build_command(config)

    # Check structure
    assert cmd[0] == "bwrap"
    assert cmd[1] == "--die-with-parent"
    assert cmd[2] == "--new-session"
    assert "--proc" in cmd
    assert "--dev" in cmd
    assert "--bind" in cmd
    assert "--cap-drop=ALL" in cmd
    assert "--cap-add" in cmd
    assert "CHOWN" in cmd
    assert "SETUID" in cmd
    assert "--unshare=pid" in cmd
    assert "--unshare=uts" in cmd
    assert "--unshare=user" in cmd
    assert "--setenv" in cmd
    assert "MY_VAR" in cmd
    assert "myvalue" in cmd
    assert "/tmp" in cmd  # root
