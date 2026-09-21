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

## XDG Configuration Setup

### Overview

All tests respect the [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html):

- **Config**: `~/.config/agent-nook/sandbox.yaml`
- **State/Logs**: `~/.local/state/agent-nook/`

### Automatic Setup

Test fixtures create temporary XDG directories under `/tmp` to avoid polluting the git repository:

- **Config directory**: `/tmp/pytest-of-<user>/pytest-<id>/xdg_config/config/agent-nook/`
- **State directory**: `/tmp/pytest-of-<user>/pytest-<id>/xdg_state/state/`

The fixtures are defined as session-scoped in `tests/conftest.py`:

```python
@pytest.fixture(scope="session")
def xdg_config_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Creates temporary XDG config directory under /tmp."""
    temp_config_dir = tmp_path_factory.mktemp("xdg_config") / "config"
    temp_state_dir = tmp_path_factory.mktemp("xdg_state") / "state"
    # Create agent-nook subdirectory
    agent_nook_config_dir = temp_config_dir / "agent-nook"
    # Copy bundled config
    bundled_config = Path(__file__).parent.parent / "src" / "agent_nook" / "config" / "sandbox.yaml"
    user_config = agent_nook_config_dir / "sandbox.yaml"
    if bundled_config.exists():
        shutil.copy2(bundled_config, user_config)
    # Set XDG environment variables for entire test session
    os.environ["XDG_CONFIG_HOME"] = str(temp_config_dir)
    os.environ["XDG_STATE_HOME"] = str(temp_state_dir)
    return agent_nook_config_dir
```

This ensures:
1. No pollution of the git repository with test artifacts
2. Isolated test environments automatically cleaned up after tests
3. Consistent XDG-compliant paths during testing
4. Session-wide availability of config for all integration tests

## Pipx Test Environment

Integration tests use a shared pipx environment isolated via `PIPX_HOME`:

```python
@pytest.fixture(scope="session")
def pipx_test_env(tmp_path_factory: pytest.TempPathFactory):
    """Provides shared isolated pipx environment for the session."""
    pipx_home = tmp_path_factory.mktemp("pipx")
    pipx_bin = pipx_home / "bin"
    env = os.environ.copy()
    env["PIPX_HOME"] = str(pipx_home)
    env["PIPX_BIN_DIR"] = str(pipx_bin)
    env["PATH"] = str(pipx_bin) + os.pathsep + env.get("PATH", "")
    return pipx_home, pipx_bin, env
```

This isolates test venvs from the host system's pipx installations.

### Wheel Building

Integration tests install a wheel that the `built_wheel` fixture rebuilds
unconditionally at session start:

```python
@pytest.fixture(scope="session")
def built_wheel() -> Path:
    """Rebuilds the wheel from the current source once per session."""
    for stale_wheel in dist_dir.glob("*.whl"):
        stale_wheel.unlink()
    subprocess.run([sys.executable, "-m", "hatch", "build", "-t", "wheel"], check=True)
    return wheel_path[0]
```

The unconditional rebuild guarantees integration tests always run against
the current source, never a stale build left in `dist/`.

### Test Runner Fixture

The `test_runner` fixture installs the package once per session:

```python
@pytest.fixture(scope="session")
def test_runner(
    xdg_config_dir: Path,
    pipx_test_env: tuple[Path, Path, list[str]],
    built_wheel: Path,
) -> subprocess.CompletedProcess:
    """Installs agent-nook via pipx once for all integration tests."""
    result = subprocess.run(
        [sys.executable, "-m", "pipx", "install", "--force", str(built_wheel)],
        env=env,
    )
    # Returns a callable that runs commands in the sandbox
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

## Test Isolation

### Session Scope

Fixtures marked `scope="session"` are created once per test run and shared:

- `xdg_config_dir`: Shared XDG config directory
- `xdg_state_dir`: Shared XDG state directory
- `pipx_test_env`: Shared pipx environment
- `built_wheel`: Wheel rebuilt from the current source
- `test_runner`: Pre-installed agent-nook binary

This speeds up test execution by avoiding redundant setup.

### Function Scope

Fixtures marked `scope="function"` are fresh for each test:

- `test_sandbox_run`: Returns a fresh runner callable for each test
- Standard `tmp_path` fixture: Fresh temp directory for each test

## Available Fixtures

### Session Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `xdg_config_dir` | session | Returns temp config directory under /tmp |
| `xdg_state_dir` | session | Returns temp state directory under /tmp |
| `pipx_test_env` | session | Isolated pipx environment (Path, Path, dict) |
| `built_wheel` | session | Wheel rebuilt from the current source (once per session) |
| `test_runner` | session | Installed agent-nook callable |

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

The `built_wheel` fixture removes any stale wheels from `dist/` and rebuilds
the wheel from the current source at session start. Integration tests
therefore always run against the latest code — no manual cleaning of `dist/`
is needed.

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

The session-wide XDG setup only runs if `XDG_CONFIG_HOME` is not already set.
Use `temp_xdg_dirs` fixture to override for specific tests.

### Slow Test Execution

If tests are slow:
- Session-scoped fixtures should already be working
- The wheel is rebuilt on every session (a few seconds), which is expected

### Lint/Format Failures

Run these after code changes:
```bash
ruff check src/
ruff format src/ --check
```
