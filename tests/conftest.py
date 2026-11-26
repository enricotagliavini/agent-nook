"""Fixtures for pipx installation tests and XDG configuration setup."""

import contextlib
import fcntl
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent

# Where the wheel + pipx install are shared across all pytest-xdist workers.
# ``run_tests.sh`` creates a fresh mktemp tree and exports AGENT_NOOK_SHARED_BUILD
# (removed on exit); a direct ``pytest`` invocation falls back to a deterministic
# per-checkout dir under the OS temp dir. The fallback must be deterministic so
# every xdist worker computes the same path without an env var, and per-checkout
# (hashed from the repo path) so separate working copies don't collide. The whole
# tree is throwaway -- pipx/wheel work never touches the real ``~/.local``.
# ``hashlib`` is used, not the builtin ``hash`` (which is randomized per-process
# and would not agree across workers).
_repo_digest = hashlib.md5(str(REPO).encode("utf-8")).hexdigest()[:12]
_SHARED_DIR = Path(os.environ.get("AGENT_NOOK_SHARED_BUILD") or (Path(tempfile.gettempdir()) / f"agent-nook-tests-{_repo_digest}"))


@contextlib.contextmanager
def _exclusive_lock(lock_path: Path):
    """Hold a cross-process exclusive lock.

    pytest-xdist workers are separate OS processes, so an in-process lock
    cannot coordinate them; ``fcntl.flock`` is the POSIX cross-process
    primitive. The lock file lives inside the shared dir, so it is created
    and cleaned up with the rest of it.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # The handle must stay open across the yield (the lock lives on the fd),
    # so it cannot be wrapped in a `with` block.
    handle = open(lock_path, "a+", encoding="utf-8")  # noqa: SIM115
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _pipx_env(pipx_home: Path, pipx_bin: Path) -> dict:
    """Environment pointing every pipx dir at the shared (throwaway) tree."""
    env = os.environ.copy()
    env.update(
        {
            "PIPX_HOME": str(pipx_home),
            "PIPX_BIN_DIR": str(pipx_bin),
            "PIPX_MAN_DIR": str(_SHARED_DIR / "pipx-man"),
            "PIPX_CACHE_DIR": str(_SHARED_DIR / "pipx-cache"),
        }
    )
    env["PATH"] = str(pipx_bin) + os.pathsep + env.get("PATH", "")
    return env


def _build_wheel_into_shared(wheel_dir: Path) -> None:
    """Build the wheel from source and copy it into the per-run shared dir.

    hatch always outputs to the repo ``dist/``; the wheel is copied into the
    throwaway shared tree so it is fresh each run and never races a
    concurrent session's ``dist/``.
    """
    dist_dir = REPO / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    for stale_wheel in dist_dir.glob("*.whl"):
        stale_wheel.unlink()
    subprocess.run(
        [sys.executable, "-m", "hatch", "build", "-t", "wheel"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    wheels = sorted(dist_dir.glob("*.whl"))
    if not wheels:
        raise AssertionError("hatch build produced no wheel in dist/")
    wheel_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(wheels[-1], wheel_dir / wheels[-1].name)


def _install_pipx_into_shared(wheel_dir: Path) -> None:
    """Install the shared wheel via pipx into the shared tree (run once)."""
    pipx_home = _SHARED_DIR / "pipx"
    pipx_bin = _SHARED_DIR / "pipx-bin"
    pipx_bin.mkdir(parents=True, exist_ok=True)
    wheels = sorted(wheel_dir.glob("*.whl"))
    if not wheels:
        raise AssertionError("no wheel available for pipx install")
    result = subprocess.run(
        [sys.executable, "-m", "pipx", "install", "--force", str(wheels[0])],
        env=_pipx_env(pipx_home, pipx_bin),
        capture_output=True,
        text=True,
        timeout=300,  # generous bound for the install itself
        check=False,  # non-zero rc is handled below with captured output
    )
    if result.returncode != 0:
        raise AssertionError(f"pipx install failed:\n  stdout: {result.stdout}\n  stderr: {result.stderr}")


def _ensure_shared_artifacts() -> None:
    """Build the wheel and install pipx ONCE per run, guarded by a lock.

    pytest-xdist re-instantiates session fixtures once per worker, so without
    a cross-process guard every worker would rebuild the wheel and reinstall
    pipx into the same shared tree (a race). The first worker to hold the lock
    does the work; the rest block, then find the artifacts already present and
    skip, reusing them read-only. The wheel build and pipx install share one
    critical section so no worker can interleave a second install.
    """
    _SHARED_DIR.mkdir(parents=True, exist_ok=True)
    wheel_dir = _SHARED_DIR / "wheel"
    with _exclusive_lock(_SHARED_DIR / "setup.lock"):
        if not any(wheel_dir.glob("*.whl")):
            _build_wheel_into_shared(wheel_dir)
        if not (_SHARED_DIR / "pipx" / "venvs").exists():
            _install_pipx_into_shared(wheel_dir)


@pytest.fixture(scope="session")
def built_wheel() -> Path:
    """Return the wheel, building it once (lock-guarded) and sharing it.

    Every pytest-xdist worker sees the same wheel: the first builds it into
    the shared dir, the rest reuse it.
    """
    _ensure_shared_artifacts()
    wheels = sorted(_SHARED_DIR.glob("wheel/*.whl"))
    if not wheels:
        raise AssertionError("built_wheel fixture: no wheel was built")
    return wheels[0]


@pytest.fixture(scope="session")
def pipx_test_env() -> tuple[Path, Path, dict]:
    """Shared pipx environment, installed once (lock-guarded) in the shared dir.

    Returns ``(pipx_home, pipx_bin, env)`` for every integration test in this
    process. The venv and binary are shared read-only across all workers —
    tests only execute the installed binary, never mutate the install.
    """
    _ensure_shared_artifacts()
    pipx_home = _SHARED_DIR / "pipx"
    pipx_bin = _SHARED_DIR / "pipx-bin"
    return pipx_home, pipx_bin, _pipx_env(pipx_home, pipx_bin)


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

    ``XDG_RUNTIME_DIR`` is given its own per-worker dir too: it is the base
    directory the orchestrator generates ``track-socket`` names into. Without
    a per-worker isolation, parallel runs would share one socket base and the
    e2e cleanup check (glob for leftover sockets) would see other workers'
    sockets. With it, each worker only ever sees its own.

    The directory is created at: /tmp/pytest-of-<user>/pytest-<id>/xdg_config/config

    This fixture is called automatically at session start if XDG environment
    variables are not already set, and copies the bundled config to the
    test config directory.
    """
    # Use tmp_path_factory which creates directories under /tmp
    temp_config_dir = tmp_path_factory.mktemp("xdg_config") / "config"
    temp_state_dir = tmp_path_factory.mktemp("xdg_state") / "state"
    temp_runtime_dir = tmp_path_factory.mktemp("xdg_runtime")
    # Create the agent-nook subdirectory for config (XDG-compliant)
    agent_nook_config_dir = temp_config_dir / "agent-nook"
    temp_config_dir.mkdir(parents=True, exist_ok=True)
    temp_state_dir.mkdir(parents=True, exist_ok=True)
    temp_runtime_dir.mkdir(parents=True, exist_ok=True)
    agent_nook_config_dir.mkdir(parents=True, exist_ok=True)

    # Copy bundled config to user config dir (simulating _auto_init)
    bundled_config = REPO / "src" / "agent_nook" / "config" / "sandbox.yaml"
    user_config = agent_nook_config_dir / "sandbox.yaml"
    if bundled_config.exists():
        shutil.copy2(bundled_config, user_config)

    # Set XDG environment variables for the entire test session
    os.environ["XDG_CONFIG_HOME"] = str(temp_config_dir)
    os.environ["XDG_STATE_HOME"] = str(temp_state_dir)
    os.environ["XDG_RUNTIME_DIR"] = str(temp_runtime_dir)

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
    pipx_test_env: tuple[Path, Path, dict],
    built_wheel: Path,
) -> subprocess.CompletedProcess:
    """Session-scoped runner that reuses the once-installed agent-nook.

    The pipx install happens in ``_ensure_shared_artifacts`` (shared,
    lock-guarded, built from the wheel built_wheel exposes). This fixture
    just verifies the binary is present and returns a callable that runs it
    in the shared environment, so all integration tests run against the
    same, current build.
    """
    pipx_home, _pipx_bin, env = pipx_test_env

    # The install already happened (shared); confirm the binary is present.
    venv_bin = pipx_home / "venvs" / "agent-nook" / "bin"
    assert (venv_bin / "agent-nook").exists(), f"agent-nook not installed in {pipx_home}"

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


@pytest.fixture(autouse=True)
def _reset_logger_to_session_xdg():
    """Point the ``agent_nook`` logger at the session XDG state dir before each
    test, so its resolved log path (cached by main_logger) matches where the
    subprocess CLI writes and stays isolated under the test's /tmp state dir.

    The orchestrator imports ``main_logger`` at collection time — before this
    fixture runs and before the session XDG env vars are set — so its handler
    then points at the unset-XDG default (~/.local/state). Reconfiguring here
    (before every test) keeps child output isolated and makes
    get_log_file_path()/get_log_directory() agree with the subprocess. Tests
    that need a different path (their own tmp_path) reconfigure the logger
    during the test body.
    """
    import logging

    from agent_nook.utils.logger import main_logger

    agent_logger = logging.getLogger("agent_nook")
    for handler in list(agent_logger.handlers):
        agent_logger.removeHandler(handler)
        handler.close()
    main_logger("agent_nook")
    yield


# Fixtures that trigger heavy, session-wide setup (a wheel build and a pipx
# install, both shared across xdist workers). run_tests.sh runs the
# non-integration group in parallel with pytest-xdist, where each worker
# rebuilds session fixtures independently. A test that is not marked
# integration but pulls in one of these would silently be dispatched to the
# parallel phase and trigger a per-worker rebuild into the shared tree. Guard
# against that at collection so the routing invariant stays correct.
_SESSION_SCOPED_INTEGRATION_FIXTURES = frozenset({"built_wheel", "pipx_test_env", "test_runner"})


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
            "Mark each of these 'integration' so they run alongside the "
            "bwrap/pipx tests (they share one lock-guarded wheel build + pipx "
            "install in the shared dir)."
        )
