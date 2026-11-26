"""Tests for the secrets: config section (Secret + loader validation)."""

import os
import sys

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_nook.config.config import ConfigValidationError
from agent_nook.config.config import Mount
from agent_nook.config.config import SandboxConfig
from agent_nook.config.config import Secret
from agent_nook.config.loader import ConfigLoader


def _base(secrets: list | None = None) -> dict:
    """Minimal valid config, optionally with a secrets section."""
    data = {
        "name": "test",
        "mounts": [{"target": "/proc", "type": "proc"}],
    }
    if secrets is not None:
        data["secrets"] = secrets
    return data


def _entry(**overrides) -> dict:
    """Minimal valid secret entry (name + exactly one source), per-test overrides.

    The caller supplies the source via `file=` or `command=`; the base carries
    only `name` so an override can never leave two sources on one entry.
    Overrides accept Python-friendly keys; secret keys carry no hyphens, so the
    normalization below is a no-op, kept only for parity with the orchestration
    test helper.
    """
    entry = {"name": "gh-token"}
    for key, value in overrides.items():
        entry[key.replace("_", "-")] = value
    return entry


# --- Secret dataclass ---


def test_entry_defaults():
    """A file-only secret has no source_command and no env_var."""
    secret = Secret(name="gh-token", source_file="/tmp/x")
    assert secret.source_command is None
    assert secret.env_var is None


def test_entry_is_frozen():
    """Secrets are immutable value objects."""
    secret = Secret(name="gh-token", source_command=["gh", "auth", "token"])
    with pytest.raises(AttributeError):
        secret.env_var = "GH_TOKEN"  # type: ignore[misc]


def test_sandbox_config_defaults_to_no_secrets():
    """Existing configs are unchanged: secrets defaults to an empty list."""
    config = SandboxConfig(name="t", mounts=[Mount(target="/proc", type="proc")])
    assert config.secrets == []


# --- Loader: valid configs ---


def test_load_valid_file_secret():
    """A file-source secret keeps the host path; env_var is None (file-only)."""
    config = ConfigLoader().load_from_dict(_base([_entry(file="/tmp/creds")]))
    secret = config.secrets[0]
    assert isinstance(secret, Secret)
    assert secret.name == "gh-token"
    assert secret.source_file == "/tmp/creds"
    assert secret.source_command is None
    assert secret.env_var is None


def test_load_valid_command_secret():
    """A command-source secret is tokenized into an argv list (no shell)."""
    config = ConfigLoader().load_from_dict(_base([_entry(command="gh auth token")]))
    secret = config.secrets[0]
    assert secret.source_file is None
    assert secret.source_command == ["gh", "auth", "token"]


def test_command_tokenized_by_shlex():
    """Quoted arguments with internal spaces are one argv element (shlex, not literal)."""
    config = ConfigLoader().load_from_dict(_base([_entry(command="python3 -c 'a   b c'")]))
    assert config.secrets[0].source_command == ["python3", "-c", "a   b c"]


def test_secret_with_env_export():
    """An explicit env: value is honored as the variable name to export."""
    config = ConfigLoader().load_from_dict(_base([_entry(command="gh auth token", env="GH_TOKEN")]))
    assert config.secrets[0].env_var == "GH_TOKEN"


def test_env_absent_makes_secret_file_only():
    """No env: → env_var is None, so the secret is file-only (opt-in exposure)."""
    config = ConfigLoader().load_from_dict(_base([_entry(command="gh auth token")]))
    assert config.secrets[0].env_var is None


def test_name_is_sanitized_to_filename():
    """Path components in name are stripped so the secret can't escape /run/secrets."""
    config = ConfigLoader().load_from_dict(_base([_entry(name="sub/dir/gh-token", command="gh auth token")]))
    assert config.secrets[0].name == "gh-token"


def test_load_multiple_secrets():
    """Several secrets parse independently."""
    data = _base(
        [
            _entry(command="gh auth token"),
            _entry(file="/tmp/a", env="FOO"),
            _entry(name="other", command="echo hi"),
        ]
    )
    config = ConfigLoader().load_from_dict(data)
    secrets = config.secrets
    assert [(s.name, s.source_file, s.env_var) for s in secrets] == [
        ("gh-token", None, None),
        ("gh-token", "/tmp/a", "FOO"),
        ("other", None, None),
    ]


def test_absent_section_defaults_to_empty_list():
    """No secrets: key → empty list (zero behavior change for existing configs)."""
    assert ConfigLoader().load_from_dict(_base()).secrets == []


def test_env_expansion_inside_command(monkeypatch):
    """${VAR} expansion recurses into secret command strings."""
    monkeypatch.setenv("MY_TOKEN", "s3cr3t")
    data = _base([_entry(command="echo ${MY_TOKEN}")])
    assert ConfigLoader().load_from_dict(data).secrets[0].source_command == ["echo", "s3cr3t"]


# --- Loader: invalid configs ---


def test_secrets_must_be_a_list():
    with pytest.raises(ConfigValidationError, match="must be a list of dicts"):
        ConfigLoader().load_from_dict(_base({"command": "gh auth token"}))


def test_entry_must_be_a_dict():
    """The error message names the field (regression: was hardcoded 'mount[i]')."""
    with pytest.raises(ConfigValidationError, match="secrets\\[0\\] must be a dict"):
        ConfigLoader().load_from_dict(_base(["not-a-dict"]))


def test_name_must_be_a_string():
    for bad in (42, {"x": 1}, ["a"]):
        with pytest.raises(ConfigValidationError, match=r"secrets\[0\]\.name must be a non-empty string"):
            ConfigLoader().load_from_dict(_base([_entry(name=bad)]))


def test_name_must_be_nonempty():
    """A missing name is rejected."""
    with pytest.raises(ConfigValidationError, match=r"secrets\[0\]\.name must be a non-empty string"):
        ConfigLoader().load_from_dict(_base([{"command": "gh auth token"}]))


def test_name_empty_string_rejected():
    """An empty/whitespace name is rejected."""
    for bad in ("", "   ", "\t"):
        with pytest.raises(ConfigValidationError, match=r"secrets\[0\]\.name must be a non-empty string"):
            ConfigLoader().load_from_dict(_base([_entry(name=bad)]))


def test_command_must_be_a_string():
    """A command given as a list is rejected: it is a single string now."""
    for bad in (["gh", "auth", "token"], 42, {"x": 1}):
        with pytest.raises(ConfigValidationError, match=r"secrets\[0\]\.command must be a non-empty string"):
            ConfigLoader().load_from_dict(_base([_entry(command=bad)]))


def test_command_empty_string_rejected():
    """An empty string tokenizes into no arguments and is rejected."""
    with pytest.raises(ConfigValidationError, match=r"secrets\[0\]\.command must be a non-empty string"):
        ConfigLoader().load_from_dict(_base([_entry(command="")]))


def test_command_whitespace_only_rejected():
    """A whitespace-only string tokenizes into no arguments and is rejected."""
    for bad in ("   ", "\t", "\n"):
        with pytest.raises(ConfigValidationError, match=r"secrets\[0\]\.command must be a non-empty string"):
            ConfigLoader().load_from_dict(_base([_entry(command=bad)]))


def test_command_unterminated_quote_rejected():
    """A shell tokenization error (e.g. unterminated quote) is surfaced as a config error."""
    with pytest.raises(ConfigValidationError, match=r"secrets\[0\]\.command could not be parsed as a shell command"):
        ConfigLoader().load_from_dict(_base([_entry(command='"unterminated')]))


def test_file_must_be_a_string():
    """A file given as an int or list is rejected; None means "absent" (handled by exactly-one)."""
    for bad in (42, ["x"]):
        with pytest.raises(ConfigValidationError, match=r"secrets\[0\]\.file must be a non-empty string"):
            ConfigLoader().load_from_dict(_base([_entry(file=bad)]))


def test_file_empty_string_rejected():
    """An empty file path is rejected."""
    with pytest.raises(ConfigValidationError, match=r"secrets\[0\]\.file must be a non-empty string"):
        ConfigLoader().load_from_dict(_base([_entry(file="")]))


def test_command_and_file_both_rejected():
    """A secret with both a file and a command is rejected."""
    with pytest.raises(ConfigValidationError, match=r"only one is allowed"):
        ConfigLoader().load_from_dict(_base([_entry(file="/tmp/x", command="gh auth token")]))


def test_neither_command_nor_file_rejected():
    """A secret with neither a file nor a command is rejected."""
    with pytest.raises(ConfigValidationError, match=r"exactly one of 'file' or 'command'"):
        ConfigLoader().load_from_dict(_base([_entry(command=None, file=None)]))


def test_env_must_be_a_string():
    """env must be a non-empty string; None means "absent" (valid, file-only)."""
    for bad in (1, True, ["FOO"]):
        with pytest.raises(ConfigValidationError, match=r"secrets\[0\]\.env must be a non-empty string"):
            ConfigLoader().load_from_dict(_base([_entry(command="gh auth token", env=bad)]))


def test_env_empty_string_rejected():
    """An empty env value is rejected."""
    with pytest.raises(ConfigValidationError, match=r"secrets\[0\]\.env must be a non-empty string"):
        ConfigLoader().load_from_dict(_base([_entry(command="gh auth token", env="")]))


def test_unknown_key_rejected():
    with pytest.raises(ConfigValidationError, match=r"secrets\[0\] has unknown key 'bogus'"):
        ConfigLoader().load_from_dict(_base([_entry(bogus=1)]))


def test_error_message_points_at_right_entry():
    """The second bad entry is blamed by index, not the first one."""
    data = _base([_entry(command="gh auth token"), _entry(command="gh auth token", env=1)])
    with pytest.raises(ConfigValidationError, match=r"secrets\[1\]\.env must be a non-empty string"):
        ConfigLoader().load_from_dict(data)
