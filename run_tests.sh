#!/bin/bash

# This is convenient, as it allows not to install the package to run the
# test suite and might also remind the AI how to do it

export PYTHONPATH=$PYTHONPATH:$(pwd)/src

# The autouse ``xdg_config_dir`` session fixture (tests/conftest.py) gives
# every test and every pytest-xdist worker a writable XDG state, so unit
# tests that redirect child output to the log do not hit the read-only
# real ~/.local path. No manual XDG_* setup is needed here.

# Phase 1: the ~260 unit tests are isolated (tmp_path/monkeypatch, pid-based
# socket names, ephemeral ports, per-process logger) and safe under
# pytest-xdist. Parallelize them; xdist scales them ~3x.
# Phase 2: integration tests are bwrap-heavy and share one session-scoped
# wheel build + pipx env. Keep them serial so the suite stays exactly as
# reliable as the original full-suite run (no per-worker duplicate build,
# no shared dist/ race).
python3 -m pytest tests/ -q -m "not integration" -n auto
python3 -m pytest tests/integration tests/config/test_orchestration.py -q -m integration
