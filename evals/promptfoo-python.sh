#!/usr/bin/env bash
# Single-executable wrapper so PROMPTFOO_PYTHON can point to a real file.
# promptfoo's Python provider requires PROMPTFOO_PYTHON to be an executable path,
# not a multi-word shell command — it uses execFile(), not exec().
exec uv run --all-packages --with openpyxl python "$@"
