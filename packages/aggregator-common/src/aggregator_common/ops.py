from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aggregator_common.models import Article, Source
from aggregator_common.state import ArticleStatus


def pipeline_status(session: Session) -> dict[str, Any]:
    """Return article counts per status, in-flight count, and source counts."""
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

    return {
        "article_counts": status_counts,
        "in_flight": in_flight,
        "sources": {"enabled": enabled_count, "disabled": disabled_count},
    }


def list_stuck(session: Session, lease_seconds: int) -> list[dict[str, Any]]:
    """Return articles with a stale claim older than lease_seconds, including source_name."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(seconds=lease_seconds)
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
    return [
        {
            "id": row.id,
            "status": row.status,
            "claimed_by": row.claimed_by,
            "claimed_at": row.claimed_at,
            "source_name": row.source_name,
        }
        for row in session.execute(stmt).all()
    ]


def list_failures(
    session: Session,
    *,
    stage: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return failed articles joined with source name.

    stage must be None, 'processor', or 'summarize_rank'.
    """
    if stage == "processor":
        statuses = [ArticleStatus.failed_processing.value]
    elif stage == "summarize_rank":
        statuses = [ArticleStatus.failed_ranking.value]
    elif stage is None:
        statuses = [ArticleStatus.failed_processing.value, ArticleStatus.failed_ranking.value]
    else:
        raise ValueError(f"stage must be None, 'processor', or 'summarize_rank'; got {stage!r}")

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
    return [
        {
            "id": row.id,
            "status": row.status,
            "retry_count": row.retry_count,
            "last_error": row.last_error,
            "source_name": row.source_name,
        }
        for row in session.execute(stmt).all()
    ]
