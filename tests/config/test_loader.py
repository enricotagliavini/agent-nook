"""Tests for ConfigLoader."""

import argparse
import os
import sys

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_nook.config.config import SandboxConfig
from agent_nook.config.loader import ConfigLoader
from agent_nook.config.loader import ConfigValidationError


def test_load_yaml_basic():
    """Test loading a basic config."""
    loader = ConfigLoader()
    data = {
        "name": "test-sandbox",
        "chdir": "/tmp",
        "mounts": [
            {"target": "/proc", "type": "proc"},
            {"target": "/dev", "type": "dev"},
            {"target": "/tmp", "type": "tmpfs", "size": "100M"},
            {"source": "/host", "target": "/sandbox", "type": "bind"},
        ],
        "capabilities": {"drop": ["ALL"]},
        "unshare": {"pid": True, "uts": True, "user": True},
        "die_with_parent": True,
        "new_session": True,
    }
    config = loader.load_from_dict(data)

    assert config.name == "test-sandbox"
    assert config.chdir == "/tmp"
    assert len(config.mounts) == 4
    assert config.mounts[0].type == "proc"
    assert config.mounts[1].type == "dev"
    assert config.mounts[2].type == "tmpfs"
    assert config.mounts[2].size == "100M"
    assert config.mounts[2]._parse_size(config.mounts[2].size) == 104857600


def test_load_yaml_list_of_mounts():
    """Test loading config with list of mounts including tmpfs."""
    loader = ConfigLoader()
    data = {
        "name": "test",
        "chdir": "/tmp",
        "mounts": [
            {"target": "/proc", "type": "proc"},
            {"target": "/dev", "type": "dev"},
            {"target": "/home", "type": "tmpfs"},
            {"target": "/tmp", "type": "tmpfs", "size": "500M"},
            {"source": "/host/src", "target": "/app/src", "type": "bind"},
            {"source": "/host/ssl", "target": "/app/ssl", "type": "ro-bind"},
        ],
    }
    config = loader.load_from_dict(data)

    assert len(config.mounts) == 6
    assert config.mounts[0].type == "proc"
    assert config.mounts[1].type == "dev"
    assert config.mounts[2].type == "tmpfs"
    assert config.mounts[3].type == "tmpfs"
    assert config.mounts[3].size == "500M"
    assert config.mounts[4].type == "bind"
    assert config.mounts[5].type == "ro-bind"


    loader = ConfigLoader()
    data = {
        "name": "test",
        "chdir": "/tmp",
        "mounts": [{"target": "/proc", "type": "proc"}],
        "env_vars": {"PATH": "/usr/bin", "MY_VAR": "value"},
    }
    config = loader.load_from_dict(data)

    assert config.env_vars == {"PATH": "/usr/bin", "MY_VAR": "value"}


def test_load_yaml_unset_vars():
    """Test loading config with unset_vars as comma-separated string."""
    loader = ConfigLoader()
    data = {
        "name": "test",
        "chdir": "/tmp",
        "mounts": [{"target": "/proc", "type": "proc"}],
        "unset_vars": "PATH HOME",
    }
    config = loader.load_from_dict(data)

    assert config.unset_vars == "PATH HOME"


def test_load_yaml_hostname():
    """Test loading config with hostname."""
    loader = ConfigLoader()
    data = {
        "name": "test",
        "chdir": "/tmp",
        "mounts": [{"target": "/proc", "type": "proc"}],
        "hostname": "myhost",
    }
    config = loader.load_from_dict(data)

    assert config.hostname == "myhost"


def test_load_yaml_override():
    """Test loading config without hostname results in None hostname.

    When hostname is not specified in the config dict, it defaults to None.
    """
    loader = ConfigLoader()
    data = {
        "name": "test",
        "chdir": "/tmp",
        "mounts": [{"target": "/proc", "type": "proc"}],
    }
    config = loader.load_from_dict(data)
    assert config.hostname is None

def test_load_yaml_unknown_key_errors():
    """Test that unknown top-level keys raise ConfigValidationError."""
    loader = ConfigLoader()

    # Completely unknown key
    data = {
        "name": "test",
        "chdir": "/tmp",
        "mounts": [{"target": "/proc", "type": "proc"}],
        "weird_key": "foo",
    }
    with pytest.raises(ConfigValidationError, match="unknown key 'weird_key'"):
        loader.load_from_dict(data)


def test_load_yaml_unknown_mount_type_errors():
    """Test that unknown mount types raise ConfigValidationError."""
    loader = ConfigLoader()
    data = {
        "name": "test",
        "chdir": "/tmp",
        "mounts": [
            {"target": "/proc", "type": "proc"},
            {"target": "/data", "type": "invalid-type"},
        ],
    }

    with pytest.raises(ConfigValidationError, match="unknown mount type 'invalid-type'"):
        loader.load_from_dict(data)

def test_load_from_dict_malformed_mount_entry_without_chdir_errors():
    """Test that non-dict mount entries are rejected even when chdir is absent.

    Regression test: _validate_structure() previously evaluated the mounts
    branch against a stale `value` left over from the previous field (chdir),
    so with chdir unset the mounts field was silently skipped and non-dict
    entries passed structure validation.
    """
    loader = ConfigLoader()

    # chdir absent — the case that was silently skipped before the fix
    data = {
        "name": "test",
        "mounts": ["not-a-dict"],
    }
    with pytest.raises(ConfigValidationError, match=r"mount\[0\] must be a dict"):
        loader.load_from_dict(data)

    # chdir set — control case, validated before the fix as well
    data = {
        "name": "test",
        "chdir": "/tmp",
        "mounts": ["not-a-dict"],
    }
    with pytest.raises(ConfigValidationError, match=r"mount\[0\] must be a dict"):
        loader.load_from_dict(data)


def test_load_from_dict():
    """Test that load_from_dict returns a SandboxConfig."""
    loader = ConfigLoader()
    data = {
        "name": "test",
        "chdir": "/tmp",
        "mounts": [
            {"target": "/proc", "type": "proc"},
            {"target": "/dev", "type": "dev"},
            {"target": "/tmp", "type": "tmpfs", "size": "100M"},
        ],
        "capabilities": {"drop": ["ALL"]},
    }

    config = loader.load_from_dict(data)
    assert isinstance(config, SandboxConfig)
    assert config.name == "test"
    assert len(config.mounts) == 3
    assert config.mounts[0].type == "proc"
    assert config.mounts[1].type == "dev"
    assert config.mounts[2].type == "tmpfs"
    assert config.capabilities.dropped == ["ALL"]


def test_load_from_dict_empty_mounts_errors():
    """Test that an empty mounts list raises ConfigValidationError."""
    loader = ConfigLoader()
    data = {
        "name": "test",
        "mounts": [],
    }

    with pytest.raises(ConfigValidationError, match="at least one mount point"):
        loader.load_from_dict(data)


def _make_run_args(bind: list[str]) -> argparse.Namespace:
    """Build a minimal argparse.Namespace mimicking `agent-nook run` flags."""
    return argparse.Namespace(
        config=None,
        chdir=None,
        die_with_parent=True,
        new_session=True,
        hostname=None,
        bind=bind,
        ro_bind=[],
        cap_add=[],
        cap_drop=[],
        unshare=[],
        env=[],
        unset_env=[],
    )


def test_load_with_overrides_empty_mounts_with_cli_bind_succeeds(tmp_path):
    """A config file without mounts is rescued by a CLI --bind override."""
    config_file = tmp_path / "sandbox.yaml"
    config_file.write_text("name: test\n", encoding="utf-8")

    config = ConfigLoader().load_with_overrides(_make_run_args(["/host/src:/sandbox/src"]), path=str(config_file))

    assert len(config.mounts) == 1
    assert config.mounts[0].source == "/host/src"
    assert config.mounts[0].target == "/sandbox/src"
    assert config.mounts[0].type == "bind"


def test_load_with_overrides_empty_mounts_without_cli_bind_errors(tmp_path):
    """A config file without mounts and no CLI bind fails validation."""
    config_file = tmp_path / "sandbox.yaml"
    config_file.write_text("name: test\n", encoding="utf-8")

    with pytest.raises(ConfigValidationError, match="at least one mount point"):
        ConfigLoader().load_with_overrides(_make_run_args([]), path=str(config_file))


def test_load_from_dict_create_source():
    """create-source true/false parse into Mount.create_source; omitted defaults to False."""
    loader = ConfigLoader()
    data = {
        "name": "test",
        "mounts": [
            {"source": "/a", "target": "/a", "type": "bind", "create-source": True},
            {"source": "/b", "target": "/b", "type": "ro-bind", "create-source": False},
            {"source": "/c", "target": "/c", "type": "bind"},
        ],
    }
    config = loader.load_from_dict(data)
    assert config.mounts[0].create_source is True
    assert config.mounts[1].create_source is False
    assert config.mounts[2].create_source is False


def test_load_from_dict_create_as():
    """create-as 'file'/'dir' parse into Mount.create_as; omitted defaults to 'dir'."""
    loader = ConfigLoader()
    data = {
        "name": "test",
        "mounts": [
            {"source": "/a", "target": "/a", "type": "bind", "create-source": True, "create-as": "file"},
            {"source": "/b", "target": "/b", "type": "bind", "create-source": True, "create-as": "dir"},
            {"source": "/c", "target": "/c", "type": "bind", "create-source": True},
        ],
    }
    config = loader.load_from_dict(data)
    assert config.mounts[0].create_as == "file"
    assert config.mounts[1].create_as == "dir"
    assert config.mounts[2].create_as == "dir"


def test_load_from_dict_create_source_non_boolean_errors():
    """A non-boolean create-source (e.g. quoted 'true') is rejected."""
    loader = ConfigLoader()
    data = {"name": "test", "mounts": [{"source": "/a", "target": "/a", "type": "bind", "create-source": "true"}]}
    with pytest.raises(ConfigValidationError, match="boolean"):
        loader.load_from_dict(data)


def test_load_from_dict_create_source_on_sourceless_mount_errors():
    """create-source on a source-less mount type (tmpfs) is rejected."""
    loader = ConfigLoader()
    data = {"name": "test", "mounts": [{"target": "/tmp", "type": "tmpfs", "create-source": True}]}
    with pytest.raises(ConfigValidationError, match="only valid for"):
        loader.load_from_dict(data)


def test_load_from_dict_create_as_without_create_source_errors():
    """create-as without create-source: true is rejected."""
    loader = ConfigLoader()
    data = {"name": "test", "mounts": [{"source": "/a", "target": "/a", "type": "bind", "create-as": "file"}]}
    with pytest.raises(ConfigValidationError, match="only valid when"):
        loader.load_from_dict(data)


def test_load_from_dict_create_as_invalid_value_errors():
    """A create-as value other than 'dir'/'file' is rejected."""
    loader = ConfigLoader()
    data = {
        "name": "test",
        "mounts": [
            {"source": "/a", "target": "/a", "type": "bind", "create-source": True, "create-as": "volume"},
        ],
    }
    with pytest.raises(ConfigValidationError, match="'dir' or 'file'"):
        loader.load_from_dict(data)


def test_load_from_dict_overlay_mount():
    """An overlay mount with the hyphenated overlay-src key parses into Mount."""
    config = ConfigLoader().load_from_dict(
        {
            "name": "test",
            "mounts": [
                {
                    "source": "/rw",
                    "workdir": "/wd",
                    "target": "/d",
                    "type": "overlay",
                    "overlay-src": ["/a", "/b"],
                }
            ],
        }
    )
    mount = config.mounts[0]
    assert mount.type == "overlay"
    assert mount.source == "/rw"
    assert mount.workdir == "/wd"
    assert mount.target == "/d"
    assert mount.overlay_src == ["/a", "/b"]


def test_load_from_dict_ro_overlay_mount():
    """A ro-overlay mount parses and builds the expected bwrap arguments."""
    config = ConfigLoader().load_from_dict(
        {"name": "test", "mounts": [{"target": "/d", "type": "ro-overlay", "overlay-src": ["/a", "/b"]}]}
    )
    assert config.mounts[0].build() == ["--overlay-src", "/a", "--overlay-src", "/b", "--ro-overlay", "/d"]


def test_load_from_dict_tmp_overlay_mount():
    """A tmp-overlay mount parses and builds the expected bwrap arguments."""
    config = ConfigLoader().load_from_dict(
        {"name": "test", "mounts": [{"target": "/d", "type": "tmp-overlay", "overlay-src": ["/a"]}]}
    )
    assert config.mounts[0].build() == ["--overlay-src", "/a", "--tmp-overlay", "/d"]


def test_load_from_dict_overlay_env_var_expansion_in_srcs():
    """Environment variables expand inside overlay-src list entries."""
    os.environ["OVL_TEST_DIR"] = "/envdir"
    try:
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [
                    {
                        "source": "/rw",
                        "workdir": "/wd",
                        "target": "/d",
                        "type": "overlay",
                        "overlay-src": ["${OVL_TEST_DIR}/lower"],
                    }
                ],
            }
        )
        assert config.mounts[0].overlay_src == ["/envdir/lower"]
    finally:
        del os.environ["OVL_TEST_DIR"]


def test_load_from_dict_overlay_src_on_non_overlay_mount_errors():
    """overlay-src on a non-overlay mount type (bind) is rejected."""
    with pytest.raises(ConfigValidationError, match="only valid for overlay"):
        ConfigLoader().load_from_dict(
            {"name": "test", "mounts": [{"source": "/s", "target": "/t", "type": "bind", "overlay-src": ["/a"]}]}
        )


def test_load_from_dict_workdir_on_non_overlay_mount_errors():
    """workdir on a non-overlay mount type (tmpfs) is rejected."""
    with pytest.raises(ConfigValidationError, match="only valid for overlay"):
        ConfigLoader().load_from_dict(
            {"name": "test", "mounts": [{"target": "/t", "type": "tmpfs", "workdir": "/wd"}]}
        )


def test_load_from_dict_source_on_ro_overlay_errors():
    """source on a ro-overlay mount is rejected (use overlay-src layers instead)."""
    with pytest.raises(ConfigValidationError, match="'source' is not valid"):
        ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"source": "/s", "target": "/d", "type": "ro-overlay", "overlay-src": ["/a", "/b"]}],
            }
        )


def test_load_from_dict_overlay_src_wrong_type_errors():
    """overlay-src must be a list of non-empty strings."""
    with pytest.raises(ConfigValidationError, match="list of non-empty strings"):
        ConfigLoader().load_from_dict(
            {"name": "test", "mounts": [{"target": "/d", "type": "ro-overlay", "overlay-src": "/a"}]}
        )
    with pytest.raises(ConfigValidationError, match="list of non-empty strings"):
        ConfigLoader().load_from_dict(
            {"name": "test", "mounts": [{"target": "/d", "type": "ro-overlay", "overlay-src": ["/a", ""]}]}
        )


def test_load_from_dict_workdir_wrong_type_errors():
    """workdir must be a non-empty string."""
    with pytest.raises(ConfigValidationError, match="'workdir' must be a non-empty string"):
        ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"source": "/rw", "workdir": 42, "target": "/d", "type": "overlay", "overlay-src": ["/a"]}],
            }
        )


def _make_overlay_run_args(overlay_ops: list) -> argparse.Namespace:
    """Build a minimal argparse.Namespace with an ordered overlay-ops list.

    Mimics what the CLI's _OverlayOpAction stores in namespace._overlay_ops.
    """
    return argparse.Namespace(
        config=None,
        chdir=None,
        die_with_parent=True,
        new_session=True,
        hostname=None,
        bind=[],
        ro_bind=[],
        cap_add=[],
        cap_drop=[],
        unshare=[],
        env=[],
        unset_env=[],
        _overlay_ops=overlay_ops,
    )


def test_load_with_overrides_overlay_cli_flags(tmp_path):
    """CLI --overlay-src/--overlay/--ro-overlay merge into overlay mounts in order."""
    config_file = tmp_path / "sandbox.yaml"
    config_file.write_text("name: test\n", encoding="utf-8")

    args = _make_overlay_run_args(
        [
            ("overlay-src", "/a"),
            ("overlay-src", "/b"),
            ("overlay", "/rw:/wd:/d"),
            ("overlay-src", "/c"),
            ("ro-overlay", "/ro"),
        ]
    )
    config = ConfigLoader().load_with_overrides(args, path=str(config_file))

    assert [m.type for m in config.mounts] == ["overlay", "ro-overlay"]
    overlay, ro = config.mounts
    assert (overlay.source, overlay.workdir, overlay.target) == ("/rw", "/wd", "/d")
    assert overlay.overlay_src == ["/a", "/b"]
    assert (ro.source, ro.workdir) == (None, None)
    assert ro.target == "/ro"
    assert ro.overlay_src == ["/c"]


def test_load_with_overrides_dangling_overlay_src_errors(tmp_path):
    """--overlay-src without a following overlay flag is rejected."""
    config_file = tmp_path / "sandbox.yaml"
    config_file.write_text("name: test\n", encoding="utf-8")

    args = _make_overlay_run_args([("overlay-src", "/a")])
    with pytest.raises(ConfigValidationError, match="no following"):
        ConfigLoader().load_with_overrides(args, path=str(config_file))


def test_load_with_overrides_malformed_overlay_value_errors(tmp_path):
    """--overlay with a value that is not SRC:WORKDIR:DEST is rejected."""
    config_file = tmp_path / "sandbox.yaml"
    config_file.write_text("name: test\n", encoding="utf-8")

    args = _make_overlay_run_args([("overlay", "/only-two:parts")])
    with pytest.raises(ConfigValidationError, match="SRC:WORKDIR:DEST"):
        ConfigLoader().load_with_overrides(args, path=str(config_file))


def test_load_with_overrides_malformed_tmp_overlay_value_errors(tmp_path):
    """--tmp-overlay with a value that is not a bare DEST is rejected."""
    config_file = tmp_path / "sandbox.yaml"
    config_file.write_text("name: test\n", encoding="utf-8")

    args = _make_overlay_run_args([("tmp-overlay", "/a:/b")])
    with pytest.raises(ConfigValidationError, match="expects DEST"):
        ConfigLoader().load_with_overrides(args, path=str(config_file))
