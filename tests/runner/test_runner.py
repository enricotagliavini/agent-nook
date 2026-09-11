"""Tests for the sandbox runner."""

import pytest
import tempfile
import os
import subprocess
from contextlib import redirect_stdout, redirect_stderr

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_nook.config.loader import ConfigLoader
from agent_nook.runner import (
    run_in_sandbox,
    build_command,
    validate_config,
    BwrapError,
)
from agent_nook.config.config import ConfigValidationError, Mount, CapabilitySet, NamespaceSet


def test_build_command_valid():
    """Test build_command with valid config."""
    config = ConfigLoader().set(
        {
            "name": "test",
            "root": "/tmp",
            "mounts": [
                {"source": "/", "target": "/"},
                {"target": "/tmp", "type": "tmpfs"},
            ],
            "capabilities": {"drop": "ALL"},
            "unshare": {"pid": True, "uts": True},
        }
    )
    cmd = build_command(config)
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
            "root": "/tmp",
            "mounts": [
                {"source": "/readonly", "target": "/readonly", "readonly": True},
                {"source": "/writable", "target": "/writable"},
            ],
            "capabilities": {"drop": "ALL"},
            "unshare": {"pid": True},
        }
    )
    cmd = build_command(config)
    assert "--ro-bind" in cmd
    assert "/readonly" in cmd
    assert "--bind" in cmd
    assert "/writable" in cmd


def test_build_command_with_bad_size():
    """Test that invalid mount sizes raise ValueError in build_command()."""
    config = ConfigLoader().set(
        {
            "name": "test",
            "root": "/tmp",
            "mounts": [{"target": "/tmp", "type": "tmpfs", "size": "100AM"}],
            "capabilities": {"drop": "ALL"},
            "unshare": {"pid": True},
        }
    )
    with pytest.raises(ValueError, match="Invalid size suffix"):
        build_command(config)


def test_build_command_hostname():
    """Test that hostname sets unshare-uts and --hostname."""
    config = ConfigLoader().set(
        {
            "name": "test",
            "root": "/tmp",
            "mounts": [{"source": "/", "target": "/"}],
            "hostname": "sandbox-host",
            "unshare": {"pid": True},
        }
    )
    cmd = build_command(config)
    assert "--unshare-uts" in cmd
    assert "--hostname" in cmd
    assert "sandbox-host" in cmd


def test_build_command_env_vars():
    """Test that env_vars are added as --setenv arguments."""
    config = ConfigLoader().set(
        {
            "name": "test",
            "root": "/tmp",
            "mounts": [{"source": "/", "target": "/"}],
            "env_vars": {"MY_VAR": "myvalue", "ANOTHER": "another"},
        }
    )
    cmd = build_command(config)
    assert "--setenv" in cmd
    assert "MY_VAR" in cmd
    assert "myvalue" in cmd


def test_build_command_unset_env_vars():
    """Test that unenv_vars are added as --unsetenv arguments."""
    config = ConfigLoader().set(
        {
            "name": "test",
            "root": "/tmp",
            "mounts": [{"source": "/", "target": "/"}],
            "unenv_vars": ["VAR1", "VAR2"],
        }
    )
    cmd = build_command(config)
    assert "--unsetenv" in cmd
    assert "VAR1" in cmd
    assert "VAR2" in cmd


def test_build_command_all_unenv_vars():
    """Test that unenv_vars=['ALL'] uses --clearenv."""
    config = ConfigLoader().set(
        {
            "name": "test",
            "root": "/tmp",
            "mounts": [{"source": "/", "target": "/"}],
            "unenv_vars": ["ALL"],
        }
    )
    cmd = build_command(config)
    assert "--clearenv" in cmd


def test_run_in_sandbox_basic():
    """Test basic sandbox execution."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(
            """name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
  - target: /tmp
    type: tmpfs
die_with_parent: true
new_session: true
"""
        )
        path = f.name

    try:
        config = ConfigLoader().load(path)
        result = run_in_sandbox(config, ["echo", "hello"])
        assert result.success is True
        assert result.return_code == 0
        assert "hello" in result.stdout
    finally:
        os.unlink(path)


def test_run_in_sandbox_with_hostname():
    """Test run_in_sandbox with custom hostname."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(
            """name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
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
        result = run_in_sandbox(config, ["bash", "-c", "cat /proc/sys/kernel/hostname"])
        assert result.success is True
        assert result.stdout.strip() == "myhost"
    finally:
        os.unlink(path)


def test_run_in_sandbox_with_env_vars():
    """Test run_in_sandbox with environment variables."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(
            """name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
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
        result = run_in_sandbox(config, ["echo", "hello"])
        assert result.success is True
        assert "hello" in result.stdout
    finally:
        os.unlink(path)


def test_run_in_sandbox_unshare_namespace():
    """Test run_in_sandbox with unshare namespace."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(
            """name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
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
        result = run_in_sandbox(config, ["bash", "-c", "echo sandbox"])
        assert result.success is True
        assert result.return_code == 0
    finally:
        os.unlink(path)


def test_run_in_sandbox_path_error():
    """Test run_in_sandbox raises BwrapError for non-existent bind source."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(
            """name: test-sandbox
root: /tmp
mounts:
  - source: /nonexistent/path
    target: /sandbox
  - target: /tmp
    type: tmpfs
die_with_parent: true
new_session: true
"""
        )
        path = f.name

    try:
        config = ConfigLoader().load(path)
        with pytest.raises(BwrapError, match="No such file"):
            run_in_sandbox(config, ["echo", "hello"])
    finally:
        os.unlink(path)


def test_run_in_sandbox_command_not_found():
    """Test run_in_sandbox raises BwrapError for non-existent command."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(
            """name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
  - target: /tmp
    type: tmpfs
die_with_parent: true
new_session: true
"""
        )
        path = f.name

    try:
        config = ConfigLoader().load(path)
        with pytest.raises(BwrapError, match="No such file"):
            run_in_sandbox(config, ["nonexistent-cmd-xyz"])
    finally:
        os.unlink(path)


def test_run_in_sandbox_timeout():
    """Test run_in_sandbox with a timeout."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(
            """name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
  - target: /tmp
    type: tmpfs
die_with_parent: true
new_session: true
"""
        )
        path = f.name

    try:
        config = ConfigLoader().load(path)
        with pytest.raises(subprocess.TimeoutExpired, match="timed out"):
            run_in_sandbox(config, ["bash", "-c", "sleep 5"], timeout=2)
    finally:
        os.unlink(path)


def test_run_in_sandbox_with_die_with_parent_false():
    """Test run_in_sandbox with die_with_parent=False."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(
            """name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
  - target: /tmp
    type: tmpfs
die_with_parent: false
new_session: true
"""
        )
        path = f.name

    try:
        config = ConfigLoader().load(path)
        result = run_in_sandbox(config, ["echo", "hello"])
        assert result.success is True
        assert "hello" in result.stdout
    finally:
        os.unlink(path)
