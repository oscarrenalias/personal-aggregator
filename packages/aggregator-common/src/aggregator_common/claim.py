from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import Article
from .state import ArticleStatus, can_transition, failure_status_for

_STATUS_TO_STAGE: dict[str, str] = {
    ArticleStatus.pending_processing: "processor",
    ArticleStatus.pending_ranking: "summarize_rank",
}


def claim_batch(
    session: Session,
    status: ArticleStatus,
    worker_id: str,
    limit: int,
    now: datetime,
) -> list[Article]:
    stmt = (
        select(Article)
        .where(Article.status == status)
        .where(or_(Article.next_retry_at.is_(None), Article.next_retry_at <= now))
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    articles = list(session.scalars(stmt).all())
    for article in articles:
        article.claimed_by = worker_id
        article.claimed_at = now
    session.flush()
    return articles


def complete(
    session: Session,
    article: Article,
    new_status: ArticleStatus,
) -> None:
    current = ArticleStatus(article.status)
    if not can_transition(current, new_status):
        raise ValueError(f"Invalid transition: {article.status!r} → {new_status!r}")
    article.status = new_status
    article.claimed_by = None
    article.claimed_at = None
    article.next_retry_at = None
    session.flush()


def fail(
    session: Session,
    article: Article,
    error: str,
    max_retries: int,
    backoff: float,
    now: datetime,
) -> None:
    article.retry_count += 1
    article.last_error = error
    article.claimed_by = None
    article.claimed_at = None

    if article.retry_count >= max_retries:
        stage = _STATUS_TO_STAGE.get(ArticleStatus(article.status))
        if stage is None:
            raise ValueError(f"Cannot determine stage for status {article.status!r}")
        article.status = failure_status_for(stage)
        article.next_retry_at = None
    else:
        delay = backoff * (2 ** (article.retry_count - 1))
        article.next_retry_at = now + timedelta(seconds=delay)

    session.flush()


def reap_stale_claims(
    session: Session,
    lease_seconds: float,
    now: datetime,
) -> int:
    cutoff = now - timedelta(seconds=lease_seconds)
    stmt = (
        select(Article)
        .where(Article.claimed_at.is_not(None))
        .where(Article.claimed_at < cutoff)
        .with_for_update(skip_locked=True)
    )
    articles = list(session.scalars(stmt).all())
    for article in articles:
        article.claimed_by = None
        article.claimed_at = None
    session.flush()
    return len(articles)
