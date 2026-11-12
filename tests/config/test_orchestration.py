"""Tests for the orchestration: config section (OrchestrationEntry + loader validation)."""

import os
import sys

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_nook.config.config import ConfigValidationError
from agent_nook.config.config import Mount
from agent_nook.config.config import OrchestrationEntry
from agent_nook.config.config import SandboxConfig
from agent_nook.config.loader import ConfigLoader


def _base(orchestration: list | None = None) -> dict:
    """Minimal valid config, optionally with an orchestration section."""
    data = {
        "name": "test",
        "mounts": [{"target": "/proc", "type": "proc"}],
    }
    if orchestration is not None:
        data["orchestration"] = orchestration
    return data


def _entry(**overrides) -> dict:
    """Minimal valid orchestration entry, with per-test overrides."""
    entry = {
        "command": ["python3", "/opt/stdio_bridge.py", "--listen", "/run/mcp.sock", "--", "node", "index.js"],
        "socket": "/run/mcp.sock",
    }
    entry.update(overrides)
    return entry


# --- OrchestrationEntry dataclass ---


def test_entry_defaults():
    """wait_timeout defaults to 30, keep defaults to False."""
    entry = OrchestrationEntry(command=["sleep", "30"], socket="/run/x.sock")
    assert entry.wait_timeout == 30
    assert entry.keep is False


def test_entry_is_frozen():
    """Entries are immutable value objects."""
    entry = OrchestrationEntry(command=["sleep", "30"], socket="/run/x.sock")
    with pytest.raises(AttributeError):
        entry.keep = True  # type: ignore[misc]


def test_sandbox_config_defaults_to_no_orchestration():
    """Existing configs are unchanged: orchestration defaults to an empty list."""
    config = SandboxConfig(name="t", mounts=[Mount(target="/proc", type="proc")])
    assert config.orchestration == []


# --- Loader: valid configs ---


def test_load_valid_unix_entry():
    """A single valid unix-socket entry parses into an OrchestrationEntry."""
    config = ConfigLoader().load_from_dict(_base([_entry()]))
    assert len(config.orchestration) == 1
    entry = config.orchestration[0]
    assert isinstance(entry, OrchestrationEntry)
    assert entry.command == ["python3", "/opt/stdio_bridge.py", "--listen", "/run/mcp.sock", "--", "node", "index.js"]
    assert entry.socket == "/run/mcp.sock"
    assert entry.wait_timeout == 30
    assert entry.keep is False


def test_load_valid_tcp_entry():
    """tcp:host:port sockets are accepted."""
    config = ConfigLoader().load_from_dict(_base([_entry(socket="tcp:127.0.0.1:9400")]))
    assert config.orchestration[0].socket == "tcp:127.0.0.1:9400"


def test_load_wait_timeout_and_keep():
    """Explicit wait-timeout and keep are honored."""
    config = ConfigLoader().load_from_dict(_base([_entry(**{"wait-timeout": 5.5, "keep": True})]))
    assert config.orchestration[0].wait_timeout == 5.5
    assert config.orchestration[0].keep is True


def test_load_multiple_entries():
    """Several entries parse independently."""
    config = ConfigLoader().load_from_dict(_base([_entry(), _entry(socket="tcp:127.0.0.1:9401", **{"wait-timeout": 10})]))
    assert [e.socket for e in config.orchestration] == ["/run/mcp.sock", "tcp:127.0.0.1:9401"]
    assert [e.wait_timeout for e in config.orchestration] == [30, 10]


def test_absent_section_defaults_to_empty_list():
    """No orchestration: key → empty list (zero behavior change for existing configs)."""
    config = ConfigLoader().load_from_dict(_base())
    assert config.orchestration == []


def test_env_expansion_inside_command(monkeypatch):
    """${VAR} expansion recurses into orchestration command lists."""
    monkeypatch.setenv("MCP_SOCK", "/run/mcp.sock")
    data = _base(
        [_entry(command=["python3", "stdio_bridge.py", "--listen", "${MCP_SOCK}", "--", "node", "index.js"], socket="${MCP_SOCK}")]
    )
    config = ConfigLoader().load_from_dict(data)
    entry = config.orchestration[0]
    assert entry.command[3] == "/run/mcp.sock"
    assert entry.socket == "/run/mcp.sock"


# --- Loader: invalid configs ---


def test_orchestration_must_be_a_list():
    with pytest.raises(ConfigValidationError, match="must be a list of dicts"):
        ConfigLoader().load_from_dict(_base({"command": ["sleep", "30"], "socket": "/run/mcp.sock"}))


def test_entry_must_be_a_dict():
    """The error message names the field (regression: was hardcoded 'mount[i]')."""
    with pytest.raises(ConfigValidationError, match="orchestration\\[0\\] must be a dict"):
        ConfigLoader().load_from_dict(_base(["not-a-dict"]))


def test_command_required():
    with pytest.raises(ConfigValidationError, match=r"orchestration\[0\]\.command must be a non-empty list"):
        ConfigLoader().load_from_dict(_base([{"socket": "/run/mcp.sock"}]))


def test_command_must_be_nonempty():
    with pytest.raises(ConfigValidationError, match=r"orchestration\[0\]\.command must be a non-empty list"):
        ConfigLoader().load_from_dict(_base([_entry(command=[])]))


def test_command_must_be_a_list():
    with pytest.raises(ConfigValidationError, match=r"orchestration\[0\]\.command must be a non-empty list"):
        ConfigLoader().load_from_dict(_base([_entry(command="python3 --listen /run/mcp.sock")]))


def test_command_elements_must_be_nonempty_strings():
    with pytest.raises(ConfigValidationError, match=r"orchestration\[0\]\.command\[1\] must be a non-empty string"):
        ConfigLoader().load_from_dict(_base([_entry(command=["python3", "", "node"])]))


def test_command_elements_must_be_strings():
    with pytest.raises(ConfigValidationError, match=r"orchestration\[0\]\.command\[0\] must be a non-empty string"):
        ConfigLoader().load_from_dict(_base([_entry(command=[42, "node"])]))


def test_socket_required():
    with pytest.raises(ConfigValidationError, match=r"orchestration\[0\]\.socket is required"):
        ConfigLoader().load_from_dict(_base([{"command": ["sleep", "30"]}]))


def test_socket_must_be_nonempty_string():
    with pytest.raises(ConfigValidationError, match=r"orchestration\[0\]\.socket is required"):
        ConfigLoader().load_from_dict(_base([_entry(socket="")]))
    with pytest.raises(ConfigValidationError, match=r"orchestration\[0\]\.socket is required"):
        ConfigLoader().load_from_dict(_base([_entry(socket=42)]))


def test_udp_socket_rejected():
    """udp: addresses are rejected at config time (D4)."""
    with pytest.raises(ConfigValidationError, match="udp: addresses are not supported"):
        ConfigLoader().load_from_dict(_base([_entry(socket="udp:127.0.0.1:9400")]))


@pytest.mark.parametrize(
    "bad",
    [0, -1, -0.5, "30", True, None, ["30"]],
    ids=["zero", "negative", "negative-float", "string", "bool", "none", "list"],
)
def test_wait_timeout_must_be_positive_number(bad):
    with pytest.raises(ConfigValidationError, match=r"orchestration\[0\]\.wait-timeout must be a positive number"):
        ConfigLoader().load_from_dict(_base([_entry(**{"wait-timeout": bad})]))


def test_wait_timeout_positive_values_accepted():
    for good in (1, 0.5, 30, 300):
        config = ConfigLoader().load_from_dict(_base([_entry(**{"wait-timeout": good})]))
        assert config.orchestration[0].wait_timeout == good


def test_keep_must_be_boolean():
    with pytest.raises(ConfigValidationError, match=r"orchestration\[0\]\.keep must be a boolean"):
        ConfigLoader().load_from_dict(_base([_entry(keep="yes")]))


def test_unknown_key_rejected():
    with pytest.raises(ConfigValidationError, match=r"orchestration\[0\] has unknown key 'bogus'"):
        ConfigLoader().load_from_dict(_base([_entry(bogus=1)]))


def test_error_message_points_at_right_entry():
    """The second bad entry is blamed by index, not the first one."""
    data = _base([_entry(), _entry(socket="")])
    with pytest.raises(ConfigValidationError, match=r"orchestration\[1\]\.socket is required"):
        ConfigLoader().load_from_dict(data)
