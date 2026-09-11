"""Tests for ConfigLoader."""

import os
import tempfile
import pytest
from pathlib import Path

import sys
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_nook.config.loader import ConfigLoader, ConfigValidationError
from agent_nook.config.config import SandboxConfig, Mount, CapabilitySet, NamespaceSet


def test_load_yaml_basic():
    """Test loading a basic config."""
    loader = ConfigLoader()
    data = {
        "name": "test-sandbox",
        "root": "/tmp",
        "mounts": [
            {"target": "/proc", "type": "proc"},
            {"target": "/dev", "type": "dev"},
            {"target": "/tmp", "type": "tmpfs", "size": "100M"},
            {"source": "/host", "target": "/sandbox", "type": "bind"},
        ],
        "capabilities": {"drop": ["ALL"]},
        "unshare": ["pid", "uts", "user"],
        "die_with_parent": True,
        "new_session": True,
    }
    config = loader.set(data)

    assert config.name == "test-sandbox"
    assert config.root == "/tmp"
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
        "root": "/tmp",
        "mounts": [
            {"target": "/proc", "type": "proc"},
            {"target": "/dev", "type": "dev"},
            {"target": "/home", "type": "tmpfs"},
            {"target": "/tmp", "type": "tmpfs", "size": "500M"},
            {"source": "/host/src", "target": "/app/src", "type": "bind"},
            {"source": "/host/ssl", "target": "/app/ssl", "type": "ro-bind"},
        ],
    }
    config = loader.set(data)

    assert len(config.mounts) == 6
    assert config.mounts[0].type == "proc"
    assert config.mounts[1].type == "dev"
    assert config.mounts[2].type == "tmpfs"
    assert config.mounts[3].type == "tmpfs"
    assert config.mounts[3].size == "500M"
    assert config.mounts[4].type == "bind"
    assert config.mounts[5].type == "ro-bind"


def test_load_yaml_flat_dict_mounts():
    """Test loading config with flat dict mounts."""
    loader = ConfigLoader()
    data = {
        "name": "test",
        "root": "/tmp",
        "mounts": {
            "/proc": {"type": "proc"},
            "/host/data": {"target": "/data", "type": "ro-bind"},
        },
    }
    config = loader.set(data)

    assert len(config.mounts) == 2
    assert config.mounts[0].type == "proc"
    assert config.mounts[0].target == "/proc"
    assert config.mounts[1].type == "ro-bind"
    assert config.mounts[1].source == "/host/data"
    assert config.mounts[1].target == "/data"


def test_load_yaml_env_vars():
    """Test loading config with env_vars."""
    loader = ConfigLoader()
    data = {
        "name": "test",
        "root": "/tmp",
        "mounts": [{"target": "/proc", "type": "proc"}],
        "env_vars": {"PATH": "/usr/bin", "MY_VAR": "value"},
    }
    config = loader.set(data)

    assert config.env_vars == {"PATH": "/usr/bin", "MY_VAR": "value"}


def test_load_yaml_unenv_vars():
    """Test loading config with unenv_vars."""
    loader = ConfigLoader()
    data = {
        "name": "test",
        "root": "/tmp",
        "mounts": [{"target": "/proc", "type": "proc"}],
        "unenv_vars": ["PATH", "HOME"],
    }
    config = loader.set(data)

    assert config.unenv_vars == ["PATH", "HOME"]


def test_load_yaml_hostname():
    """Test loading config with hostname."""
    loader = ConfigLoader()
    data = {
        "name": "test",
        "root": "/tmp",
        "mounts": [{"target": "/proc", "type": "proc"}],
        "hostname": "myhost",
    }
    config = loader.set(data)

    assert config.hostname == "myhost"


def test_load_yaml_override():
    """Test loading config with override dict."""
    loader = ConfigLoader()
    data = {
        "name": "test",
        "root": "/tmp",
        "mounts": [{"target": "/proc", "type": "proc"}],
    }
    override = {"hostname": "overridden-host"}
    config = loader.set(data)
    config = loader._merge(config, override)

    assert config.hostname == "overridden-host"


def test_load_yaml_flat_list_unshare():
    """Test loading config with flat unshare list."""
    loader = ConfigLoader()
    data = {
        "name": "test",
        "root": "/tmp",
        "mounts": [{"target": "/proc", "type": "proc"}],
        "unshare": ["pid", "uts", "network"],
    }
    config = loader.set(data)

    assert config.unshare.pid is True
    assert config.unshare.uts is True
    assert config.unshare.network is True


def test_load_yaml_missing_required_field_errors():
    """Test that missing mounts raises error."""
    loader = ConfigLoader()
    data = {"mounts": [{"target": "/proc", "type": "proc"}]}  # missing name

    with pytest.raises(ConfigValidationError, match="name cannot be empty"):
        loader.set(data)


def test_load_yaml_empty_name_errors():
    """Test that empty name raises error."""
    loader = ConfigLoader()
    data = {"name": "", "root": "/tmp", "mounts": [{"target": "/proc", "type": "proc"}]}

    with pytest.raises(ConfigValidationError, match="name cannot be empty"):
        loader.set(data)


def test_load_yaml_cap_dropped_kept_errors():
    """Test that dropping ALL with kept caps raises error."""
    loader = ConfigLoader()
    data = {
        "name": "test",
        "root": "/tmp",
        "mounts": [{"target": "/proc", "type": "proc"}],
        "capabilities": {"drop": ["ALL"], "keep": ["CHOWN"]},
    }

    with pytest.raises(ConfigValidationError, match="Cannot keep capabilities"):
        loader.set(data)


def test_load_yaml_unknown_key_errors():
    """Test that unknown top-level keys raise ConfigValidationError."""
    loader = ConfigLoader()

    # tmpfs_mounts is deprecated and should not be recognized
    data = {
        "name": "test",
        "root": "/tmp",
        "mounts": [{"target": "/proc", "type": "proc"}],
    }

    # Add unknown key
    data["tmpfs_mounts"] = [{"target": "/tmp", "size": "100M"}]
    with pytest.raises(ConfigValidationError, match="unknown key 'tmpfs_mounts'"):
        loader.set(data)

    # Completely unknown key
    data2 = {
        "name": "test",
        "root": "/tmp",
        "mounts": [{"target": "/proc", "type": "proc"}],
        "weird_key": "foo",
    }
    with pytest.raises(ConfigValidationError, match="unknown key 'weird_key'"):
        loader.set(data2)


def test_load_yaml_unknown_mount_type_errors():
    """Test that unknown mount types raise ValueError."""
    loader = ConfigLoader()
    data = {
        "name": "test",
        "root": "/tmp",
        "mounts": [
            {"target": "/proc", "type": "proc"},
            {"target": "/data", "type": "invalid-type"},
        ],
    }

    with pytest.raises(ValueError, match="Mount type 'invalid-type' is not valid"):
        loader.set(data)


def test_set_config():
    """Test that set_config returns a SandboxConfig."""
    loader = ConfigLoader()
    data = {
        "name": "test",
        "root": "/tmp",
        "mounts": [
            {"target": "/proc", "type": "proc"},
            {"target": "/dev", "type": "dev"},
            {"target": "/tmp", "type": "tmpfs", "size": "100M"},
        ],
        "capabilities": {"drop": ["ALL"]},
    }

    config = loader.set(data)
    assert isinstance(config, SandboxConfig)
    assert config.name == "test"
    assert len(config.mounts) == 3
    assert config.mounts[0].type == "proc"
    assert config.mounts[1].type == "dev"
    assert config.mounts[2].type == "tmpfs"
    assert config.capabilities.dropped == ["ALL"]
