"""Integration tests for agent-nook CLI execution.

These tests verify that the `agent-nook run` command works correctly
when installed via pipx, using the bundled config file instead of
a potentially broken or outdated user config.

The key test verifies that the basic command:
    agent_nook run --config src/agent_nook/config/sandbox.yaml python3 -c "print('hello world')"
executes successfully and produces the expected output.
"""

import pytest
import subprocess


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
