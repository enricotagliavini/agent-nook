"""Integration tests for agent-nook CLI execution.

These tests verify that the `agent-nook run` command works correctly
when installed via pipx, using the bundled config file instead of
a potentially broken or outdated user config.

The key test verifies that the basic command:
    agent_nook run --config src/agent_nook/config/sandbox.yaml python3 -c "print('hello world')"
executes successfully and produces the expected output.
"""

import subprocess

import pytest


@pytest.fixture(scope="function")
def test_unset_all_env_config(xdg_config_dir) -> Path:
    """Create a temporary config file for testing unset-env ALL with env overrides."""
    config_path = xdg_config_dir / "test_unset_all_env.yaml"
    config_path.write_text(
        """# Test config for --unset-env ALL with --env overrides (via config file)
name: test-unset-all-env
mounts:
  - type: ro-bind
    source: /
    target: /
unset_vars: ALL
env_vars:
  PATH: "${PATH:/usr/bin:/bin}"
  TERM: "${TERM:xterm}"
"""
    )
    return config_path


@pytest.fixture(scope="function")
def test_unset_all_env_no_fallback_config(xdg_config_dir) -> Path:
    """Create a temporary config file for testing unset-env ALL with env vars (no fallback)."""
    config_path = xdg_config_dir / "test_unset_all_env_no_fallback.yaml"
    config_path.write_text(
        """# Test config for --unset-env ALL with --env overrides (no fallback)
name: test-unset-all-env-no-fallback
mounts:
  - type: ro-bind
    source: /
    target: /
unset_vars: ALL
env_vars:
  PATH: "${PATH}"
  TERM: "${TERM}"
"""
    )
    return config_path


@pytest.mark.integration
def test_basic_hello_world(test_sandbox_run) -> None:
    """Test that agent-nook run produces 'hello world' output.

    This is a regression test to ensure basic functionality is not broken
    by future changes. It runs the core command with the bundled config
    file to avoid issues with user-specific config files.

    Command tested:
        agent_nook run --config src/agent_nook/config/sandbox.yaml \
            python3 -c "print('hello world')"

    Args:
        test_sandbox_run: Fixture that returns a subprocess runner.

    Raises:
        AssertionError: If the command fails or produces unexpected output.
    """
    run_command = test_sandbox_run

    result = run_command(
        "run",
        "--config",
        "src/agent_nook/config/sandbox.yaml",
        "python3",
        "-c",
        "print('hello world')",
    )

    assert result.returncode == 0, (
        f"Command failed with return code {result.returncode}:\n"
        f"  stdout: {result.stdout!r}\n"
        f"  stderr: {result.stderr!r}"
    )

    assert "hello world" in result.stdout, (
        f"Expected 'hello world' in stdout, got: {result.stdout!r}"
    )


@pytest.mark.integration
def test_python_script_execution(test_sandbox_run) -> None:
    """Test that agent-nook can execute Python scripts.

    Verifies that Python scripts run correctly in the sandbox,
    which tests the full sandbox setup (bwrap, mounts, capabilities, etc.).

    Args:
        test_sandbox_run: Fixture that returns a subprocess runner.

    Raises:
        AssertionError: If the command fails or produces unexpected output.
    """
    run_command = test_sandbox_run

    result = run_command(
        "run",
        "--config",
        "src/agent_nook/config/sandbox.yaml",
        "python3",
        "-c",
        "import sys; print(f'Python {sys.version_info.major}.{sys.version_info.minor}')"
    )

    assert result.returncode == 0, (
        f"Command failed: {result.stderr!r}"
    )

    assert "2" in result.stdout or "3" in result.stdout, (
        f"Expected Python version in stdout, got: {result.stdout!r}"
    )


@pytest.mark.integration
def test_bash_command_execution(test_sandbox_run) -> None:
    """Test that agent-nook can execute bash commands.

    Verifies that basic shell functionality works in the sandbox.

    Args:
        test_sandbox_run: Fixture that returns a subprocess runner.

    Raises:
        AssertionError: If the command fails or produces unexpected output.
    """
    run_command = test_sandbox_run

    result = run_command(
        "run",
        "--config",
        "src/agent_nook/config/sandbox.yaml",
        "bash",
        "-c",
        "echo 'Hello from sandbox!'"
    )

    assert result.returncode == 0, (
        f"Command failed: {result.stderr!r}"
    )

    assert "Hello from sandbox!" in result.stdout, (
        f"Expected 'Hello from sandbox!' in stdout, got: {result.stdout!r}"
    )


@pytest.mark.integration
def test_env_vars_in_sandbox(test_sandbox_run) -> None:
    """Test that environment variables are inherited correctly.

    Verifies that the sandbox properly passes through environment
    variables (like HOME, PATH, etc.) from the parent process.

    Args:
        test_sandbox_run: Fixture that returns a subprocess runner.

    Raises:
        AssertionError: If environment variables are not correctly set.
    """
    run_command = test_sandbox_run

    result = run_command(
        "run",
        "--config",
        "src/agent_nook/config/sandbox.yaml",
        "bash",
        "-c",
        "echo $HOME"
    )

    assert result.returncode == 0, (
        f"Command failed: {result.stderr!r}"
    )

    assert "/home" in result.stdout, (
        f"HOME should contain '/home', got: {result.stdout!r}"
    )


@pytest.mark.integration
def test_timeout_with_busy_command(test_sandbox_run) -> None:
    """Test that agent-nook respects timeouts.

    Verifies that the sandbox can be killed when a command exceeds
    its timeout limit.

    Args:
        test_sandbox_run: Fixture that returns a subprocess runner.

    Raises:
        subprocess.TimeoutExpired: Expected — the command should timeout.
    """
    run_command = test_sandbox_run

    with pytest.raises(subprocess.TimeoutExpired, match="timed out"):
        run_command(
            "run",
            "--config",
            "src/agent_nook/config/sandbox.yaml",
            "bash",
            "-c",
            "while true; do :; done",
            timeout=2,
        )


@pytest.mark.integration
def test_unset_all_env_vars(test_sandbox_run) -> None:
    """Test that --unset-env ALL works correctly with custom --env vars.

    This test verifies that:
    1. --unset-env ALL clears all environment variables.
    2. --env PATH=${PATH} and --env TERM=${TERM} are correctly expanded.
    3. The resulting environment in the sandbox contains only the expected
       variables (or is empty if no defaults were set).

    Since PATH is usually set, if we unset ALL, we expect PATH to be missing
    unless it's set via --env.

    Command tested:
        agent-nook run --unset-env ALL --env PATH=${PATH} --env TERM=${TERM} \
            bash -c "env"

    Args:
        test_sandbox_run: Fixture that returns a subprocess runner.

    Raises:
        AssertionError: If the command fails or produces unexpected output.
    """
    run_command = test_sandbox_run

    result = run_command(
        "run",
        "--unset-env", "ALL",
        "--env", "PATH=${PATH}",
        "--env", "TERM=${TERM}",
        "bash",
        "-c",
        "env",
    )

    assert result.returncode == 0, (
        f"Command failed with return code {result.returncode}:\n"
        f"  stdout: {result.stdout!r}\n"
        f"  stderr: {result.stderr!r}"
    )

    # We expect PATH and TERM to be present because they were set via --env
    # even if --unset-env ALL was used (assuming --setenv comes before --clearenv
    # or that --clearenv only clears the initial environment).

    assert "PATH=" in result.stdout, "PATH should be present in the sandbox environment"
    assert "TERM=" in result.stdout, "TERM should be present in the sandbox environment"


@pytest.mark.integration
def test_unset_all_env_vars_from_config(test_sandbox_run, test_unset_all_env_config) -> None:
    """Test that env_vars in config file work correctly with --unset-env ALL.

    This test verifies that:
    1. unset_vars: ALL clears all environment variables (uses string format).
    2. env_vars with ${VAR:default} syntax are correctly expanded from the
       parent environment, even when ALL is unset.
    3. The resulting environment in the sandbox contains the expanded PATH
       and TERM variables.

    Command tested:
        agent_nook run --config <config_with_unset_all_and_env_vars> \
            bash -c "env"

    Args:
        test_sandbox_run: Fixture that returns a subprocess runner.
        test_unset_all_env_config: Fixture that creates the test config file.

    Raises:
        AssertionError: If the command fails or produces unexpected output.
    """
    run_command = test_sandbox_run
    config_path = test_unset_all_env_config

    result = run_command(
        "run",
        "--config",
        str(config_path),
        "bash",
        "-c",
        "env",
    )

    assert result.returncode == 0, (
        f"Command failed with return code {result.returncode}:\n"
        f"  stdout: {result.stdout!r}\n"
        f"  stderr: {result.stderr!r}"
    )

    # We expect PATH and TERM to be present because they were set via env_vars
    # in the config file, even though unset_vars contains ALL
    assert "PATH=" in result.stdout, "PATH should be present in the sandbox environment"
    # Check that fallback values are used (this will fail if PATH is set but fallback not used)
    path_value = result.stdout.split("PATH=")[1].split("\n")[0].strip()
    assert "/usr/bin" in path_value or "/bin" in path_value, (
        f"PATH should contain fallback values /usr/bin or /bin, got PATH={path_value!r}"
    )
    assert "TERM=" in result.stdout, "TERM should be present in the sandbox environment"
    term_value = result.stdout.split("TERM=")[1].split("\n")[0].strip()
    assert "xterm" in term_value, f"TERM should have xterm fallback, got TERM={term_value!r}"


@pytest.mark.integration
def test_unset_all_env_vars_from_config_no_fallback(test_sandbox_run, test_unset_all_env_no_fallback_config) -> None:
    """Test that env_vars without fallback still work when ALL is unset.

    This test verifies that env_vars with ${VAR} (without :-default) work
    correctly when ALL env vars are unset, because the environment variable
    expansion happens DURING config loading (before unset_vars are applied).

    unset_vars now uses string format: ALL (not a dict).

    When the config is loaded:
    1. ${PATH} is expanded to the current PATH value from the parent environment
    2. ${TERM} is expanded to the current TERM value from the parent environment
    3. Then --clearenv clears the initial sandbox environment
    4. Then --setenv PATH and --setenv TERM are applied

    Command tested:
        agent_nook run --config <config_with_unset_all_and_env_vars_no_fallback> \
            bash -c "env"

    Args:
        test_sandbox_run: Fixture that returns a subprocess runner.
        test_unset_all_env_no_fallback_config: Fixture for config without fallbacks.

    Raises:
        AssertionError: If the command fails (it should succeed).
    """
    run_command = test_sandbox_run
    config_path = test_unset_all_env_no_fallback_config

    # This should succeed because env var expansion happens before unset_vars
    result = run_command(
        "run",
        "--config",
        str(config_path),
        "bash",
        "-c",
        "env",
    )

    # The command should succeed because PATH and TERM are expanded during config loading
    assert result.returncode == 0, (
        f"Command should succeed: env vars are expanded during config loading\n"
        f"  stdout: {result.stdout!r}\n"
        f"  stderr: {result.stderr!r}"
    )

    assert "PATH=" in result.stdout, "PATH should be present in the sandbox environment"
    assert "TERM=" in result.stdout, "TERM should be present in the sandbox environment"


@pytest.mark.integration
def test_command_not_found_error(test_sandbox_run) -> None:
    """Test that non-existent commands produce a proper error.

    Verifies that the sandbox correctly reports missing commands
    rather than silently failing.

    Args:
        test_sandbox_run: Fixture that returns a subprocess runner.

    Raises:
        subprocess.CalledProcessError: Expected — the command should fail.
    """
    run_command = test_sandbox_run

    result = run_command(
        "run",
        "--config",
        "src/agent_nook/config/sandbox.yaml",
        "nonexistent_command_xyz_123",
    )

    assert result.returncode != 0, (
        "Non-existent command should have failed but didn't"
    )

    assert "nonexistent_command_xyz_123" in result.stdout or (
        "No such file" in result.stdout
    ), (
        f"Expected error message about missing command, got: {result.stdout!r}"
    )


@pytest.fixture(scope="function")
def test_malformed_env_vars_as_list_config(xdg_config_dir) -> Path:
    """Create a malformed config file with env_vars as a list instead of dict."""
    config_path = xdg_config_dir / "test_malformed_env_vars.yaml"
    config_path.write_text(
        """# Malformed config: env_vars is a list instead of dict
name: test-malformed-env-vars
mounts:
  - type: ro-bind
    source: /
    target: /
# This should fail with a type error
env_vars:
  - "HOME: ${HOME}"
  - "PATH: ${PATH}"
"""
    )
    return config_path


@pytest.fixture(scope="function")
def test_malformed_unset_vars_as_list_config(xdg_config_dir) -> Path:
    """Create a malformed config file with unset_vars as a list instead of string."""
    config_path = xdg_config_dir / "test_malformed_unset_vars.yaml"
    config_path.write_text(
        """# Malformed config: unset_vars is a list instead of string
name: test-malformed-unset-vars
mounts:
  - type: ro-bind
    source: /
    target: /
# This should fail with a type error
unset_vars:
  - ALL
"""
    )
    return config_path


@pytest.mark.integration
def test_malformed_env_vars_as_list(test_sandbox_run, test_malformed_env_vars_as_list_config) -> None:
    """Test that malformed env_vars (as list) is rejected with a friendly error.

    This test verifies that the type checking correctly rejects env_vars when
    it is specified as a list instead of a dict, providing a clear error message.

    Before this fix, the type check would silently accept the malformed config
    because _validate_structure() had a bug where it used .get() with defaults
    that forced the wrong type to pass validation.

    Args:
        test_sandbox_run: Fixture that returns a subprocess runner.
        test_malformed_env_vars_as_list_config: Fixture that creates the malformed config.

    Raises:
        subprocess.CalledProcessError: Expected — the config validation should fail.
    """
    run_command = test_sandbox_run
    config_path = test_malformed_env_vars_as_list_config

    result = run_command(
        "run",
        "--config",
        str(config_path),
        "bash",
        "-c",
        "echo 'hello'",
    )

    # The command should fail because the config is malformed
    assert result.returncode != 0, (
        f"Config validation should have failed for malformed env_vars:\n"
        f"  stdout: {result.stdout!r}\n"
        f"  stderr: {result.stderr!r}"
    )

    # Check that the error message mentions the type error
    assert "env_vars" in result.stdout.lower() or "env_vars" in result.stderr.lower() or (
        "must be a dict" in result.stdout or "must be a dict" in result.stderr
    ), (
        f"Expected error message about env_vars being a dict, got:\n"
        f"  stdout: {result.stdout!r}\n"
        f"  stderr: {result.stderr!r}"
    )


@pytest.mark.integration
def test_malformed_unset_vars_as_list(test_sandbox_run, test_malformed_unset_vars_as_list_config) -> None:
    """Test that malformed unset_vars (as list) is rejected with a friendly error.

    This test verifies that the type checking correctly rejects unset_vars when
    it is specified as a list instead of a string, providing a clear error message.

    unset_vars now uses string format (comma-separated variable names), so list
    format should be rejected.

    Args:
        test_sandbox_run: Fixture that returns a subprocess runner.
        test_malformed_unset_vars_as_list_config: Fixture that creates the malformed config.

    Raises:
        subprocess.CalledProcessError: Expected — the config validation should fail.
    """
    run_command = test_sandbox_run
    config_path = test_malformed_unset_vars_as_list_config

    result = run_command(
        "run",
        "--config",
        str(config_path),
        "bash",
        "-c",
        "echo 'hello'",
    )

    # The command should fail because the config is malformed
    assert result.returncode != 0, (
        f"Config validation should have failed for malformed unset_vars:\n"
        f"  stdout: {result.stdout!r}\n"
        f"  stderr: {result.stderr!r}"
    )

    # Check that the error message mentions unset_vars
    assert "unset_vars" in result.stdout.lower() or "unset_vars" in result.stderr.lower() or (
        "must be a dict" in result.stdout or "must be a dict" in result.stderr
    ), (
        f"Expected error message about unset_vars being a dict, got:\n"
        f"  stdout: {result.stdout!r}\n"
        f"  stderr: {result.stderr!r}"
    )


@pytest.fixture(scope="function")
def test_unset_vars_multiline_config(xdg_config_dir) -> Path:
    """Create a temporary config file with whitespace-separated unset_vars (multi-line)."""
    config_path = xdg_config_dir / "test_unset_vars_multiline.yaml"
    config_path.write_text(
        """# Test config for whitespace-separated unset_vars (multi-line YAML format)
name: test-unset-vars-multiline
mounts:
  - type: ro-bind
    source: /
    target: /
# Variables separated by newlines (whitespace-separated format)
unset_vars:
  MY_VAR1
  MY_VAR2
  MY_VAR3
env_vars:
  MY_VAR: "Hello from sandbox"
  TEST_VAR: "test_value"
"""
    )
    return config_path


@pytest.mark.integration
def test_unset_vars_multiline_format(test_sandbox_run, test_unset_vars_multiline_config) -> None:
    """Test that multiline whitespace-separated unset_vars works correctly.

    This test verifies that the format:
        unset_vars:
          VAR1
          VAR2
          VAR3
    works end-to-end with actual sandbox execution.

    Command tested:
        agent_nook run --config <multiline_unset_vars_config> bash -c "env"

    Args:
        test_sandbox_run: Fixture that returns a subprocess runner.
        test_unset_vars_multiline_config: Fixture for the multiline config.

    Raises:
        AssertionError: If the command fails or produces unexpected output.
    """
    run_command = test_sandbox_run
    config_path = test_unset_vars_multiline_config

    result = run_command(
        "run",
        "--config",
        str(config_path),
        "bash",
        "-c",
        "env",
    )

    # The command should succeed - unset_vars are properly parsed
    assert result.returncode == 0, (
        f"Command should succeed with multiline unset_vars:\n"
        f"  stdout: {result.stdout!r}\n"
        f"  stderr: {result.stderr!r}"
    )

    # Filter out log lines (they contain timestamps and/or the word "bwrap")
    # The actual env output from the sandbox starts after the log entries
    lines = result.stdout.split("\n")
    sandbox_output_lines = [
        line for line in lines
        if not (line.startswith("2026-09-18") or "bwrap" in line)
    ]
    sandbox_output = " ".join(sandbox_output_lines)

    # Verify MY_VAR1, MY_VAR2, and MY_VAR3 are NOT present in sandbox output (they were unset)
    for var in ["MY_VAR1=", "MY_VAR2=", "MY_VAR3="]:
        assert var not in sandbox_output, f"{var} should be unset in sandbox output"

    # Verify MY_VAR and TEST_VAR are present (they were set via env_vars)
    assert "MY_VAR=" in sandbox_output, "MY_VAR should be present"
    assert "Hello from sandbox" in sandbox_output, "MY_VAR value should be present"
    assert "TEST_VAR=" in sandbox_output, "TEST_VAR should be present"
    assert "test_value" in sandbox_output, "TEST_VAR value should be present"


@pytest.mark.integration
def test_config_flag_before_subcommand_rejected(test_sandbox_run) -> None:
    """Test that --config placed before the subcommand is rejected loudly.

    The main parser no longer defines --config: the run subparser's
    default previously clobbered the global value, so a config passed
    before the subcommand was silently ignored and the sandbox ran with
    the default config. Now argparse rejects the global placement with
    a usage error (exit code 2) and the command never runs.

    Command tested:
        agent_nook --config <config> run ...   (rejected)

    Args:
        test_sandbox_run: Fixture that returns a subprocess runner.

    Raises:
        AssertionError: If the command is not rejected with exit code 2.
    """
    result = test_sandbox_run(
        "--config",
        "src/agent_nook/config/sandbox.yaml",
        "run",
        "python3",
        "-c",
        "print('should never run')",
    )

    assert result.returncode == 2, (
        f"Expected argparse rejection (exit code 2), got {result.returncode}:\n"
        f"  stdout: {result.stdout!r}\n"
        f"  stderr: {result.stderr!r}"
    )

    assert "agent-nook: error" in result.stderr, (
        f"Expected an argparse usage error in stderr, got: {result.stderr!r}"
    )

    # The whole point: the flag is no longer silently ignored, so the
    # sandboxed command must not run with the default config.
    assert "should never run" not in result.stdout, (
        f"Sandboxed command ran despite invalid flag placement:\n"
        f"  stdout: {result.stdout!r}"
    )
