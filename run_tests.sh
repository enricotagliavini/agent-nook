#!/bin/bash

# This is convenient, as it allows not to install the package to run the
# test suite and might also remind the AI how to do it

export PYTHONPATH=$PYTHONPATH:$(pwd)/src

# The autouse ``xdg_config_dir`` session fixture (tests/conftest.py) gives
# every test and every pytest-xdist worker a writable XDG state (config,
# state and runtime dirs), so unit tests that redirect child output to the
# log do not hit the read-only real ~/.local path, and parallel track-socket
# runs never share a socket base. No manual XDG_* setup is needed here.

# Shared build/install tree, created fresh per run and removed on exit.
# The integration fixtures build the wheel + install pipx ONCE here (guarded
# by an fcntl lock) and every xdist worker reuses that read-only result, so
# there is no per-worker duplicate build and no shared-dir race. All
# pipx/wheel work stays inside this throwaway tree (never the real ~/.local).
export AGENT_NOOK_SHARED_BUILD="$(mktemp -d "${TMPDIR:-/tmp}/agent-nook-tests.XXXXXX")"
trap 'rm -rf "$AGENT_NOOK_SHARED_BUILD"' EXIT

# Phase 1: the ~260 unit tests are isolated (tmp_path/monkeypatch, pid-based
# socket names, ephemeral ports, per-process logger) and safe under
# pytest-xdist. Parallelize them; xdist scales them ~3x.
python3 -m pytest tests/ -q -m "not integration" -n auto

# Phase 2: integration tests are bwrap-heavy but parallel-safe: each test's
# writable dirs (tmp_path) and its XDG_RUNTIME_DIR are independent, and the
# wheel + pipx env are built and installed exactly once (lock-guarded) in
# $AGENT_NOOK_SHARED_BUILD, then shared read-only. Parallelize them too.
python3 -m pytest tests/integration tests/config/test_orchestration.py -q -m integration -n auto
