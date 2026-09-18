"""Fixtures for pipx installation tests and XDG configuration setup."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """Build wheel if not present, but don't clear dist/ directory.

    Tests should be run after building the wheel from source to ensure
    they test the latest code. Clearing dist/ would cause tests to rebuild
    from an outdated sdist.
    """
    pass




@pytest.fixture(scope="session")
def built_wheel() -> Path:
    """Build and return the path to the wheel file.

    Session-scoped: builds ONCE per pytest run. Rebuilds automatically
    if the dist/ directory is cleared by pytest_configure.
    """
    dist_dir = Path(__file__).parent.parent / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    # Build wheel if not present
    wheel_path = list(dist_dir.glob("*.whl"))
    if not wheel_path:
        subprocess.run(
            [sys.executable, "-m", "hatch", "build", "-t", "wheel"],
            cwd=Path(__file__).parent.parent,
            capture_output=True,
            text=True,
            check=True,
        )
        wheel_path = list(dist_dir.glob("*.whl"))

    return wheel_path[0]


@pytest.fixture(scope="session")
def built_sdist() -> Path:
    """Build and return the path to the sdist file.

    Session-scoped: builds ONCE per pytest run. Rebuilds automatically
    if the dist/ directory is cleared.
    
    Note: This fixture is only used as a fallback if no wheel exists.
    Tests should prefer the built_wheel fixture to ensure they test
    the latest code.
    """
    dist_dir = Path(__file__).parent.parent / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    # Build sdist if not present
    sdist_path = list(dist_dir.glob("*.tar.gz"))
    if not sdist_path:
        subprocess.run(
            [sys.executable, "-m", "hatch", "build", "-t", "sdist", "-c"],
            cwd=Path(__file__).parent.parent,
            capture_output=True,
            text=True,
            check=True,
        )
        sdist_path = list(dist_dir.glob("*.tar.gz"))

    return sdist_path[0]


@pytest.fixture(scope="session")
def pipx_test_env(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, list[str]]:
    """Provide a shared pipx test environment for the entire session.

    Uses PIPX_HOME to override the default ~/.local/share/pipx/venvs location,
    ensuring the session-wide pipx installation is isolated from the host.

    The installed venv will be at: {tmpdir}/pipx/venvs/agent-nook/
    The symlinked binary will be at: {tmpdir}/pipx/bin/agent-nook

    This fixture is session-scoped to share the pipx environment across
    all integration tests, avoiding redundant pipx installs.
    """
    pipx_home = tmp_path_factory.mktemp("pipx")
    pipx_bin = pipx_home / "bin"

    pipx_home.mkdir(parents=True, exist_ok=True)
    pipx_bin.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PIPX_HOME"] = str(pipx_home)
    env["PIPX_BIN_DIR"] = str(pipx_bin)
    env["PATH"] = str(pipx_bin) + os.pathsep + env.get("PATH", "")

    return pipx_home, pipx_bin, env


@pytest.fixture(scope="session")
def xdg_config_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return the XDG config directory path for tests.

    This fixture creates temporary XDG directories under /tmp to avoid
    polluting the git repository with test artifacts.

    The directory is created at: /tmp/pytest-of-<user>/pytest-<id>/xdg_config/config

    This fixture is called automatically at session start if XDG environment
    variables are not already set, and copies the bundled config to the
    test config directory.
    """
    # Use tmp_path_factory which creates directories under /tmp
    temp_config_dir = tmp_path_factory.mktemp("xdg_config") / "config"
    temp_state_dir = tmp_path_factory.mktemp("xdg_state") / "state"
    # Create the agent-nook subdirectory for config (XDG-compliant)
    agent_nook_config_dir = temp_config_dir / "agent-nook"
    temp_config_dir.mkdir(parents=True, exist_ok=True)
    temp_state_dir.mkdir(parents=True, exist_ok=True)
    agent_nook_config_dir.mkdir(parents=True, exist_ok=True)

    # Copy bundled config to user config dir (simulating _auto_init)
    bundled_config = Path(__file__).parent.parent / "src" / "agent_nook" / "config" / "sandbox.yaml"
    user_config = agent_nook_config_dir / "sandbox.yaml"
    if bundled_config.exists():
        shutil.copy2(bundled_config, user_config)

    # Set XDG environment variables for the entire test session
    os.environ["XDG_CONFIG_HOME"] = str(temp_config_dir)
    os.environ["XDG_STATE_HOME"] = str(temp_state_dir)

    return agent_nook_config_dir


@pytest.fixture(scope="session")
def xdg_state_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return the XDG state directory path for tests.

    This fixture is session-scoped and is set up at function call time to
    ensure XDG directories are created under /tmp (not the git repo root).

    The directory is created at: /tmp/pytest-of-<user>/pytest-<id>/xdg_state/state

    Note: If XDG environment variables are already set, this fixture does
    nothing and returns the existing value.
    """
    if "XDG_STATE_HOME" in os.environ:
        return Path(os.environ["XDG_STATE_HOME"])

    # This should be called from xdg_config_dir, but handle edge case
    temp_state_dir = tmp_path_factory.mktemp("xdg_state") / "state"
    temp_state_dir.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_STATE_HOME"] = str(temp_state_dir)
    return temp_state_dir


@pytest.fixture(scope="session")
def test_runner(
    xdg_config_dir: Path,
    pipx_test_env: tuple[Path, Path, list[str]],
    built_wheel: Path,
    built_sdist: Path,
) -> subprocess.CompletedProcess:
    """Session-scoped runner that installs agent-nook once for all tests.

    Uses a shared isolated pipx environment but with the freshly-built
    wheel from built_wheel. Falls back to sdist only if wheel is not found.
    
    Note: We use the wheel instead of sdist because the wheel is rebuilt
    from the latest source code before testing, ensuring tests run against
    the most recent changes.
    """
    pipx_home, pipx_bin, env = pipx_test_env

    # Verify directories are empty before install
    assert not list(pipx_home.glob("venvs")), (
        f"pipx home already contains venvs: {list(pipx_home.glob('venvs'))}"
    )
    assert not list(pipx_bin.glob("*")), (
        f"pipx bin already contains files: {list(pipx_bin.glob('*'))}"
    )

    # Use wheel if available, otherwise fall back to sdist
    package_to_install = built_wheel if built_wheel.exists() else built_sdist
    
    # Run pipx install once for the entire session
    result = subprocess.run(
        [sys.executable, "-m", "pipx", "install", "--force", str(package_to_install)],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,  # 5 minutes for install
    )

    # Should succeed (return code 0)
    assert result.returncode == 0, (
        f"pipx install failed:\n  stdout: {result.stdout}\n  stderr: {result.stderr}"
    )
    
    # Print the package that was installed for debugging
    if package_to_install == built_wheel:
        print(f"INFO: Installed agent-nook from wheel: {built_wheel}")
    else:
        print(f"INFO: Installed agent-nook from sdist: {built_sdist}")

    def run_command(*args: str, **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["agent-nook"] + list(args),
            capture_output=True,
            text=True,
            env=env,
            **kwargs,
        )

    return run_command


@pytest.fixture(scope="function")
def test_sandbox_run(
    test_runner: subprocess.CompletedProcess,
) -> subprocess.CompletedProcess:
    """Delegate to the session-scoped runner, returning a fresh callable.

    Each test gets its own isolated environment (via tmp_path) and a fresh
    runner function, while sharing the pre-installed agent-nook binary.
    """
    return test_runner
