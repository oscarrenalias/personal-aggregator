"""Shared retention helpers for the janitor service.

Each function accepts a caller-supplied session and returns a deleted-row count.
None of them commit — callers are responsible for commit/rollback.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from aggregator_common.models import Article, Brief, LlmCall, Thread, ThreadMembership


def purge_expired_articles(session: Session, retention_days: int) -> int:
    """Delete expired, unsaved articles that are not in any active or dormant thread.

    Deletes ThreadMembership rows before Article rows to satisfy the FK constraint
    (thread_memberships.article_id has no ON DELETE CASCADE).  Articles that belong
    to active or dormant threads are never touched; is_saved=True articles are never
    touched; read status is not considered.

    Returns the number of articles deleted.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    # Article ids that are members of at least one active or dormant thread.
    protected_sq = (
        select(ThreadMembership.article_id)
        .join(Thread, ThreadMembership.thread_id == Thread.id)
        .where(Thread.status.in_(["active", "dormant"]))
        .scalar_subquery()
    )

    eligible_ids: list[int] = list(
        session.execute(
            select(Article.id).where(
                Article.retrieved_at < cutoff,
                Article.is_saved == False,  # noqa: E712
                ~Article.id.in_(protected_sq),
            )
        ).scalars().all()
    )

    if not eligible_ids:
        return 0

    # Remove orphaned/archived-thread membership rows first to avoid FK violations.
    session.execute(
        delete(ThreadMembership).where(ThreadMembership.article_id.in_(eligible_ids))
    )

    session.execute(delete(Article).where(Article.id.in_(eligible_ids)))
    return len(eligible_ids)


def purge_expired_threads(session: Session, retention_days: int) -> int:
    """Delete threads whose last_updated is older than the retention window.

    ThreadMembership rows are removed via DB-level CASCADE
    (thread_memberships.thread_id has ON DELETE CASCADE).  Underlying articles
    are never touched.  Returns the number of threads deleted.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    expired_ids: list[int] = list(
        session.execute(
            select(Thread.id).where(Thread.last_updated < cutoff)
        ).scalars().all()
    )

    if not expired_ids:
        return 0

    session.execute(delete(Thread).where(Thread.id.in_(expired_ids)))
    return len(expired_ids)


def purge_expired_briefs(session: Session, retention_days: int) -> int:
    """Delete briefs whose created_at is older than the retention window.

    BriefTopic rows are removed via DB-level CASCADE
    (brief_topics.brief_id has ON DELETE CASCADE).
    Returns the number of briefs deleted.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    expired_ids: list[int] = list(
        session.execute(
            select(Brief.id).where(Brief.created_at < cutoff)
        ).scalars().all()
    )

    if not expired_ids:
        return 0

    session.execute(delete(Brief).where(Brief.id.in_(expired_ids)))
    return len(expired_ids)


def purge_expired_llm_calls(session: Session, retention_days: int) -> int:
    """Delete llm_calls rows older than the retention window.

    Returns the number of rows deleted.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    expired_ids: list = list(
        session.execute(
            select(LlmCall.id).where(LlmCall.created_at < cutoff)
        ).scalars().all()
    )

    if not expired_ids:
        return 0

    session.execute(delete(LlmCall).where(LlmCall.id.in_(expired_ids)))
    return len(expired_ids)
