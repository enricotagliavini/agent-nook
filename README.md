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
python --version  # Python 3.10 or newer required
python src/main.py
```

## Project Structure

```
agent-nook/
├── AGENTS.md          # Agent rules & engineering guidelines
├── README.md          # This file
├── docs/
│   ├── ARCHITECTURE.md     # System architecture & design
│   └── CONFIG.md           # Configuration reference
├── src/
│   ├── __init__.py
│   ├── main.py           # Entry point
│   ├── sandbox.py        # bwrap sandbox orchestration
│   └── runner.py         # Agent execution within sandbox
├── config/
│   └── sandbox.yaml      # Default sandbox configuration (YAML)
├── tests/
│   └── test_sandbox.py
├── scripts/
│   └── setup.sh
├── LICENSE
└── pyproject.toml
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

## Configuration

Configuration follows [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html).

### Where config lives

```
~/.config/agent-nook/sandbox.yaml  # Main sandbox configuration
~/.config/agent-nook/logging.yaml  # Logger configuration
~/.local/state/agent-nook/logs/    # Log files
~/.local/state/agent-nook/cache/   # Cache (bwrap cache, artifacts)
```

### Example sandbox config

```yaml
# ~/.config/agent-nook/sandbox.yaml
sandbox:
  name: "my-agent"
  root: "./sandbox"

  # Filesystem restrictions
  mounts:
    - source: "/home"
      target: "/data"
      read_only: true

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
```

### Example logging config

```yaml
# ~/.config/agent-nook/logging.yaml
version: 1
formatters:
  simple:
    format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
handlers:
  console:
    class: logging.StreamHandler
    level: INFO
    formatter: simple
  file:
    class: logging.handlers.RotatingFileHandler
    level: DEBUG
    formatter: simple
    filename: ~/.local/state/agent-nook/logs/agent-nook.log
    maxBytes: 10485760
    backupCount: 10
    encoding: utf-8
    when: midnight
    interval: 1
root:
  level: DEBUG
  handlers: [console, file]
```

## Technical Details

### How we sandbox

- **Filesystem isolation**: Agent's view of the filesystem is restricted to a subset of host paths.
- **Capability restrictions**: Drops unnecessary Linux capabilities.
- **Network controls**: Can restrict outbound connections.
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

## License

GPLv3 — see [`LICENSE`](LICENSE) for details.
