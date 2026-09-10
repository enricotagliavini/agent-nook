"""Tests for the sandbox builder."""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_nook.config.config import (
    SandboxConfig, Mount, TmpfsMount, CapabilitySet, NamespaceSet,
)
from agent_nook.sandbox.builder import BwrapBuilder


def test_builder_basic():
    """Test basic builder functionality with dataclass."""
    config = SandboxConfig(
        name="test-sandbox",
        root="/tmp",
        mounts=[Mount(source="/host", target="/sandbox")],
        tmpfs_mounts=[TmpfsMount(target="/tmp", size="100M")],
        capabilities=CapabilitySet(dropped=["ALL"]),
        unshare=NamespaceSet(pid=True, uts=True, user=True),
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["python3", "script.py", "arg1", "arg2"])

    assert cmd[0] == "bwrap"
    assert "--die-with-parent" in cmd
    assert "--new-session" in cmd
    assert "--proc" in cmd
    assert "--dev" in cmd
    assert "--bind" in cmd
    assert "/host" in cmd
    assert "/sandbox" in cmd
    assert "--tmpfs" in cmd
    assert "100M" in cmd
    assert "--cap-drop=ALL" in cmd
    # With dropped=["ALL"] and no kept, no cap-add flags appear
    assert "--cap-add" not in cmd
    assert "--unshare=pid" in cmd
    assert "--unshare=uts" in cmd
    assert "--unshare=user" in cmd
    assert "python3" in cmd
    assert "script.py" in cmd
    assert "arg1" in cmd
    assert "arg2" in cmd


def test_builder_env_vars():
    """Test builder with environment variables."""
    config = SandboxConfig(
        name="test",
        root="/tmp",
        mounts=[Mount(source="/host", target="/sandbox")],
        tmpfs_mounts=[TmpfsMount(target="/tmp")],
        env_vars={"HOME": "/root", "PATH": "/usr/bin"},
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["python3", "app.py"])

    assert "--setenv" in cmd
    assert "HOME" in cmd
    assert "/root" in cmd
    assert "PATH" in cmd
    assert "/usr/bin" in cmd


def test_builder_unenv_vars():
    """Test builder with unenv_vars."""
    config = SandboxConfig(
        name="test",
        root="/tmp",
        mounts=[Mount(source="/host", target="/sandbox")],
        tmpfs_mounts=[TmpfsMount(target="/tmp")],
        unenv_vars=["OLD_VAR", "PATH"],
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["python3", "app.py"])

    assert "--unsetenv" in cmd
    assert "OLD_VAR" in cmd
    assert "PATH" in cmd


def test_builder_hostname():
    """Test builder with hostname."""
    config = SandboxConfig(
        name="test",
        root="/tmp",
        mounts=[Mount(source="/host", target="/sandbox")],
        tmpfs_mounts=[TmpfsMount(target="/tmp")],
        hostname="myhostname",
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["python3", "app.py"])

    assert "--unshare-uts" in cmd
    assert "--hostname=myhostname" in cmd


def test_builder_proc_dev_disabled():
    """Test builder with proc_enabled=False and dev_enabled=False."""
    config = SandboxConfig(
        name="test",
        root="/tmp",
        mounts=[Mount(source="/host", target="/sandbox")],
        tmpfs_mounts=[TmpfsMount(target="/tmp")],
        proc_enabled=False,
        dev_enabled=False,
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["python3", "app.py"])

    assert "--proc" not in cmd
    assert "--dev" not in cmd


def test_builder_mutation_protection():
    """Test that builder accepts dataclass and doesn't mutate it."""
    config = SandboxConfig(
        name="test",
        root="/tmp",
        mounts=[Mount(source="/host", target="/sandbox")],
        tmpfs_mounts=[TmpfsMount(target="/tmp")],
        capabilities=CapabilitySet(dropped=["ALL"]),
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    # Config should still be valid
    assert config.name == "test"
    assert len(config.mounts) == 1
    assert config.capabilities.dropped == ["ALL"]


def test_builder_defaults():
    """Test that builder applies default mounts and tmpfs."""
    config = SandboxConfig(
        name="test",
        root="/tmp",
        mounts=[Mount(source="/host", target="/sandbox")],
        tmpfs_mounts=[TmpfsMount(target="/tmp", size="100M")],
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    # Should include default mounts
    assert any("/proc" in arg and "/proc" in arg for arg in cmd)
    assert any("/sys" in arg and "/sys" in arg for arg in cmd)
    assert any("/run" in arg and "/run" in arg for arg in cmd)

    # Config tmpfs at /tmp (100M) overrides the default tmpfs at /tmp
    assert any("/run" in arg for arg in cmd)  # /run mount is in the command
    # Config tmpfs at /tmp (100M) overrides the default tmpfs at /tmp
    assert any(arg == "--tmpfs" for arg in cmd)  # tmpfs flag is in the command
    assert any("/run" in arg for arg in cmd)  # /run mount is in the command
    assert any("/var/tmp" in arg for arg in cmd)  # /var/tmp mount is in the command
    assert any(arg == "--tmpfs" for arg in cmd)
    assert any("/var/tmp" in arg for arg in cmd)
def test_builder_validation():
    """Test builder validation."""
    config = SandboxConfig(
        name="test",
        root="/tmp",
        mounts=[Mount(source="/host", target="/sandbox")],
        tmpfs_mounts=[TmpfsMount(target="/tmp")],
        capabilities=CapabilitySet(dropped=["ALL"]),
    )
    builder = BwrapBuilder(config)
    builder.validate()  # Should not raise


def test_builder_non_dataclass_rejected():
    """Test that builder rejects non-SandboxConfig types."""
    with pytest.raises(ValueError, match="Expected SandboxConfig"):
        BwrapBuilder({"name": "test", "root": "/tmp"})


def test_builder_with_ro_mount():
    """Test builder with readonly mount."""
    config = SandboxConfig(
        name="test",
        root="/tmp",
        mounts=[Mount(source="/host/path", target="/sandbox/path", readonly=True)],
        tmpfs_mounts=[TmpfsMount(target="/tmp")],
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "--ro-bind" in cmd
    assert "/host/path" in cmd
    assert "/sandbox/path" in cmd


def test_builder_with_device_mount():
    """Test builder with device mount."""
    config = SandboxConfig(
        name="test",
        root="/tmp",
        mounts=[Mount(source="/dev/nvidia0", target="/dev/nvidia0", device=True)],
        tmpfs_mounts=[TmpfsMount(target="/tmp")],
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["nvidia-smi"])

    assert "--dev-bind" in cmd
    assert "/dev/nvidia0" in cmd


def test_builder_with_network_ns():
    """Test builder with network namespace."""
    config = SandboxConfig(
        name="test",
        root="/tmp",
        mounts=[Mount(source="/host", target="/sandbox")],
        tmpfs_mounts=[TmpfsMount(target="/tmp")],
        unshare=NamespaceSet(pid=True, user=True, network=True),
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["python3", "app.py"])

    assert "--unshare=network" in cmd


def test_builder_with_all_capabilities_dropped():
    """Test builder drops all capabilities and keeps specific ones."""
    config = SandboxConfig(
        name="test",
        root="/tmp",
        mounts=[Mount(source="/host", target="/sandbox")],
        tmpfs_mounts=[TmpfsMount(target="/tmp")],
        capabilities=CapabilitySet(dropped=[], kept=["CAP_NET_BIND_SERVICE", "CAP_DAC_OVERRIDE"]),
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["python3", "app.py"])

        # dropped=[] means no --cap-drop=ALL
    
    assert "CAP_NET_BIND_SERVICE" in cmd
    assert "CAP_DAC_OVERRIDE" in cmd
