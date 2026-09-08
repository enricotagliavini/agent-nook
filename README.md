# Agent Nook — Simple Agent Sandbox

A lightweight sandbox for AI agents using **bubblewrap (bwrap)** to isolate the agent from the host system.

> **Lightweight**. **Fast**. **No special privileges**.

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![GitHub](https://img.shields.io/badge/github-agent--nook-blue)](https://github.com/enricotagliavini/agent-nook)

## What is this?

A simple sandbox for AI agents, built on top of **bubblewrap** — the low-level sandboxing tool at the core of [Flatpak](https://flatpak.org/).

Unlike full container systems (podman, docker, apptainer), this is:
- **No dedicated image required** — sandbox starts from the host filesystem
- **No root required** — works with unprivileged users
- **Lightweight & fast** — minimal overhead, no daemon required

## Quick Start

```bash
git clone https://github.com/enricotagliavini/agent-nook
cd agent-nook
pip install -e .
agent-nook run --command "echo hello from sandbox"
```

## Project Structure

```
agent-nook/
├── AGENTS.md                 # Agent rules & engineering guidelines
├── README.md                 # This file
├── pyproject.toml           # Build & package configuration
├── src/
│   ├── agent_nook/
│   │   ├── __init__.py
│   │   ├── __main__.py      # CLI entry point
│   │   ├── config/
│   │   │   ├── loader.py    # Config loader (XDG-compliant)
│   │   │   └── sandbox.yaml # Default config (bundled with package)
│   │   ├── sandbox/
│   │   │   └── builder.py   # bwrap command builder
│   │   ├── runner/
│   │   │   └── _core.py     # Sandbox execution orchestration
│   │   └── utils/
│   │       └── logger.py    # Logging utilities
│   └── config/
│       └── sandbox.yaml     # Development override (copied to package)
├── config/
│   └── sandbox.yaml         # Development config (template)
├── LICENSE
└── AGENTS.md
```

## Design Decisions

| Concern | Choice | Why |
|---------|--------|-----|
| Sandboxing | Bubblewrap | Low-level, no root, no daemon, Flatpak-proven |
| Images | None | Keep it simple; sandbox uses host filesystem |
| Runtime | None | bwrap has no background service |
| Config format | YAML | Human-readable, standard for config files |
| Logging | `logging` module + XDG | Standard library, no dependencies |
| Language | Python 3.10+ | Type hints, dataclasses, modern features |

## How it works

```
Agent → bwrap sandbox → isolated filesystem + capabilities → agent runs safely
```

Bubblewrap applies filesystem isolation, network controls, and capability restrictions in userspace — no kernel namespaces or privileged containers needed.

### Example bwrap command

```bash
bwrap --bind /host/path /sandbox --ro-bind /host/path /sandbox/host-path \
      --capabilities drop=all -- cap-add=CHOWN -- python3 agent.py
```

## Installation

### From source

```bash
git clone https://github.com/enricotagliavini/agent-nook
cd agent-nook
pip install -e ".[dev]"  # with dev dependencies
pip install -e ".[bubblewrap]"  # with bubblewrap dependency
```

### With pip

```bash
pip install agent-nook[bubblewrap]
agent-nook run --command "echo hello"
```

## CLI Usage

### Running a command in a sandbox

```bash
# Basic usage
agent-nook run --command "echo hello from sandbox"

# With a Python script
agent-nook run --command "python3 -c 'print(\"hello\")'"

# Custom config file
agent-nook run -f /path/to/config.yaml --command "ls -la"

# Override capabilities
agent-nook run --cap-add NET_BIND_SERVICE --cap-add SYS_PTRACE --command "netstat -tlnp"

# Unshare namespaces (comma-separated)
agent-nook run --unshare pid --unshare uts --unshare ipc --command "whoami"

# Bind mount a host directory (read-only)
agent-nook run --ro-bind /host/data /data --command "ls /data"

# Environment variables
agent-nook run --env PATH=/usr/bin --env HOME=/root --command "ls -la"

# Multiple commands
agent-nook run --command "echo a" --command "echo b"

# Python script mode
agent-nook run --script "import sys; print(sys.version)"
```

### Available subcommands

```bash
agent-nook run       # Run a command in a sandbox
agent-nook init      # Initialize config directory
agent-nook status    # Show sandbox status
agent-nook logs      # Show recent logs
agent-nook list      # List available sandboxes
```

## Configuration

Configuration follows [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html).

### Where config lives

- `~/.config/agent-nook/sandbox.yaml` — Main sandbox configuration
- `~/.config/agent-nook/logging.yaml` — Logger configuration
- `~/.local/state/agent-nook/logs/` — Log files
- `~/.local/state/agent-nook/cache/` — Cache (bwrap cache, artifacts)

### Setting up config

Run `agent-nook init` to initialize the config directory and create a default `sandbox.yaml`:

```bash
agent-nook init
```

This creates `~/.config/agent-nook/sandbox.yaml` with sensible defaults.

### Default sandbox config

```yaml
# ~/.config/agent-nook/sandbox.yaml
sandbox:
  name: "my-agent"
  root: "./sandbox"
  readonly: true

  # Filesystem restrictions
  mounts:
    - source: "/home"
      target: "/data"
      readonly: true

  # Network controls
  network:
    allow_hosts:
      - "localhost"
    allow_ports:
      - 8080

  # Capabilities
  capabilities:
    drop:
      - ALL
    keep:
      - CHOWN
      - SETUID

  # Namespace isolation
  unshare:
    pid: true
    uts: true
    ipc: true
    cgroup: true
    user: true
    network: false

  # Process isolation
  die_with_parent: true
  new_session: true
  hostname: "agent-nook"
```

### Customizing config

Edit `~/.config/agent-nook/sandbox.yaml` to customize:

- **`name`**: Sandbox identifier (used in logs)
- **`root`**: The sandbox root directory (created in tmpfs)
- **`readonly`**: If true, the sandbox root is mounted read-only
- **`mounts`**: Read-write bind mounts from host
- **`ro-mounts`**: Read-only bind mounts from host
- **`network`**: Hosts and ports allowed for network access
- **`capabilities`**: Linux capabilities to drop or keep
- **`unshare`**: Which namespaces to unshare (isolate)
- **`hostname`**: Sandbox hostname
- **`die_with_parent`**: Kill sandbox children when parent exits
- **`new_session`**: Create a new session (prevents TIOCSTI attacks)

### Example: Running an agent with specific capabilities

```bash
# Allow the agent to read from host directories but restrict system access
agent-nook run \
  --ro-bind /home/enrico /data \
  --ro-bind /var/lib/docker /docker-data \
  --env DOCKER_DATA=/docker-data \
  --env HOME=/data/home \
  --command "python3 my_agent.py"
```

### Example: Dropping all capabilities

```bash
agent-nook run --cap-drop ALL --command "echo still works but can't chmod/etc"
```

### Example: Network isolation

```yaml
# ~/.config/agent-nook/sandbox.yaml
sandbox:
  network:
    allow_hosts:
      - "localhost"
      - "127.0.0.1"
    allow_ports:
      - 8080
      - 9090
```

```bash
agent-nook run --config ~/.config/agent-nook/sandbox.yaml --command "curl http://localhost:8080"
```

## Technical Details

### How we sandbox

- **Filesystem isolation**: Agent's view of the filesystem is restricted to a subset of host paths via bind mounts.
- **Capability restrictions**: Drops unnecessary Linux capabilities (e.g., `SYS_ADMIN`, `SYS_PTRACE`).
- **Network controls**: Can restrict outbound connections by namespace.
- **No root needed**: Works entirely in userspace using `--unshare=cgroup` and `--die-with-parent`.

### Why bubblewrap?

Bubblewrap is the same technology behind Flatpak. It's a single binary (`bubblewrap`, available via `flatpak run com.github.chmlr.Bubblewrap`) that applies sandbox rules without requiring a container runtime.

### Conventions

- **Python 3.10+** — type hints, dataclasses, type checking.
- **`src/` layout** — one module per concern.
- **Tests first** — we write tests before implementation.
- **Logging** — use `logging` module with XDG-compliant paths and levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`.
- **Error handling** — explicit try/except with specific exception types; clear user-facing messages for non-fatal issues.
- **XDG compliance** — all config, cache, and log files follow the XDG Base Directory Specification.

## Development

### Prerequisites

- Python 3.10 or newer
- bubblewrap (install via `flatpak install flathub com.github.chmlr.Bubblewrap` or `apt install bubblewrap`)

### Setting up development

```bash
git clone https://github.com/enricotagliavini/agent-nook
cd agent-nook
pip install -e ".[dev]"

# Run tests
pytest

# Check types
mypy src/

# Format code
ruff check src/
ruff format src/
```

### Running tests

```bash
pytest
pytest -v --cov=agent_nook
```

## License

GPLv3 — see [`LICENSE`](LICENSE) for details.

## Contributing

Contributions are welcome! Please see [`AGENTS.md`](AGENTS.md) for engineering guidelines and contribution rules.
