"""Integration tests for agent-nook CLI execution.

These tests verify that the `agent-nook run` command works correctly
when installed via pipx, using the bundled config file instead of
a potentially broken or outdated user config.

The key test verifies that the basic command:
    agent_nook run --config src/agent_nook/config/sandbox.yaml python3 -c "print('hello world')"
executes successfully and produces the expected output.
"""


from pathlib import Path

import pytest
import re


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
def test_timeout_with_busy_command(test_sandbox_run, tmp_path) -> None:
    """Test that agent-nook kills a command that exceeds the config timeout.

    The config sets timeout: 0.1, far below the 30-second sleep, so
    agent-nook must terminate the sandbox, report the timeout, and exit
    non-zero — well within the generous 10s outer timeout, which must
    never trigger.

    Args:
        test_sandbox_run: Fixture that returns a subprocess runner.
        tmp_path: Temp directory for the timeout config.

    Raises:
        AssertionError: If agent-nook does not report the timeout.
    """
    run_command = test_sandbox_run

    config_file = tmp_path / "timeout.yaml"
    config_file.write_text(
        "name: test-timeout\n"
        "chdir: \"/tmp\"\n"
        "mounts:\n"
        "  - source: /\n"
        "    target: /\n"
        "    type: bind\n"
        "  - target: /tmp\n"
        "    type: tmpfs\n"
        "timeout: 0.1\n"
        "die_with_parent: true\n"
        "new_session: true\n",
        encoding="utf-8",
    )

    result = run_command(
        "run",
        "--config",
        str(config_file),
        "sleep",
        "30",
        timeout=10,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0, (
        f"Command should fail on timeout, got return code {result.returncode}:\n"
        f"  stdout: {result.stdout!r}\n"
        f"  stderr: {result.stderr!r}"
    )
    assert "timed out" in output.lower(), f"Expected a timeout error message, got:\n  {output!r}"


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


@pytest.fixture(scope="function")
def test_create_source_config(xdg_config_dir, tmp_path) -> tuple[Path, Path]:
    """Create a config file whose bind source does not exist on the host yet."""
    source = tmp_path / "agent" / "workspace"
    config_path = xdg_config_dir / "test_create_source.yaml"
    config_path.write_text(
        f"""# Test config for create-source: the bind source does not exist yet
name: test-create-source
mounts:
  - type: ro-bind
    source: /
    target: /
  - type: tmpfs
    target: /tmp
  - type: bind
    source: {source}
    target: /tmp/agent_workspace
    create-source: true
"""
    )
    return config_path, source


@pytest.mark.integration
def test_create_source_creates_missing_directory(test_sandbox_run, test_create_source_config) -> None:
    """create-source: true creates a missing source directory before execution.

    Runs the real CLI with a config whose bind mount source does not exist
    yet (including missing parents). The sandbox must start successfully and
    the host source directory must exist afterwards.

    Command tested:
        agent_nook run --config <config> python3 -c "print('create-source ok')"

    Args:
        test_sandbox_run: Fixture that returns a subprocess runner.
        test_create_source_config: (config_path, source) fixture pair.

    Raises:
        AssertionError: If the command fails or the source is not created.
    """
    config_path, source = test_create_source_config
    assert not source.exists(), "Precondition: source directory must not exist"

    result = test_sandbox_run(
        "run",
        "--config",
        str(config_path),
        "python3",
        "-c",
        "print('create-source ok')",
    )

    assert result.returncode == 0, (
        f"Command failed with return code {result.returncode}:\n"
        f"  stdout: {result.stdout!r}\n"
        f"  stderr: {result.stderr!r}"
    )

    assert source.is_dir(), f"Expected create-source to create {source}, but it does not exist"


_overlay_probe: bool | None = None


def _require_overlay_support() -> None:
    """Skip the test if unprivileged bwrap overlayfs is not available here.

    Runs a minimal real bwrap overlay once (cached) to detect kernel/bwrap
    support; the overlay integration tests require unprivileged user
    namespaces with overlayfs.
    """
    global _overlay_probe
    if _overlay_probe is None:
        import shutil
        import subprocess
        import tempfile

        _overlay_probe = False
        if shutil.which("bwrap") is not None:
            with tempfile.TemporaryDirectory(prefix="agent-nook-overlay-probe-") as tmp:
                t = Path(tmp)
                (t / "l").mkdir()
                (t / "u").mkdir()
                (t / "w").mkdir()
                (t / "l" / "f.txt").write_text("probe")
                try:
                    proc = subprocess.run(
                        [
                            "bwrap",
                            "--bind",
                            "/",
                            "/",
                            "--overlay-src",
                            str(t / "l"),
                            "--overlay",
                            str(t / "u"),
                            str(t / "w"),
                            str(t / "d"),
                            "/bin/sh",
                            "-c",
                            f"cat {t}/d/f.txt",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    proc = None
                _overlay_probe = proc is not None and proc.returncode == 0 and "probe" in proc.stdout
    if not _overlay_probe:
        pytest.skip("bwrap overlayfs not available unprivileged in this environment")


def _command_output(result) -> list[str]:
    """Return the sandboxed command's stdout lines, with agent-nook log lines removed.

    The CLI logger writes INFO lines to stdout (``YYYY-MM-DD HH:MM:SS -
    agent_nook ...``); strip them so only the command's own output remains.
    """
    return [
        line for line in result.stdout.splitlines() if not re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - ", line)
    ]


@pytest.mark.integration
def test_overlay_mounts(test_sandbox_run, xdg_config_dir, tmp_path) -> None:
    """Overlay mounts work end-to-end (single CLI session, 3 runs total).

    Verifies layer visibility and precedence, write persistence to the RWSRC
    (host), read-only rejection for ro-overlay, non-persistent writes for
    tmp-overlay, workdir reuse across runs, and the CLI flag forms.
    """
    _require_overlay_support()

    l1 = tmp_path / "l1"
    l2 = tmp_path / "l2"
    upper = tmp_path / "upper"
    workdir = tmp_path / "work"
    dest = tmp_path / "dest"
    dest_ro = tmp_path / "dest-ro"
    dest_tmp = tmp_path / "dest-tmp"
    for d in (l1, l2, upper):
        d.mkdir()
    (l1 / "f.txt").write_text("base\n")
    (l2 / "f.txt").write_text("mid\n")  # shadows l1 (listed last)
    (l1 / "only-l1.txt").write_text("l1\n")

    upper2 = tmp_path / "upper2"
    workdir2 = tmp_path / "work2"
    dest_cli = tmp_path / "dest-cli"
    upper2.mkdir()

    config_path = xdg_config_dir / "test_overlay.yaml"
    config_path.write_text(
        f"""name: test-overlay
mounts:
  - source: /
    target: /
    type: bind
  - target: /proc
    type: proc
  - target: /dev
    type: dev
  - type: overlay
    source: {upper}
    workdir: {workdir}
    target: {dest}
    overlay-src:
      - {l1}
      - {l2}
  - type: ro-overlay
    target: {dest_ro}
    overlay-src:
      - {l1}
      - {l2}
  - type: tmp-overlay
    target: {dest_tmp}
    overlay-src:
      - {l1}
""",
        encoding="utf-8",
    )

    script = (
        f"cat {dest}/f.txt"
        f" && cat {dest}/only-l1.txt"
        f" && echo written > {dest}/new.txt"
        f" && cat {dest_ro}/f.txt"
        f" && (echo x > {dest_ro}/w.txt) && echo RO-BROKEN || echo ro-denied"
        f" && cat {dest_tmp}/only-l1.txt"
        f" && echo tmp > {dest_tmp}/t.txt && cat {dest_tmp}/t.txt"
    )
    result = test_sandbox_run("run", "--config", str(config_path), "sh", "-c", script)
    assert result.returncode == 0, (
        f"Overlay run failed with return code {result.returncode}:\n"
        f"  stdout: {result.stdout!r}\n"
        f"  stderr: {result.stderr!r}"
    )
    lines = _command_output(result)
    assert lines[0] == "mid", f"layer precedence wrong (last src should win): {lines!r}"
    assert "ro-denied" in lines, f"ro-overlay accepted a write: {lines!r}"
    assert "RO-BROKEN" not in lines
    assert lines[-1] == "tmp"
    # The writable-overlay write persisted to the RWSRC on the host
    assert (upper / "new.txt").read_text() == "written\n"

    # Second run: the workdir now holds the kernel's leftover state; it must
    # still work (no emptiness enforcement).
    result2 = test_sandbox_run("run", "--config", str(config_path), "sh", "-c", f"cat {dest}/new.txt")
    assert result2.returncode == 0, (
        f"Workdir reuse run failed:\n  stdout: {result2.stdout!r}\n  stderr: {result2.stderr!r}"
    )
    assert _command_output(result2) == ["written"]

    # tmp-overlay writes are not persisted anywhere on the host
    assert not list(tmp_path.rglob("t.txt"))

    # CLI flag forms: --overlay-src … --overlay SRC:WORKDIR:DEST
    result3 = test_sandbox_run(
        "run",
        "--config",
        str(config_path),
        "--overlay-src",
        str(l1),
        "--overlay-src",
        str(l2),
        "--overlay",
        f"{upper2}:{workdir2}:{dest_cli}",
        "sh",
        "-c",
        f"cat {dest_cli}/f.txt",
    )
    assert result3.returncode == 0, (
        f"CLI overlay run failed:\n  stdout: {result3.stdout!r}\n  stderr: {result3.stderr!r}"
    )
    assert _command_output(result3) == ["mid"]
