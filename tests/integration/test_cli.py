"""Integration tests for agent-nook CLI execution.

These tests verify that the `agent-nook run` command works correctly
when installed via pipx, using the bundled config file instead of
a potentially broken or outdated user config.

The key test verifies that the basic command:
    agent_nook run --config src/agent_nook/config/sandbox.yaml python3 -c "print('hello world')"
executes successfully and produces the expected output.
"""

import os
import pytest
import subprocess


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
unset_vars:
  - ALL
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
unset_vars:
  - ALL
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
    1. unset_vars: [ALL] clears all environment variables.
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
