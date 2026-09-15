# AGENTS.md — Rules & Guidelines

## Core Principles

- **Simplicity first** — keep it as simple as possible, as complex as necessary.
- **No overengineering** — prefer straightforward solutions.
- **Solid engineering** — always review for best practices.
- **Document everything** — functions, design, architecture, decisions, conventions.
- **Stay current** — with every change, verify AGENTS.md and README.md are up to date.

## Technical Decisions

- **Sandboxing**: Bubblewrap (bwrap) — lightweight, no root required.
- **No containers**: We do NOT use podman, docker, or apptainer.
- **No images**: No dedicated images; sandbox starts from host filesystem.
- **Flatpak-inspired**: Uses the same low-level isolation technique as Flatpak.
- **Configuration**: Human-readable YAML following XDG standard directories. The default config is bundled at `src/agent_nook/config/sandbox.yaml` and automatically copied to `~/.config/agent-nook/sandbox.yaml` on first run.
- **Logging**: Python standard `logging` module with XDG-compliant output.
- **Unprivileged**: Designed to run without root or sudo.

## Code Duplication & Abstraction Rules

### 1. Identify Existing Utilities Before Writing New Code
- **Search First:** Before implementing helpers, array manipulations, data transformations, or API wrappers, check the codebase for pre-existing implementations.
- **Re-use & Extend:** Prefer reusing or slightly extending existing helper functions / classes over writing single-use duplicates.

### 2. Consolidate Structural Duplication (Generalization Rule)
- **Rule of Two:** If you write or modify code and notice the exact same control flow, validation pattern, or data processing logic occurring in 2 or more places, do not duplicate it.
- **Parametrize or Abstract:** Extract the shared logic into a single, generic helper function or class method, using parameters or higher-order functions to handle variations.
- **Avoid Over-Engineering:** Keep abstract functions minimal and single-purpose. Do not create complex "do-everything" utility functions with multiple boolean flag parameters.

### 3. Maintain Single Source of Truth for Domain Rules
- Business logic (e.g., validation schemas, calculation formulas, permission checks) must exist in exactly ONE authoritative location. Never copy-paste business logic between files.

### 4. Permitted Duplication (Where NOT to DRY)
- **Unit & Integration Tests:** Prioritize test readability and isolation (DAMP) over DRY. Do not create complex test helper abstractions unless setup code exceeds ~20 lines across multiple files.
- **Decoupled System Boundaries:** DTOs, API requests/responses, or schemas across distinct service boundaries may duplicate structural shapes if they represent different domain concepts.

### Refactoring Protocol
- **Propose Before Refactoring:** If eliminating duplication requires introducing a new shared module, modifying a public signature, or refactoring existing callers, propose the refactoring plan first and wait for approval before generating the code implementation.

## Directory Layout (XDG Compliant)

```
~/.config/agent-nook/     # Config files
~/.cache/agent-nook/      # Cache and temporary data
~/.local/state/agent-nook/# Runtime state (logs, sandbox metadata)
```

### XDG Paths

| Directory | Purpose |
|-----------|---------|
| `~/.config/agent-nook/sandbox.yaml` | Main configuration |
| `~/.config/agent-nook/logging.yaml` | Logger configuration |
| `~/.local/state/agent-nook/logs/` | Log output |
| `~/.local/state/agent-nook/cache/` | Cache (bwrap cache, agent artifacts) |

## Review Checklist

- [ ] Does this change require a README.md or AGENTS.md update?
- [ ] Is the code properly documented (functions, design, architecture)?
- [ ] Were technical choices / decisions / conventions documented?
- [ ] Is this the simplest correct solution?
- [ ] Is configuration human-readable (YAML) and XDG-compliant?
- [ ] Is logging XDG-compliant?
- [ ] Does it work unprivileged (no root/sudo required)?
- [ ] Error handling is explicit and handles edge cases gracefully.
- [ ] Run full test suite: `python -m pytest tests/ -v`
- [ ] Lint check passes: `ruff check src/`
- [ ] Format check passes: `ruff format src/`

## Error Handling

- Wrap agent execution in try/except with specific exception types.
- Log errors at `ERROR` level with stack traces when debugging.
- Provide clear user-facing error messages for non-fatal issues.
- Sandbox failures should be reported prominently to the user.
- Use `sys.exit()` with non-zero codes for unrecoverable errors.

## Testing

All tests must be updated as part of every change:
- Remove tests that validate old/removed behavior
- Fix tests if intended behavior changed
- Write new tests for new features / code paths
- Tests are the contract — do not let tests accumulate debt

### Verification Commands

After every code change, run the following verification commands:

```bash
# Full test suite (required)
python -m pytest tests/ -v

# Lint check (required)
ruff check src/

# Format check (required)
ruff format src/ --check
```

## Docs are Part of the Contract

All documentation is considered part of the public API. When you implement a change, you are responsible for updating:

| What changed?                          | What docs must change?                                                                 |
|----------------------------------------|----------------------------------------------------------------------------------------|
| Function/method signature              | Docstring + all public docstrings                                                       |
| Configuration key name                 | Config file examples + README config section                                            |
| CLI flag name                         | CLI usage section + README CLI table                                                    |
| Error type/message                     | README error handling section + docstring                                               |
| Behavior / algorithm                   | README "How it works" section                                                            |
| New feature                           | README example section                                                                  |
| Removed feature                       | README (if still referenced) + old tests                                                |

**Rule of thumb:** If a developer reads the README and expects the behavior to work, it must work. If a developer reads the docstring and writes code that works, it must work.

**Pre-commit checklist:**
- ✅ Did I update the docstring for every public API change?
- ✅ Did I update the README usage/CLI section?
- ✅ Did I remove dead code paths or stale imports?
- ✅ Do the examples in the README still run as written?

## Code Quality

### Capability Names

Capability names are passed through unchanged to bwrap. Short names (e.g., `CHOWN`)
are NOT normalized — users must use fully qualified names (`CAP_DAC_READ_SEARCH`).
Validation is deferred to bubblewrap; invalid names produce bwrap's native error.

## Unprivileged Design

The application:
- Runs as a normal user (no `sudo` required)
- Writes config to `~/.config/agent-nook/`
- Writes logs to `~/.local/state/agent-nook/logs/`
- Creates sandbox directories in user-writable locations by default
- Uses `--unshare=cgroup` with bwrap for isolation without privileges

## Configuration

Configuration follows [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html).

### Where the config lives

- **Bundled default**: `src/agent_nook/config/sandbox.yaml` — copied to the user config dir on first run.
- **User config**: `~/.config/agent-nook/sandbox.yaml` — main sandbox configuration.
- **User config**: `~/.config/agent-nook/logging.yaml` — logger configuration.
- **Logs**: `~/.local/state/agent-nook/logs/` — log files.
- **Cache**: `~/.local/state/agent-nook/cache/` — bwrap cache, artifacts.

The default configuration is copied from `src/agent_nook/config/sandbox.yaml` to `~/.config/agent-nook/sandbox.yaml` on first run. You can edit the user config file to customize behavior.

### Tmpfs mounts

By default, `/home` is mounted as a tmpfs (writable in-memory filesystem, invisible to host):

```bash
# This is the default:
bwrap ... --tmpfs /home ...
```

You can customize tmpfs mounts in your config:

```yaml
sandbox:
  tmpfs_mounts:
    - target: "/home"          # unbounded (default)
    - target: "/app"           # unbounded
      size: "100M"             # max 100 MiB
```

This is useful for:
- Sandboxing agent workspaces (`/app`, `/workspace`)
- Protecting sensitive directories from the host
- Keeping sandbox data isolated from persistent storage

See [`builder.py`](src/agent_nook/sandbox/builder.py#L70-L87) for the `TmpfsMount` dataclass.

## 📝 LSP / LINTER CONFIGURATION (Ruff)

### Overview

This project uses **Ruff** as its single linting/formatting tool. Ruff replaces:
- `flake8` (pyflakes, pycodestyle, mccabe, isort, etc.)
- `autopep8` (automatic formatting)
- `mypy` (type checking - optional, not used here)

### Why Ruff?

1. **Speed**: 20-30x faster than flake8, autopep8, or mypy
2. **Single tool**: Replaces multiple linters
3. **Automatic fixes**: Safe fixes are applied automatically
4. **Configurable**: Easy to customize what to fix vs. ignore
5. **Modern**: Actively maintained, supports all Python features

### Configuration Files

- **[pyproject.toml](pyproject.toml)**: Main project configuration
- **[.ruff.toml](.ruff.toml)**: LSP (pylsp) integration configuration

### What Ruff Fixes Automatically

These are **safe** violations that won't change the program's behavior:

| Rule Category | Rule Code | Example |
|---------------|-----------|---------|
| **pycodestyle** | E (errors), W (warnings) | Indentation, line length |
| **flake8** | F | Undefined names, unused imports |
| **Bugbear** | B | Bug-prone patterns |
| **isort** | I | Import ordering |
| **pep8-naming** | N | Variable naming conventions |
| **Comprehensions** | C4 | List/set comprehension simplifications |
| **Type Checking** | TCH | Type hint improvements |
| **PyUpgrade** | UP | Modern Python syntax |
| **Pylint** | PLC, PLR, PLW | Code quality issues (errors only) |
| **Ruff-specific** | RUF | Ruff-specific rules |
| **Simplify** | SIM | Code simplifications |
| **Blind Except** | BLE | Catching Exception instead of specific types |
| **Builtins** | A | Shadowing built-in names |

### What Ruff IGNORES (Manual Review Required)

These violations require human judgment and won't be auto-fixed:

| Rule Category | Rule Code | Reason |
|---------------|-----------|--------|
| **Minor style** | W191, W291, W293 | Let humans decide |
| **Type hints** | PYI034, PYI036 | We use type: ignore if needed |
| **Complexity** | C901, PLR0911-PLR0917 | We manage complexity manually |
| **Mutable defaults** | RUF012 | We use dataclasses with field() |
| **Nested if** | SIM102 | Useful in some cases |
| **Blind except** | BLE001 | We handle specific exception types |
| **Function imports** | PLC0415 | CLI functions need late imports |
| **Magic values** | PLR2004 | Intentional in this code |
| **Loop variable** | PLW2901 | Intentional pattern |
| **Equality with True** | E712 | Used for clarity in some cases |
| **Unused loop var** | B007 | Intentional pattern |
| **Global statement** | PLW0603 | Used in logger initialization |
| **Type checking imports** | TC003 | Conditional imports |
| **Line too long** | E501 | We have 135 char limit |

### Rules DISABLED Entirely

These are **not** enabled in Ruff configuration:

| Category | Why |
|----------|-----|
| **D** (pydocstyle) | We use comprehensive docstrings |
| **ERA** (eradicate) | We don't use commented-out code |
| **FA** (flake8-future-annotations) | We use `from __future__ import annotations` |
| **PGH** (pygrep-hooks) | Not needed |
| **PT** (flake8-pytest-style) | We have our own conventions |
| **RSE** (flake8-raise) | We use logging for errors |
| **S** (flake8-bandit) | We handle security manually |
| **SLF** (flake8-self) | Private method access is fine |
| **TID** (flake8-tidy-imports) | We allow relative imports |
| **TRY** (tryceratops) | We handle exceptions intentionally |

### Best Practices for Future Development

1. **Never disable auto-fix**: Only ignore rules that require human judgment
2. **Use `# type: ignore` for intentional type issues**: Don't disable TCH entirely
3. **Document why you ignore a rule**: Add comments explaining the rationale
4. **Prefer explicit exception handling**: Don't catch `Exception`, catch specific types
5. **Use comprehensive docstrings**: Don't rely on pydocstyle for documentation
6. **Keep line length at 135**: Matches PEP 8, allows for longer docstrings
7. **Avoid magic numbers**: Use constants when possible, but some are intentional
8. **Use late imports in CLI functions**: Import only what's needed at function level

### Running Ruff Manually

```bash
# Check for violations
ruff check src/agent_nook/

# Auto-fix safe violations
ruff check --fix src/agent_nook/

# Format code
ruff format src/agent_nook/

# Check and format together
ruff check --fix src/agent_nook/ && ruff format src/agent_nook/

# Watch mode (development)
ruff check --watch src/agent_nook/

# JSON output (for CI/CD)
ruff check src/agent_nook/ --output-format=json
```

### Adding New Rules

If you want to enable a new rule, edit `.ruff.toml` or `pyproject.toml`:

```toml
[lint]
select = [
    "E", "W", "F", "B", "I", "N", "C4", "TCH", "UP",
    "PLC", "PLR", "PLW", "RUF", "SIM", "BLE", "A",
    # Add new rules here (e.g., "ANN", "ARG", "ASYNC")
]
```

### Troubleshooting

**Auto-fix changes behavior?**
- Check the diff before committing
- Some "safe" fixes may not be safe in all contexts
- Review changes manually if uncertain

## File Integrity & Git Hygiene

### ⚠️ Do NOT delete untracked files
- **Never** delete untracked files unless:
  - You are **explicitly** asked to remove them by the user, OR
  - The file is **clearly** part of the change (e.g., a deleted file that is part of the feature you're implementing)
- If unsure, **prompt the user** rather than taking independent action.
- Untracked files that are **not** part of the change should be **ignored**, not deleted.
- Never run `git add -A` or `git add .` — always explicitly add only the files that are part of the change.
- When committing, only stage files that are directly part of the change.

### File Deletion Rule
Before deleting any file (tracked or untracked):
1. Confirm it is **explicitly** part of the requested change, OR
2. Ask the user for confirmation.

### Summary of Git Commands
- ✅ `git add <file1> <file2>` — add specific files
- ✅ `git add -p` — interactive patch selection
- ❌ `git add -A` — **NEVER** (adds all files, including untracked)
- ❌ `git add .` — **NEVER** (adds all files including untracked)
- ❌ `rm <file>` followed by `git add -A` — **NEVER** without explicit user confirmation

If you are ever uncertain about whether a file should be modified or deleted, **ask the user** before proceeding.
