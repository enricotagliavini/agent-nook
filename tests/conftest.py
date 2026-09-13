"""Fixtures for pipx installation tests."""

import os
import sys
import subprocess
import pytest
from pathlib import Path


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """Clean dist/ directory at session start to ensure fresh builds.

    This ensures that tests always run against the latest source code.
    """
    dist_dir = Path(__file__).parent.parent / "dist"
    if dist_dir.exists():
        # Clean all dist artifacts
        for item in dist_dir.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()


import shutil


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
    if the dist/ directory is cleared by pytest_configure.
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
def test_runner(
    pipx_test_env: tuple[Path, Path, list[str]],
    built_sdist: Path,
) -> subprocess.CompletedProcess:
    """Session-scoped runner that installs agent-nook once for all tests.

    Uses a shared isolated pipx environment but with the freshly-built
    sdist from built_sdist. This avoids redundant pipx installs across
    all integration tests.
    """
    pipx_home, pipx_bin, env = pipx_test_env

    # Verify directories are empty before install
    assert not list(pipx_home.glob("venvs")), (
        f"pipx home already contains venvs: {list(pipx_home.glob('venvs'))}"
    )
    assert not list(pipx_bin.glob("*")), (
        f"pipx bin already contains files: {list(pipx_bin.glob('*'))}"
    )

    # Run pipx install once for the entire session
    result = subprocess.run(
        [sys.executable, "-m", "pipx", "install", "--force", str(built_sdist)],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,  # 5 minutes for download/install
    )

    # Should succeed (return code 0)
    assert result.returncode == 0, (
        f"pipx install failed:\n  stdout: {result.stdout}\n  stderr: {result.stderr}"
    )

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
