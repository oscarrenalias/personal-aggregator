from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Union

from sqlalchemy import select
from sqlalchemy.orm import Session

from aggregator_common.models import Article, ClassificationLabel, Thread
from aggregator_clusterer import management
from aggregator_clusterer.classification import ClassificationResult, _truncate_title
from aggregator_clusterer.config import ClustererSettings
from aggregator_clusterer.dedup import DedupResult

logger = logging.getLogger(__name__)

_SUPPRESSED_LABELS = frozenset({
    ClassificationLabel.same_thread_duplicate,
    ClassificationLabel.same_thread_background_only,
})

_DELTA_LABELS = frozenset({
    ClassificationLabel.same_thread_new_fact,
    ClassificationLabel.correction_or_clarification,
})


def _normalize(
    result: Union[ClassificationResult, DedupResult],
) -> tuple[int | None, ClassificationLabel, float, list[str], str, Optional[str]]:
    if isinstance(result, DedupResult):
        return result.thread_id, result.classification_label, 1.0, [], "", None
    return result.thread_id, result.label, result.confidence, result.new_facts, result.reason, result.thread_title


def process_classification(
    session: Session,
    article: Article,
    result: Union[ClassificationResult, DedupResult],
    settings: ClustererSettings,
) -> None:
    thread_id, label, confidence, new_facts, reason, thread_title = _normalize(result)

    suppressed = label in _SUPPRESSED_LABELS

    if thread_id is None:
        thread = management.create_thread(
            session,
            representative_title=thread_title or _truncate_title(article.clean_title or article.feed_title or ""),
            rolling_summary=article.summary,
            known_facts=[],
            source_list=[article.source_id],
            confidence=confidence,
        )
    else:
        thread = session.execute(
            select(Thread).where(Thread.id == thread_id)
        ).scalar_one_or_none()

        if thread is None:
            logger.warning(
                "thread_id=%s not found for article_id=%s; creating new thread",
                thread_id,
                article.id,
            )
            thread = management.create_thread(
                session,
                representative_title=thread_title or _truncate_title(article.clean_title or article.feed_title or ""),
                rolling_summary=article.summary,
                known_facts=[],
                source_list=[article.source_id],
                confidence=confidence,
            )
        else:
            _update_thread(thread, article, label, new_facts, reason, thread_title)

    management.assign_article_to_thread(
        session,
        thread=thread,
        article=article,
        label=label,
        new_facts=new_facts,
        reason=reason,
        confidence=confidence,
        suppressed=suppressed,
    )


def _update_thread(
    thread: Thread,
    article: Article,
    label: ClassificationLabel,
    new_facts: list[str],
    reason: str,
    thread_title: Optional[str] = None,
) -> None:
    now = datetime.now(tz=timezone.utc)

    if label in _DELTA_LABELS and new_facts:
        existing_facts: list = list(thread.known_facts or [])
        for fact in new_facts:
            if fact not in existing_facts:
                existing_facts.append(fact)
        thread.known_facts = existing_facts

        delta_entry = {
            "article_id": article.id,
            "label": label.value,
            "new_facts": new_facts,
            "timestamp": now.isoformat(),
            "reason": reason,
        }
        thread.deltas = list(thread.deltas or []) + [delta_entry]

    if label in _DELTA_LABELS and thread_title:
        thread.representative_title = thread_title

    source_list: list = list(thread.source_list or [])
    if article.source_id not in source_list:
        source_list.append(article.source_id)
    thread.source_list = source_list
    n = len(source_list)
    thread.source_diversity = len(set(source_list)) / n if n > 0 else None

    thread.last_updated = now
