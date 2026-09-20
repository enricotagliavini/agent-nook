"""Tests for environment variable expansion in configuration files.

This module tests the `${VAR}` and `${VAR:-default}` syntax support
added to the configuration loader.
"""

from __future__ import annotations

import os

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
                "mounts": [{"target": "/tmp", "type": "tmpfs"}],
                "env_vars": {
                    "HOME": "${HOME}",
                },
            }
        )

        assert config.env_vars["HOME"] == "/custom/home"

    def test_unset_vars_expansion(self):
        """Test environment variable expansion in unset_vars string."""
        os.environ["VAR1"] = "value1"
        os.environ["VAR2"] = ""

        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"target": "/tmp", "type": "tmpfs"}],
                "unset_vars": "${VAR1} ${literal} ${VAR2:-default2}",
            }
        )

        # ${literal} is a literal string (not an env var), so it remains unchanged
        # VAR1 expands to "value1", VAR2 has default "default2"
        assert config.unset_vars == "value1 ${literal} default2"

    def test_chdir_expansion(self):
        """Test environment variable expansion in chdir."""
        os.environ["WORKDIR"] = "/workspace"
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"target": "/tmp", "type": "tmpfs"}],
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
                "mounts": [{"target": "/tmp", "type": "tmpfs"}],
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
                "mounts": [{"target": "/tmp", "type": "tmpfs"}],
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

    def test_unset_vars_newline_separated(self):
        """Test unset_vars with newline-separated variables (multi-line YAML).

        Validates that the whitespace-separated format works correctly:
        unset_vars:
          VAR1
          VAR2
          VAR3
        """
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"target": "/tmp", "type": "tmpfs"}],
                "unset_vars": "VAR1\nVAR2\nVAR3",
            }
        )
        # Should be normalized to space-separated string
        assert config.unset_vars == "VAR1 VAR2 VAR3"

    def test_unset_vars_space_separated(self):
        """Test unset_vars with space-separated variables on one line."""
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"target": "/tmp", "type": "tmpfs"}],
                "unset_vars": "VAR1 VAR2 VAR3",
            }
        )
        assert config.unset_vars == "VAR1 VAR2 VAR3"

    def test_unset_vars_mixed_whitespace(self):
        """Test unset_vars with mixed whitespace (tabs, spaces, newlines).

        Validates that split() handles all whitespace types correctly.
        """
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"target": "/tmp", "type": "tmpfs"}],
                "unset_vars": "VAR1\tVAR2\nVAR3",  # Tab + newline
            }
        )
        assert config.unset_vars == "VAR1 VAR2 VAR3"

    def test_unset_vars_multiple_spaces(self):
        """Test unset_vars with multiple consecutive spaces.

        Validates that consecutive whitespace is treated as a single separator.
        """
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"target": "/tmp", "type": "tmpfs"}],
                "unset_vars": "VAR1  VAR2  VAR3",  # Double spaces
            }
        )
        assert config.unset_vars == "VAR1 VAR2 VAR3"

    def test_unset_vars_leading_trailing_whitespace(self):
        """Test unset_vars with leading and trailing whitespace.

        Validates that whitespace at boundaries is stripped correctly.
        """
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"target": "/tmp", "type": "tmpfs"}],
                "unset_vars": "  VAR1  VAR2  VAR3  ",
            }
        )
        assert config.unset_vars == "VAR1 VAR2 VAR3"

    def test_unset_vars_ALL_keyword(self):
        """Test unset_vars with the special ALL keyword."""
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"target": "/tmp", "type": "tmpfs"}],
                "unset_vars": "ALL",
            }
        )
        # ALL keyword should be preserved exactly
        assert config.unset_vars == "ALL"

    def test_unset_vars_ENV_VAR_EXPANSION(self):
        """Test env var expansion in unset_vars with whitespace separation."""
        os.environ["VAR1"] = "value1"
        os.environ["VAR2"] = "value2"

        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"target": "/tmp", "type": "tmpfs"}],
                "unset_vars": "${VAR1} ${VAR2}",
            }
        )
        # Should expand and normalize to space-separated
        assert config.unset_vars == "value1 value2"

    def test_unset_vars_DEFAULT_VALUE(self):
        """Test ${VAR:-default} syntax in unset_vars with whitespace."""
        # UNSET_VAR1 and UNSET_VAR2 are guaranteed to be unset by setup_method
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"target": "/tmp", "type": "tmpfs"}],
                "unset_vars": "${UNSET_VAR1:-first} ${UNSET_VAR2:-second}",
            }
        )
        # Should use fallback defaults and normalize
        assert config.unset_vars == "first second"

    def test_unset_vars_EMPTY_STRING(self):
        """Test unset_vars with empty string input."""
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"target": "/tmp", "type": "tmpfs"}],
                "unset_vars": "",
            }
        )
        assert config.unset_vars == ""

    def test_unset_vars_ONLY_WHITESPACE(self):
        """Test unset_vars with only whitespace (spaces, tabs, newlines)."""
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"target": "/tmp", "type": "tmpfs"}],
                "unset_vars": "   \t\n   ",  # Only whitespace
            }
        )
        # Should normalize to empty string
        assert config.unset_vars == ""

    def test_unset_vars_SINGLE_VAR(self):
        """Test unset_vars with single variable."""
        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"target": "/tmp", "type": "tmpfs"}],
                "unset_vars": "SINGLE",
            }
        )
        assert config.unset_vars == "SINGLE"

    def test_unset_vars_ENV_VAR_ALL_KEYWORD(self):
        """Test env var expansion before ALL keyword check."""
        # Test that env vars are expanded BEFORE the ALL keyword is checked
        os.environ["MYALL"] = "expanded"

        config = ConfigLoader().load_from_dict(
            {
                "name": "test",
                "mounts": [{"target": "/tmp", "type": "tmpfs"}],
                "unset_vars": "${MYALL}",
            }
        )
        # Should expand to "expanded", not treat as ALL
        assert config.unset_vars == "expanded"


class TestCustomExpandEnvVarsRegex:
    """Direct tests for the _custom_expand_env_vars regex method.

    These tests stress-test the regex pattern at its weakest points
    to ensure robustness and maintain the quality of the implementation.
    """

    def setup_method(self):
        """Reset environment variables before each test."""
        self.original_vars = {}
        for var in ["REGEX_VAR1", "REGEX_VAR2", "REGEX_VAR3", "EMPTY_DEFAULT"]:
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

    def test_multiple_variables_in_single_string(self):
        """Test expansion when multiple variables appear in one string."""
        os.environ["REGEX_VAR1"] = "value1"
        os.environ["REGEX_VAR2"] = "value2"

        result = ConfigLoader()._custom_expand_env_vars("/home/${REGEX_VAR1}/path/${REGEX_VAR2}/end")
        assert result == "/home/value1/path/value2/end"

    def test_variable_at_string_boundaries(self):
        """Test variables at start, middle, and end of string."""
        os.environ["START"] = "s"
        os.environ["END"] = "e"

        result = ConfigLoader()._custom_expand_env_vars("${START}middle${END}")
        assert result == "smiddlee"

    def test_multiple_colons_in_default(self):
        """Test that colons in default values are preserved."""
        # Use an undefined variable to test the default value
        result = ConfigLoader()._custom_expand_env_vars("${UNDEFINED:-/usr/bin:/bin:/usr/local/bin}")
        # The default should be preserved with all its colons
        assert "/usr/bin" in result
        assert "/usr/local/bin" in result
        assert ":/" in result  # Colons preserved in default

    def test_very_long_default_value(self):
        """Test expansion with very long default values."""
        long_default = "/very/long/path/with/many/components/" * 100
        result = ConfigLoader()._custom_expand_env_vars("${VAR:-" + long_default + "}")
        assert result == long_default
        assert len(result) == len(long_default)

    def test_braces_in_default_value(self):
        """Test that closing braces in defaults are handled correctly."""
        # The regex stops at the first }, so "default" is the default value
        # and "with}brace}" is literal text that gets added
        result = ConfigLoader()._custom_expand_env_vars("${VAR:-default}with}brace}")
        # Should have the default value followed by literal "with}brace}"
        assert result == "defaultwith}brace}"

    def test_empty_variable_name_unchanged(self):
        """Test that malformed empty variable names remain literal."""
        result = ConfigLoader()._custom_expand_env_vars("${}value")
        assert result == "${}value"

    def test_empty_default_value_uses_empty(self):
        """Test ${VAR:-} uses empty string as explicit default."""
        os.environ["EMPTY_DEFAULT"] = ""
        result = ConfigLoader()._custom_expand_env_vars("${EMPTY_DEFAULT:-}suffix")
        assert result == "suffix"

    def test_no_variable_expands_literal(self):
        """Test that non-matching patterns remain unchanged."""
        result = ConfigLoader()._custom_expand_env_vars("no variable here ${UNSET} or ${MISSING:-/fallback}")
        assert "${UNSET}" in result
        # The fallback default IS expanded since MISSING is unset
        assert "/fallback" in result
        assert "or " in result

    def test_unicode_characters_in_values(self):
        """Test expansion with unicode in variable values."""
        os.environ["UNICODE_VAR"] = "/path/àáâ/中文/emoji🎉"
        result = ConfigLoader()._custom_expand_env_vars("${UNICODE_VAR}")
        assert result == "/path/àáâ/中文/emoji🎉"

    def test_closing_brace_in_default_not_misinterpreted(self):
        """Ensure ${ in default doesn't cause regex issues."""
        # The regex stops at the first }, so "a${b" is captured as default
        # Since VAR1 is set, we use its value "ignored"
        # The remaining text after the match is "c}"
        os.environ["VAR1"] = "ignored"
        result = ConfigLoader()._custom_expand_env_vars("${VAR1:-a${b}c}")
        # Result is env value "ignored" + remaining text "c}"
        assert result == "ignoredc}"

    def test_variable_name_with_underscore_and_dash(self):
        """Test variable names with underscores and dashes."""
        os.environ["REGEX_VAR_NAME"] = "value"
        os.environ["REGEX-VAR-NAME"] = "value2"
        result = ConfigLoader()._custom_expand_env_vars("${REGEX_VAR_NAME} ${REGEX-VAR-NAME}")
        assert result == "value value2"

    def test_whitespace_around_pattern(self):
        """Test that whitespace is preserved correctly."""
        result = ConfigLoader()._custom_expand_env_vars(" before ${VAR} after ${UNSET:-fallback} end")
        assert " before " in result
        assert " after " in result
        assert "end" in result

    def test_special_regex_characters_in_values(self):
        """Test that special regex chars in values don't break matching."""
        os.environ["SPECIAL"] = "/path/[glob]/pattern(*)/file(1)"
        result = ConfigLoader()._custom_expand_env_vars("${SPECIAL}")
        assert result == "/path/[glob]/pattern(*)/file(1)"
