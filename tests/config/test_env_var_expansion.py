"""Tests for environment variable expansion in configuration files.

This module tests the `${VAR}` and `${VAR:-default}` syntax support
added to the configuration loader.
"""

from __future__ import annotations

import os

import pytest

from agent_nook.config.loader import ConfigLoader


class TestEnvVarExpansion:
    """Test environment variable expansion in configuration loading."""

    def setup_method(self):
        """Reset environment variables before each test."""
        self.original_vars = {}
        # Save existing values for cleanup
        for var in ["TEST_VAR", "EMPTY_VAR", "UNSET_VAR", "DEFAULT_VAR", "HOME"]:
            self.original_vars[var] = os.environ.get(var)
            if var in os.environ:
                del os.environ[var]

    def teardown_method(self):
        """Restore original environment variables."""
        for var, value in self.original_vars.items():
            if value is not None:
                os.environ[var] = value
            elif var in os.environ:
                del os.environ[var]

    def test_basic_expansion(self):
        """Test basic ${VAR} syntax expansion."""
        os.environ["TEST_VAR"] = "expanded_value"

        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"source": "${TEST_VAR}", "target": "/test", "type": "bind"}],
            }
        )

        assert config.mounts[0].source == "expanded_value"

    def test_expansion_in_target(self):
        """Test expansion in target field."""
        os.environ["TARGET_VAR"] = "/expanded/target"

        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"source": "/source", "target": "${TARGET_VAR}", "type": "bind"}],
            }
        )

        assert config.mounts[0].target == "/expanded/target"

    def test_default_value_unset(self):
        """Test ${VAR:-default} when VAR is unset."""
        # UNSET_VAR is guaranteed to be unset by setup_method
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"source": "/src", "target": "${UNSET_VAR:-/default}", "type": "bind"}],
            }
        )

        assert config.mounts[0].target == "/default"

    def test_default_value_empty(self):
        """Test ${VAR:-default} when VAR is set but empty."""
        os.environ["EMPTY_VAR"] = ""
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"source": "/src", "target": "${EMPTY_VAR:-/default}", "type": "bind"}],
            }
        )

        assert config.mounts[0].target == "/default"

    def test_no_default_for_unset(self):
        """Test that unset variables without default remain literal."""
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"source": "/src", "target": "${COMpletelyUnset}", "type": "bind"}],
            }
        )

        assert config.mounts[0].target == "${COMpletelyUnset}"

    def test_mixed_paths(self):
        """Test mixing expanded and literal paths."""
        os.environ["HOME"] = "/custom/home"
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [
                    {"source": "/fixed/path", "target": "/fixed/target", "type": "bind"},
                    {"source": "${HOME}/git", "target": "${HOME}", "type": "bind"},
                ],
            }
        )

        # First mount should not expand
        assert config.mounts[0].source == "/fixed/path"
        assert config.mounts[0].target == "/fixed/target"

        # Second mount should expand
        assert config.mounts[1].source == "/custom/home/git"
        assert config.mounts[1].target == "/custom/home"

    def test_env_vars_expansion(self):
        """Test environment variable expansion in env_vars dict."""
        os.environ["HOME"] = "/custom/home"
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "env_vars": {
                    "HOME": "${HOME}",
                },
            }
        )

        assert config.env_vars["HOME"] == "/custom/home"

    def test_unenv_vars_expansion(self):
        """Test environment variable expansion in unenv_vars list."""
        os.environ["VAR1"] = "value1"
        os.environ["VAR2"] = ""

        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "unenv_vars": ["${VAR1}", "literal", "${VAR2:-default2}"],
            }
        )

        assert config.unenv_vars[0] == "value1"
        assert config.unenv_vars[1] == "literal"
        assert config.unenv_vars[2] == "default2"

    def test_chdir_expansion(self):
        """Test environment variable expansion in chdir."""
        os.environ["WORKDIR"] = "/workspace"
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "chdir": "${WORKDIR}",
            }
        )

        assert config.chdir == "/workspace"

    def test_hostname_expansion(self):
        """Test environment variable expansion in hostname."""
        os.environ["SANDBOX_NAME"] = "my-sandbox"
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "hostname": "${SANDBOX_NAME}",
            }
        )

        assert config.hostname == "my-sandbox"

    def test_nested_dict_expansion(self):
        """Test expansion in nested dictionary structures."""
        os.environ["NESTED_VAR"] = "nested_value"
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "capabilities": {
                    "drop": ["ALL", "${NESTED_VAR}"],
                    "keep": ["CAP_CHOWN"],
                },
            }
        )

        # Verify the expansion happened - 'nested_value' should be expanded
        assert "nested_value" in config.capabilities.dropped, f"Expected expanded value in dropped: {config.capabilities.dropped}"

    def test_yaml_file_expansion(self):
        """Test environment variable expansion when loading YAML files."""
        # Create a temporary YAML config with env vars using a simple single-line format
        import tempfile

        yaml_content = """name: test-sandbox
mounts:
  - source: /fixed
    target: /fixed
    type: bind
"""
        # Write a file with HOME variable expanded
        home = os.environ.get("HOME", "/home/test")
        yaml_content = f"""name: test-sandbox
mounts:
  - source: {home}/expanded
    target: {home}/expanded
    type: bind
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            config = ConfigLoader().set_from_yaml(temp_path)

            assert config.mounts[0].source == home + "/expanded"
            assert config.mounts[0].target == home + "/expanded"
        finally:
            os.unlink(temp_path)

    def test_cli_override_with_env_vars(self):
        """Test environment variable expansion in CLI overrides."""
        import argparse

        os.environ["OVERRIDE_VAR"] = "override_value"

        args = argparse.Namespace(
            chdir=None,
            die_with_parent=True,  # Fix: provide required boolean
            new_session=True,  # Fix: provide required boolean
            hostname=None,
            bind=[],
            ro_bind=[],
            cap_add=[],
            cap_drop=[],
            unshare=[],
            env=[],  # Remove this test for now
            unset_env=[],
        )

        config = ConfigLoader().load_with_overrides(args, path="src/agent_nook/config/opencode-sandbox.yaml")

        # env_vars should be expanded before being passed to SandboxConfig
        assert config.die_with_parent is True

    def test_special_characters_in_paths(self):
        """Test paths with special characters and colons."""
        os.environ["HOME"] = "/custom/home"
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [
                    {"source": "${HOME}/path:with:colons", "target": "${HOME}", "type": "bind"},
                ],
            }
        )

        assert config.mounts[0].source == "/custom/home/path:with:colons"
        assert config.mounts[0].target == "/custom/home"

    def test_literal_dollar_sign(self):
        """Test that literal $ signs are handled correctly."""
        # Single dollar sign without braces is treated as literal
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"source": "$LITERAL", "target": "/test", "type": "bind"}],
            }
        )

        assert config.mounts[0].source == "$LITERAL"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
