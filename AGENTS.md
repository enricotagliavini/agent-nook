# AGENTS.md — Rules & Guidelines

## Core Principles
- **Simplicity first**: Straightforward solutions, no overengineering.
- **Solid engineering**: Follow best practices, document everything.
- **Unprivileged**: NO root/sudo. Runs as normal user.
- **Sandboxing**: Bubblewrap (`bwrap`) ONLY. NO podman, docker, or apptainer. No dedicated images; starts from host FS. Uses `--unshare=cgroup`.

## XDG Configuration & Paths
- **Config**: `~/.config/agent-nook/sandbox.yaml` (default copied from `src/agent_nook/config/sandbox.yaml`), `logging.yaml`
- **State/Logs**: `~/.local/state/agent-nook/`
- **Cache**: `~/.local/state/agent-nook/cache/`
- **Format**: Human-readable YAML. `/home` mounted as tmpfs by default.

## Code Quality & DRY
- **NEVER DUPLICATE CODE**: Do not copy-paste logic. If you write the same logic twice, you failed.
- **Rule of Two (MANDATORY)**: If identical control flow, validation, or data processing appears ≥2 times, you MUST extract it into a shared, generic helper.
- **Search First**: Always check existing utilities before writing new helpers, array manipulations, or API wrappers. Re-use and extend existing code.
- **Single Source of Truth**: Business logic (validation, calculations, permissions) must exist in EXACTLY ONE authoritative location. NEVER duplicate domain rules across files.
- **Avoid Over-Engineering**: Keep extracted helpers minimal and single-purpose. No complex "do-everything" functions with boolean flags.
- **Permitted Duplication**: ONLY allowed for Unit/Integration tests (prioritize DAMP readability) and decoupled DTOs/schemas across distinct service boundaries.
- **Refactoring Protocol**: If removing duplication requires changing public signatures or adding shared modules, PROPOSE the plan first and wait for approval.

## Testing & Verification (MANDATORY)
Run after EVERY change:
- `./run_tests.sh`
- `ruff check src/ --fix`
- `ruff format src/`
- Update tests for all behavior changes. Tests are the contract.

## Linting (Ruff)
- **Tool**: Ruff only. Line length: 135.
- **Action**: Auto-fix safe violations. Use `# type: ignore` for intentional type issues.
- **Disabled entirely**: D, ERA, FA, PGH, PT, RSE, S, SLF, TID, TRY.
- **Ignore (manual review)**: W191, W291, W293, PYI034, PYI036, C901, PLR0911-PLR0917, RUF012, SIM102, BLE001, PLC0415, PLR2004, PLW2901, E712, B007, PLW0603, TC003, E501.

## Documentation Contract
Update docs for ANY change:
- Signature change → Update docstrings.
- Config/CLI change → Update README + examples.
- New/Removed feature → Update README.
- **Pre-commit**: Verify docstrings, README examples run, and remove dead code.

## Error Handling
- Wrap agent execution in `try/except` with specific exceptions.
- Log `ERROR` with stack traces for debugging.
- Use `sys.exit(non-zero)` for unrecoverable errors. Prominently report sandbox failures.

## Git Hygiene (STRICT)
- ONLY `git add <specific files>` or `git add -p`.
- NEVER `git add -A` or `git add .`.
- NEVER delete untracked files unless explicitly requested or part of the feature. Ask if unsure.
