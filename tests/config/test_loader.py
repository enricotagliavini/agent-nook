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
