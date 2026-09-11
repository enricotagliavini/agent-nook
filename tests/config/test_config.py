"""Tests for SandboxConfig and Mount dataclasses."""

import pytest
from dataclasses import fields

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_nook.config.config import (
    SandboxConfig,
    Mount,
    CapabilitySet,
    NamespaceSet,
    ConfigValidationError,
)


def test_mount_build_bind():
    """Test bind mount produces --bind SRC DEST."""
    mount = Mount(source="/host", target="/sandbox", type="bind")
    assert mount.build() == ["--bind", "/host", "/sandbox"]


def test_mount_build_ro_bind():
    """Test ro-bind mount produces --ro-bind SRC DEST."""
    mount = Mount(source="/host", target="/sandbox", type="ro-bind")
    assert mount.build() == ["--ro-bind", "/host", "/sandbox"]


def test_mount_build_dev_bind():
    """Test dev-bind mount produces --dev-bind SRC DEST."""
    mount = Mount(source="/dev/sda", target="/dev/sda", type="dev-bind", device=True)
    assert mount.build() == ["--dev-bind", "/dev/sda", "/dev/sda"]


def test_mount_build_dev_bind_without_device_errors():
    """Test dev-bind without device=True raises ValueError."""
    mount = Mount(source="/dev/sda", target="/dev/sda", type="dev-bind", device=False)
    with pytest.raises(ValueError, match="device=True"):
        mount.build()


def test_mount_build_tmpfs():
    """Test tmpfs mount produces --tmpfs TARGET [SIZE]."""
    mount = Mount(target="/data", type="tmpfs", size="100M")
    # 100M = 104857600 bytes
    assert mount.build() == ["--tmpfs", "/data", "104857600"]

    mount_no_size = Mount(target="/tmp", type="tmpfs", size="")
    assert mount_no_size.build() == ["--tmpfs", "/tmp"]


def test_mount_build_proc():
    """Test proc mount produces --proc /proc (default target)."""
    mount = Mount(type="proc")
    assert mount.build() == ["--proc", "/proc"]

    mount_explicit = Mount(type="proc", target="/custom-proc")
    assert mount_explicit.build() == ["--proc", "/custom-proc"]


def test_mount_build_dev():
    """Test dev mount produces --dev /dev (default target)."""
    mount = Mount(type="dev")
    assert mount.build() == ["--dev", "/dev"]

    mount_explicit = Mount(type="dev", target="/custom-dev")
    assert mount_explicit.build() == ["--dev", "/custom-dev"]


def test_mount_build_dir():
    """Test dir mount produces --dir TARGET."""
    mount = Mount(target="/workspace", type="dir")
    assert mount.build() == ["--dir", "/workspace"]


def test_mount_build_error_no_source():
    """Test bind mount without source raises ValueError."""
    mount = Mount(target="/sandbox", type="bind")
    with pytest.raises(ValueError, match="requires a non-empty source"):
        mount.build()


def test_mount_build_error_no_target():
    """Test bind mount without target raises ValueError."""
    mount = Mount(source="/host", type="bind")
    with pytest.raises(ValueError, match="requires a non-empty target"):
        mount.build()


def test_mount_build_error_unknown_type():
    """Test unknown mount type raises ValueError."""
    mount = Mount(source="/host", target="/sandbox", type="unknown")
    with pytest.raises(ValueError, match="Unknown mount type"):
        mount.build()


def test_mount_build_error_dev_bind_without_device():
    """Test dev-bind without device=True raises ValueError."""
    mount = Mount(source="/dev/sda", target="/dev/sda", type="dev-bind", device=False)
    with pytest.raises(ValueError, match="requires device=True"):
        mount.build()


def test_mount_parse_size():
    """Test Mount._parse_size handles various formats."""
    assert Mount(size="100")._parse_size("100") == 100
    assert Mount(size="100M")._parse_size("100M") == 104857600
    assert Mount(size="500G")._parse_size("500G") == 536870912000
    assert Mount(size="1024K")._parse_size("1024K") == 1048576
    assert Mount(size="2.5G")._parse_size("2.5G") == 2684354560
    assert Mount(size="1B")._parse_size("1B") == 1
    assert Mount(size="")._parse_size("") is None
    assert Mount(size=1024)._parse_size(1024) == 1024

    # Invalid formats raise ValueError
    with pytest.raises(ValueError, match="Invalid size suffix"):
        Mount(size="100XY")._parse_size("100XY")

    with pytest.raises(ValueError, match="Invalid size format"):
        Mount(size="abc")._parse_size("abc")


def test_capability_set_build():
    """Test CapabilitySet.build() produces correct args."""
    # Drop all
    assert CapabilitySet(dropped=["ALL"]).build() == ["--cap-drop", "ALL"]

    # Drop specific
    assert CapabilitySet(dropped=["NET_ADMIN", "NET_RAW"]).build() == [
        "--cap-drop", "NET_ADMIN",
        "--cap-drop", "NET_RAW"
    ]

    # Keep specific
    assert CapabilitySet(kept=["CHOWN", "SETUID"]).build() == [
        "--cap-add", "CHOWN", "--cap-add", "SETUID"
    ]

    # No constraints
    assert CapabilitySet().build() == []


def test_capability_set_validation():
    """Test that dropping ALL with kept caps raises error."""
    with pytest.raises(ConfigValidationError, match="Cannot keep capabilities"):
        CapabilitySet(dropped=["ALL"], kept=["CHOWN"])


def test_namespace_set_namespaces():
    """Test NamespaceSet.namespaces property."""
    # Only namespaces explicitly set to True are included (opt-in behavior)
    ns = NamespaceSet(pid=True, uts=True)
    assert ns.namespaces == ["pid", "uts"]

    ns2 = NamespaceSet(pid=True, uts=True, ipc=True, cgroup=True, user=True, network=True)
    assert ns2.namespaces == ["cgroup", "ipc", "network", "pid", "uts", "user"]


def test_sandbox_config_valid():
    """Test a valid SandboxConfig builds correctly."""
    config = SandboxConfig(
        name="test-sandbox",
        root="/tmp",
        mounts=[
            Mount(source="/host", target="/sandbox", type="bind"),
            Mount(type="proc"),
            Mount(type="dev"),
            Mount(target="/data", type="tmpfs", size="100M"),
        ],
        capabilities=CapabilitySet(dropped=["ALL"]),
        unshare=NamespaceSet(pid=True, uts=True, user=True),
    )
    config.validate()

    cmd = config.build()
    assert cmd[0] == "bwrap"
    assert "--die-with-parent" in cmd
    assert "--new-session" in cmd
    assert "--bind" in cmd
    assert "/host" in cmd
    assert "/sandbox" in cmd
    assert "--proc" in cmd
    assert "--dev" in cmd
    assert "--tmpfs" in cmd
    assert "104857600" in cmd  # 100M in bytes
    assert "--cap-drop" in cmd
    assert "--unshare=pid" in cmd
    assert "--unshare=uts" in cmd
    assert "--unshare=user" in cmd


def test_sandbox_config_hostname():
    """Test hostname adds --unshare-uts and --hostname."""
    config = SandboxConfig(
        name="test",
        root="/tmp",
        mounts=[Mount(type="proc")],
        hostname="myhost",
    )
    cmd = config.build()
    assert "--unshare-uts" in cmd
    assert "--hostname" in cmd
    assert "myhost" in cmd


def test_sandbox_config_unenv_vars():
    """Test unenv_vars adds --unsetenv."""
    config = SandboxConfig(
        name="test",
        root="/tmp",
        mounts=[Mount(type="proc")],
        unenv_vars=["PATH", "HOME"],
    )
    cmd = config.build()
    assert "--unsetenv" in cmd
    assert "PATH" in cmd
    assert "HOME" in cmd


def test_sandbox_config_env_vars():
    """Test env_vars adds --setenv."""
    config = SandboxConfig(
        name="test",
        root="/tmp",
        mounts=[Mount(type="proc")],
        env_vars={"PATH": "/usr/bin", "VAR": "value"},
    )
    cmd = config.build()
    assert "--setenv" in cmd
    assert "PATH" in cmd
    assert "/usr/bin" in cmd


def test_mount_type_enum_values():
    """Test that Mount type enum values are recognized."""
    mount = Mount(type="bind", target="/sandbox")
    assert mount.type.lower() == "bind"

    mount = Mount(type="RO-BIND", target="/sandbox", source="/host")
    assert mount.type.lower().strip() == "ro-bind"

    mount = Mount(type="  tmpfs  ", target="/tmp")
    assert mount.type.lower().strip() == "tmpfs"

    mount = Mount(type="UNKNOWN")
    with pytest.raises(ValueError, match="Unknown mount type"):
        mount.build()
