"""Thread consolidation: merge near-duplicates and surface threads.

Two sequential sub-passes are run by ``run_consolidation_pass`` each cycle:

1. **Merge pass** — finds active thread pairs whose composite entity/topic/FTS
   similarity meets ``CLUSTERER_MERGE_SIMILARITY_FLOOR``, confirms duplicates via
   an LLM call, and absorbs the lower-ranked thread into the higher-ranked one.

2. **Surfacing pass** — recomputes ``surfaced`` and ``top_grade`` for every active
   thread.  A thread is surfaced when its top member grade, distinct source count,
   or member count clears the configured thresholds.  See ``scoring.compute_surfaced``
   for the exact OR-conditions.

Hard deletion of expired threads is handled by the dedicated janitor service.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aggregator_common.management import merge_threads
from aggregator_common.models import Article, Thread, ThreadMembership
from aggregator_clusterer.config import ClustererSettings
from aggregator_clusterer.scoring import compute_surfaced

logger = logging.getLogger(__name__)


@dataclass
class ConsolidationResult:
    merges: int
    curated: int
    pruned: int


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
    *,
    changed_since: datetime | None = None,
) -> list[tuple[int, int]]:
    """Return (keep_id, absorb_id) pairs whose similarity meets the merge floor.

    keep_id is always the lower integer id; absorb_id the higher. Read-only —
    does not modify any state.

    When changed_since is provided and settings.clusterer_incremental_merge is
    True, stale×stale pairs (both threads last_updated < changed_since) are
    excluded. A changed thread is still scored against all other active threads.
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
    incremental = changed_since is not None and settings.clusterer_incremental_merge
    scored: list[tuple[float, int, int]] = []

    for i, ta_t in enumerate(threads):
        for tb_t in threads[i + 1:]:
            # Skip stale×stale pairs when incremental mode is active.
            if incremental and (
                ta_t.last_updated < changed_since  # type: ignore[operator]
                and tb_t.last_updated < changed_since  # type: ignore[operator]
            ):
                continue

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


def run_surfacing_pass(session: Session, settings: ClustererSettings) -> int:
    """Compute and persist surfaced/top_grade for all active threads.

    For each thread, derives top_grade from the max importance_score of its
    member articles, then calls compute_surfaced with the config thresholds.
    """
    threads: list[Thread] = list(
        session.execute(
            select(Thread).where(Thread.status == "active")
        ).scalars().all()
    )

    if not threads:
        return 0

    thread_ids = [t.id for t in threads]

    # Fetch max importance_score and member count per thread in one query.
    rows = session.execute(
        select(
            ThreadMembership.thread_id,
            func.max(Article.importance_score).label("max_score"),
            func.count(Article.id).label("member_count"),
        )
        .join(Article, ThreadMembership.article_id == Article.id)
        .where(ThreadMembership.thread_id.in_(thread_ids))
        .group_by(ThreadMembership.thread_id)
    ).all()

    stats: dict[int, tuple[int | None, int]] = {
        row.thread_id: (
            int(row.max_score) if row.max_score is not None else None,
            row.member_count,
        )
        for row in rows
    }

    updated = 0
    for thread in threads:
        top_grade, member_count = stats.get(thread.id, (None, 0))
        distinct_sources = len(set(thread.source_list or []))

        surfaced, top_grade_out = compute_surfaced(
            top_grade,
            distinct_sources,
            member_count,
            min_grade=settings.clusterer_surface_min_grade,
            min_sources=settings.clusterer_surface_min_sources,
            min_members=settings.clusterer_surface_min_members,
        )
        thread.surfaced = surfaced
        thread.top_grade = top_grade_out
        updated += 1

    logger.info("surfacing pass complete: %d threads updated", updated)
    return updated


def run_consolidation_pass(
    session: Session,
    settings: ClustererSettings,
    llm_merge_fn: Callable[[Thread, Thread], bool],
) -> ConsolidationResult:
    """Run merge and surfacing sub-passes in order and return a summary.

    Calls run_merge_pass and run_surfacing_pass sequentially. Hard deletion of
    expired threads is handled by the dedicated janitor service, not here.
    The session is not committed here — callers are responsible for commit/rollback.
    """
    merges = run_merge_pass(session, settings, llm_merge_fn)
    curated = run_surfacing_pass(session, settings)
    logger.info(
        "consolidation pass complete: merges=%d surfaced=%d",
        merges, curated,
    )
    return ConsolidationResult(merges=merges, curated=curated, pruned=0)
