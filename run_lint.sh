#!/bin/bash

# Lint and format the codebase, mirroring the CI lint job.
#
# Usage:
#   ./run_lint.sh          auto-fix safe lint issues, then format (developer flow)
#   ./run_lint.sh --check  check only, exit non-zero on any issue (used by CI)

set -e

if [ "${1:-}" = "--check" ]; then
    ruff check src/ --no-fix
    ruff format src/ --check
else
    ruff check src/ --fix
    ruff format src/
fi
