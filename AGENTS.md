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

## Testing

All tests must be updated as part of every change:
- Remove tests that validate old/removed behavior
- Fix tests if intended behavior changed
- Write new tests for new features / code paths
- Tests are the contract — do not let tests accumulate debt

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
