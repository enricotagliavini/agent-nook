# Testing Agent Nook

This document describes the test structure, fixtures, and setup for Agent Nook.

## Test Organization

```
tests/
├── conftest.py                  # Session-wide fixtures and XDG setup
├── integration/                 # Integration tests
│   ├── test_auto_init.py       # Tests for auto-initialization
│   └── test_cli.py             # CLI command tests
├── config/                      # Unit tests for config module
│   ├── test_config.py          # Config building tests
│   ├── test_env_var_expansion.py # Environment variable expansion
│   └── test_loader.py          # Config loader tests
├── sandbox/                     # Unit tests for sandbox module
│   ├── test_bwrap_sandbox.py   # BwrapSandbox build/run tests
│   └── test_builder.py         # Bubblewrap builder tests
└── README.md                   # This file
```

## Test Conventions

The conventions for writing and maintaining tests — document purpose in
two layers, test our own code rather than third-party tools, keep the
suite lean — live in [`AGENTS.md`](../AGENTS.md) under "Testing
Conventions". That section is authoritative for both humans and AI
agents; consult it rather than duplicating the rules here. This README
covers structure, fixtures, and running tests.

## XDG Configuration Setup

### Overview

All tests respect the [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html):

- **Config**: `~/.config/agent-nook/sandbox.yaml`
- **State/Logs**: `~/.local/state/agent-nook/`

### Automatic Setup

Test fixtures create temporary XDG directories under `/tmp` to avoid polluting the git repository:

- **Config directory**: `/tmp/pytest-of-<user>/pytest-<id>/xdg_config/config/agent-nook/`
- **State directory**: `/tmp/pytest-of-<user>/pytest-<id>/xdg_state/state/`
- **Runtime directory**: `/tmp/pytest-of-<user>/pytest-<id>/xdg_runtime/` (per worker)

The autouse, session-scoped fixture in `tests/conftest.py` sets all three for
every test and every pytest-xdist worker:

```python
@pytest.fixture(scope="session", autouse=True)
def xdg_config_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Creates temporary XDG config/state/runtime directories under /tmp."""
    temp_config_dir = tmp_path_factory.mktemp("xdg_config") / "config"
    temp_state_dir = tmp_path_factory.mktemp("xdg_state") / "state"
    temp_runtime_dir = tmp_path_factory.mktemp("xdg_runtime")
    # Create agent-nook subdirectory and copy the bundled config
    ...
    # Set XDG environment variables for the entire test session
    os.environ["XDG_CONFIG_HOME"] = str(temp_config_dir)
    os.environ["XDG_STATE_HOME"] = str(temp_state_dir)
    os.environ["XDG_RUNTIME_DIR"] = str(temp_runtime_dir)
    return agent_nook_config_dir
```

The per-worker `XDG_RUNTIME_DIR` matters for the orchestration tests: it is the
base directory `track-socket` names are generated into, so giving each xdist
worker its own keeps parallel runs from seeing each other's sockets.

This ensures:
1. No pollution of the git repository with test artifacts
2. Isolated test environments automatically cleaned up after tests
3. Consistent XDG-compliant paths during testing
4. Session-wide availability of config for all integration tests

## Shared Build & Install Directory

Integration tests build the wheel and install it via pipx **once per run** and
share the result read-only across every pytest-xdist worker. The whole tree is
throwaway and never touches the real `~/.local`:

- **Primary**: `run_tests.sh` creates a fresh `mktemp -d` tree under
  `$TMPDIR` (default `/tmp`) and exports it as `AGENT_NOOK_SHARED_BUILD`, then
  removes it on exit (`trap ... EXIT`). Fresh each run, so the wheel is always
  rebuilt from the current source.
- **Fallback**: a direct `pytest` invocation (no `run_tests.sh`) uses a
  deterministic per-checkout dir under the OS temp dir
  (`$TMPDIR`/`agent-nook-tests-<hash of the repo path>`). It is deterministic so
  every xdist worker computes the same path without an env var, and per-checkout
  so separate working copies don't collide. Because it persists across bare
  `pytest` runs, a second run reuses the previously built wheel; use
  `run_tests.sh` (or delete the dir) to force a rebuild.

Both paths are resolved by `_SHARED_DIR` in `tests/conftest.py`. The shared tree
holds the built wheel, the pipx venv, the `pipx-bin` shims, and the pipx
cache/man dirs. Because pipx and hatch work only inside this tree (all `PIPX_*`
variables and the build output are redirected there), the suite is fully
isolated from the host and from parallel workers.

### Lock-Guarded, Built Once

pytest-xdist re-instantiates session fixtures **once per worker**, so without a
guard every worker would rebuild the wheel and reinstall pipx into the same
shared tree (a race). `_ensure_shared_artifacts()` holds a cross-process
exclusive lock (`fcntl.flock` — workers are separate OS processes, so an
in-process lock cannot coordinate them). The first worker to hold the lock does
the build + install; the rest block, then find the artifacts already present and
skip. The wheel build and the pipx install share one critical section so no
worker can interleave a second install.

### Pipx Test Environment

The `pipx_test_env` fixture returns a fully-redirected pipx environment for the
shared tree:

```python
@pytest.fixture(scope="session")
def pipx_test_env() -> tuple[Path, Path, dict]:
    """Shared pipx environment, installed once (lock-guarded) in the shared dir."""
    _ensure_shared_artifacts()
    pipx_home = _SHARED_DIR / "pipx"
    pipx_bin = _SHARED_DIR / "pipx-bin"
    return pipx_home, pipx_bin, _pipx_env(pipx_home, pipx_bin)
```

`_pipx_env` points `PIPX_HOME`, `PIPX_BIN_DIR`, `PIPX_MAN_DIR` and
`PIPX_CACHE_DIR` at the shared tree and prepends `pipx_bin` to `PATH`. The venv
and binary are shared read-only across all workers — tests only execute the
installed binary, never mutate the install.

### Wheel Building

The `built_wheel` fixture returns the wheel that `_ensure_shared_artifacts()`
built once into the shared tree. The build itself happens in
`_build_wheel_into_shared()`: hatch outputs to the repo `dist/`, and the wheel is
copied into the shared tree so it is fresh each run and never races a concurrent
session's `dist/`:

```python
@pytest.fixture(scope="session")
def built_wheel() -> Path:
    """Return the wheel, building it once (lock-guarded) and sharing it."""
    _ensure_shared_artifacts()
    wheels = sorted(_SHARED_DIR.glob("wheel/*.whl"))
    if not wheels:
        raise AssertionError("built_wheel fixture: no wheel was built")
    return wheels[0]
```

### Test Runner Fixture

The `test_runner` fixture does **not** install anything — the install already
happened in the shared, lock-guarded setup. It just verifies the installed
binary is present and returns a callable that runs it in the shared environment,
so all integration tests run against the same, current build:

```python
@pytest.fixture(scope="session")
def test_runner(
    xdg_config_dir: Path,
    pipx_test_env: tuple[Path, Path, dict],
    built_wheel: Path,
) -> subprocess.CompletedProcess:
    """Session-scoped runner that reuses the once-installed agent-nook."""
    pipx_home, pipx_bin, env = pipx_test_env
    venv_bin = pipx_home / "venvs" / "agent-nook" / "bin"
    assert (venv_bin / "agent-nook").exists(), f"agent-nook not installed in {pipx_home}"
    # Returns a callable (run_command) that runs `agent-nook` in the shared env
    return run_command
```

## Running Tests

### Full Test Suite

```bash
python -m pytest tests/ -v
```

### Specific Test Categories

```bash
# Unit tests for config module
python -m pytest tests/config/ -v

# Integration tests
python -m pytest tests/integration/ -v

# CLI tests
python -m pytest tests/integration/test_cli.py -v
```

### Specific Test File

```bash
python -m pytest tests/integration/test_auto_init.py -v
```

### The `run_tests.sh` entrypoint

`./run_tests.sh` is the recommended way to run everything. It creates a fresh
throwaway **shared build/install tree** (`AGENT_NOOK_SHARED_BUILD`, a `mktemp -d`
dir removed on exit), then splits the suite into two phases:

1. **Unit group** (`-m "not integration" -n auto`) — runs the ~260 isolated
   unit tests in parallel with pytest-xdist. These tests use `tmp_path` /
   `monkeypatch`, pid-based socket names, ephemeral ports and a per-process
   logger, so they are safe under xdist.
2. **Integration group** (`-m integration -n auto`) — also parallel. The
   bwrap-heavy integration tests are parallel-safe: each test's writable dirs
   (`tmp_path`) and its `XDG_RUNTIME_DIR` are independent, and the wheel + pipx
   env are built and installed **exactly once** (lock-guarded, see [Shared Build
   & Install Directory](#shared-build--install-directory)) in
   `$AGENT_NOOK_SHARED_BUILD`, then shared read-only by every worker.

Every test and xdist worker gets a writable XDG state via the autouse
`xdg_config_dir` session fixture, which also gives each worker its own
`XDG_RUNTIME_DIR`. That per-worker runtime dir is the base directory
orchestration `track-socket`s are generated into, so parallel runs never see
each other's sockets (the e2e leftover-socket check stays clean).

A `pytest_collection_modifyitems` guard rejects any test that is **not** marked
`integration` but still depends on a session-scoped integration fixture
(`built_wheel`, `pipx_test_env`, `test_runner`); such a test must be marked
`integration` so it runs in the integration phase alongside the bwrap/pipx tests.

## Test Isolation

### Session Scope

Fixtures marked `scope="session"` are created once per test run and shared:

- `xdg_config_dir`: Shared XDG config directory (autouse — sets config, state
  and per-worker `XDG_RUNTIME_DIR` once per process)
- `xdg_state_dir`: Shared XDG state directory
- `pipx_test_env`: Shared pipx environment (installed once in the shared tree)
- `built_wheel`: Wheel built once into the shared tree
- `test_runner`: Callable running the once-installed agent-nook binary

This speeds up test execution by avoiding redundant setup.

### Function Scope

Fixtures marked `scope="function"` are fresh for each test:

- `test_sandbox_run`: Returns a fresh runner callable for each test
- Standard `tmp_path` fixture: Fresh temp directory for each test

## Available Fixtures

### Session Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `xdg_config_dir` | session | Autouse; temp config/state + per-worker runtime dirs under /tmp |
| `xdg_state_dir` | session | Returns temp state directory under /tmp |
| `pipx_test_env` | session | Shared pipx env, installed once in the shared tree (Path, Path, dict) |
| `built_wheel` | session | Wheel built once (lock-guarded) into the shared tree |
| `test_runner` | session | Callable running the once-installed agent-nook binary |

### Function Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `test_sandbox_run` | function | Test runner with shared installation |
| `tmp_path` | function | pytest's default temp directory |

## Writing New Tests

### Adding an Integration Test

```python
# tests/integration/test_something.py

import pytest
from pathlib import Path


def test_something(test_runner: subprocess.CompletedProcess) -> None:
    """Run a command using the pre-installed agent-nook."""
    result = test_runner("run", "python3", "-c", "print('hello')")
    assert result.returncode == 0
```

### Adding a Unit Test

```python
# tests/config/test_something.py

import pytest


def test_config_something(tmp_path: Path) -> None:
    """Test that uses isolated temp directory."""
    config_file = tmp_path / "sandbox.yaml"
    config_file.write_text("name: test-sandbox\n")
    # ... test logic ...
```

## Wheel Freshness

Under `run_tests.sh` the shared build tree is a fresh `mktemp -d` dir each run
(removed on exit), so the wheel is always rebuilt from the current source. A
direct `pytest` run reuses the persistent per-checkout `$TMPDIR` fallback dir
instead. Inside whichever tree, `_ensure_shared_artifacts()` builds the wheel
from the current source once — clearing any stale `dist/` wheels first — and
installs it via pipx once, then shares the wheel read-only by every xdist
worker: exactly one build and one install, no manual cleaning of `dist/` needed.
For a guaranteed-fresh wheel on a bare `pytest` run, delete the fallback dir
first.

## Best Practices

1. **Use `tmp_path` for temporary files**: pytest provides this automatically
2. **Prefer session fixtures** for expensive setup (e.g., building wheels)
3. **Use custom fixtures** when you need custom XDG paths
4. **Test isolation**: Ensure tests don't rely on side effects
5. **Run full test suite**: `python -m pytest tests/ -v`
6. **Lint checks**: `ruff check src/` and `ruff format src/ --check`

## Troubleshooting

### Tests Fail Due to Missing Config

If tests fail because the config file doesn't exist:
- Ensure `tests/conftest.py` sets up XDG directories
- Check that `bundled_config` exists and is copied
- Run `pytest -v` to see detailed error messages

### Tests Conflict with User Environment

The autouse `xdg_config_dir` fixture sets `XDG_CONFIG_HOME`, `XDG_STATE_HOME`
and `XDG_RUNTIME_DIR` for the whole process, overriding any inherited values so
tests never touch the real `~/.local`. Use the `temp_xdg_dirs` fixture to
override config/state for a specific test.

### Slow Test Execution

If tests are slow:
- Session-scoped fixtures should already be working
- The wheel + pipx install happen once per run (a few seconds), guarded by a
  lock; all xdist workers share that one result
- Both phases run with `-n auto`, so parallelism (not repeated setup) drives speed

### Lint/Format Failures

Run these after code changes:
```bash
ruff check src/
ruff format src/ --check
```
