"""Shared presentation utilities: table rendering, JSON output, and confirmation."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
_error_console = Console(stderr=True)


def json_default(obj: Any) -> Any:
    """JSON serializer for types not handled by the standard json module."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


def render_table(rows: list[dict[str, Any]], columns: list[str]) -> None:
    """Render a list of dicts as a Rich table with the given column order."""
    table = Table(show_header=True, header_style="bold")
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*[str(row.get(col, "")) for col in columns])
    console.print(table)


def render_kv_table(row: dict[str, Any]) -> None:
    """Render a single dict as a two-column field/value Rich table."""
    table = Table(show_header=True, header_style="bold")
    table.add_column("Field")
    table.add_column("Value")
    for key, val in row.items():
        table.add_row(key, "" if val is None else str(val))
    console.print(table)


def json_or_table(
    rows: list[dict[str, Any]],
    columns: list[str],
    *,
    as_json: bool,
) -> None:
    """Print rows as JSON when as_json is True, otherwise render as a Rich table."""
    if as_json:
        typer.echo(json.dumps(rows, default=json_default))
    else:
        render_table(rows, columns)


def error_panel(message: str) -> None:
    """Print a Rich error panel to stderr."""
    _error_console.print(Panel(str(message), title="Error", border_style="red"))


def confirm(*, yes: bool, prompt: str = "Continue?") -> None:
    """Uniform confirmation gate for destructive commands.

    --yes flag: proceed silently.
    TTY stdin: prompt interactively and abort on anything other than 'y'/'yes'.
    Non-interactive without --yes: exit non-zero without acting.
    """
    if yes:
        return
    if sys.stdin.isatty():
        answer = typer.prompt(f"{prompt} [y/N]", default="N")
        if answer.strip().lower() not in ("y", "yes"):
            raise typer.Exit(code=1)
    else:
        typer.echo(
            "Error: non-interactive session — pass --yes to confirm destructive action.",
            err=True,
        )
        raise typer.Exit(code=1)
