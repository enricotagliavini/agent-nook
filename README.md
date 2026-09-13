# Agent Nook — Simple Agent Sandbox

A lightweight sandbox for AI agents using **[bubblewrap](https://github.com/containers/bubblewrap) (bwrap)** to isolate the agent from the host system.

> **Lightweight**. **Fast**. **No special privileges**. **Easy to install**.

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![GitHub](https://img.shields.io/badge/github-agent--nook-blue)](https://github.com/enricotagliavini/agent-nook)

## What is this?

A simple sandbox for AI agents, built on top of **[bubblewrap](https://github.com/containers/bubblewrap)** — the low-level sandboxing tool at the core of [Flatpak](https://flatpak.org/).

Unlike full container systems (podman, docker, apptainer), this is:
- **No dedicated image required** — sandbox starts from the host filesystem
- **No root required** — works with unprivileged users
- **Lightweight & fast** — minimal overhead, no daemon required
- **Easy to install** — python based, nearly zero dependencies (Python + PyYAML), pip or pipx install
- **No online services interaction** — does need to download recipes or templates online. Should be simple enough to setup the configuration from the examples

This project has similarities and shares many goal with the following projects, which you can consider as alternative is you wish:
 - [Docker Sandboxes](https://www.docker.com/products/docker-sandboxes/)
 - [Nono](https://github.com/nolabs-ai/nono)
 - [Fence](https://github.com/fencesandbox/fence)
 - [Anthropics' sandbox-runtime](https://github.com/anthropics/sandbox-runtime)

While the main driver for writing this project is the sandboxing / isolation of AI agents, <ins>it can be used to sandobox pretty much anything</ins>.

## Why?

AI Agents usually run with the same UID / GID as your daily user. This means that discretionary access control (DAC) has no way to distinguish it from the human behind the screen. You might not want to share all of your data with the AI Agent, or let it change files in your home directory. As we all know sometimes AI makes mistakes, like in this case, which happened during the development of this very project:

<img width="1651" height="395" alt="Screenshot_20260908_131001" src="https://github.com/user-attachments/assets/c2c7fb5b-2afb-48ca-98fb-361e97ded752" />

the agent tried to `rm -rf ~/.config` that would have wiped a lot of important files in my personal home directory. Luckily opencode stopped the execution and asked as it detected this was not within the project directory. However, there are easy bypass for the AI agent, and I might not be so lucky next time. However, I still want to share data with the agent, for example the git repo itself so it can help me coding. This tool is meant to give you the possibility of decide how to isolate the agent and what to share with it. You can even force it to be unable to use the network, if you like. However, I would **discourage considering this tool a bulletproof security tool**. The main objective is to control what the AI agent can do / access, but it assume the agent itself is trust-able software, that might make, unintentional, mistakes.

## Important notes!

This is a project I do during my spare time. **It may contain errors or security bugs**, so use it at your own risk and don't run untrusted / malicious code within the sandbox. It might find a way out and eat your hamster.

## Special thanks

This project is being developed with the assistance of AI open weight models and a lot of free and open software. With all of the following (plus more) this would not have been possible. A big thank you to everybody!

 - [Qwen AI model](https://huggingface.co/Qwen) for providing the open weight model.
 - [Opencode](https://opencode.ai/) the AI coding agent I used for this project.
 - [Lemonade AI server](https://lemonade-server.ai/) for the easy to use personal and local AI server.
 - [AMD ROCm](https://github.com/ROCm) for the great Linux support. Using ROCm on Fedora worked out of the box and it was a trouble free experience.
 - [Fedora](https://fedoraproject.org/kde) [Plasma Desktop](https://kde.org/) edition, for making a great Linux distro and packaging ROCm and llama-cpp making it a trouble free installation experience.
 - [GNU](https://www.gnu.org/) for start and promoting important free software projects.
 - [Linux](https://www.kernel.org/) For providing the kernel to run all of this.

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
| Images | Optional | Keep it simple; the sandbox can use host filesystem, or, if desired, also a custom image |
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
pip install -e ".[bubblewrap]"  # with bubblewrap dependency in case it's not already installed and available by the OS
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
agent-nook run --chdir /home/enrico python3 my_script.py

# With a Python script
agent-nook run python3 -c "print('hello')"

# With bind mounts
agent-nook run --bind /host/data:/sandbox/data python3 script.py

# Read-only bind mount
agent-nook run --ro-bind /host/config:/sandbox/config python3 script.py

# Custom config file
agent-nook run --config /path/to/config.yaml python3 script.py

# Override capabilities
agent-nook run --cap-add NET_BIND_SERVICE --cap-add SYS_PTRACE python3 net_script.py

# Unshare namespaces (comma-separated)
agent-nook run --unshare pid,uts,ipc python3 script.py

# Environment variables
agent-nook run --env PATH=/usr/bin --env HOME=/root python3 script.py

# Unset environment variables
agent-nook run --unset-env PATH --unset-env HOME python3 script.py

# Custom hostname
agent-nook run --hostname my-agent python3 script.py

# No new session (allows TIOCSTI)
agent-nook run --no-new-session python3 script.py

# Exit immediately on failure (no output)
agent-nook run --exit-on-fail python3 script.py

# Additional capabilities
agent-nook run --caps CHOWN,DAC_READ_SEARCH python3 script.py

# Drop specific capabilities
agent-nook run --drop-caps SYS_ADMIN,SYS_PTRACE python3 script.py

# Don't die with parent
agent-nook run --no-die-with-parent python3 script.py
```

### CLI Reference

| Flag | Description |
|------|-------------|
| `CMD [ARGS...]` | Command to execute in the sandbox (e.g. `python3 agent.py`) |
| `--config CONFIG` | Override config file path (default: `~/.config/agent-nook/sandbox.yaml`) |
| `--chdir DIR` | Change working directory in sandbox (overrides config) |
| `--cap-add CAP` | Add capability (e.g. `CAP_NET_BIND_SERVICE`, repeat for multiple) |
| `--cap-drop CAP` | Drop capability (e.g. `CAP_SYS_ADMIN`, repeat for multiple) |
| `--unshare NS` | Unshare namespace (e.g. `pid,uts,ipc`, repeat for multiple) |
| `--bind SRC:DEST` | Bind mount host SRC to sandbox DEST (repeat for multiple) |
| `--ro-bind SRC:DEST` | Read-only bind mount host SRC to sandbox DEST (repeat for multiple) |
| `--env KEY=VALUE` | Set environment variable (repeat for multiple) |
| `--unset-env KEY` | Unset environment variable (repeat for multiple) |
| `--hostname HOSTNAME` | Set sandbox hostname |
| `--new-session` | Create new session (prevents TIOCSTI attacks, default: on) |
| `--no-new-session` | Don't create new session (allows TIOCSTI) |
| `--exit-on-fail` | Exit immediately on failure (don't show logs) |
| `--caps CAPS` | Additional capabilities to add (repeat for multiple) |
| `--drop-caps DROP_CAPS` | Additional capabilities to drop (repeat for multiple) |
| `--die-with-parent` | Kill sandbox child when parent dies (default: on) |
| `--no-die-with-parent` | Keep sandbox alive after parent exits |

## Configuration

Configuration follows [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html).

### Where config lives

- `~/.config/agent-nook/sandbox.yaml` — Main sandbox configuration
- `~/.config/agent-nook/logging.yaml` — Logger configuration
- `~/.local/state/agent-nook/logs/` — Log files
- `~/.local/state/agent-nook/cache/` — Cache (bwrap cache, artifacts)

### Setting up config

To create or edit the configuration, edit `~/.config/agent-nook/sandbox.yaml` directly.

The config uses YAML format with these top-level keys:

```yaml
name: agent-sandbox
chdir: /home/enrico
mounts:
  - source: /home/enrico/project
    target: /workspace
    type: bind
capabilities:
  drop: ["ALL"]
  keep: ["CAP_CHOWN", "CAP_DAC_OVERRIDE"]
unshare:
  pid: true
  ipc: true
  uts: false
  user: true
```

You can override any config option at runtime via CLI flags:

```bash
agent-nook run --chdir /tmp --cap-add NET_BIND_SERVICE python3 agent.py
```

### Default sandbox config

See [sandbox.yaml](src/agent_nook/config/sandbox.yaml)

### Customizing config

Edit `~/.config/agent-nook/sandbox.yaml` to customize:

- **`name`**: Sandbox identifier (used in logs)
- **`chdir`**: Working directory inside the sandbox
- **`mounts`**: Read-write bind mounts from host (YAML array of `source:target:type`)
- **`ro-mounts`**: Read-only bind mounts from host (YAML array of `source:target`)
- **`capabilities`**: Linux capabilities to drop (`drop: ["ALL"]`) or keep (`keep: ["CAP_CHOWN"]`)
- **`unshare`**: Which namespaces to unshare (isolate) — `pid`, `uts`, `ipc`, `cgroup`, `user`, `network`
- **`hostname`**: Sandbox hostname (implies UTS namespace unshare)
- **`die_with_parent`**: Kill sandbox children when parent exits (default: `true`)
- **`new_session`**: Create a new session (prevents TIOCSTI attacks, default: `true`)
- **`env_vars`**: Environment variables to set (YAML map: `KEY: value`)
- **`unenv_vars`**: Environment variables to unset (YAML array: `["PATH", "HOME"]`)
- **`timeout`**: Maximum execution time in seconds (`null` = no timeout)

### Example: Running an agent with specific capabilities

```bash
# Allow the agent to read from host directories but restrict system access
agent-nook run \
  --bind /home/enrico/data:/data \
  --ro-bind /var/lib/docker:/docker-data \
  --env DOCKER_DATA=/docker-data \
  --env HOME=/data/home \
  python3 my_agent.py
```

### Example: Dropping all capabilities

```bash
agent-nook run --cap-drop ALL --chdir /workspace python3 agent.py
```

### Example: Full isolation

```bash
agent-nook run \
  --cap-drop ALL \
  --unshare pid,ipc,uts,user \
  --chdir /sandbox \
  --env HOME=/sandbox/home \
  --env PATH=/usr/bin:/bin \
  python3 safe_agent.py
```

## Technical Details

### How we sandbox

- **Filesystem isolation**: Agent's view of the filesystem is restricted to a subset of host paths via bind mounts.
- **Capability restrictions**: Drops unnecessary Linux capabilities (e.g., `SYS_ADMIN`, `SYS_PTRACE`).
- **Network controls**: Can restrict outbound connections by namespace.
- **No root needed**: Works entirely in userspace using `--unshare=cgroup` and `--die-with-parent`.

### Why bubblewrap?

Bubblewrap is the same technology behind Flatpak. It's a single binary that applies sandbox rules without requiring a container runtime. It's available out of the box on any distribution supporting Flatpak packages.

### Conventions

- **Python 3.10+** — type hints, dataclasses, type checking.
- **`src/` layout** — one module per concern.
- **Logging** — use `logging` module with XDG-compliant paths and levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`.
- **XDG compliance** — all config, cache, and log files follow the XDG Base Directory Specification.

## Development

### Prerequisites

- Python 3.10 or newer
- bubblewrap (install via `dnf install bubblewrap`, `apt install bubblewrap`, `pip install bubblewrap-bin` or download from the [bubblewrap github releases](https://github.com/containers/bubblewrap/releases))

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
