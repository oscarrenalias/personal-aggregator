#!/usr/bin/env bash
# Run each workspace package's tests in its own pytest process.
#
# Why per-package: every package keeps its tests under packages/<pkg>/tests/ with
# its own conftest.py. A single `pytest` over the whole workspace assigns both the
# identical module name `tests.conftest`, which collides
# (ImportPathMismatchError / "plugin already registered"). Running each package
# separately keeps exactly one conftest in scope per process, and also limits
# testcontainers to one session-scoped Postgres at a time.
set -euo pipefail

cd "$(dirname "$0")/.."

shopt -s nullglob
dirs=(packages/*/tests)
if [ ${#dirs[@]} -eq 0 ]; then
  echo "No package test directories found under packages/*/tests" >&2
  exit 0
fi

for d in "${dirs[@]}"; do
  echo "===== pytest ${d} ====="
  # --all-packages installs every workspace member + its deps (e.g. alembic from
  # aggregator-common). Required because the root [project] (CI version source)
  # makes a plain `uv run`/`uv sync` install only the root, not the members.
  uv run --all-packages pytest "${d}"
done
