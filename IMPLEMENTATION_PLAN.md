# Agent Nook — Implementation Plan

## Overview

A lightweight Python-based sandbox for AI agents using bubblewrap (bwrap) to isolate agents from the host system. Designed to be installed via `pipx install agent-nook` with minimal dependencies and no container runtimes.

## Project Structure

```
agent-nook/
├── pyproject.toml              # pipx-installable package definition
├── README.md                   # Public documentation
├── AGENTS.md                   # Engineering guidelines
├── LICENSE                     # GPLv3
├── .gitignore                  # Python + project-specific ignores
├── src/
│   ├── __init__.py
│   ├── main.py                 # CLI entry point (argparse)
│   ├── config/
│   │   ├── __init__.py
│   │   └── loader.py           # XDG-based YAML config loader
│   ├── sandbox/
│   │   ├── __init__.py
│   │   └── builder.py          # bwrap command builder (dataclass-driven)
│   ├── runner/
│   │   ├── __init__.py
│   │   └── runner.py           # Sandbox execution + error handling
│   └── utils/
│       ├── __init__.py
│       └── logger.py           # XDG-compliant logging
├── config/
│   └── sandbox.yaml            # Default config (copied to XDG location on install)
└── tests/
    └── test_*.py
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         agent-nook                               │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐    ┌──────────────┐    ┌──────────────────┐    │
│  │  CLI Entry  │───▶│  ConfigLoader│───▶│  SandboxBuilder  │    │
│  │   main.py   │    │  (YAML+XDG)  │    │   (bwrap cmd)    │    │
│  └─────────────┘    └──────────────┘    └──────────────────┘    │
│         │                    │                      │            │
│         ▼                    ▼                      ▼            │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  SandboxRunner (orchestrates execution + error handling)    ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

## File-by-File Design

### `pyproject.toml`

- **Name**: `agent-nook`
- **Dependencies**:
  - `pyyaml>=6.0` (for config parsing)
  - `bubblewrap-bin` (optional: only installed if `bwrap` is not found on PATH)
- **Install**: `pipx install agent-nook`
- **Scripts**: `agent-nook` CLI command

### `src/sandbox/builder.py`

**Core concept**: Dataclass-driven bwrap command builder.

```python
@dataclass(frozen=True)
class BindMount:
    source: str
    target: str
    readonly: bool = False
    device: bool = False

@dataclass(frozen=True)
class SandboxConfig:
    name: str
    root: str
    mounts: list[BindMount]
    capabilities: list[str]
    unshare: list[str]
    # ... other fields
```

**Why dataclasses?**
- Frozen immutability matches bwrap's immutable command line
- Direct serialization to CLI args
- IDE-friendly, type-checkable, testable

### `src/config/loader.py`

Resolves config paths via **XDG Base Directory Specification**:

- `~/.config/agent-nook/sandbox.yaml` — main config
- `~/.config/agent-nook/logging.yaml` — logging config
- `~/.local/state/agent-nook/logs/` — log output

**Install-time behavior**: On first run, copies `config/sandbox.yaml` to `~/.config/agent-nook/` if not present.

### `src/utils/logger.py`

Sets up `logging` module with:
- Console handler (`INFO`) → stdout
- RotatingFileHandler (`DEBUG`) → `~/.local/state/agent-nook/logs/agent-nook.log`
- Rotating: 10MB max, 10 backups

### `src/runner/runner.py`

Execution flow with explicit error handling per AGENTS.md:

```
Load config → Build bwrap cmd → Validate → Execute → Cleanup
   ↓           ↓              ↓           ↓           ↓
ConfigError  BwrapError    ValidationError Subprocess  OSError
```

Exceptions raised:
- `ConfigError` — invalid/malformed YAML
- `BwrapError` — bwrap not found or setup fails
- `ValidationError` — conflicting/invalid config values
- `PermissionError` — user namespaces not supported
- `FileNotFoundError` — agent command not found
- `TimeoutExpired` — agent exceeded walltime

### `config/sandbox.yaml` (default)

```yaml
sandbox:
  name: "default-agent"
  root: "./sandbox"

  mounts:
    - source: "/"
      target: "/"
      read_only: true
    - source: "/dev"
      target: "/dev"
      read_only: false
      device: true

  capabilities:
    dropped: ["ALL"]
    kept: ["CHOWN", "SETUID", "SETGID"]

  unshare:
    - pid
    - uts
    - ipc
    - cgroup
    - user

  network:
    allow_all: true
```

**Key design choices**:

| Decision | Rationale |
|----------|-----------|
| `--ro-bind / /` | Reuses host OS image; no image build needed |
| `--dev-bind /dev` | Mounts **full** `/dev` to support all GPU/NPU accelerators dynamically |
| `cap-drop=all` + `cap-add` whitelist | Principle of least privilege |
| `--unshare user` | Hides real UID from agent |
| `--new-session` | Prevents TIOCSTI command injection (CVE-2017-5226) |
| `--die-with-parent` | Clean shutdown on parent exit |

### `src/main.py`

CLI via `argparse`:

```bash
agent-nook run --config ~/.config/agent-nook/sandbox.yaml python3 -c "print('Hello')"
agent-nook run --command "python3 my_agent.py"
agent-nook init  # creates sandbox directory
agent-nook logs  # shows recent logs
agent-nook status  # shows running sandboxes
```

## Dependency Strategy

```
bwrap on PATH? ───YES───▶ Use host bwrap
                │
                NO
                │
                ▼
  Attempt: pip install bubblewrap-bin
                │
                YES   NO
                │      │
                ▼      ▼
          Install    Error:
          bwrap-bin    "bwrap not found"
```

## Error Handling Philosophy (per AGENTS.md)

- **Specific exception types** — never bare `Exception`
- **Log stack traces** at DEBUG level for debugging
- **User-facing messages** are clear and actionable
- **Non-zero exit codes** for unrecoverable failures
- **Never hide failures** — log at ERROR or CRITICAL

## Security Considerations

| Concern | Mitigation |
|---------|-----------|
| TIOCSTI injection | `--new-session` |
| Privilege escalation | `--cap-drop=ALL` + minimal `cap-add` |
| Root in sandbox | `--unshare=user` (agent runs as root inside, but not on host) |
| Host filesystem access | `--ro-bind / /` with selective `--dev-bind` |
| D-Bus attacks | Not supported (no D-Bus socket bind by default) |
| Seccomp bypass | `--assert-userns-disabled` can enforce this |

## Testing Strategy

- **Unit tests**: Config parsing, bwrap command generation, config validation
- **Integration tests**: Spin up sandbox, run a simple command, verify isolation
- **Security tests**: Attempt privilege escalation from inside sandbox
- **Performance**: Measure startup time, memory overhead

## Next Steps

1. Write `pyproject.toml`
2. Write `src/sandbox/builder.py` with dataclass model
3. Write `src/config/loader.py` with XDG path resolution
4. Write `src/utils/logger.py`
5. Write `src/runner/runner.py` with full execution flow
6. Write `src/main.py` CLI
7. Write `config/sandbox.yaml` default
8. Write tests
9. Run lint/typecheck
10. Commit
