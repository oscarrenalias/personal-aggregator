from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from sqlalchemy.exc import IntegrityError

from aggregator_common import management
from aggregator_common.db import get_session
from aggregator_common.errors import ConflictError, NotFoundError
from aggregator_common.models import Article, Source

from .opml import build_opml, parse_opml
from .output import confirm, error_panel, json_or_table

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
    try:
        with get_session() as session:
            result = management.add_source(
                session,
                name=name,
                feed_url=url,
                refresh_interval_seconds=interval,
                priority=priority,
                enabled=not disabled,
            )
    except ConflictError as exc:
        error_panel(str(exc))
        raise typer.Exit(code=1)
    typer.echo(result["id"])


@sources_app.command("enable")
def enable_source(
    source_id: int = typer.Argument(..., help="Source ID."),
) -> None:
    """Enable a source, reset failure count, and schedule it for immediate check."""
    try:
        with get_session() as session:
            management.enable_source(session, source_id)
    except NotFoundError as exc:
        error_panel(str(exc))
        raise typer.Exit(code=1)
    typer.echo(f"Source {source_id} enabled.")


@sources_app.command("disable")
def disable_source(
    source_id: int = typer.Argument(..., help="Source ID."),
) -> None:
    """Disable a source."""
    try:
        with get_session() as session:
            management.disable_source(session, source_id)
    except NotFoundError as exc:
        error_panel(str(exc))
        raise typer.Exit(code=1)
    typer.echo(f"Source {source_id} disabled.")


@sources_app.command("set-interval")
def set_interval(
    source_id: int = typer.Argument(..., help="Source ID."),
    seconds: int = typer.Argument(..., help="Refresh interval in seconds."),
) -> None:
    """Update the refresh interval for a source."""
    try:
        with get_session() as session:
            management.set_source_interval(session, source_id, seconds)
    except NotFoundError as exc:
        error_panel(str(exc))
        raise typer.Exit(code=1)
    typer.echo(f"Source {source_id} interval set to {seconds}s.")


@sources_app.command("remove")
def remove_source(
    source_id: int = typer.Argument(..., help="Source ID."),
    force: bool = typer.Option(False, "--force", help="Cascade delete to associated articles."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """Delete a source.

    Refused when the source has associated articles unless --force is given.
    With --force all articles belonging to the source are deleted first.
    """
    try:
        with get_session() as session:
            source = session.get(Source, source_id)
            if source is None:
                raise NotFoundError(f"Source {source_id} not found.")
            article_count = session.query(Article).filter(Article.source_id == source_id).count()
            if article_count > 0 and not force:
                typer.echo(
                    f"Error: source {source_id} has {article_count} article(s). "
                    "Use --force to cascade the delete.",
                    err=True,
                )
                raise typer.Exit(code=1)
            confirm(
                yes=yes,
                prompt=f"Delete source {source_id} ('{source.name}')"
                + (f" and {article_count} article(s)?" if article_count > 0 else "?"),
            )
            management.remove_source(session, source_id)
    except NotFoundError as exc:
        error_panel(str(exc))
        raise typer.Exit(code=1)
    typer.echo(f"Source {source_id} deleted.")


@sources_app.command("refresh-now")
def refresh_now(
    source_id: int = typer.Argument(..., help="Source ID."),
) -> None:
    """Schedule a source for immediate retrieval on the next poll cycle."""
    try:
        with get_session() as session:
            management.refresh_source_now(session, source_id)
    except NotFoundError as exc:
        error_panel(str(exc))
        raise typer.Exit(code=1)
    typer.echo(f"Source {source_id} scheduled for immediate refresh.")


@sources_app.command("export-opml")
def export_opml(
    file: Optional[Path] = typer.Argument(None, help="Output file path (defaults to stdout)."),
) -> None:
    """Export all feed sources as an OPML file."""
    with get_session() as session:
        sources = session.query(Source).all()
        opml_text = build_opml(sources)

    if file is None:
        typer.echo(opml_text)
    else:
        try:
            file.write_text(opml_text, encoding="utf-8")
        except OSError as exc:
            typer.echo(f"Error: cannot write to '{file}': {exc}", err=True)
            raise typer.Exit(code=1)


@sources_app.command("import-opml")
def import_opml(
    file: Path = typer.Argument(..., help="Path to the OPML file."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview imports without making DB changes."),
    interval: int = typer.Option(3600, "--interval", help="Refresh interval in seconds for new sources."),
    disabled: bool = typer.Option(False, "--disabled", help="Import sources in a disabled state."),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Import feed sources from an OPML file."""
    try:
        text = file.read_text(encoding="utf-8")
    except FileNotFoundError:
        typer.echo(f"Error: file '{file}' not found.", err=True)
        raise typer.Exit(code=1)
    except OSError as exc:
        typer.echo(f"Error: cannot read '{file}': {exc}", err=True)
        raise typer.Exit(code=1)

    try:
        feeds = parse_opml(text)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    added: list[str] = []
    skipped: list[str] = []

    with get_session() as session:
        existing_urls: set[str] = {row[0] for row in session.query(Source.feed_url).all()}
        seen_in_batch: set[str] = set()

        for feed in feeds:
            url = feed.url
            if url in existing_urls or url in seen_in_batch:
                skipped.append(url)
                continue
            seen_in_batch.add(url)

            if not dry_run:
                sp = session.begin_nested()
                try:
                    session.add(Source(
                        name=feed.name,
                        feed_url=url,
                        enabled=not disabled,
                        refresh_interval_seconds=interval,
                    ))
                    session.flush()
                    sp.commit()
                    added.append(url)
                except IntegrityError:
                    sp.rollback()
                    skipped.append(url)
            else:
                added.append(url)

    total = len(added) + len(skipped)

    if as_json:
        typer.echo(json.dumps({"added": added, "skipped": skipped, "total": total}))
    else:
        prefix = "[dry-run] " if dry_run else ""
        for url in added:
            typer.echo(f"  {prefix}added: {url}")
        for url in skipped:
            typer.echo(f"  skipped: {url}")
        typer.echo(
            f"{prefix}Summary: {len(added)} added, {len(skipped)} skipped, {total} total"
        )
