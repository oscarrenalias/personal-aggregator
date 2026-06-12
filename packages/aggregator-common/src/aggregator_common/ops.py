from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aggregator_common import brief_claim as _brief_claim
from aggregator_common import claim as _claim
from aggregator_common.models import Article, Source
from aggregator_common.state import ArticleStatus, can_transition


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


_FAILED_TO_PENDING: dict[ArticleStatus, ArticleStatus] = {
    ArticleStatus.failed_processing: ArticleStatus.pending_processing,
    ArticleStatus.failed_ranking: ArticleStatus.pending_ranking,
}


def reap_stale_claims(session: Session, lease_seconds: int) -> dict[str, int]:
    """Release stale claims for both articles and briefs; returns per-kind released counts."""
    now = datetime.now(tz=timezone.utc)
    articles_released = _claim.reap_stale_claims(session, lease_seconds, now)
    briefs_released = _brief_claim.reap_stale_brief_claims(session, lease_seconds, now)
    return {"articles_released": articles_released, "briefs_released": briefs_released}


def retry_failed(
    session: Session,
    *,
    stage: Optional[str] = None,
    article_id: Optional[int] = None,
) -> dict[str, int]:
    """Reset failed articles to their pending target status.

    stage must be None, 'processor', or 'summarize_rank'.
    Raises ValueError for unknown stage or disallowed transitions.
    """
    if stage == "processor":
        statuses = [ArticleStatus.failed_processing]
    elif stage == "summarize_rank":
        statuses = [ArticleStatus.failed_ranking]
    elif stage is None:
        statuses = [ArticleStatus.failed_processing, ArticleStatus.failed_ranking]
    else:
        raise ValueError(f"stage must be None, 'processor', or 'summarize_rank'; got {stage!r}")

    stmt = select(Article).where(Article.status.in_([s.value for s in statuses]))
    if article_id is not None:
        stmt = stmt.where(Article.id == article_id)

    articles = list(session.scalars(stmt).all())
    retried = 0
    for article in articles:
        current = ArticleStatus(article.status)
        target = _FAILED_TO_PENDING[current]
        if not can_transition(current, target):
            raise ValueError(f"Invalid transition: {current!r} → {target!r}")
        article.status = target
        article.claimed_by = None
        article.claimed_at = None
        article.last_error = None
        article.retry_count = 0
        article.next_retry_at = None
        retried += 1

    session.flush()
    return {"retried": retried}


def rerank(
    session: Session,
    *,
    article_id: Optional[int] = None,
    all_ready: bool = False,
    failed_only: bool = False,
) -> dict[str, int]:
    """Transition target articles to pending_ranking.

    Raises ValueError for disallowed transitions.
    """
    if article_id is not None:
        article = session.get(Article, article_id)
        articles: list[Article] = [article] if article is not None else []
    elif all_ready:
        articles = list(
            session.scalars(select(Article).where(Article.status == ArticleStatus.ready.value)).all()
        )
    elif failed_only:
        articles = list(
            session.scalars(
                select(Article).where(Article.status == ArticleStatus.failed_ranking.value)
            ).all()
        )
    else:
        return {"reranked": 0}

    reranked = 0
    for article in articles:
        current = ArticleStatus(article.status)
        target = ArticleStatus.pending_ranking
        if not can_transition(current, target):
            raise ValueError(f"Invalid transition: {current!r} → {target!r}")
        article.status = target
        article.claimed_by = None
        article.claimed_at = None
        if failed_only:
            article.retry_count = 0
            article.next_retry_at = None
            article.last_error = None
        reranked += 1

    session.flush()
    return {"reranked": reranked}
