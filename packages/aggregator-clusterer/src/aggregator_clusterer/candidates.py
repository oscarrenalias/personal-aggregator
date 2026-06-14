from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aggregator_common.models import Article, Thread, ThreadMembership
from aggregator_clusterer.config import ClustererSettings

_ENTITY_W = 0.35
_TOPIC_W = 0.25
_FTS_W = 0.25
_URL_W = 0.10
_TIME_W = 0.05


@dataclass
class CandidateMatch:
    thread_id: int
    composite_score: float
    signals: dict  # keys: entity_overlap, topic_overlap, fts_score, url_match, time_delta_hours


def _to_set(value: Any) -> frozenset:
    if value is None:
        return frozenset()
    if isinstance(value, list):
        return frozenset(str(v) for v in value if v is not None)
    if isinstance(value, dict):
        return frozenset(str(k) for k in value)
    return frozenset()


def _jaccard(a: frozenset, b: frozenset) -> float:
    union = len(a | b)
    return len(a & b) / union if union > 0 else 0.0


def _raw_url(article: Article) -> Optional[str]:
    if isinstance(article.raw_payload, dict):
        return article.raw_payload.get("link") or article.raw_payload.get("url")
    return None


def get_candidates(
    session: Session,
    article: Article,
    settings: ClustererSettings,
) -> list[CandidateMatch]:
    now = datetime.now(tz=timezone.utc)

    # Choose time window based on article age
    article_ts = article.feed_published_at or article.retrieved_at
    if article_ts is not None and article_ts.tzinfo is None:
        article_ts = article_ts.replace(tzinfo=timezone.utc)

    fast_cutoff = now - timedelta(hours=settings.clusterer_candidate_window_hours_fast)
    if article_ts is None or article_ts >= fast_cutoff:
        thread_cutoff = fast_cutoff
        window_hours = float(settings.clusterer_candidate_window_hours_fast)
    else:
        thread_cutoff = now - timedelta(days=settings.clusterer_candidate_window_days_slow)
        window_hours = float(settings.clusterer_candidate_window_days_slow * 24)

    threads: list[Thread] = list(
        session.execute(
            select(Thread).where(Thread.last_updated >= thread_cutoff)
        ).scalars().all()
    )

    if not threads:
        return []

    thread_ids = [t.id for t in threads]

    # Bulk-load all memberships + member articles for candidate threads in one round trip
    membership_rows = session.execute(
        select(ThreadMembership, Article)
        .join(Article, ThreadMembership.article_id == Article.id)
        .where(ThreadMembership.thread_id.in_(thread_ids))
        .order_by(ThreadMembership.thread_id, ThreadMembership.assigned_at.desc())
    ).all()

    members_by_thread: dict[int, list[tuple[ThreadMembership, Article]]] = defaultdict(list)
    for tm, art in membership_rows:
        members_by_thread[tm.thread_id].append((tm, art))

    # Bulk FTS scores via Postgres ts_rank + plainto_tsquery
    query_text = article.clean_title or article.feed_title or ""
    if query_text:
        fts_doc = func.to_tsvector(
            "english",
            func.concat_ws(" ", Thread.representative_title, Thread.rolling_summary),
        )
        fts_query = func.plainto_tsquery("english", query_text)
        fts_rows = session.execute(
            select(Thread.id, func.ts_rank(fts_doc, fts_query).label("score"))
            .where(Thread.id.in_(thread_ids))
        ).all()
        fts_scores: dict[int, float] = {row.id: float(row.score) for row in fts_rows}
    else:
        fts_scores = {tid: 0.0 for tid in thread_ids}

    article_entities = _to_set(article.entities)
    article_topics = _to_set(article.topics)
    article_url = _raw_url(article)

    candidates: list[CandidateMatch] = []

    for thread in threads:
        last_updated = thread.last_updated
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=timezone.utc)
        time_delta_hours = (now - last_updated).total_seconds() / 3600.0

        thread_members = members_by_thread.get(thread.id, [])

        # Entity and topic overlap against the union of all thread members' entities/topics
        thread_entities: frozenset = frozenset()
        thread_topics: frozenset = frozenset()
        for _, member_art in thread_members:
            thread_entities = thread_entities | _to_set(member_art.entities)
            thread_topics = thread_topics | _to_set(member_art.topics)

        entity_overlap = _jaccard(article_entities, thread_entities)
        topic_overlap = _jaccard(article_topics, thread_topics)
        fts_score = fts_scores.get(thread.id, 0.0)

        url_match = False
        if article_url:
            member_urls = {_raw_url(art) for _, art in thread_members}
            url_match = article_url in member_urls

        # Normalise time proximity: 1.0 at t=0, 0.0 at the window boundary
        time_proximity = max(0.0, 1.0 - time_delta_hours / window_hours)

        composite_score = (
            _ENTITY_W * entity_overlap
            + _TOPIC_W * topic_overlap
            + _FTS_W * min(fts_score, 1.0)
            + _URL_W * (1.0 if url_match else 0.0)
            + _TIME_W * time_proximity
        )

        candidates.append(
            CandidateMatch(
                thread_id=thread.id,
                composite_score=composite_score,
                signals={
                    "entity_overlap": entity_overlap,
                    "topic_overlap": topic_overlap,
                    "fts_score": fts_score,
                    "url_match": url_match,
                    "time_delta_hours": time_delta_hours,
                },
            )
        )

    candidates.sort(key=lambda c: c.composite_score, reverse=True)
    return candidates[: settings.clusterer_max_candidate_threads]
