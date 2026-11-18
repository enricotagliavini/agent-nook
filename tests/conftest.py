"""Fixtures for pipx installation tests and XDG configuration setup."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def built_wheel() -> Path:
    """Build the wheel from the current source and return its path.

    Session-scoped: built ONCE per pytest run, unconditionally. Stale
    wheels in dist/ are removed first so integration tests never run
    against an outdated build.
    """
    dist_dir = Path(__file__).parent.parent / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    for stale_wheel in dist_dir.glob("*.whl"):
        stale_wheel.unlink()

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


@pytest.fixture(scope="session", autouse=True)
def xdg_config_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return the XDG config directory path for tests.

    This fixture is autouse so that every test — including each
    pytest-xdist worker — gets a writable XDG state under /tmp. The
    application's own ``log_file_handle`` has no ``/tmp`` fallback, so a
    run mode that leaves XDG pointing at the read-only ``~/.local`` tree
    (a subset run, an xdist unit run) would fail with ``OSError`` when an
    orchestrator or sandbox redirects child output to the log. Setting the
    environment once per process is what makes isolated/parallel runs safe;
    integration tests already request this fixture explicitly, so their
    behavior is unchanged.

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
) -> subprocess.CompletedProcess:
    """Session-scoped runner that installs agent-nook once for all tests.

    Uses a shared isolated pipx environment with the wheel that
    built_wheel just rebuilt from the current source, so the tests
    always run against the most recent code.
    """
    pipx_home, pipx_bin, env = pipx_test_env

    # Verify directories are empty before install
    assert not list(pipx_home.glob("venvs")), f"pipx home already contains venvs: {list(pipx_home.glob('venvs'))}"
    assert not list(pipx_bin.glob("*")), f"pipx bin already contains files: {list(pipx_bin.glob('*'))}"

    # Run pipx install once for the entire session
    result = subprocess.run(
        [sys.executable, "-m", "pipx", "install", "--force", str(built_wheel)],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,  # 5 minutes for install
    )

    # Should succeed (return code 0)
    assert result.returncode == 0, f"pipx install failed:\n  stdout: {result.stdout}\n  stderr: {result.stderr}"

    print(f"INFO: Installed agent-nook from wheel: {built_wheel}")

    def run_command(*args: str, **kwargs) -> subprocess.CompletedProcess:
        """Run the installed agent-nook and capture its output.

        Output is redirected to temporary files instead of pipes. With
        pipes, waiting for the output would block until every write end
        is closed — including ones inherited by sandboxed grandchild
        processes that outlive agent-nook (bwrap's --die-with-parent
        does not reliably cover the full process tree on all
        bwrap/distro combinations). With files, only the child's exit is
        awaited; agent-nook flushes stdout/stderr before exiting, so the
        files are complete once the child has been reaped.

        Args:
            *args: Arguments passed to the agent-nook executable.
            **kwargs: ``timeout`` (seconds) bounds how long agent-nook
                itself may run; any other keyword is forwarded to
                ``subprocess.Popen``.

        Returns:
            CompletedProcess with the command, return code, and decoded
            stdout/stderr.

        Raises:
            subprocess.TimeoutExpired: If agent-nook does not exit within
                ``timeout`` seconds. The child is killed before raising.
        """
        timeout = kwargs.pop("timeout", None)
        cmd = ["agent-nook", *args]

        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                cmd,
                stdout=stdout_file,
                stderr=stderr_file,
                env=env,
                **kwargs,
            )
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise

            stdout_file.seek(0)
            stdout_text = stdout_file.read().decode("utf-8", "replace")
            stderr_file.seek(0)
            stderr_text = stderr_file.read().decode("utf-8", "replace")

        return subprocess.CompletedProcess(cmd, process.returncode, stdout_text, stderr_text)

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


# Fixtures that trigger heavy, session-wide setup (a wheel build in the
# shared dist/ and a pipx install). run_tests.sh runs the non-integration
# group in parallel with pytest-xdist, where each worker rebuilds session
# fixtures independently. A test that is not marked integration but pulls
# in one of these would silently be dispatched to the parallel phase and
# rebuilt once per worker (wasted work plus a shared dist/ race). Guard
# against that at collection so the routing invariant stays correct.
_SESSION_SCOPED_INTEGRATION_FIXTURES = frozenset(
    {"built_wheel", "pipx_test_env", "test_runner"}
)


def pytest_collection_modifyitems(config, items):
    offenders_by_item: dict[str, list[str]] = {}
    for item in items:
        if item.get_closest_marker("integration") is not None:
            continue
        offenders = _SESSION_SCOPED_INTEGRATION_FIXTURES & set(item.fixturenames)
        if offenders:
            offenders_by_item[item.nodeid] = sorted(offenders)

    if offenders_by_item:
        details = "\n".join(f"  - {nid}: {fixs}" for nid, fixs in offenders_by_item.items())
        raise pytest.UsageError(
            "Non-integration tests pulled into the parallel unit phase depend "
            "on session-scoped integration fixtures:\n"
            f"{details}\n"
            "Mark each of these 'integration' so they run serial alongside the "
            "bwrap/pipx tests (a per-xdist-worker rebuild races on dist/)."
        )
