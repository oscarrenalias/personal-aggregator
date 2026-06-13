from __future__ import annotations

import json
from typing import Any, Optional

import typer
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from aggregator_common.db import get_session
from aggregator_common.management import enqueue_recluster
from aggregator_common.models import Thread, ThreadMembership

from .output import console, error_panel, json_default, json_or_table

clusters_app = typer.Typer(help="Manage article clusters (threads).")

_LIST_COLUMNS = ["id", "tier", "representative_title", "member_count", "last_updated"]
_TITLE_MAX_LEN = 60


def _truncate(text: str | None, max_len: int = _TITLE_MAX_LEN) -> str:
    if not text:
        return ""
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


@clusters_app.command("list")
def list_clusters(
    tier: Optional[str] = typer.Option(
        None, "--tier", help="Filter by tier (must_know, worth_tracking, deep_read, low_noise)."
    ),
    status: Optional[str] = typer.Option(
        None, "--status", help="Filter by status (active, dormant, archived)."
    ),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List threads with id, tier, representative_title, member_count, last_updated."""
    try:
        with get_session() as session:
            stmt = select(Thread).options(selectinload(Thread.members))
            if tier:
                stmt = stmt.where(Thread.tier == tier)
            if status:
                stmt = stmt.where(Thread.status == status)
            stmt = stmt.order_by(Thread.last_updated.desc())
            threads = session.scalars(stmt).all()
            rows: list[dict[str, Any]] = [
                {
                    "id": t.id,
                    "tier": t.tier or "",
                    "representative_title": _truncate(t.representative_title),
                    "member_count": sum(1 for m in t.members if not m.suppressed),
                    "last_updated": t.last_updated,
                }
                for t in threads
            ]
    except Exception as exc:
        error_panel(str(exc))
        raise typer.Exit(code=1)

    json_or_table(rows, _LIST_COLUMNS, as_json=as_json)


@clusters_app.command("show")
def show_cluster(
    thread_id: int = typer.Argument(..., help="Thread ID."),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Show full detail for a thread: summary, known_facts, member articles, scores."""
    try:
        with get_session() as session:
            thread = session.get(
                Thread,
                thread_id,
                options=[selectinload(Thread.members).selectinload(ThreadMembership.article)],
            )
            if thread is None:
                typer.echo(f"Error: thread {thread_id} not found.", err=True)
                raise typer.Exit(code=1)

            members_data: list[dict[str, Any]] = [
                {
                    "article_id": m.article_id,
                    "title": (
                        (m.article.clean_title or m.article.feed_title) if m.article else None
                    ),
                    "label": m.classification_label,
                    "confidence": m.confidence,
                    "reason": m.reason,
                    "assigned_at": m.assigned_at,
                }
                for m in thread.members
                if not m.suppressed
            ]

            thread_data: dict[str, Any] = {
                "id": thread.id,
                "representative_title": thread.representative_title,
                "status": thread.status,
                "tier": thread.tier,
                "tier_reason": thread.tier_reason,
                "rolling_summary": thread.rolling_summary,
                "known_facts": thread.known_facts,
                "first_seen": thread.first_seen,
                "last_updated": thread.last_updated,
                "relevance_score": thread.relevance_score,
                "novelty_score": thread.novelty_score,
                "importance_score": thread.importance_score,
                "diversity_score": thread.diversity_score,
                "time_sensitivity_score": thread.time_sensitivity_score,
                "confidence": thread.confidence,
                "members": members_data,
            }
    except typer.Exit:
        raise
    except Exception as exc:
        error_panel(str(exc))
        raise typer.Exit(code=1)

    if as_json:
        typer.echo(json.dumps(thread_data, default=json_default))
        return

    console.print(
        f"[bold]Thread {thread_data['id']}[/bold]"
        f"  status={thread_data['status']}"
        f"  tier={thread_data['tier'] or '—'}"
    )
    console.print(f"Title: {thread_data['representative_title']}")
    console.print()

    if thread_data["rolling_summary"]:
        console.print("[bold]Summary[/bold]")
        console.print(thread_data["rolling_summary"])
        console.print()

    known_facts = thread_data["known_facts"] or []
    if known_facts:
        console.print("[bold]Known Facts[/bold]")
        for fact in known_facts:
            console.print(f"  • {fact}")
        console.print()

    if thread_data["tier_reason"]:
        console.print(f"[bold]Tier Reason[/bold]: {thread_data['tier_reason']}")
        console.print()

    console.print("[bold]Scores[/bold]")
    console.print(
        f"  relevance={thread_data['relevance_score']}"
        f"  novelty={thread_data['novelty_score']}"
        f"  importance={thread_data['importance_score']}"
        f"  diversity={thread_data['diversity_score']}"
        f"  time_sensitivity={thread_data['time_sensitivity_score']}"
    )
    console.print()

    if members_data:
        console.print(f"[bold]Members ({len(members_data)})[/bold]")
        for m in members_data:
            label = m["label"] or "—"
            title = m["title"] or "(no title)"
            console.print(f"  [{label}] article={m['article_id']}  {title}")
    else:
        console.print("(no visible members)")


@clusters_app.command("recluster")
def recluster() -> None:
    """Enqueue a full recluster pass for the clustering worker."""
    try:
        with get_session() as session:
            enqueue_recluster(session)
    except Exception as exc:
        error_panel(str(exc))
        raise typer.Exit(code=1)

    typer.echo("Re-cluster cycle enqueued.")
