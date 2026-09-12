"""Tests for the bwrap sandbox builder."""

import pytest
import sys

sys.path.insert(0, "src")

from agent_nook.config.config import SandboxConfig, Mount, CapabilitySet, NamespaceSet
from agent_nook.sandbox.builder import BwrapBuilder


def test_builder_basic():
    """Test builder with basic config."""
    config = SandboxConfig(
        name="test",
        chdir="/tmp",
        mounts=[Mount(source="/host", target="/sandbox"), Mount(type="tmpfs", target="/tmp")],
        capabilities=CapabilitySet(dropped=["ALL"]),
        unshare=NamespaceSet(pid=True, uts=True),
        die_with_parent=True,
        new_session=True,
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert cmd[0] == "bwrap"
    assert "--die-with-parent" in cmd
    assert "--new-session" in cmd
    assert "--bind" in cmd
    assert "--tmpfs" in cmd
    assert "--cap-drop" in cmd
    assert "ALL" in cmd
    assert "--unshare-pid" in cmd
    assert "--unshare-uts" in cmd
    # bwrap --die-with-parent creates a zombie, so we need /tmp as root
    assert "/tmp" in cmd


def test_builder_hostname():
    """Test builder with custom hostname."""
    config = SandboxConfig(
        name="test",
        chdir="/tmp",
        mounts=[Mount(source="/host", target="/sandbox")],
        capabilities=CapabilitySet(),
        unshare=NamespaceSet(pid=True, uts=True),
        hostname="myhost",
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "--unshare-uts" in cmd
    assert "--hostname" in cmd
    assert "myhost" in cmd


def test_builder_with_proc_mount():
    """Test builder with proc mount."""
    config = SandboxConfig(
        name="test",
        chdir="/tmp",
        mounts=[Mount(type="proc"), Mount(source="/host", target="/sandbox")],
        capabilities=CapabilitySet(),
        unshare=NamespaceSet(),
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "--proc" in cmd
    assert "/proc" in cmd


def test_builder_with_dev_mount():
    """Test builder with dev mount."""
    config = SandboxConfig(
        name="test",
        chdir="/tmp",
        mounts=[Mount(type="dev"), Mount(source="/host", target="/sandbox")],
        capabilities=CapabilitySet(),
        unshare=NamespaceSet(),
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "--dev" in cmd
    assert "/dev" in cmd


def test_builder_with_dir_mount():
    """Test builder with dir mount."""
    config = SandboxConfig(
        name="test",
        chdir="/tmp",
        mounts=[Mount(type="dir", target="/mydir")],
        capabilities=CapabilitySet(),
        unshare=NamespaceSet(),
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "--dir" in cmd
    assert "/mydir" in cmd


def test_builder_with_env_vars():
    """Test builder with environment variables."""
    config = SandboxConfig(
        name="test",
        chdir="/tmp",
        mounts=[Mount(source="/host", target="/sandbox")],
        capabilities=CapabilitySet(),
        unshare=NamespaceSet(),
        env_vars={"MY_VAR": "myvalue"},
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "--setenv" in cmd
    assert "MY_VAR" in cmd
    assert "myvalue" in cmd
    assert "myvalue" in cmd


def test_builder_with_unset_env_vars():
    """Test builder with unenv_vars."""
    config = SandboxConfig(
        name="test",
        chdir="/tmp",
        mounts=[Mount(source="/host", target="/sandbox")],
        capabilities=CapabilitySet(),
        unshare=NamespaceSet(),
        unenv_vars=["UNSET_VAR"],
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "UNSET_VAR" in cmd


def test_builder_with_all_unenv_vars():
    """Test builder with clearenv (all vars unset)."""
    config = SandboxConfig(
        name="test",
        chdir="/tmp",
        mounts=[Mount(source="/host", target="/sandbox")],
        capabilities=CapabilitySet(),
        unshare=NamespaceSet(),
        unenv_vars=["ALL"],
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "--clearenv" in cmd


def test_builder_with_specific_capabilities_kept():
    """Test builder with kept capabilities."""
    config = SandboxConfig(
        name="test",
        chdir="/tmp",
        mounts=[Mount(source="/host", target="/sandbox")],
        capabilities=CapabilitySet(kept=["CHOWN", "SETUID"]),
        unshare=NamespaceSet(),
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "--cap-add" in cmd
    assert "CHOWN" in cmd
    assert "SETUID" in cmd


def test_builder_with_specific_capabilities_dropped():
    """Test builder with specific capabilities dropped."""
    config = SandboxConfig(
        name="test",
        chdir="/tmp",
        mounts=[Mount(source="/host", target="/sandbox")],
        capabilities=CapabilitySet(dropped=["NET_ADMIN", "NET_RAW"]),
        unshare=NamespaceSet(),
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "--cap-drop" in cmd
    assert "NET_ADMIN" in cmd
    assert "NET_RAW" in cmd


def test_builder_with_all_capabilities_dropped():
    """Test builder with all capabilities dropped."""
    config = SandboxConfig(
        name="test",
        chdir="/tmp",
        mounts=[Mount(type="proc")],
        capabilities=CapabilitySet(dropped=["ALL"]),
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "--cap-drop" in cmd
    assert "ALL" in cmd


def test_builder_with_network_ns():
    """Test builder with network namespace."""
    config = SandboxConfig(
        name="test",
        chdir="/tmp",
        mounts=[Mount(type="proc")],
        unshare=NamespaceSet(network=True),
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "--unshare-net" in cmd
