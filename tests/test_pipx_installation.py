"""End-to-end tests for pipx installation and runtime behavior."""

import os
import sys
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def built_wheel() -> Path:
    """Build and return the path to the wheel file."""
    dist_dir = Path(__file__).parent.parent / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [sys.executable, "-m", "hatch", "build", "-t", "wheel"],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
        check=True,
    )

    wheel_path = list(dist_dir.glob("agent_nook-*.whl"))
    assert len(wheel_path) > 0, f"No wheel found in {dist_dir}"
    return wheel_path[0]


@pytest.fixture(scope="session")
def built_sdist() -> Path:
    """Build and return the path to the sdist file."""
    dist_dir = Path(__file__).parent.parent / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [sys.executable, "-m", "hatch", "build", "-t", "sdist"],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
        check=True,
    )

    sdist_path = list(dist_dir.glob("agent_nook-*.tar.gz"))
    assert len(sdist_path) > 0, f"No sdist found in {dist_dir}"
    return sdist_path[0]


@pytest.fixture(scope="function")
def pipx_test_env(tmp_path: Path, built_sdist: Path) -> tuple[Path, Path, list[str]]:
    """Provide a clean pipx test environment for each test.

    Uses PIPX_HOME to override the default ~/.local/share/pipx/venvs location,
    ensuring the test is truly isolated and doesn't interfere with the host
    pipx installation.
    """
    pipx_home = tmp_path / "pipx"
    pipx_bin = tmp_path / "bin"

    pipx_home.mkdir(parents=True, exist_ok=True)
    pipx_bin.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PIPX_HOME"] = str(pipx_home)
    env["PIPX_BIN_DIR"] = str(pipx_bin)
    env["PATH"] = str(pipx_bin) + os.pathsep + env.get("PATH", "")

    return pipx_home, pipx_bin, env


def test_build_artifacts_exist(built_wheel: Path, built_sdist: Path) -> None:
    """Verify wheel and sdist are buildable and exist."""
    assert built_wheel.exists(), f"Wheel not found: {built_wheel}"
    assert built_sdist.exists(), f"Sdist not found: {built_sdist}"


def test_wheel_contains_bundled_config(built_wheel: Path) -> None:
    """Verify the built wheel contains the bundled config file."""
    import zipfile

    with zipfile.ZipFile(built_wheel, "r") as zf:
        members = zf.namelist()

    pattern = r"agent_nook/config/sandbox.yaml"
    found = any(re.match(pattern, m) for m in members)

    assert found, "Bundled config (agent_nook/config/sandbox.yaml) not found in wheel"


def test_pipx_installation(
    built_sdist: Path,
    pipx_test_env: tuple[Path, Path, list[str]],
) -> None:
    """Phase 3: Install package via pipx in an isolated, non-standard directory."""
    pipx_home, pipx_bin, env = pipx_test_env

    # Verify directories are empty before install
    assert not list(pipx_home.glob("venvs")), (
        f"pipx home already contains venvs: {list(pipx_home.glob('venvs'))}"
    )
    assert not list(pipx_bin.glob("*")), (
        f"pipx bin already contains files: {list(pipx_bin.glob('*'))}"
    )

    # Run pipx install with our isolated environment
    result = subprocess.run(
        [
            sys.executable, "-m", "pipx", "install",
            str(built_sdist),
            "--force",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert result.returncode == 0, (
        f"pipx install failed:\n  stdout: {result.stdout}\n  stderr: {result.stderr}"
    )

    # Verify the venv was created in our isolated directory
    assert pipx_home.exists(), f"pipx home not created at {pipx_home}"

    venv_dirs = list(pipx_home.glob("venvs/agent-nook-*"))
    assert len(venvs_dirs) >= 1, (
        f"No venv directory was created in {pipx_home}. "
        f"Contents: {list(pipx_home.iterdir())}"
    )

    # Verify the binary symlink exists
    agent_nook_exe = pipx_bin / "agent-nook"
    assert agent_nook_exe.exists(), (
        f"Binary not symlinked at {agent_nook_exe}. "
        f"Contents of {pipx_bin}: {list(pipx_bin.iterdir())}"
    )
    assert agent_nook_exe.is_symlink(), f"{agent_nook_exe} is not a symlink"


def test_pipx_executable_runs(
    built_sdist: Path,
    pipx_test_env: tuple[Path, Path, list[str]],
) -> None:
    """Phase 4: Verify the installed agent-nook binary works from the isolated PATH."""
    pipx_home, pipx_bin, env = pipx_test_env

    # Ensure the environment includes our isolated bin directory FIRST
    env["PATH"] = str(pipx_bin) + os.pathsep + env.get("PATH", "")

    # Test: --version should work
    result = subprocess.run(
        ["agent-nook", "--version"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"agent-nook --version failed:\n  stdout: {result.stdout}\n  stderr: {result.stderr}"
    )


def test_sandbox_execution(
    built_sdist: Path,
    pipx_test_env: tuple[Path, Path, list[str]],
) -> None:
    """Phase 5: Verify the sandbox actually runs commands with isolation."""
    pipx_home, pipx_bin, env = pipx_test_env

    # Ensure the environment includes our isolated bin directory FIRST
    env["PATH"] = str(pipx_bin) + os.pathsep + env.get("PATH", "")

    # Test 1: Basic command execution
    result = subprocess.run(
        ["agent-nook", "run", "--command", "echo hello"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"agent-nook run --command failed:\n  stdout: {result.stdout}\n  stderr: {result.stderr}"
    )
    assert "hello" in result.stdout, (
        f"Expected 'hello' in output, got: {result.stdout}"
    )

    # Test 2: Command with quotes
    result = subprocess.run(
        ["agent-nook", "run", "--command", "echo 'hello world'"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"agent-nook run --command failed:\n  stdout: {result.stdout}\n  stderr: {result.stderr}"
    )
    assert "hello world" in result.stdout, (
        f"Expected 'hello world' in output, got: {result.stdout}"
    )


def test_config_loader(
    built_sdist: Path,
    pipx_test_env: tuple[Path, Path, list[str]],
) -> None:
    """Phase 6: Verify ConfigLoader can find the bundled config after pipx install."""
    pipx_home, pipx_bin, env = pipx_test_env

    # Create a test script that imports and tests ConfigLoader
    test_script = f"""
import sys
import os
import importlib.util
import json
import re

# Add the pipx bin dir to path so agent-nook can be found
sys.path.insert(0, r'{pipx_bin}')

# Force find the installed agent_nook module
spec = importlib.util.find_spec("agent_nook")
if spec is None:
    print("ERROR: agent_nook not found", file=sys.stderr)
    sys.exit(1)

if spec.origin is None:
    print("ERROR: agent_nook is a namespace package", file=sys.stderr)
    sys.exit(1)

print(f"SPEC_ORIGIN={{spec.origin}}")

# Import ConfigLoader
from agent_nook.config.loader import ConfigLoader
loader = ConfigLoader()

# Find the default config
config_path = loader.find_default_config()
print(f"CONFIG_PATH={{config_path}}")

# Verify it exists
if not os.path.exists(config_path):
    print("ERROR: Config file does not exist", file=sys.stderr)
    sys.exit(1)

# Verify it's valid YAML with required keys
import yaml
with open(config_path) as f:
    config = yaml.safe_load(f)

if config is None or not isinstance(config, dict):
    print("ERROR: Config is empty or not a dict", file=sys.stderr)
    sys.exit(1)

if "sandbox" not in config:
    print("ERROR: Config missing 'sandbox' key", file=sys.stderr)
    sys.exit(1)

print("CONFIG_VALID=true")
"""

    result = subprocess.run(
        [sys.executable, "-c", test_script],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert result.returncode == 0, (
        f"Config loader test failed:\n  stdout: {result.stdout}\n  stderr: {result.stderr}"
    )
    assert "CONFIG_VALID=true" in result.stdout, (
        f"Config validation failed. Output: {result.stdout}"
    )


def test_env_propagation(
    built_sdist: Path,
    pipx_test_env: tuple[Path, Path, list[str]],
) -> None:
    """Phase 7: Verify environment variables are properly passed through."""
    pipx_home, pipx_bin, env = pipx_test_env

    # Ensure the environment includes our isolated bin directory FIRST
    env["PATH"] = str(pipx_bin) + os.pathsep + env.get("PATH", "")

    # Test: --env works
    result = subprocess.run(
        ["agent-nook", "run", "--command", "echo $TEST_VAR", "--env", "TEST_VAR=hello_world"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"agent-nook run --env failed:\n  stdout: {result.stdout}\n  stderr: {result.stderr}"
    )
    assert "hello_world" in result.stdout, (
        f"Expected 'hello_world' in output, got: {result.stdout}"
    )

    # Test: --unset-env works
    env_with_var = os.environ.copy()
    env_with_var["PATH"] = str(pipx_bin) + os.pathsep + env.get("PATH", "")
    env_with_var["TEST_VAR"] = "should_be_unset"

    result = subprocess.run(
        ["agent-nook", "run", "--command", "echo $TEST_VAR", "--unset-env", "TEST_VAR"],
        capture_output=True,
        text=True,
        env=env_with_var,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"agent-nook run --unset-env failed:\n  stdout: {result.stdout}\n  stderr: {result.stderr}"
    )
    assert result.stdout.strip() == "", (
        f"Expected empty output (unset var), got: {result.stdout}"
    )


def test_config_file_override(
    built_sdist: Path,
    pipx_test_env: tuple[Path, Path, list[str]],
) -> None:
    """Phase 8: Verify the --config/-f flag properly overrides config."""
    pipx_home, pipx_bin, env = pipx_test_env

    # Ensure the environment includes our isolated bin directory FIRST
    env["PATH"] = str(pipx_bin) + os.pathsep + env.get("PATH", "")

    # Create a custom config with a custom hostname
    custom_config = pipx_home / "custom.yaml"
    custom_config.write_text(
        """
sandbox:
  name: "custom-agent"
  hostname: "custom-hostname"
"""
    )

    # Run with custom config
    result = subprocess.run(
        ["agent-nook", "run", "-f", str(custom_config), "--command", "hostname"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"agent-nook run -f failed:\n  stdout: {result.stdout}\n  stderr: {result.stderr}"
    )
    # Custom hostname should be respected
    assert "custom-hostname" in result.stdout, (
        f"Expected 'custom-hostname' in output, got: {result.stdout}"
    )

    # Run without custom config - should use default sandbox name
    result = subprocess.run(
        ["agent-nook", "run", "--command", "hostname"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"agent-nook run (no config) failed:\n  stdout: {result.stdout}\n  stderr: {result.stderr}"
    )
