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
    """Minimal valid orchestration entry, with per-test overrides.

    `command` is a single string (the documented format); the loader
    tokenizes it into an argv list with shlex.split. The socket path is no
    longer config — `track-socket: true` asks agent-nook to generate a
    unique name at run time and wire it into the command + sandbox.

    Overrides accept Python-friendly underscore keys (e.g. `track_socket`);
    they are normalized to the YAML hyphen keys the loader expects.
    """
    entry = {
        "command": "python3 /opt/stdio_bridge.py --listen ${STDIO_SOCKET_PATH} -- node index.js",
        "track-socket": True,
    }
    for key, value in overrides.items():
        entry[key.replace("_", "-")] = value
    return entry


# --- OrchestrationEntry dataclass ---


def test_entry_defaults():
    """wait_timeout defaults to 30, keep defaults to False, track_socket to False."""
    entry = OrchestrationEntry(command=["sleep", "30"], track_socket=True)
    assert entry.wait_timeout == 30
    assert entry.keep is False
    assert entry.track_socket is True


def test_track_socket_defaults_false():
    """track_socket defaults to False: without it, no runtime socket is generated."""
    entry = OrchestrationEntry(command=["sleep", "30"])
    assert entry.track_socket is False


def test_entry_is_frozen():
    """Entries are immutable value objects."""
    entry = OrchestrationEntry(command=["sleep", "30"], track_socket=True)
    with pytest.raises(AttributeError):
        entry.keep = True  # type: ignore[misc]


def test_sandbox_config_defaults_to_no_orchestration():
    """Existing configs are unchanged: orchestration defaults to an empty list."""
    config = SandboxConfig(name="t", mounts=[Mount(target="/proc", type="proc")])
    assert config.orchestration == []


# --- Loader: valid configs ---


def test_load_valid_unix_entry():
    """A single valid command string parses into an OrchestrationEntry (tokenized to argv)."""
    config = ConfigLoader().load_from_dict(_base([_entry()]))
    assert len(config.orchestration) == 1
    entry = config.orchestration[0]
    assert isinstance(entry, OrchestrationEntry)
    assert entry.command == ["python3", "/opt/stdio_bridge.py", "--listen", "${STDIO_SOCKET_PATH}", "--", "node", "index.js"]
    assert entry.track_socket is True
    assert entry.wait_timeout == 30
    assert entry.keep is False


def test_command_string_with_spaces_is_tokenized_by_shlex():
    """A single string with multiple spaces around an argument is one argv element (shlex, not literal)."""
    config = ConfigLoader().load_from_dict(_base([_entry(command="python3 -c 'a   b c'")]))
    assert config.orchestration[0].command == ["python3", "-c", "a   b c"]


def test_track_socket_default_false_when_absent():
    """track-socket omitted → False, so no runtime socket is generated."""
    config = ConfigLoader().load_from_dict(_base([{"command": "sleep 30"}]))
    assert config.orchestration[0].track_socket is False


def test_load_wait_timeout_and_keep():
    """Explicit wait-timeout and keep are honored."""
    config = ConfigLoader().load_from_dict(_base([_entry(**{"wait-timeout": 5.5, "keep": True})]))
    assert config.orchestration[0].wait_timeout == 5.5
    assert config.orchestration[0].keep is True


def test_load_multiple_entries():
    """Several entries parse independently."""
    config = ConfigLoader().load_from_dict(
        _base([_entry(), _entry(track_socket=False, **{"wait-timeout": 10}), _entry(track_socket=True, **{"keep": True})])
    )
    assert [e.track_socket for e in config.orchestration] == [True, False, True]
    assert [e.wait_timeout for e in config.orchestration] == [30, 10, 30]


def test_absent_section_defaults_to_empty_list():
    """No orchestration: key → empty list (zero behavior change for existing configs)."""
    config = ConfigLoader().load_from_dict(_base())
    assert config.orchestration == []


def test_env_expansion_inside_command(monkeypatch):
    """${VAR} expansion recurses into orchestration command strings (the ${STDIO_SOCKET_PATH}
    placeholder is left intact unless the variable is actually set in the host env)."""
    monkeypatch.setenv("MCP_SOCK", "/run/mcp.sock")
    data = _base([_entry(command="python3 stdio_bridge.py --listen ${MCP_SOCK} -- node index.js")])
    config = ConfigLoader().load_from_dict(data)
    entry = config.orchestration[0]
    assert entry.command[3] == "/run/mcp.sock"


# --- Loader: invalid configs ---


def test_orchestration_must_be_a_list():
    with pytest.raises(ConfigValidationError, match="must be a list of dicts"):
        ConfigLoader().load_from_dict(_base({"command": ["sleep", "30"], "track-socket": True}))


def test_entry_must_be_a_dict():
    """The error message names the field (regression: was hardcoded 'mount[i]')."""
    with pytest.raises(ConfigValidationError, match="orchestration\\[0\\] must be a dict"):
        ConfigLoader().load_from_dict(_base(["not-a-dict"]))


def test_command_must_be_a_string():
    """A command given as a list (old format) is rejected: it is a single string now."""
    for bad in (["sleep", "30"], 42, {"x": 1}):
        with pytest.raises(ConfigValidationError, match=r"orchestration\[0\]\.command must be a non-empty string"):
            ConfigLoader().load_from_dict(_base([_entry(command=bad)]))


def test_command_must_be_nonempty():
    """A missing command is rejected (the only required field)."""
    with pytest.raises(ConfigValidationError, match=r"orchestration\[0\]\.command must be a non-empty string"):
        ConfigLoader().load_from_dict(_base([{"track-socket": True}]))


def test_command_empty_string_rejected():
    """An empty string tokenizes into no arguments and is rejected."""
    with pytest.raises(ConfigValidationError, match=r"orchestration\[0\]\.command must be a non-empty string"):
        ConfigLoader().load_from_dict(_base([_entry(command="")]))


def test_command_whitespace_only_rejected():
    """A whitespace-only string tokenizes into no arguments and is rejected."""
    for bad in ("   ", "\t", "\n"):
        with pytest.raises(ConfigValidationError, match=r"orchestration\[0\]\.command must be a non-empty string"):
            ConfigLoader().load_from_dict(_base([_entry(command=bad)]))


def test_command_unterminated_quote_rejected():
    """A shell tokenization error (e.g. unterminated quote) is surfaced as a config error."""
    with pytest.raises(ConfigValidationError, match=r"orchestration\[0\]\.command could not be parsed as a shell command"):
        ConfigLoader().load_from_dict(_base([_entry(command='"unterminated')]))


def test_track_socket_must_be_boolean():
    """track-socket must be a boolean, not a truthy string like 'true'."""
    for bad in ("true", "yes", 1, None, [True]):
        with pytest.raises(ConfigValidationError, match=r"orchestration\[0\]\.track-socket must be a boolean"):
            ConfigLoader().load_from_dict(_base([_entry(track_socket=bad)]))


def test_track_socket_truthy_string_rejected():
    """'true' as a string is rejected (YAML strings are not booleans); the user must omit the
    value or write an actual boolean."""
    with pytest.raises(ConfigValidationError, match=r"orchestration\[0\]\.track-socket must be a boolean"):
        ConfigLoader().load_from_dict(_base([_entry(**{"track-socket": "true"})]))


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
    data = _base([_entry(), _entry(track_socket="yes")])
    with pytest.raises(ConfigValidationError, match=r"orchestration\[1\]\.track-socket must be a boolean"):
        ConfigLoader().load_from_dict(data)
