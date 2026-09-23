"""Tests for SandboxConfig and Mount dataclasses."""

import os
import sys

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_nook.config.config import CapabilitySet
from agent_nook.config.config import ConfigValidationError
from agent_nook.config.config import Mount
from agent_nook.config.config import NamespaceSet
from agent_nook.config.config import SandboxConfig
from agent_nook.sandbox.builder import BwrapBuilder


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


def test_mount_create_source_defaults():
    """create_source defaults to False and create_as to 'dir'."""
    mount = Mount(source="/host", target="/sandbox", type="bind")
    assert mount.create_source is False
    assert mount.create_as == "dir"


def test_mount_create_source_flags_do_not_change_args():
    """create-source/create-as never leak into the bwrap command line."""
    mount = Mount(source="/host", target="/sandbox", type="bind", create_source=True, create_as="file")
    assert mount.build() == ["--bind", "/host", "/sandbox"]


def test_mount_build_error_invalid_create_as():
    """An invalid create-as value raises ValueError in build()."""
    mount = Mount(source="/host", target="/sandbox", type="bind", create_as="volume")
    with pytest.raises(ValueError, match="create-as"):
        mount.build()


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


def test_namespace_set_namespaces():
    """Test NamespaceSet.namespaces property."""
    # Only namespaces explicitly set to True are included (opt-in behavior)
    ns = NamespaceSet(pid=True, uts=True)
    assert ns.namespaces == ["pid", "uts"]

    ns2 = NamespaceSet(pid=True, uts=True, ipc=True, cgroup=True, user=True, network=True)
    assert ns2.namespaces == ["cgroup", "ipc", "network", "pid", "uts", "user"]


def test_sandbox_config_hostname():
    """Test hostname adds --unshare-uts and --hostname."""
    config = SandboxConfig(
        name="test",
        chdir="/tmp",
        mounts=[Mount(type="proc")],
        hostname="myhost",
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])
    assert "--hostname" in cmd
    assert "myhost" in cmd


def test_sandbox_config_unset_vars():
    """Test unset_vars adds --unsetenv."""
    config = SandboxConfig(
        name="test",
        chdir="/tmp",
        mounts=[Mount(type="proc")],
        unset_vars="PATH HOME",  # whitespace-separated string
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])
    assert "--unsetenv" in cmd
    assert "PATH" in cmd
    assert "HOME" in cmd


def test_sandbox_config_unset_vars_all():
    """Test unset_vars with ALL triggers --clearenv."""
    config = SandboxConfig(
        name="test",
        chdir="/tmp",
        mounts=[Mount(type="proc")],
        unset_vars="ALL",
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])
    assert "--clearenv" in cmd
    assert "--unsetenv" not in cmd


def test_sandbox_config_unset_vars_mixed():
    """Test unset_vars with ALL and specific vars (whitespace-separated)."""
    config = SandboxConfig(
        name="test",
        chdir="/tmp",
        mounts=[Mount(type="proc")],
        unset_vars="ALL PATH HOME",  # whitespace-separated string
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])
    # ALL comes first (triggers --clearenv)
    idx = cmd.index("--clearenv")
    assert "--unsetenv" not in cmd[idx:idx+1]
    # No --unsetenv should follow since ALL clears everything


def test_sandbox_config_env_vars():
    """Test env_vars adds --setenv."""
    config = SandboxConfig(
        name="test",
        chdir="/tmp",
        mounts=[Mount(type="proc")],
        env_vars={"PATH": "/usr/bin", "VAR": "value"},
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])
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


def test_sandbox_config_requires_at_least_one_mount():
    """A SandboxConfig without any mount points is rejected at construction."""
    with pytest.raises(ConfigValidationError, match="at least one mount point"):
        SandboxConfig(name="test")

    with pytest.raises(ConfigValidationError, match="at least one mount point"):
        SandboxConfig(name="test", mounts=[])


def test_mount_build_overlay():
    """Test overlay mount produces --overlay-src layers then --overlay RWSRC WORKDIR DEST."""
    mount = Mount(source="/rw", workdir="/wd", target="/d", type="overlay", overlay_src=["/a", "/b"])
    assert mount.build() == ["--overlay-src", "/a", "--overlay-src", "/b", "--overlay", "/rw", "/wd", "/d"]


def test_mount_build_ro_overlay():
    """Test ro-overlay mount produces --overlay-src layers then --ro-overlay DEST."""
    mount = Mount(target="/d", type="ro-overlay", overlay_src=["/a", "/b"])
    assert mount.build() == ["--overlay-src", "/a", "--overlay-src", "/b", "--ro-overlay", "/d"]


def test_mount_build_tmp_overlay():
    """Test tmp-overlay mount produces --overlay-src layers then --tmp-overlay DEST."""
    mount = Mount(target="/d", type="tmp-overlay", overlay_src=["/a"])
    assert mount.build() == ["--overlay-src", "/a", "--tmp-overlay", "/d"]


def test_mount_overlay_field_defaults():
    """overlay_src defaults to an empty list and workdir to None."""
    mount = Mount(source="/host", target="/sandbox", type="bind")
    assert mount.overlay_src == []
    assert mount.workdir is None


def test_mount_build_overlay_requires_source():
    """Test overlay mount without source raises ValueError."""
    with pytest.raises(ValueError, match="requires a source"):
        Mount(workdir="/wd", target="/d", type="overlay", overlay_src=["/a"]).build()


def test_mount_build_overlay_requires_workdir():
    """Test overlay mount without workdir raises ValueError."""
    with pytest.raises(ValueError, match="requires a workdir"):
        Mount(source="/rw", target="/d", type="overlay", overlay_src=["/a"]).build()


def test_mount_build_overlay_requires_target():
    """Test overlay mount without target raises ValueError."""
    with pytest.raises(ValueError, match="requires a target"):
        Mount(source="/rw", workdir="/wd", type="overlay", overlay_src=["/a"]).build()


def test_mount_build_overlay_requires_at_least_one_layer():
    """Test overlay mount without overlay-src layers raises ValueError."""
    with pytest.raises(ValueError, match="at least one overlay-src"):
        Mount(source="/rw", workdir="/wd", target="/d", type="overlay").build()


def test_mount_build_ro_overlay_requires_two_layers():
    """Test ro-overlay mount with a single layer raises ValueError."""
    with pytest.raises(ValueError, match="at least two overlay-src"):
        Mount(target="/d", type="ro-overlay", overlay_src=["/a"]).build()


def test_mount_build_tmp_overlay_requires_one_layer():
    """Test tmp-overlay mount without layers raises ValueError."""
    with pytest.raises(ValueError, match="at least one overlay-src"):
        Mount(target="/d", type="tmp-overlay").build()


def test_mount_build_ro_overlay_rejects_source_and_workdir():
    """Test ro-overlay/tmp-overlay with source or workdir raise ValueError."""
    with pytest.raises(ValueError, match="does not accept"):
        Mount(source="/rw", target="/d", type="ro-overlay", overlay_src=["/a", "/b"]).build()
    with pytest.raises(ValueError, match="does not accept"):
        Mount(workdir="/wd", target="/d", type="tmp-overlay", overlay_src=["/a"]).build()


def test_mount_build_non_overlay_rejects_overlay_fields():
    """Test non-overlay mounts with overlay_src or workdir raise ValueError."""
    with pytest.raises(ValueError, match="does not support"):
        Mount(source="/s", target="/t", type="bind", overlay_src=["/a"]).build()
    with pytest.raises(ValueError, match="does not support"):
        Mount(target="/t", type="tmpfs", workdir="/wd").build()
