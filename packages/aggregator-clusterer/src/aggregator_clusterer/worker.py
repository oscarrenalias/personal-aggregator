import json
import logging
import os
import signal
import socket
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable

import litellm
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from aggregator_common.db import SessionFactory as _DefaultSessionFactory
from aggregator_common.models import Article, Thread, ThreadMembership
from aggregator_common.queries import list_unassigned_ready_articles
from aggregator_clusterer.candidates import get_candidates
from aggregator_clusterer.classification import classify_article, is_section_title_blocked
from aggregator_clusterer.config import ClustererSettings
from aggregator_clusterer.consolidate import run_consolidation_pass
from aggregator_clusterer.dedup import check_duplicate
from aggregator_clusterer.scoring import compute_surfaced
from aggregator_clusterer.upsert import process_classification

logger = logging.getLogger(__name__)

# Stable advisory lock key for the clusterer daemon ('CRLS' hex → 1129855059)
_ADVISORY_LOCK_KEY = 1129855059


def _make_llm_merge_fn(settings: ClustererSettings) -> Callable[[Thread, Thread], bool]:
    """Return a litellm-backed same-story decider.

    Fail-open: returns False (don't merge) on any LLM or parse error so a bad
    response never crashes the consolidation pass.
    """

    def llm_merge_fn(keep: Thread, absorb: Thread) -> bool:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a news editor deciding whether two thread summaries cover the "
                    "same story. Reply with a JSON object only — no markdown: "
                    '{"same_story": true|false, "reason": "..."}. '
                    "Be conservative: only answer true when both threads are unambiguously "
                    "about the same event or ongoing story."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Thread A:\nTitle: {keep.representative_title or '(no title)'}\n"
                    f"Summary: {keep.rolling_summary or '(no summary)'}\n\n"
                    f"Thread B:\nTitle: {absorb.representative_title or '(no title)'}\n"
                    f"Summary: {absorb.rolling_summary or '(no summary)'}\n\n"
                    "Are these the same story?"
                ),
            },
        ]
        try:
            response = litellm.completion(
                model=settings.clusterer_llm_model,
                messages=messages,
                max_tokens=128,
                temperature=0.0,
                timeout=settings.clusterer_llm_timeout_seconds,
                metadata={"service": "clusterer", "operation": "merge"},
            )
            content = response.choices[0].message.content or ""
            data = json.loads(content)
            return bool(data.get("same_story", False))
        except Exception:
            logger.exception(
                "LLM merge check failed for threads %s/%s; skip merge (fail-open)",
                keep.id,
                absorb.id,
            )
            return False

    return llm_merge_fn


def _try_acquire_advisory_lock(session: Session) -> bool:
    result = session.execute(
        text("SELECT pg_try_advisory_lock(:key)"), {"key": _ADVISORY_LOCK_KEY}
    )
    return bool(result.scalar())


def _release_advisory_lock(session: Session) -> None:
    session.execute(
        text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY}
    )


def _check_and_clear_recluster_flag(session: Session) -> bool:
    """Atomically clear recluster_requested. Returns True if it was set."""
    result = session.execute(
        text(
            "UPDATE cluster_state "
            "SET recluster_requested = false "
            "WHERE id = true AND recluster_requested = true "
            "RETURNING id"
        )
    )
    return result.rowcount > 0


def _set_dirty_flag(session: Session) -> None:
    """Set dirty=true on cluster_state, creating the singleton row if absent."""
    session.execute(
        text(
            "INSERT INTO cluster_state (id, recluster_requested, dirty) "
            "VALUES (true, false, true) "
            "ON CONFLICT (id) DO UPDATE SET dirty = true"
        )
    )


def _check_should_consolidate(session: Session, settings: ClustererSettings) -> bool:
    """Return True if dirty=true and enough time has passed since last consolidation."""
    row = session.execute(
        text("SELECT dirty, last_consolidated_at FROM cluster_state WHERE id = true")
    ).one_or_none()
    if row is None or not row.dirty:
        return False
    min_interval = timedelta(minutes=settings.clusterer_consolidation_min_interval_minutes)
    epoch = datetime.fromtimestamp(0, tz=timezone.utc)
    elapsed = datetime.now(tz=timezone.utc) - (row.last_consolidated_at or epoch)
    return elapsed >= min_interval


def _mark_consolidation_done(session: Session) -> None:
    """Clear dirty flag and record last_consolidated_at = now."""
    session.execute(
        text(
            "UPDATE cluster_state "
            "SET dirty = false, last_consolidated_at = now() "
            "WHERE id = true"
        )
    )


def _run_one_cycle(
    settings: ClustererSettings,
    session_factory,
    stop_event: threading.Event,
) -> None:
    # Dedicated session for the advisory lock.  Never commit it so the
    # underlying connection stays checked out and the lock persists for the
    # whole cycle.  Explicitly released in the finally block before close().
    lock_session = session_factory()
    lock_acquired = False
    try:
        lock_acquired = _try_acquire_advisory_lock(lock_session)
        if not lock_acquired:
            logger.debug("Advisory lock unavailable; skipping cycle (another instance running)")
            return

        since = datetime.now(tz=timezone.utc) - timedelta(
            days=settings.clusterer_candidate_window_days_slow
        )

        fetch_session = session_factory()
        try:
            articles = list_unassigned_ready_articles(
                fetch_session,
                since=since,
                limit=settings.clusterer_batch_size,
            )
            # Capture ids while the session is still open — the Article instances
            # become detached after close() and attribute access would raise
            # DetachedInstanceError.
            article_ids = [a.id for a in articles]
            fetch_session.commit()
        finally:
            fetch_session.close()

        if not article_ids:
            logger.debug("No unassigned articles to cluster")
        else:
            logger.info("Clustering %d unassigned article(s)", len(article_ids))

        touched_thread_ids: set[int] = set()
        any_article_assigned = False

        for article_id in article_ids:
            if stop_event.is_set():
                logger.info("Stop requested; halting between articles for clean shutdown")
                break

            assigned_this_article = False
            work_session = session_factory()
            try:
                art = work_session.get(Article, article_id)
                if art is None:
                    logger.warning("Article %d vanished before clustering; skipping", article_id)
                    work_session.commit()
                    continue

                if is_section_title_blocked(art, settings):
                    work_session.commit()
                    continue

                dedup = check_duplicate(work_session, art, settings)
                if dedup is not None:
                    logger.debug(
                        "Article %d is a duplicate of thread %d; skipping LLM",
                        art.id,
                        dedup.thread_id,
                    )
                    process_classification(work_session, art, dedup, settings)
                else:
                    candidates = get_candidates(work_session, art, settings)
                    top_n = min(settings.clusterer_max_classifier_candidates, len(candidates))
                    result = classify_article(art, candidates[:top_n], work_session, settings)
                    if result.is_error:
                        logger.warning(
                            "Classification error for article %d (%s); skipping — article stays unassigned for retry",
                            art.id,
                            result.reason,
                        )
                        work_session.commit()
                        continue
                    process_classification(work_session, art, result, settings)

                # Flush so the ThreadMembership row has its IDs, then read it
                # to learn which thread was touched (handles both new and existing).
                work_session.flush()
                tm = work_session.execute(
                    select(ThreadMembership).where(ThreadMembership.article_id == art.id)
                ).scalar_one_or_none()
                if tm is not None:
                    touched_thread_ids.add(tm.thread_id)
                    assigned_this_article = True

                work_session.commit()
            except Exception:
                work_session.rollback()
                logger.exception("Error clustering article %d; skipping", article_id)
                assigned_this_article = False
            finally:
                work_session.close()

            if assigned_this_article:
                any_article_assigned = True

        # Compute and persist surfaced/top_grade for threads touched during this cycle.
        for thread_id in touched_thread_ids:
            if stop_event.is_set():
                break
            score_session = session_factory()
            try:
                thread = score_session.get(Thread, thread_id)
                if thread is not None:
                    row = score_session.execute(
                        select(
                            func.max(Article.importance_score).label("max_score"),
                            func.count(Article.id).label("member_count"),
                        )
                        .join(ThreadMembership, ThreadMembership.article_id == Article.id)
                        .where(ThreadMembership.thread_id == thread_id)
                    ).one()
                    top_grade = int(row.max_score) if row.max_score is not None else None
                    member_count = row.member_count
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
                    score_session.commit()
            except Exception:
                score_session.rollback()
                logger.exception("Error scoring thread %d; skipping", thread_id)
            finally:
                score_session.close()

        # Mark cluster_state dirty when at least one article was newly assigned.
        # Do NOT set dirty from consolidation's own re-scoring to avoid self-triggering.
        if any_article_assigned:
            dirty_session = session_factory()
            try:
                _set_dirty_flag(dirty_session)
                dirty_session.commit()
            except Exception:
                dirty_session.rollback()
                logger.exception("Error setting dirty flag on cluster_state")
            finally:
                dirty_session.close()

        # Atomically check and clear the recluster flag
        recluster_triggered = False
        flag_session = session_factory()
        try:
            recluster_triggered = _check_and_clear_recluster_flag(flag_session)
            flag_session.commit()
            if recluster_triggered:
                logger.info("recluster flag detected and reset; immediate cycle was triggered")
        except Exception:
            flag_session.rollback()
            logger.exception("Error checking recluster flag")
        finally:
            flag_session.close()

        # Determine consolidation trigger:
        # - Explicit recluster bypasses the interval floor and runs immediately.
        # - Otherwise, only consolidate if dirty=true AND elapsed >= MIN_INTERVAL.
        # An idle clusterer (no new articles, dirty never set) makes zero LLM calls.
        consolidate_trigger: str | None = None
        if recluster_triggered:
            consolidate_trigger = "recluster-request"
        elif not stop_event.is_set():
            check_session = session_factory()
            try:
                if _check_should_consolidate(check_session, settings):
                    consolidate_trigger = "dirty-threshold"
            except Exception:
                logger.exception("Error checking consolidation eligibility")
            finally:
                check_session.close()

        if consolidate_trigger is not None and not stop_event.is_set():
            logger.info("Starting consolidation pass (trigger=%s)", consolidate_trigger)
            consol_session = session_factory()
            try:
                result = run_consolidation_pass(
                    consol_session,
                    settings,
                    _make_llm_merge_fn(settings),
                )
                _mark_consolidation_done(consol_session)
                consol_session.commit()
                logger.info(
                    "Consolidation pass complete (trigger=%s): merges=%d curated=%d pruned=%d",
                    consolidate_trigger,
                    result.merges,
                    result.curated,
                    result.pruned,
                )
            except Exception:
                consol_session.rollback()
                logger.exception("Consolidation pass failed; skipping")
            finally:
                consol_session.close()

    finally:
        if lock_acquired:
            try:
                _release_advisory_lock(lock_session)
            except Exception:
                logger.exception("Error releasing advisory lock; connection may be tainted")
        lock_session.close()


def run_once(settings: ClustererSettings, session_factory=None) -> None:
    if session_factory is None:
        session_factory = _DefaultSessionFactory
    worker_id = f"clusterer-{socket.gethostname()}-{os.getpid()}"
    logger.info("clusterer run_once (worker_id=%s)", worker_id)
    stop_event = threading.Event()
    _run_one_cycle(settings, session_factory, stop_event)


def run(settings: ClustererSettings, session_factory=None) -> None:
    if session_factory is None:
        session_factory = _DefaultSessionFactory

    worker_id = f"clusterer-{socket.gethostname()}-{os.getpid()}"
    stop_event = threading.Event()

    def _handle_signal(signum: int, _: object) -> None:
        logger.info("Signal %d received, stopping after current cycle", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info("Clusterer daemon starting (worker_id=%s)", worker_id)

    while not stop_event.is_set():
        try:
            _run_one_cycle(settings, session_factory, stop_event)
        except Exception:
            logger.exception("Unexpected error in poll iteration")
        stop_event.wait(timeout=settings.clusterer_poll_interval_seconds)

    logger.info("Clusterer daemon stopped cleanly")
