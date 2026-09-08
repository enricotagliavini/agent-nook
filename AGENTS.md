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
- **Configuration**: Human-readable YAML following XDG standard directories.
- **Logging**: Python standard `logging` module with XDG-compliant output.
- **Unprivileged**: Designed to run without root or sudo.

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

## Error Handling

- Wrap agent execution in try/except with specific exception types.
- Log errors at `ERROR` level with stack traces when debugging.
- Provide clear user-facing error messages for non-fatal issues.
- Sandbox failures should be reported prominently to the user.
- Use `sys.exit()` with non-zero codes for unrecoverable errors.

## Unprivileged Design

The application:
- Runs as a normal user (no `sudo` required)
- Writes config to `~/.config/agent-nook/`
- Writes logs to `~/.local/state/agent-nook/logs/`
- Creates sandbox directories in user-writable locations by default
- Uses `--unshare=cgroup` with bwrap for isolation without privileges
