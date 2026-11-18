# Plan: Runtime-generated stdio socket names (parallel-safe orchestration)

**Status: DONE** — All 6 phases complete. Full suite green (261 passed), `ruff check src/` and `ruff format src/` clean.
**Branch:** `feature_bridges`

## Problem
The orchestration `socket:` value is a **static config string**. Running two
`agent-nook` processes in parallel (e.g. two Qwen Code windows on two projects)
starts the same host command twice but the target socket is identical → **clash**.

## Fix (high level)
Move socket naming from **config** to **runtime**. Generate a unique
`{name}-{pid}-{counter}.sock` per process and wire it into:
- the **host** orchestration command (placeholder substitution into argv), and
- the **sandbox** (env var `STDIO_SOCKET_PATH` + auto bind-mount into `/run`,
  which `stdio_stub.py` reads instead of a hard-coded argument).

The socket path **no longer lives in the frozen `OrchestrationEntry`** — it is
generated and tracked by the `Orchestrator` at runtime.

## Locked decisions
- **Config change (breaking, acceptable — not released):** `socket:` (required
  string) → `track-socket:` (bool, default `false`). Update default config +
  README. **No** migration note / backward compat.
- **Placeholders:** `${STDIO_SOCKET_PATH}` (full host path) + `${STDIO_SOCKET_NAME}`
  (basename, e.g. `name-pid-0.sock`) + `${STDIO_SOCKET_DIR}` (the directory the
  socket is reserved in; created before the command starts, so it can be
  bind-mounted even though the socket file does not exist yet). The directory
  placeholder enables container commands like
  `podman run ... -v ${STDIO_SOCKET_DIR}:/app:z --name ${STDIO_SOCKET_NAME}`.
- **Env var:** single `STDIO_SOCKET_PATH` for the common one-entry case;
  `STDIO_SOCKET_PATH1`, `2`, … when a config has multiple `track-socket` entries.
- **Sandbox mount target:** `/run/<STDIO_SOCKET_NAME>` (auto bind-mounted; `/run`
  is a tmpfs in the default config). The env var value inside the sandbox equals
  this `/run` path.
- **Counter:** per-process, starts at 0, increments once per generated socket
  (dedupes multiple `track-socket` entries in one run). PIDs differ across runs,
  so cross-run uniqueness comes for free.
- **Enhancements (included by default):**
  - Cleanup leaked sockets on exit (non-keep only, in a `finally` after
    `wait_ready`) — covers SIGKILL/crash where the socket isn't unlinked.
  - Socket dir created with mode `0700` (esp. the `/tmp` fallback) — don't let
    a local user sniff a bridge socket.
  - Sanitize filename: use `Path(name).name` (strip path components) so a config
    can't escape the base dir via `../`.
  - Injectable factory: `generate(name, pid, counter, pid=os.getpid(),
    base_dir=…)` — unit test with a temp dir + fixed pid, no real sockets.

## Key constraint
Orchestration command is a frozen `list[str]` produced at parse time
(`loader._parse_command` / `shlex.split`). **Runtime substitution operates on
those already-tokenized elements — never re-tokenize a string.**

## NOTE: loader env expansion
`_expand_env_vars` runs at config-load time. `${STDIO_SOCKET_PATH}` (and the
related `${STDIO_SOCKET_NAME}` / `${STDIO_SOCKET_DIR}` placeholders) are left
**always** literal, regardless of whether they are set in the host environment —
the loader skips any variable whose name starts with `STDIO_SOCKET_`. This keeps
config parsing unaffected by the sandbox's `STDIO_SOCKET_*` vars, so the
orchestrator can fill them with the real path at run time.
DONE (2026-11-17): implemented as a `STDIO_SOCKET_*` prefix skip in
`loader._custom_expand_env_vars`; covered by
`tests/config/test_orchestration.py::test_socket_placeholders_preserved_even_when_set_in_host_env`.

## Phases (checkpoint = run `./run_tests.sh` + `ruff check src/ --fix` + `ruff format src/`)

### Phase 1 — Config schema: `socket` → `track-socket` (bool) [CHECKPOINT]
- `config.py OrchestrationEntry`: replace `socket: str` field with
  `track_socket: bool = False`; update dataclass docstring.
- `loader._parse_orchestration`: `known_keys = ("command", "track-socket",
  "wait-timeout", "keep")`; validate bool; **remove** socket-string + udp
  validation; build `OrchestrationEntry(track_socket=...)`. Update docstring.
- `tests/config/test_orchestration.py`: repurpose `socket_*` → `track-socket_*`
  (add a `track-socket` validation test; drop `socket_required` / `udp_rejected`
  tests).
- README + default `sandbox.yaml` comment update.

### Phase 2 — Socket factory (single source of truth) [CHECKPOINT — DONE]
- New `socket_factory.py`:
  `generate(name: str, counter: int, *, pid: int = os.getpid(), base_dir: str | None = None) -> str`
  → `{base_dir}/{Path(name).name}-{pid}-{counter}.sock`.
  `base_dir` = `$XDG_RUNTIME_DIR/agent-nook` if set, else `/tmp`;
  `os.makedirs(base_dir, 0o700, exist_ok=True)` (+ explicit chmod to defeat umask).
- Unit test (`tests/test_socket_factory.py`, temp dir + fixed pid; no bwrap, no
  real socket): name derivation, counter increment, `Path(name).name`
  sanitization, 0700 dir mode, XDG base, `/tmp` fallback, socket file NOT
  created. **8 passing, ruff clean.**
- NOTE: lives at flat `src/agent_nook/socket_factory.py` (not `orchestrator/`)
  because the flat `orchestrator.py` cannot coexist with an `orchestrator/`
  package in the same dir.

### Phase 3 — Substitution + orchestrator wiring [CHECKPOINT — DONE]
- Substitute `${STDIO_SOCKET_PATH}` (full host path) + `${STDIO_SOCKET_NAME}`
  (basename) + `${STDIO_SOCKET_DIR}` (the reserved directory) into host argv
  `list[str]` elements (single helper, no re-tokenize; the directory
  placeholder is derived from `os.path.dirname(host_path)`).
- `Orchestrator.start()`: generate path per `track_socket` entry (counter++),
  substitute, record `host_path`; keep numbered env var mapping
  (`STDIO_SOCKET_PATH1..N`).
- `wait_ready()` / `_is_ready()`: wait on the **generated** path.
- Return a runtime map to `bwrap_sandbox`: `{host_path → /run/<name>}`.
- Update `tests/test_orchestrator.py`.

### Phase 4 — Builder plumbing (sandbox side) [CHECKPOINT — DONE]
- `bwrap_sandbox.run()` threads the runtime map into `build()`:
  `--setenv STDIO_SOCKET_PATH=<numbered env>` +
  `--bind <host_path> /run/<name>` per entry.
- Monkeypatch-bwrap test asserting env var + bind-mount presence.
  **DONE** — `test_run_wire_single_socket_mount` (single-entry
  `STDIO_SOCKET_PATH`) + `test_run_wire_numbered_socket_mounts`
  (`STDIO_SOCKET_PATH1/2`) assert the `--setenv`/`--bind` args in the built
  command. Checkpoint: 20 orchestrator tests pass, ruff clean.

### Phase 5 — `stdio_stub.py` env-var input [CHECKPOINT — DONE]
- Read socket path from `STDIO_SOCKET_PATH` env var as an alternative to
  `--socket` (still connect a single socket). Test.
- **DONE** — `--socket` remains optional; exactly one of `--socket`/env
  required (`"no socket address"` if neither). Added to
  `tests/test_stdio_stub.py`: `test_stub_reads_socket_from_env` (env path read,
  connect-fail proves env used), `test_stub_requires_address_or_env` (neither
  set → exit 1, `"no socket address"`), `test_stub_socket_flag_precedes_env`
  (--socket wins over a stale STDIO_SOCKET_PATH via a real server). Checkpoint:
  259 passed, ruff clean (only the 2 e2e tests remain red pending Phase 6).

### Phase 6 — Docs + e2e + verify [FINAL — DONE]
- README Orchestration section + default `sandbox.yaml` example
  (`track-socket: true` + `${STDIO_SOCKET_PATH}`).
- e2e (real bwrap): socket **generated**, no hardcoded path in config; data
  flows both ways; socket unlinked after clean stop.
- Full verify + commit specific files only.

## Files touched
- `src/agent_nook/config/config.py` (OrchestrationEntry)
- `src/agent_nook/config/loader.py` (_parse_orchestration)
- `src/agent_nook/orchestrator.py` (start / wait_ready / _is_ready)
- `src/agent_nook/socket_factory.py` (new, flat — see Phase 2 NOTE)
- `src/agent_nook/sandbox/builder.py` (setenv + bind-mount)
- `src/agent_nook/sandbox/bwrap_sandbox.py` (runtime map plumbing)
- `src/agent_nook/stdio_stub.py` (STDIO_SOCKET_PATH env)
- `tests/config/test_orchestration.py`, `tests/test_orchestrator.py`
- `tests/integration/test_orchestration_e2e.py`
- `README.md`, `src/agent_nook/config/sandbox.yaml`
