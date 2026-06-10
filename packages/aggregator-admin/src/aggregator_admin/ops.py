from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import typer
from sqlalchemy import func, select

from aggregator_common.config import Settings
from aggregator_common.db import get_session
from aggregator_common.models import Article, Source
from aggregator_common.state import ArticleStatus

from .output import json_or_table, render_table

ops_app = typer.Typer(help="Pipeline diagnostics and maintenance.")


@ops_app.command("status")
def status_cmd(
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Show article counts by status, in-flight count, and source counts."""
    with get_session() as session:
        status_counts: dict[str, int] = {}
        for s in ArticleStatus:
            count = session.scalar(
                select(func.count()).select_from(Article).where(Article.status == s.value)
            ) or 0
            status_counts[s.value] = count

        in_flight = session.scalar(
            select(func.count()).select_from(Article).where(Article.claimed_at.is_not(None))
        ) or 0

        enabled_count = session.scalar(
            select(func.count()).select_from(Source).where(Source.enabled.is_(True))
        ) or 0
        disabled_count = session.scalar(
            select(func.count()).select_from(Source).where(Source.enabled.is_(False))
        ) or 0

    if as_json:
        typer.echo(
            json.dumps(
                {
                    "article_counts": status_counts,
                    "in_flight": in_flight,
                    "sources": {"enabled": enabled_count, "disabled": disabled_count},
                }
            )
        )
    else:
        article_rows = [{"status": k, "count": str(v)} for k, v in status_counts.items()]
        article_rows.append({"status": "in_flight (claimed)", "count": str(in_flight)})
        render_table(article_rows, ["status", "count"])
        render_table(
            [
                {"state": "enabled", "count": str(enabled_count)},
                {"state": "disabled", "count": str(disabled_count)},
            ],
            ["state", "count"],
        )


@ops_app.command("stuck")
def stuck_cmd(
    lease_seconds: Optional[int] = typer.Option(
        None,
        "--lease-seconds",
        help="Staleness threshold in seconds (default: CLAIM_LEASE_SECONDS from config).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List articles with a stale claim older than the lease threshold."""
    threshold = lease_seconds if lease_seconds is not None else Settings().claim_lease_seconds
    cutoff = datetime.now(tz=timezone.utc) - timedelta(seconds=threshold)

    with get_session() as session:
        stmt = (
            select(
                Article.id,
                Article.status,
                Article.claimed_by,
                Article.claimed_at,
                Source.name.label("source_name"),
            )
            .join(Source, Article.source_id == Source.id)
            .where(Article.claimed_at.is_not(None))
            .where(Article.claimed_at < cutoff)
            .order_by(Article.claimed_at)
        )
        rows = [
            {
                "id": row.id,
                "status": row.status,
                "claimed_by": row.claimed_by,
                "claimed_at": row.claimed_at,
                "source": row.source_name,
            }
            for row in session.execute(stmt).all()
        ]

    json_or_table(rows, ["id", "status", "claimed_by", "claimed_at", "source"], as_json=as_json)


@ops_app.command("failures")
def failures_cmd(
    stage: Optional[str] = typer.Option(
        None,
        "--stage",
        help="Filter by stage: processing or ranking. Omit to show all failures.",
    ),
    limit: int = typer.Option(50, "--limit", help="Maximum number of rows to return."),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List failed articles with last_error, retry_count, and source name."""
    if stage == "processing":
        statuses = [ArticleStatus.failed_processing.value]
    elif stage == "ranking":
        statuses = [ArticleStatus.failed_ranking.value]
    elif stage is None:
        statuses = [ArticleStatus.failed_processing.value, ArticleStatus.failed_ranking.value]
    else:
        typer.echo(f"Error: --stage must be 'processing' or 'ranking', got {stage!r}", err=True)
        raise typer.Exit(code=1)

    with get_session() as session:
        stmt = (
            select(
                Article.id,
                Article.status,
                Article.retry_count,
                Article.last_error,
                Source.name.label("source_name"),
            )
            .join(Source, Article.source_id == Source.id)
            .where(Article.status.in_(statuses))
            .order_by(Article.updated_at.desc())
            .limit(limit)
        )
        rows = [
            {
                "id": row.id,
                "status": row.status,
                "retry_count": row.retry_count,
                "last_error": row.last_error,
                "source": row.source_name,
            }
            for row in session.execute(stmt).all()
        ]

    json_or_table(rows, ["id", "status", "retry_count", "last_error", "source"], as_json=as_json)
