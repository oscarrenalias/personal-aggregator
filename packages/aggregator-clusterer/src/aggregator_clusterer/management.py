from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from aggregator_common.models import Article, ClassificationLabel, Thread, ThreadMembership


def create_thread(
    session: Session,
    *,
    representative_title: str,
    rolling_summary: Optional[str],
    known_facts: list,
    source_list: list,
    confidence: Optional[float],
) -> Thread:
    now = datetime.now(tz=timezone.utc)
    thread = Thread(
        representative_title=representative_title,
        rolling_summary=rolling_summary,
        known_facts=list(known_facts),
        first_seen=now,
        last_updated=now,
        source_list=list(source_list),
        source_diversity=_source_diversity(source_list),
        confidence=confidence,
        deltas=[],
    )
    session.add(thread)
    session.flush()
    return thread


def assign_article_to_thread(
    session: Session,
    *,
    thread: Thread,
    article: Article,
    label: ClassificationLabel,
    new_facts: Optional[list[str]] = None,
    reason: Optional[str] = None,
    confidence: Optional[float] = None,
    suppressed: bool = False,
) -> ThreadMembership:
    existing = session.execute(
        select(ThreadMembership).where(ThreadMembership.article_id == article.id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    membership = ThreadMembership(
        thread_id=thread.id,
        article_id=article.id,
        classification_label=label.value,
        new_facts=new_facts or [],
        reason=reason,
        confidence=confidence,
        suppressed=suppressed,
        assigned_at=datetime.now(tz=timezone.utc),
    )
    session.add(membership)
    return membership


def _source_diversity(source_list: list) -> Optional[float]:
    n = len(source_list)
    if n == 0:
        return None
    return len(set(source_list)) / n
