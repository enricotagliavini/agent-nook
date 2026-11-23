# Plan: Secrets support

**Status:** Draft — pending review
**Branch:** `feature_secrets` (currently at `main` / `afd488c`, clean working tree)

## Problem
No way to pass secrets (API tokens, keys, credentials) into the sandbox without
either putting them on the command line (`--setenv` → visible in `ps` and logged)
or bind-mounting a host file directly (exposes host path/permissions, can't
target a not-yet-existing path, no snapshot).

## Approach (locked)
- Materialize each secret on the host to a private temp file (`0600`) in a
  per-run `0700` dir under `$XDG_RUNTIME_DIR/agent-nook` (→ `/tmp` fallback,
  exactly like socket generation).
- `--ro-bind` each temp file to `/run/secrets/<name>` inside the sandbox.
- For env exposure (opt-in), a thin **Python wrapper** (`secrets_env.py`,
  bundled like `stdio_stub.py`) is bind-mounted and runs the command; it reads
  the secret files via a manifest and `exec`s. No shell → no escaping issues.
- Remove the temp dir on teardown (`chmod` before `rmtree`).

## Locked decisions
- **Python wrapper + manifest** (not shell). Env values read as text
  (utf-8, `surrogateescape`); env vars can't carry NUL/binary anyway.
- **No custom `target`** — every secret lands at `/run/secrets/<name>`.
- **Temp dir** under `$XDG_RUNTIME_DIR/agent-nook` with `/tmp` fallback
  (`socket_factory.generate` pattern).
- **Flat schema** — no `source:` wrapper; `command:` / `file:` are flat and
  mutually exclusive.
- **Env exposure opt-in** — `env:` set → export that var; absent → file-only.
- **Command source runs without a shell** — tokenized with `shlex` (single
  string), consistent with orchestration; stdout captured as bytes (preserves
  trailing newlines, unlike `$(cat file)`).

## Config schema
```yaml
secrets:
  - name: gh-token                 # required; filename-safe -> /run/secrets/gh-token
    command: "gh auth token"       # host command; stdout = secret (mutually exclusive with file:)
    #file: ~/.config/gh/hosts.yml
    env: GH_TOKEN                  # optional; export inside sandbox as GH_TOKEN
```
- `name`: required, non-empty; `Path(name).name` strips any path components.
- exactly one of `file` / `command` (both or neither → validation error).
- `env`: optional str; if omitted the secret is file-only.

## CLI
- `--secret NAME=SOURCE` (repeatable): `SOURCE` is a host file path, or
  `cmd:<command>`. e.g. `--secret gh-token=cmd:gh auth token`,
  `--secret id=~/.ssh/id_rsa`.
- `--secret-env NAME=VAR` (repeatable): set the env var name for a `--secret`
  (overrides the default).
- (No `--secret-target` — dropped.)

## How it's wired (mirrors the orchestration / socket feature)
- `config.py`: `Secret` frozen dataclass
  (`name`, `source_file`, `source_command`, `env_var`) +
  `SandboxConfig.secrets: list[Secret]`.
- `loader.py`: `_FIELDS["secrets"] = "list[dict]"`; `_parse_secrets()` (Layer 5,
  mirrors `_parse_orchestration`: known keys, exactly-one-source, sanitization,
  env derivation, unknown-key rejection).
- `sandbox/secret.py` (new): `SecretMount` (name, host_path, target, env_var),
  `SecretsBundle` (per-run temp dir + mounts + manifest path + `cleanup()` +
  `needs_env`), `prepare_secrets(config.secrets) -> SecretsBundle` (materialize
  + write manifest).
- `sandbox/secrets_env.py` (new): the wrapper.
- `sandbox/builder.py`: `--ro-bind` per secret; `--dir /run/secrets` (if not
  already a mount) when any secret; if `needs_env`, bind manifest + wrapper and
  prepend the wrapper to the command.
- `sandbox/bwrap_sandbox.py`: `prepare_secrets` → pass `secrets=` to builder;
  cleanup in `finally`.
- `__main__.py`: `--secret` / `--secret-env` + merge in `_merge_cli_overrides`.
- `config/sandbox.yaml` + `README.md`: docs / examples.

## Phases (checkpoint = `./run_tests.sh` + `ruff check src/ --fix` + `ruff format src/`)

### Phase 1 — Config schema: `Secret` + `_parse_secrets` [CHECKPOINT]
- `config.py`: `Secret` frozen dataclass + `SandboxConfig.secrets`.
- `loader.py`: `_FIELDS["secrets"]="list[dict]"`; `_parse_secrets()` mirrors
  `_parse_orchestration` (known keys `name`/`file`/`command`/`env`; exactly one
  of `file`/`command`; `Path(name).name` sanitization; `env_var` derived from
  name when `env` absent: uppercased, non-`[A-Za-z0-9]`→`_`, `_` prefix if it
  starts with a digit; unknown per-entry keys rejected).
- `tests/config/test_secrets.py`: name required; exactly-one-of file/command
  (both + neither → error); unknown-key rejection; sanitization; env derivation
  vs explicit; bad types.
- README + default `sandbox.yaml` comment update.

### Phase 2 — Secret preparation: `secret.py` [CHECKPOINT]
- New `sandbox/secret.py`:
  - `prepare_secrets(secrets) -> SecretsBundle`: create a per-run
    `tempfile.mkdtemp(prefix="agent-nook-secrets-", dir=<base>)` (base =
    `$XDG_RUNTIME_DIR/agent-nook` or `/tmp`, ensured `0700`); for each secret
    read content (file → `Path.read_bytes()`, command →
    `subprocess.run(shlex.split(cmd), check=True, capture_output=True).stdout`);
    write `<tempdir>/<name>` mode `0600`; write manifest
    `<tempdir>/secrets.env` with `ENV_VAR=/run/secrets/<name>` lines (`0600`).
  - `SecretsBundle`: `temp_dir`, `mounts: list[SecretMount]`,
    `manifest_host_path`, `wrapper_host_path`, `cleanup()` (chmod before
    `rmtree`), `needs_env` (any mount with `env_var` set).
  - On materialize failure, clean up the partial temp dir before re-raising.
- `tests/test_secrets.py`: file + command sources; trailing-newline fidelity;
  `0600` file / `0700` dir perms; cleanup removes dir + files; XDG base +
  `/tmp` fallback; command non-zero exit → error.

### Phase 3 — Wrapper: `secrets_env.py` [CHECKPOINT]
- New bundled `sandbox/secrets_env.py` (stdlib-only, like `stdio_stub.py`):
  read `/run/agent_nook_secrets.env`, `os.environ[VAR] = Path(path).read_text()`
  per line, then `os.execvpe(cmd, sys.argv[1:], os.environ)`.
- `tests/test_secrets_env.py`: real run (guarded by python availability) with a
  manifest + fake secret files → assert env exported and command exec'd; error
  paths (unreadable secret).

### Phase 4 — Builder plumbing [CHECKPOINT]
- `builder.build(..., secrets=None)`: `--ro-bind` per secret; `--dir /run/secrets`
  (skipped if `/run/secrets` is already a mount) when any secret; if `needs_env`,
  `--ro-bind` manifest + `--bind` wrapper and prepend the wrapper to the command.
- `bwrap_sandbox.run()`: `prepare_secrets` before `build`; `secrets_bundle.cleanup()`
  in `finally` alongside `orchestrator.stop()`.
- `tests/sandbox/test_builder_secrets.py` (monkeypatch-bwrap): `--ro-bind` per
  secret, `--dir /run/secrets`, manifest+wrapper binds, wrapper prepend, and
  none of the wrapper bits when no env secrets.

### Phase 5 — CLI [CHECKPOINT]
- `__main__.py`: `--secret NAME=SOURCE` (append) + `--secret-env NAME=VAR`
  (append); `_merge_cli_overrides` builds the `secrets` list from `--secret`
  (`cmd:` prefix → command source, else file) and applies `--secret-env` by name.
- CLI tests: parsing + merge (file vs `cmd:`), `--secret-env` applied.

### Phase 6 — Docs + e2e + verify [FINAL]
- README secrets section + default `sandbox.yaml` example.
- e2e (real bwrap): file secret appears at `/run/secrets/<name>` with correct
  content; command secret (`printf 'tok'`) works; env exposure
  (`env: GH_TOKEN`) → `printenv GH_TOKEN` inside the sandbox returns the value;
  temp dir removed after exit; secret value not present in the log.
- Full suite + `ruff check src/ --fix` + `ruff format src/`.

## Files touched
- New: `src/agent_nook/sandbox/secret.py`, `src/agent_nook/sandbox/secrets_env.py`
- Changed: `config/config.py`, `config/loader.py`, `config/sandbox.yaml`,
  `sandbox/builder.py`, `sandbox/bwrap_sandbox.py`, `__main__.py`, `README.md`
- Tests: `tests/config/test_secrets.py`, `tests/test_secrets.py`,
  `tests/test_secrets_env.py`, `tests/sandbox/test_builder_secrets.py`, CLI
  tests, `tests/integration/test_secrets_e2e.py`

## Notes / assumptions
- Secret values are read as **text** (utf-8) — fine for tokens / keys / PEM;
  env vars can't carry binary/NUL anyway.
- `--dir /run/secrets` is skipped if the config already mounts `/run/secrets`.
- The DEBUG bwrap log shows temp-file / target **paths** (inside the `0700` temp
  dir), not secret bytes.
