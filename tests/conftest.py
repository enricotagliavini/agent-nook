"""Fixtures for pipx installation tests."""

import os
import sys
import shutil
import subprocess
import pytest
from pathlib import Path


@pytest.fixture(scope="session")
def built_wheel() -> Path:
    """Build and return the path to the wheel file.
    
    This is a session-scoped fixture so it's only built once across all tests.
    """
    dist_dir = Path(__file__).parent.parent / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    # Build wheel if not present
    wheel_path = list(dist_dir.glob("*.whl"))
    if not wheel_path:
        subprocess.run(
            [sys.executable, "-m", "hatch", "build", "--wheel"],
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
    
    This is a session-scoped fixture so it's only built once across all tests.
    """
    dist_dir = Path(__file__).parent.parent / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    # Build sdist if not present
    sdist_path = list(dist_dir.glob("*.tar.gz"))
    if not sdist_path:
        subprocess.run(
            [sys.executable, "-m", "hatch", "build", "--sdist"],
            cwd=Path(__file__).parent.parent,
            capture_output=True,
            text=True,
            check=True,
        )
        sdist_path = list(dist_dir.glob("*.tar.gz"))

    return sdist_path[0]


@pytest.fixture(scope="function")
def pipx_test_env(tmp_path: Path, built_sdist: Path) -> tuple[Path, Path, list[str]]:
    """Provide a clean pipx test environment for each test.
    
    Uses PIPX_HOME to override the default ~/.local/share/pipx/venvs location,
    ensuring the test is truly isolated and doesn't interfere with the host
    pipx installation.

    The installed venv will be at: /tmp/test-pipx-home/venvs/agent-nook/
    The symlinked binary will be at: /tmp/test-pipx-home/bin/agent-nook

    The sdist is passed as a parameter so it's available to each test.
    """
    pipx_home = tmp_path / "pipx"
    pipx_bin = tmp_path / "bin"

    pipx_home.mkdir(parents=True, exist_ok=True)
    pipx_bin.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PIPX_HOME"] = str(pipx_home)
    env["PIPX_BIN_DIR"] = str(pipx_bin)
    # PATH must include our bin dir so the symlinked executable is found
    env["PATH"] = str(pipx_bin) + os.pathsep + env.get("PATH", "")

    return pipx_home, pipx_bin, env


@pytest.fixture(scope="function")
def test_sandbox_run(
    tmp_path: Path,
    pipx_test_env: tuple[Path, Path, list[str]],
    built_sdist: Path,
) -> subprocess.CompletedProcess:
    """Install agent-nook via pipx and return a runner subprocess for tests."""
    import os
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
            "--force",  # force reinstall over existing
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,  # 5 minutes for download/install
    )

    # Should succeed (return code 0)
    assert result.returncode == 0, (
        f"pipx install failed:\n  stdout: {result.stdout}\n  stderr: {result.stderr}"
    )

    # Verify the venv was created in our isolated directory
    assert pipx_home.exists(), f"pipx home not created at {pipx_home}"

    venv_dirs = list(pipx_home.glob("venvs/agent-nook-*"))
    assert len(venvs) >= 1, (
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

    # Return the runner subprocess that can be reused
    def run_command(*args: str, **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["agent-nook"] + list(args),
            capture_output=True,
            text=True,
            env=env,
            **kwargs,
        )

    return run_command
