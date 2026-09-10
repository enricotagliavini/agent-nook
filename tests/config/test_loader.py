"""Tests for config loader."""

import pytest
import tempfile
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_nook.config.loader import ConfigLoader, ConfigValidationError
from agent_nook.config.config import SandboxConfig, Mount, TmpfsMount, CapabilitySet, NamespaceSet


def test_load_yaml_basic():
    """Test loading a basic YAML config."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
tmpfs_mounts:
  - target: /tmp
capabilities:
  drop: ALL
unshare:
  - pid
  - uts
die_with_parent: true
new_session: true
""")
        path = f.name

    try:
        loader = ConfigLoader()
        config = loader.load(path)
        assert config.name == "test-sandbox"
        assert config.root == "/tmp"
        assert len(config.mounts) == 1
        assert config.mounts[0].source == "/"
        assert config.mounts[0].target == "/"
        assert len(config.tmpfs_mounts) == 1
        assert config.tmpfs_mounts[0].target == "/tmp"
        assert config.capabilities.dropped == ["ALL"]
        assert config.capabilities.kept == []
        assert config.unshare.pid is True
        assert config.unshare.uts is True
    finally:
        os.unlink(path)


def test_load_yaml_list_of_mounts():
    """Test loading YAML with list of mounts."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""name: test-sandbox
root: /tmp
mounts:
  - source: /host/path1
    target: /sandbox/path1
    readonly: true
  - source: /host/path2
    target: /sandbox/path2
tmpfs_mounts:
  - target: /tmp
    size: 100M
capabilities:
  drop: ALL
unshare:
  - pid
  - user
""")
        path = f.name

    try:
        loader = ConfigLoader()
        config = loader.load(path)
        assert len(config.mounts) == 2
        assert config.mounts[0].source == "/host/path1"
        assert config.mounts[0].target == "/sandbox/path1"
        assert config.mounts[0].readonly is True
        assert config.mounts[1].target == "/sandbox/path2"
        assert len(config.tmpfs_mounts) == 1
        assert config.tmpfs_mounts[0].target == "/tmp"
        assert config.tmpfs_mounts[0].size == "100M"
    finally:
        os.unlink(path)


def test_load_yaml_flat_dict_mounts():
    """Test loading YAML with flat dict format mounts."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""name: test-sandbox
root: /tmp
mounts:
  /host/path1: /sandbox/path1
  /host/path2: /sandbox/path2
tmpfs_mounts:
  /tmp: 500M
  /var: 200M
capabilities:
  drop: ALL
unshare:
  - pid
  - uts
""")
        path = f.name

    try:
        loader = ConfigLoader()
        config = loader.load(path)
        assert len(config.mounts) == 2
        assert config.mounts[0].source == "/host/path1"
        assert config.mounts[0].target == "/sandbox/path1"
        assert config.mounts[1].source == "/host/path2"
        assert config.mounts[1].target == "/sandbox/path2"
        assert len(config.tmpfs_mounts) == 2
        assert config.tmpfs_mounts[0].target == "/tmp"
        assert config.tmpfs_mounts[0].size == "500M"
        assert config.tmpfs_mounts[1].target == "/var"
        assert config.tmpfs_mounts[1].size == "200M"
        # drop ALL means keep must be empty
        assert config.capabilities.dropped == ["ALL"]
        assert config.capabilities.kept == []
    finally:
        os.unlink(path)


def test_load_yaml_env_vars():
    """Test loading YAML with env_vars."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
tmpfs_mounts:
  - target: /tmp
env_vars:
  KEY1: value1
  KEY2: value2
die_with_parent: true
new_session: true
""")
        path = f.name

    try:
        loader = ConfigLoader()
        config = loader.load(path)
        assert config.env_vars["KEY1"] == "value1"
        assert config.env_vars["KEY2"] == "value2"
    finally:
        os.unlink(path)


def test_load_yaml_unenv_vars():
    """Test loading YAML with unenv_vars."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
tmpfs_mounts:
  - target: /tmp
unenv_vars:
  - OLD_VAR
  - ANOTHER_VAR
die_with_parent: true
new_session: true
""")
        path = f.name

    try:
        loader = ConfigLoader()
        config = loader.load(path)
        assert config.unenv_vars == ["OLD_VAR", "ANOTHER_VAR"]
    finally:
        os.unlink(path)


def test_load_yaml_hostname():
    """Test loading YAML with hostname."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
tmpfs_mounts:
  - target: /tmp
hostname: myhost
die_with_parent: true
new_session: true
""")
        path = f.name

    try:
        loader = ConfigLoader()
        config = loader.load(path)
        assert config.hostname == "myhost"
    finally:
        os.unlink(path)


def test_load_yaml_override():
    """Test that override parameter works."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""name: original-sandbox
root: /tmp
mounts:
  - source: /
    target: /
tmpfs_mounts:
  - target: /tmp
die_with_parent: true
new_session: true
""")
        path = f.name

    try:
        loader = ConfigLoader()
        config = loader.load(path, override={"mounts": [{"source": "/extra", "target": "/extra"}]})
        assert config.name == "original-sandbox"
        assert len(config.mounts) == 2
        assert config.mounts[1].source == "/extra"
        assert config.mounts[1].target == "/extra"
    finally:
        os.unlink(path)


def test_load_yaml_flat_list_unshare():
    """Test loading YAML with unshare as a flat list."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
tmpfs_mounts:
  - target: /tmp
capabilities:
  drop: ALL
unshare:
  - pid
  - uts
  - ipc
  - cgroup
  - user
die_with_parent: true
new_session: true
""")
        path = f.name

    try:
        loader = ConfigLoader()
        config = loader.load(path)
        assert config.unshare.pid is True
        assert config.unshare.uts is True
        assert config.unshare.ipc is True
        assert config.unshare.cgroup is True
        assert config.unshare.user is True
        assert config.unshare.network is False
    finally:
        os.unlink(path)


def test_load_yaml_missing_required_field():
    """Test that missing required fields raise ValidationError."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""name: test-sandbox
root: /tmp
""")
        path = f.name

    try:
        loader = ConfigLoader()
        with pytest.raises(ConfigValidationError, match="mounts"):
            loader.load(path)
    finally:
        os.unlink(path)


def test_load_yaml_empty_name():
    """Test that empty name raises ValidationError."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""name: ""
root: /tmp
mounts:
  - source: /
    target: /
tmpfs_mounts:
  - target: /tmp
die_with_parent: true
new_session: true
""")
        path = f.name

    try:
        loader = ConfigLoader()
        with pytest.raises(ConfigValidationError, match="empty"):
            loader.load(path)
    finally:
        os.unlink(path)


def test_load_yaml_cap_dropped_kept():
    """Test loading YAML with dropped/kept fields."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
tmpfs_mounts:
  - target: /tmp
capabilities:
  dropped:
    - CAP_NET_ADMIN
    - CAP_NET_RAW
  kept:
    - CAP_CHOWN
    - CAP_SETUID
die_with_parent: true
new_session: true
""")
        path = f.name

    try:
        loader = ConfigLoader()
        config = loader.load(path)
        assert config.capabilities.dropped == ["CAP_NET_ADMIN", "CAP_NET_RAW"]
        assert config.capabilities.kept == ["CAP_CHOWN", "CAP_SETUID"]
    finally:
        os.unlink(path)


def test_load_yaml_proc_dev_enabled():
    """Test loading YAML with proc_enabled and dev_enabled."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""name: test-sandbox
root: /tmp
mounts:
  - source: /
    target: /
tmpfs_mounts:
  - target: /tmp
proc_enabled: true
dev_enabled: true
die_with_parent: true
new_session: true
""")
        path = f.name

    try:
        loader = ConfigLoader()
        config = loader.load(path)
        assert config.proc_enabled is True
        assert config.dev_enabled is True
    finally:
        os.unlink(path)


def test_set_config():
    """Test setting config from a dict."""
    loader = ConfigLoader()
    config = loader.set({
        "name": "dict-config",
        "root": "/app",
        "mounts": [{"source": "/", "target": "/"}],
        "tmpfs_mounts": [{"target": "/tmp"}],
        "capabilities": {"drop": "ALL"},
        "unshare": ["pid"],
    })
    assert config.name == "dict-config"
    assert config.root == "/app"


def test_set_config_env_vars_flat_list():
    """Test setting config with env_vars as flat list."""
    loader = ConfigLoader()
    config = loader.set({
        "name": "test",
        "root": "/tmp",
        "mounts": [{"source": "/", "target": "/"}],
        "tmpfs_mounts": [{"target": "/tmp"}],
        "env_vars": ["KEY1=value1", "KEY2=value2"],
    })
    assert config.env_vars["KEY1"] == "value1"
    assert config.env_vars["KEY2"] == "value2"
