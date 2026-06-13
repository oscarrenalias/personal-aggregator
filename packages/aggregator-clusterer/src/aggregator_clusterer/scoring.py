from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from aggregator_common.models import Article, ClassificationLabel, Thread, ThreadMembership
from aggregator_clusterer.config import ClustererSettings

logger = logging.getLogger(__name__)

_NOVELTY_LABELS = frozenset({
    ClassificationLabel.same_thread_new_fact.value,
    ClassificationLabel.correction_or_clarification.value,
})

_TIME_PEAK_HOURS = 6.0
_TIME_FLOOR_HOURS = 72.0


def _source_diversity(distinct_sources: int, settings: ClustererSettings) -> float:
    n = settings.clusterer_diversity_saturation_n
    return min(1.0, (distinct_sources - 1) / max(1, n - 1))


def _time_sensitivity(last_updated: datetime) -> float:
    now = datetime.now(tz=timezone.utc)
    age_hours = (now - last_updated).total_seconds() / 3600.0
    if age_hours <= _TIME_PEAK_HOURS:
        return 1.0
    if age_hours >= _TIME_FLOOR_HOURS:
        return 0.0
    span = _TIME_FLOOR_HOURS - _TIME_PEAK_HOURS
    return 1.0 - (age_hours - _TIME_PEAK_HOURS) / span


def _build_tier_reason(
    tier: str,
    importance: float,
    novelty: float,
    time_sensitivity: float,
    source_count: int,
    member_count: int,
) -> str:
    if importance >= 0.7:
        headline = "High importance"
    elif importance >= 0.4:
        headline = "Moderate importance"
    else:
        headline = "Low importance"

    coverage = f"across {source_count} source{'s' if source_count != 1 else ''}"

    addons: list[str] = []
    if novelty > 0.3:
        addons.append("with recent new developments")
    if time_sensitivity >= 0.8:
        addons.append("breaking")
    elif time_sensitivity <= 0.1:
        addons.append("no recent updates")

    parts = [headline, coverage] + addons
    parts.append(f"({member_count} article{'s' if member_count != 1 else ''})")
    return " ".join(parts)


def update_thread_scores(
    thread: Thread,
    *,
    relevance: float,
    novelty: float,
    importance: float,
    diversity: float,
    time_sensitivity: float,
    tier: str,
    tier_reason: str,
) -> None:
    thread.relevance_score = relevance
    thread.novelty_score = novelty
    thread.importance_score = importance
    thread.diversity_score = diversity
    thread.time_sensitivity_score = time_sensitivity
    thread.tier = tier
    thread.tier_reason = tier_reason


def score_and_tier(
    session: Session,
    thread: Thread,
    settings: ClustererSettings,
) -> None:
    memberships = session.execute(
        select(ThreadMembership).where(ThreadMembership.thread_id == thread.id)
    ).scalars().all()

    article_ids = [m.article_id for m in memberships]
    articles: list[Article] = []
    if article_ids:
        articles = list(
            session.execute(
                select(Article).where(Article.id.in_(article_ids))
            ).scalars().all()
        )

    scored = [a.importance_score for a in articles if a.importance_score is not None]

    # relevance: mean article importance_score normalized to 0-1
    relevance = (sum(scored) / len(scored) / 100.0) if scored else 0.0

    # importance: max article importance_score normalized to 0-1
    importance = (max(scored) / 100.0) if scored else 0.0

    # novelty: fraction of members carrying new-fact or correction labels
    novelty = (
        sum(1 for m in memberships if m.classification_label in _NOVELTY_LABELS) / len(memberships)
        if memberships
        else 0.0
    )

    # diversity: singleton threads score 0.0; rises with distinct sources, saturates at N
    source_count = len(set(thread.source_list or []))
    diversity = _source_diversity(source_count, settings)
    confidence = thread.confidence or 0.0

    # time_sensitivity: 1.0 under 6 h, linear decay to 0 at 72 h
    ts = _time_sensitivity(thread.last_updated)

    # composite weighted sum across 5 dimensions; relevance is excluded from scoring
    # but is still persisted on the thread for display purposes
    w_n = settings.clusterer_weight_novelty
    w_i = settings.clusterer_weight_importance
    w_d = settings.clusterer_weight_diversity
    w_c = settings.clusterer_weight_confidence
    w_t = settings.clusterer_weight_time_sensitivity
    weight_sum = w_n + w_i + w_d + w_c + w_t
    composite = (
        w_n * novelty
        + w_i * importance
        + w_d * diversity
        + w_c * confidence
        + w_t * ts
    ) / (weight_sum or 1.0)

    if composite >= settings.clusterer_tier_must_know_threshold:
        tier = "must_know"
    elif composite >= settings.clusterer_tier_worth_tracking_threshold:
        tier = "worth_tracking"
    elif composite >= settings.clusterer_tier_deep_read_threshold:
        tier = "deep_read"
    else:
        tier = "low_noise"

    tier_reason = _build_tier_reason(tier, importance, novelty, ts, source_count, len(articles))

    update_thread_scores(
        thread,
        relevance=relevance,
        novelty=novelty,
        importance=importance,
        diversity=diversity,
        time_sensitivity=ts,
        tier=tier,
        tier_reason=tier_reason,
    )

    logger.debug(
        "thread_id=%s tier=%s composite=%.3f",
        thread.id,
        tier,
        composite,
    )

    # Aging: advance status based on time since last article was added
    now = datetime.now(tz=timezone.utc)
    age_days = (now - thread.last_updated).total_seconds() / 86400.0
    if age_days >= settings.clusterer_archive_age_days:
        thread.status = "archived"
    elif age_days >= settings.clusterer_dormant_age_days:
        thread.status = "dormant"
