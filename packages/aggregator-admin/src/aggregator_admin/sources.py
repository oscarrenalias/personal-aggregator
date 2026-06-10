from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import typer
from sqlalchemy.exc import IntegrityError

from aggregator_common.db import get_session
from aggregator_common.models import Source

from .output import json_or_table

sources_app = typer.Typer(help="Manage feed sources.")

_LIST_COLS = ["id", "name", "url", "enabled", "interval", "next_check_at", "consecutive_failures"]
_SHOW_COLS = _LIST_COLS + ["last_error", "etag", "last_modified"]


def _source_to_row(source: Source) -> dict:
    return {
        "id": source.id,
        "name": source.name,
        "url": source.feed_url,
        "enabled": source.enabled,
        "interval": source.refresh_interval_seconds,
        "next_check_at": source.next_check_at,
        "consecutive_failures": source.consecutive_failures,
        "last_error": source.last_error,
        "etag": source.etag,
        "last_modified": source.last_modified,
    }


@sources_app.command("list")
def list_sources(
    enabled: Optional[bool] = typer.Option(None, "--enabled/--disabled", help="Filter by enabled state."),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List all feed sources."""
    with get_session() as session:
        q = session.query(Source)
        if enabled is not None:
            q = q.filter(Source.enabled == enabled)
        sources = q.order_by(Source.id).all()
        rows = [_source_to_row(s) for s in sources]
    json_or_table(rows, _LIST_COLS, as_json=as_json)


@sources_app.command("show")
def show_source(
    source_id: int = typer.Argument(..., help="Source ID."),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Show full details for a source."""
    with get_session() as session:
        source = session.get(Source, source_id)
        if source is None:
            typer.echo(f"Error: source {source_id} not found.", err=True)
            raise typer.Exit(code=1)
        row = _source_to_row(source)
    json_or_table([row], _SHOW_COLS, as_json=as_json)


@sources_app.command("add")
def add_source(
    name: str = typer.Option(..., "--name", "-n", help="Human-readable feed name."),
    url: str = typer.Option(..., "--url", "-u", help="Feed URL."),
    interval: int = typer.Option(3600, "--interval", help="Refresh interval in seconds."),
    priority: int = typer.Option(0, "--priority", "-p", help="Feed priority (higher = more important)."),
    disabled: bool = typer.Option(False, "--disabled", help="Create the source in a disabled state."),
) -> None:
    """Add a new feed source."""
    source = Source(
        name=name,
        feed_url=url,
        enabled=not disabled,
        refresh_interval_seconds=interval,
        priority=priority,
    )
    try:
        with get_session() as session:
            session.add(source)
            session.flush()
            new_id = source.id
    except IntegrityError:
        typer.echo(f"Error: a source with URL '{url}' already exists.", err=True)
        raise typer.Exit(code=1)
    typer.echo(new_id)


def _get_source_or_exit(session, source_id: int) -> Source:
    source = session.get(Source, source_id)
    if source is None:
        typer.echo(f"Error: source {source_id} not found.", err=True)
        raise typer.Exit(code=1)
    return source


@sources_app.command("enable")
def enable_source(
    source_id: int = typer.Argument(..., help="Source ID."),
) -> None:
    """Enable a source, reset failure count, and schedule it for immediate check."""
    with get_session() as session:
        source = _get_source_or_exit(session, source_id)
        source.enabled = True
        source.consecutive_failures = 0
        source.next_check_at = datetime.now(timezone.utc)
    typer.echo(f"Source {source_id} enabled.")


@sources_app.command("disable")
def disable_source(
    source_id: int = typer.Argument(..., help="Source ID."),
) -> None:
    """Disable a source."""
    with get_session() as session:
        source = _get_source_or_exit(session, source_id)
        source.enabled = False
    typer.echo(f"Source {source_id} disabled.")


@sources_app.command("set-interval")
def set_interval(
    source_id: int = typer.Argument(..., help="Source ID."),
    seconds: int = typer.Argument(..., help="Refresh interval in seconds."),
) -> None:
    """Update the refresh interval for a source."""
    with get_session() as session:
        source = _get_source_or_exit(session, source_id)
        source.refresh_interval_seconds = seconds
    typer.echo(f"Source {source_id} interval set to {seconds}s.")


@sources_app.command("refresh-now")
def refresh_now(
    source_id: int = typer.Argument(..., help="Source ID."),
) -> None:
    """Schedule a source for immediate retrieval on the next poll cycle."""
    with get_session() as session:
        source = _get_source_or_exit(session, source_id)
        source.next_check_at = datetime.now(timezone.utc)
    typer.echo(f"Source {source_id} scheduled for immediate refresh.")
