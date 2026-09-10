"""Tests for config dataclasses and validation."""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_nook.config.loader import ConfigValidationError
from agent_nook.config.config import (
    SandboxConfig,
    Mount,
    TmpfsMount,
    CapabilitySet,
    NamespaceSet,
    ConfigValidationError,
)


def test_mount_build():
    """Test Mount.build() returns correct bwrap args."""
    mount = Mount(source="/host/path", target="/sandbox/path", readonly=True)
    assert mount.build() == ["--ro-bind", "/host/path", "/sandbox/path"]

    mount = Mount(source="/host/path", target="/sandbox/path")
    assert mount.build() == ["--bind", "/host/path", "/sandbox/path"]

    mount = Mount(source="/host/path", target="/sandbox/path", device=True)
    assert mount.build() == ["--dev-bind", "/host/path", "/sandbox/path"]


def test_tmpfs_mount_build():
    """Test TmpfsMount.build() returns correct bwrap args."""
    tmpfs = TmpfsMount(target="/tmp", size="100M")
    assert tmpfs.build() == ["--tmpfs", "/tmp", "100M"]

    tmpfs = TmpfsMount(target="/tmp")
    assert tmpfs.build() == ["--tmpfs", "/tmp"]


def test_capability_set_build():
    """Test CapabilitySet.build() returns correct bwrap args."""
    caps = CapabilitySet(dropped=["ALL"], kept=[])
    assert caps.build() == ["--cap-drop=ALL"]

    caps = CapabilitySet(dropped=[], kept=["CHOWN", "SETUID"])
    assert caps.build() == ["--cap-add", "CHOWN", "--cap-add", "SETUID"]

    caps = CapabilitySet(dropped=["ALL"], kept=[])
    assert caps.build() == ["--cap-drop=ALL"]


def test_capability_set_validation():
    """Test CapabilitySet validation catches conflicts."""
    with pytest.raises(ConfigValidationError):
        CapabilitySet(dropped=["ALL"], kept=["CHOWN", "SETUID"])


def test_namespace_set_namespaces():
    """Test NamespaceSet.namespaces property."""
    ns = NamespaceSet(pid=True, uts=True, user=False)
    assert ns.namespaces == ["pid", "uts", "ipc", "cgroup", "network"]


def test_sandbox_config_validation_errors():
    """Test SandboxConfig validation catches missing required fields."""
    with pytest.raises(ConfigValidationError, match="empty"):
        SandboxConfig(name="", root="")

    with pytest.raises(ConfigValidationError, match="empty"):
        SandboxConfig(name="test", root="")

    with pytest.raises(ConfigValidationError, match="empty"):
        SandboxConfig(name="test", root="/tmp", mounts=[], tmpfs_mounts=[])


def test_sandbox_config_valid():
    """Test valid SandboxConfig construction."""
    config = SandboxConfig(
        name="test-sandbox",
        root="/tmp",
        mounts=[Mount(source="/host", target="/sandbox")],
        tmpfs_mounts=[TmpfsMount(target="/tmp", size="100M")],
        capabilities=CapabilitySet(dropped=[], kept=["CHOWN"]),
        unshare=NamespaceSet(pid=True, user=True),
    )
    assert config.name == "test-sandbox"
    assert config.root == "/tmp"
    assert len(config.mounts) == 1
    assert len(config.tmpfs_mounts) == 1
    assert config.proc_enabled is True
    assert config.dev_enabled is True


def test_sandbox_config_build():
    """Test SandboxConfig.build() returns correct bwrap command."""
    config = SandboxConfig(
        name="test-sandbox",
        root="/tmp",
        mounts=[Mount(source="/host", target="/sandbox")],
        tmpfs_mounts=[TmpfsMount(target="/tmp", size="100M")],
        capabilities=CapabilitySet(dropped=[], kept=["CHOWN"]),
        unshare=NamespaceSet(pid=True, uts=True, user=True),
        env_vars={"KEY": "value"},
        die_with_parent=False,
        new_session=False,
    )
    cmd = config.build()
    assert "bwrap" in cmd
    # die_with_parent=False means --die-with-parent should NOT be present
    assert "--die-with-parent" not in cmd
    # new_session=False means --new-session should NOT be present
    assert "--new-session" not in cmd
    # But --proc and --dev should still be present by default
    assert "--proc" in cmd
    assert "--dev" in cmd
    assert "--bind" in cmd
    assert "/host" in cmd
    assert "/sandbox" in cmd
    assert "--tmpfs" in cmd
    assert "100M" in cmd
    # With dropped=[], --cap-drop=ALL should NOT be present
    assert "--cap-drop=ALL" not in cmd
    assert "--cap-add" in cmd
    assert "CHOWN" in cmd
    assert "--unshare=pid" in cmd
    assert "--unshare=uts" in cmd
    assert "--unshare=user" in cmd
    assert "--setenv" in cmd
    assert "KEY" in cmd
    assert "value" in cmd
    assert "/tmp" in cmd


def test_sandbox_config_hostname():
    """Test SandboxConfig with hostname."""
    config = SandboxConfig(
        name="test",
        root="/tmp",
        mounts=[Mount(source="/host", target="/sandbox")],
        tmpfs_mounts=[TmpfsMount(target="/tmp")],
        hostname="myhost",
    )
    cmd = config.build()
    assert "--hostname=myhost" in cmd


def test_sandbox_config_unenv_vars():
    """Test SandboxConfig with unenv_vars."""
    config = SandboxConfig(
        name="test",
        root="/tmp",
        mounts=[Mount(source="/host", target="/sandbox")],
        tmpfs_mounts=[TmpfsMount(target="/tmp")],
        unenv_vars=["OLD_VAR", "ANOTHER"],
    )
    cmd = config.build()
    assert "--unsetenv" in cmd
    assert "OLD_VAR" in cmd
    assert "ANOTHER" in cmd


def test_sandbox_config_proc_dev_disabled():
    """Test SandboxConfig with proc_enabled=False and dev_enabled=False."""
    config = SandboxConfig(
        name="test",
        root="/tmp",
        mounts=[Mount(source="/host", target="/sandbox")],
        tmpfs_mounts=[TmpfsMount(target="/tmp")],
        proc_enabled=False,
        dev_enabled=False,
    )
    cmd = config.build()
    assert "--proc" not in cmd
    assert "--dev" not in cmd
