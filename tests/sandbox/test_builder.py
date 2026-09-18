"""Tests for the bwrap sandbox builder."""

import sys


sys.path.insert(0, "src")

from agent_nook.config.config import CapabilitySet
from agent_nook.config.config import Mount
from agent_nook.config.config import NamespaceSet
from agent_nook.config.config import SandboxConfig
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

    assert "--hostname" in cmd
    assert "myhost" in cmd


def test_builder_with_proc_mount():
    """Test builder with proc mount."""
    config = SandboxConfig(
        name="test",
        chdir="/tmp",
        mounts=[Mount(type="proc")],
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
        mounts=[Mount(type="dev")],
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
        mounts=[Mount(type="dir", target="/workspace")],
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "--dir" in cmd
    assert "/workspace" in cmd


def test_builder_with_env_vars():
    """Test builder with environment variables."""
    config = SandboxConfig(
        name="test",
        chdir="/tmp",
        mounts=[Mount(type="dir", target="/workspace")],
        env_vars={"HOME": "/home/user", "PATH": "/usr/bin"},
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "--setenv" in cmd
    assert "HOME" in cmd
    assert "/home/user" in cmd
    assert "PATH" in cmd
    assert "/usr/bin" in cmd


def test_builder_with_unset_env_vars():
    """Test builder with unset environment variables (whitespace-separated string)."""
    config = SandboxConfig(
        name="test",
        chdir="/tmp",
        mounts=[Mount(type="dir", target="/workspace")],
        unset_vars="PATH HOME",
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "--unsetenv" in cmd
    assert "PATH" in cmd
    assert "HOME" in cmd


def test_builder_with_all_unset_vars():
    """Test builder with all environment variables unset (ALL keyword)."""
    config = SandboxConfig(
        name="test",
        chdir="/tmp",
        mounts=[Mount(type="dir", target="/workspace")],
        unset_vars="ALL",
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "--clearenv" in cmd


def test_builder_with_specific_capabilities_kept():
    """Test builder with specific capabilities kept."""
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


def test_builder_mount_order_parent_before_child_same_type():
    """Test that parent mount points are placed before children (same type).

    bwrap requires parent directories to be mounted before their children.
    If we have /agentnook and /agentnook/child, the mount for /agentnook
    must appear before the mount for /agentnook/child.
    """
    config = SandboxConfig(
        name="test-parent-before-child-same",
        chdir="/tmp",
        mounts=[
            Mount(type="dir", target="/agentnook/child"),  # child mount
            Mount(type="dir", target="/agentnook"),        # parent mount
        ],
        capabilities=CapabilitySet(),
        unshare=NamespaceSet(),
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    # Find all --dir arguments and their following argument
    dir_indices = []
    for i, arg in enumerate(cmd):
        if arg == "--dir":
            dir_indices.append(i)

    assert len(dir_indices) == 2, f"Expected 2 --dir args, got {len(dir_indices)}"

    # The parent /agentnook must appear before the child /agentnook/child
    assert "/agentnook" in cmd[dir_indices[0] + 1]
    assert "/agentnook/child" in cmd[dir_indices[1] + 1]
    assert dir_indices[0] < dir_indices[1], \
        "Parent /agentnook must come before child /agentnook/child"


def test_builder_mount_order_parent_before_child_different_types():
    """Test cross-type mount ordering: parent before child regardless of type.

    This is the key fix: --tmpfs /agentnook_app/data/app and --ro-bind /agentnook_app/data
    must produce --ro-bind /agentnook_app/data --tmpfs /agentnook_app/data/app.
    """
    config = SandboxConfig(
        name="test-parent-before-child-diff-types",
        chdir="/tmp",
        mounts=[
            Mount(type="tmpfs", target="/agentnook_app/data/app"),  # child mount
            Mount(type="ro-bind", source="/host/agentnook_app/data", target="/agentnook_app/data"),  # parent
        ],
        capabilities=CapabilitySet(),
        unshare=NamespaceSet(),
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    # Find indices of specific mount types
    ro_bind_idx = cmd.index("--ro-bind")
    tmpfs_idx = cmd.index("--tmpfs")

    # Parent /agentnook_app/data must come before child /agentnook_app/data/app
    assert ro_bind_idx < tmpfs_idx, \
        f"Parent --ro-bind at {ro_bind_idx} must come before --tmpfs at {tmpfs_idx}"


def test_builder_mount_order_cross_type_conflict():
    """Test that conflicting mount types are handled correctly.

    When two mounts target the same path with different types, both are included.
    bwrap will apply them in order, which may have unexpected results, but
    the important thing is that the builder produces valid output without crashing.
    """
    config = SandboxConfig(
        name="test-conflict",
        chdir="/tmp",
        mounts=[
            Mount(type="tmpfs", target="/conflict"),
            Mount(type="ro-bind", source="/host/conflict", target="/conflict"),
        ],
        capabilities=CapabilitySet(),
        unshare=NamespaceSet(),
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    # Both mounts should be present (order is determined by topological sort)
    assert "--tmpfs" in cmd
    assert "--ro-bind" in cmd


def test_builder_unset_vars_newline_separated():
    """Test builder with newline-separated unset_vars.

    Validates multi-line YAML format works end-to-end with bwrap.
    """
    config = SandboxConfig(
        name="test-newline",
        chdir="/tmp",
        mounts=[Mount(type="dir", target="/workspace")],
        unset_vars="PATH\nHOME\nTERM",
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "--unsetenv" in cmd
    # Verify all three variables appear as separate --unsetenv args
    unset_indices = [i for i, arg in enumerate(cmd) if arg == "--unsetenv"]
    assert len(unset_indices) == 3, f"Expected 3 --unsetenv args, got {len(unset_indices)}"
    assert "PATH" in cmd[unset_indices[0] + 1]
    assert "HOME" in cmd[unset_indices[1] + 1]
    assert "TERM" in cmd[unset_indices[2] + 1]


def test_builder_unset_vars_space_separated():
    """Test builder with space-separated unset_vars on one line."""
    config = SandboxConfig(
        name="test-space",
        chdir="/tmp",
        mounts=[Mount(type="dir", target="/workspace")],
        unset_vars="PATH HOME TERM",
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "--unsetenv" in cmd
    unset_indices = [i for i, arg in enumerate(cmd) if arg == "--unsetenv"]
    assert len(unset_indices) == 3


def test_builder_unset_vars_mixed_whitespace():
    """Test builder with mixed whitespace (tabs, spaces, newlines)."""
    config = SandboxConfig(
        name="test-mixed",
        chdir="/tmp",
        mounts=[Mount(type="dir", target="/workspace")],
        unset_vars="PATH\tHOME\nTERM",  # Tab + newline
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "--unsetenv" in cmd
    unset_indices = [i for i, arg in enumerate(cmd) if arg == "--unsetenv"]
    assert len(unset_indices) == 3


def test_builder_unset_vars_multiple_spaces():
    """Test builder with multiple consecutive spaces."""
    config = SandboxConfig(
        name="test-multi-space",
        chdir="/tmp",
        mounts=[Mount(type="dir", target="/workspace")],
        unset_vars="PATH  HOME  TERM",  # Double spaces
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "--unsetenv" in cmd
    unset_indices = [i for i, arg in enumerate(cmd) if arg == "--unsetenv"]
    assert len(unset_indices) == 3


def test_builder_unset_vars_ALL_with_whitespace():
    """Test builder with ALL keyword and surrounding whitespace."""
    config = SandboxConfig(
        name="test-all-whitespace",
        chdir="/tmp",
        mounts=[Mount(type="dir", target="/workspace")],
        unset_vars="  ALL  ",  # Leading/trailing spaces
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "--clearenv" in cmd
    # Make sure --clearenv is the only env-related flag
    clearenv_idx = cmd.index("--clearenv")
    assert "--unsetenv" not in cmd


def test_builder_unset_vars_ENV_VAR_EXPANSION():
    """Test builder with env var expansion in unset_vars."""
    import os
    from agent_nook.config.loader import ConfigLoader
    os.environ["TEST_VAR"] = "testvalue"
    os.environ["HOME"] = "/custom/home"

    # Use ConfigLoader to expand env vars before passing to builder
    config = ConfigLoader().load_from_dict(
        {
            "name": "test-expand",
            "chdir": "/tmp",
            "mounts": [{"source": "/source", "target": "/workspace", "type": "dir"}],
            "unset_vars": "${TEST_VAR} ${HOME}",
        }
    )
    builder = BwrapBuilder(config)
    cmd = builder.build(["echo", "hello"])

    assert "--unsetenv" in cmd
    unset_indices = [i for i, arg in enumerate(cmd) if arg == "--unsetenv"]
    assert len(unset_indices) == 2
    assert "testvalue" in cmd[unset_indices[0] + 1]
    assert "/custom/home" in cmd[unset_indices[1] + 1]
