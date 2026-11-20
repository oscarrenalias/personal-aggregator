#!/usr/bin/env bash
# Single-executable wrapper so PROMPTFOO_PYTHON can point to a real file.
# promptfoo's Python provider requires PROMPTFOO_PYTHON to be an executable path,
# not a multi-word shell command — it uses execFile(), not exec().
#
# When a .venv created by `uv sync --all-packages --inexact` exists at the repo
# root we exec its Python interpreter directly, skipping the per-call uv resolve.
# Falls back to `uv run --all-packages python` when no venv is present.
# Note: openpyxl is NOT available via this wrapper — label_sheet.py is invoked
# separately via `uv run --all-packages --with openpyxl python` in the Makefile.

REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$REPO_ROOT" ]; then
  REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fi
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"

if [ -x "$VENV_PYTHON" ]; then
  exec "$VENV_PYTHON" "$@"
else
  exec uv run --all-packages python "$@"
fi
