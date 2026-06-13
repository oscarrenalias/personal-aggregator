from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aggregator_common.management import merge_threads
from aggregator_common.models import Article, InterestProfile, Thread, ThreadMembership
from aggregator_clusterer.config import ClustererSettings
from aggregator_clusterer.scoring import score_and_tier

logger = logging.getLogger(__name__)

_ENTITY_W = 0.40
_TOPIC_W = 0.30
_FTS_W = 0.30


def _to_set(value: object) -> frozenset:
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


def find_merge_candidates(
    session: Session,
    settings: ClustererSettings,
) -> list[tuple[int, int]]:
    """Return (keep_id, absorb_id) pairs whose similarity meets the merge floor.

    keep_id is always the lower integer id; absorb_id the higher. Read-only —
    does not modify any state.
    """
    threads: list[Thread] = list(
        session.execute(
            select(Thread).where(Thread.status == "active")
        ).scalars().all()
    )

    if len(threads) < 2:
        return []

    thread_ids = [t.id for t in threads]

    # Get entity/topic sets from each thread's most-recent member article.
    latest_sub = (
        select(
            ThreadMembership.thread_id,
            func.max(ThreadMembership.assigned_at).label("max_assigned"),
        )
        .where(ThreadMembership.thread_id.in_(thread_ids))
        .group_by(ThreadMembership.thread_id)
        .subquery()
    )

    member_rows = session.execute(
        select(ThreadMembership.thread_id, Article.entities, Article.topics)
        .join(Article, ThreadMembership.article_id == Article.id)
        .join(
            latest_sub,
            (ThreadMembership.thread_id == latest_sub.c.thread_id)
            & (ThreadMembership.assigned_at == latest_sub.c.max_assigned),
        )
    ).all()

    thread_entities: dict[int, frozenset] = {
        row.thread_id: _to_set(row.entities) for row in member_rows
    }
    thread_topics: dict[int, frozenset] = {
        row.thread_id: _to_set(row.topics) for row in member_rows
    }

    # Batch FTS similarity for all ordered pairs via a Postgres self-join.
    ta = Thread.__table__.alias("ta")
    tb = Thread.__table__.alias("tb")

    fts_rows = session.execute(
        select(
            ta.c.id.label("tid_a"),
            tb.c.id.label("tid_b"),
            func.ts_rank(
                func.to_tsvector(
                    "english",
                    func.concat_ws(" ", ta.c.representative_title, ta.c.rolling_summary),
                ),
                func.plainto_tsquery(
                    "english",
                    func.concat_ws(" ", tb.c.representative_title, tb.c.rolling_summary),
                ),
            ).label("fts_score"),
        )
        .where(
            ta.c.id < tb.c.id,
            ta.c.status == "active",
            tb.c.status == "active",
        )
    ).all()

    fts_scores: dict[tuple[int, int], float] = {
        (row.tid_a, row.tid_b): float(row.fts_score) for row in fts_rows
    }

    # Compute a composite similarity score for each unordered pair.
    floor = settings.clusterer_merge_similarity_floor
    scored: list[tuple[float, int, int]] = []

    for i, ta_t in enumerate(threads):
        for tb_t in threads[i + 1:]:
            lo, hi = (ta_t.id, tb_t.id) if ta_t.id < tb_t.id else (tb_t.id, ta_t.id)

            entity_overlap = _jaccard(
                thread_entities.get(ta_t.id, frozenset()),
                thread_entities.get(tb_t.id, frozenset()),
            )
            topic_overlap = _jaccard(
                thread_topics.get(ta_t.id, frozenset()),
                thread_topics.get(tb_t.id, frozenset()),
            )
            fts = min(fts_scores.get((lo, hi), 0.0), 1.0)

            composite = (
                _ENTITY_W * entity_overlap
                + _TOPIC_W * topic_overlap
                + _FTS_W * fts
            )

            if composite >= floor:
                scored.append((composite, lo, hi))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [(lo, hi) for _, lo, hi in scored]


def run_merge_pass(
    session: Session,
    settings: ClustererSettings,
    llm_classify_fn: Callable[[Thread, Thread], bool],
) -> int:
    """Merge near-duplicate thread pairs confirmed by llm_classify_fn.

    Iterates candidates from find_merge_candidates up to
    clusterer_max_merge_checks LLM calls. Returns the number of merges
    performed. llm_classify_fn is injectable so tests can stub the LLM call.
    """
    candidates = find_merge_candidates(session, settings)

    max_checks = settings.clusterer_max_merge_checks
    llm_calls = 0
    merge_count = 0
    absorbed: set[int] = set()

    for keep_id, absorb_id in candidates:
        if llm_calls >= max_checks:
            break

        # Skip if either thread was already absorbed in this pass.
        if keep_id in absorbed or absorb_id in absorbed:
            continue

        keep = session.get(Thread, keep_id)
        absorb = session.get(Thread, absorb_id)
        if keep is None or absorb is None:
            continue

        llm_calls += 1

        try:
            is_same = llm_classify_fn(keep, absorb)
        except Exception:
            logger.exception(
                "llm_classify_fn raised for threads %s/%s; skipping pair",
                keep_id,
                absorb_id,
            )
            continue

        if is_same:
            merge_threads(session, keep_id, absorb_id)
            absorbed.add(absorb_id)
            merge_count += 1
            logger.info("merged thread %s into %s", absorb_id, keep_id)

    return merge_count


def _composite_from_stored(thread: Thread, settings: ClustererSettings) -> float:
    """Recompute the composite score from a thread's already-persisted dimension scores."""
    w_n = settings.clusterer_weight_novelty
    w_i = settings.clusterer_weight_importance
    w_d = settings.clusterer_weight_diversity
    w_c = settings.clusterer_weight_confidence
    w_t = settings.clusterer_weight_time_sensitivity
    weight_sum = w_n + w_i + w_d + w_c + w_t
    return (
        w_n * (thread.novelty_score or 0.0)
        + w_i * (thread.importance_score or 0.0)
        + w_d * (thread.diversity_score or 0.0)
        + w_c * (thread.confidence or 0.0)
        + w_t * (thread.time_sensitivity_score or 0.0)
    ) / (weight_sum or 1.0)


def run_curation_pass(
    session: Session,
    settings: ClustererSettings,
    relevance_gate_fn: Callable[[str, Thread], tuple[bool, str]],
) -> None:
    """Re-score all active threads and apply curation gates and tier caps.

    Steps:
    1. Re-score every active thread via score_and_tier().
    2. Apply relevance gate (fail-open: exceptions skip that thread).
    3. Apply single-source gate (already enforced inside score_and_tier).
    4. Enforce tier caps: top-N by composite stay in must_know/worth_tracking;
       overflow is demoted to deep_read.

    The function is idempotent: re-running it on an already-curated set
    produces no further changes.
    """
    profile = session.execute(select(InterestProfile)).scalar_one_or_none()
    interest_profile_text = profile.profile_text if profile else ""

    threads: list[Thread] = list(
        session.execute(
            select(Thread).where(Thread.status == "active")
        ).scalars().all()
    )

    # Step 1: re-score all active threads (also applies single-source gate).
    for thread in threads:
        score_and_tier(session, thread, settings)

    # Step 2: relevance gate — off-interest threads are forced to low_noise.
    for thread in threads:
        try:
            relevant, reason = relevance_gate_fn(interest_profile_text, thread)
            if not relevant:
                thread.tier = "low_noise"
                thread.tier_reason = reason
        except Exception:
            logger.exception(
                "relevance_gate_fn raised for thread %s; skipping (fail-open)",
                thread.id,
            )

    # Step 3: tier caps — keep top-N by composite, demote the rest to deep_read.
    must_know = sorted(
        [t for t in threads if t.tier == "must_know"],
        key=lambda t: _composite_from_stored(t, settings),
        reverse=True,
    )
    for t in must_know[settings.clusterer_must_know_max:]:
        t.tier = "deep_read"
        t.tier_reason = f"[tier cap: demoted from must_know] {t.tier_reason or ''}".strip()
        logger.debug("thread %s demoted from must_know by tier cap", t.id)

    worth_tracking = sorted(
        [t for t in threads if t.tier == "worth_tracking"],
        key=lambda t: _composite_from_stored(t, settings),
        reverse=True,
    )
    for t in worth_tracking[settings.clusterer_worth_tracking_max:]:
        t.tier = "deep_read"
        t.tier_reason = f"[tier cap: demoted from worth_tracking] {t.tier_reason or ''}".strip()
        logger.debug("thread %s demoted from worth_tracking by tier cap", t.id)

    logger.info(
        "curation pass complete: %d threads, %d must_know (cap %d), %d worth_tracking (cap %d)",
        len(threads),
        min(len(must_know), settings.clusterer_must_know_max),
        settings.clusterer_must_know_max,
        min(len(worth_tracking), settings.clusterer_worth_tracking_max),
        settings.clusterer_worth_tracking_max,
    )
