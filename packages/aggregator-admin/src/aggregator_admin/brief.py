from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import typer
from sqlalchemy import select

from aggregator_common.db import get_session
from aggregator_common.models import Brief, BriefTopic

from .output import console, json_default, json_or_table, render_kv_table

brief_app = typer.Typer(help="Manage daily briefs.")

_LIST_COLUMNS = ["id", "status", "origin", "generated_at", "headline"]


def _brief_to_dict(brief: Brief, include_topics: bool = False) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": brief.id,
        "status": brief.status,
        "origin": brief.origin,
        "period_start": brief.period_start,
        "period_end": brief.period_end,
        "generated_at": brief.generated_at,
        "model": brief.model,
        "headline": brief.headline,
        "intro": brief.intro,
        "error": brief.error,
        "created_at": brief.created_at,
        "updated_at": brief.updated_at,
    }
    if include_topics:
        d["topics"] = [
            {
                "position": t.position,
                "headline": t.headline,
                "what_happened": t.what_happened,
                "why_it_matters": t.why_it_matters,
                "historical_context": t.historical_context,
                "refs": t.topic_refs,
            }
            for t in brief.topics
        ]
    return d


@brief_app.command("generate")
def generate_brief() -> None:
    """Insert a pending brief for today and queue it for generation."""
    now = datetime.now(tz=timezone.utc)
    period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    period_end = now.replace(hour=23, minute=59, second=59, microsecond=0)

    brief = Brief(
        status="pending",
        origin="manual",
        period_start=period_start,
        period_end=period_end,
    )
    with get_session() as session:
        session.add(brief)
        session.flush()
        brief_id = brief.id

    typer.echo(f"Brief {brief_id} created (status=pending, origin=manual, period={period_start.date()}).")


@brief_app.command("show")
def show_brief(
    brief_id: int = typer.Argument(
        None,
        help="Brief ID. Defaults to the latest ready brief.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Show headline, intro, and topics for a brief (default: latest ready)."""
    with get_session() as session:
        if brief_id is not None:
            brief = session.get(Brief, brief_id)
            if brief is None:
                typer.echo(f"Error: brief {brief_id} not found.", err=True)
                raise typer.Exit(code=1)
        else:
            stmt = (
                select(Brief)
                .where(Brief.status == "ready")
                .order_by(Brief.generated_at.desc())
                .limit(1)
            )
            brief = session.scalars(stmt).first()
            if brief is None:
                typer.echo("No ready brief found.", err=True)
                raise typer.Exit(code=1)

        # Load topics while session is open
        topics = list(brief.topics)

        if as_json:
            data = _brief_to_dict(brief, include_topics=True)
            typer.echo(json.dumps(data, default=json_default))
            return

        # Human-readable rendering
        console.print(f"[bold]Brief {brief.id}[/bold]  status={brief.status}  origin={brief.origin}")
        if brief.generated_at:
            console.print(f"Generated: {brief.generated_at.isoformat()}")
        console.print()

        if brief.headline:
            console.print(f"[bold]{brief.headline}[/bold]")
            console.print()

        if brief.intro:
            console.print(brief.intro)
            console.print()

        if topics:
            console.print(f"[bold]Topics ({len(topics)})[/bold]")
            for t in topics:
                console.print(f"\n[underline]{t.position}. {t.headline}[/underline]")
                console.print(f"  What happened: {t.what_happened}")
                console.print(f"  Why it matters: {t.why_it_matters}")
                if t.historical_context:
                    console.print(f"  Context: {t.historical_context}")
        else:
            console.print("(no topics)")


@brief_app.command("list")
def list_briefs(
    limit: int = typer.Option(10, "--limit", help="Maximum number of results."),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List recent briefs with id, status, generated_at, and headline."""
    with get_session() as session:
        stmt = (
            select(Brief)
            .order_by(Brief.created_at.desc())
            .limit(limit)
        )
        rows = [
            {
                "id": b.id,
                "status": b.status,
                "origin": b.origin,
                "generated_at": b.generated_at,
                "headline": b.headline,
            }
            for b in session.scalars(stmt).all()
        ]

    json_or_table(rows, _LIST_COLUMNS, as_json=as_json)
