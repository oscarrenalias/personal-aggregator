import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aggregator_common.models import Article, Source
from aggregator_retriever.config import Settings
from aggregator_retriever.http import FetchResult
from aggregator_retriever.parse import NormalizedEntry

logger = logging.getLogger(__name__)


def insert_articles(
    session: Session,
    source_id: int,
    entries: Sequence[NormalizedEntry],
) -> int:
    """Insert articles, skipping duplicates. Returns count of new rows inserted."""
    if not entries:
        return 0

    now = datetime.now(tz=timezone.utc)
    values = [
        {
            "source_id": source_id,
            "dedup_key": e.dedup_key,
            "status": "pending_processing",
            "retrieved_at": now,
            "claimed_at": None,
            "claimed_by": None,
            "retry_count": 0,
            "raw_payload": e.raw_payload,
            "feed_title": e.feed_title,
            "feed_url": e.feed_url,
            "feed_summary": e.feed_summary,
            "feed_published_at": e.feed_published_at,
            "comments_url": e.comments_url,
        }
        for e in entries
    ]

    stmt = (
        insert(Article)
        .values(values)
        .on_conflict_do_nothing(constraint="uq_articles_source_dedup")
        .returning(Article.id)
    )
    result = session.execute(stmt)
    return len(result.fetchall())


def update_source_success(
    session: Session,
    source: Source,
    fetch_result: FetchResult,
) -> None:
    """Update source metadata after a successful fetch."""
    now = datetime.now(tz=timezone.utc)
    source.last_checked_at = now
    source.next_check_at = now + timedelta(seconds=source.refresh_interval_seconds)
    source.consecutive_failures = 0
    source.last_error = None

    # Only store etag/last_modified on 2xx; leave them unchanged on 304 Not Modified.
    if not fetch_result.not_modified:
        source.etag = fetch_result.etag
        source.last_modified = fetch_result.last_modified

    session.flush()


def update_source_failure(
    session: Session,
    source: Source,
    error_msg: str,
    settings: Settings,
) -> None:
    """Update source metadata after a failed fetch, applying exponential backoff with jitter."""
    source.consecutive_failures += 1
    source.last_error = error_msg

    n = source.consecutive_failures
    base_delay = min(
        settings.retriever_backoff_cap_seconds,
        settings.retriever_backoff_base_seconds * (2 ** (n - 1)),
    )
    jitter = random.uniform(0.9, 1.1)
    delay = min(settings.retriever_backoff_cap_seconds, base_delay * jitter)

    source.next_check_at = datetime.now(tz=timezone.utc) + timedelta(seconds=delay)

    if source.consecutive_failures >= settings.retriever_max_source_failures:
        source.enabled = False
        logger.warning(
            "Source %s disabled after %d consecutive failures",
            source.id,
            source.consecutive_failures,
        )

    session.flush()
