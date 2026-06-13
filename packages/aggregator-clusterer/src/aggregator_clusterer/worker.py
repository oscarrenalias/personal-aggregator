import json
import logging
import os
import signal
import socket
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable

import litellm
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from aggregator_common.db import SessionFactory as _DefaultSessionFactory
from aggregator_common.models import Article, Thread, ThreadMembership
from aggregator_common.queries import list_unassigned_ready_articles
from aggregator_clusterer.candidates import get_candidates
from aggregator_clusterer.classification import classify_article
from aggregator_clusterer.config import ClustererSettings
from aggregator_clusterer.consolidate import run_consolidation_pass
from aggregator_clusterer.dedup import check_duplicate
from aggregator_clusterer.scoring import score_and_tier
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


def _make_relevance_gate_fn(
    settings: ClustererSettings,
) -> Callable[[str, Thread], tuple[bool, str]]:
    """Return a litellm-backed relevance gate.

    Fail-open: returns (True, '') on any LLM or parse error so the thread keeps
    its current tier rather than being incorrectly gated.
    """

    def relevance_gate_fn(interest_profile_text: str, thread: Thread) -> tuple[bool, str]:
        if not settings.clusterer_relevance_gate_enabled:
            return True, ""
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a relevance filter for a personal news reader. "
                    "Given a reader's interest profile and a news thread, decide whether "
                    "the thread is relevant to the reader's interests. "
                    "Reply with a JSON object only — no markdown: "
                    '{"relevant": true|false, "reason": "..."}. '
                    "Be permissive: only mark not relevant when the thread is clearly "
                    "off-topic or of no interest to this reader."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Reader interest profile:\n{interest_profile_text or '(not specified)'}\n\n"
                    f"Thread:\nTitle: {thread.representative_title or '(no title)'}\n"
                    f"Summary: {thread.rolling_summary or '(no summary)'}\n\n"
                    "Is this thread relevant to the reader's interests?"
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
            )
            content = response.choices[0].message.content or ""
            data = json.loads(content)
            relevant = bool(data.get("relevant", True))
            reason = str(data.get("reason", ""))
            return relevant, reason
        except Exception:
            logger.exception(
                "LLM relevance gate failed for thread %s; skip gate (fail-open)",
                thread.id,
            )
            return True, ""

    return relevance_gate_fn


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

        for article_id in article_ids:
            if stop_event.is_set():
                logger.info("Stop requested; halting between articles for clean shutdown")
                break

            work_session = session_factory()
            try:
                art = work_session.get(Article, article_id)
                if art is None:
                    logger.warning("Article %d vanished before clustering; skipping", article_id)
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
                    result = classify_article(art, candidates, work_session, settings)
                    process_classification(work_session, art, result, settings)

                # Flush so the ThreadMembership row has its IDs, then read it
                # to learn which thread was touched (handles both new and existing).
                work_session.flush()
                tm = work_session.execute(
                    select(ThreadMembership).where(ThreadMembership.article_id == art.id)
                ).scalar_one_or_none()
                if tm is not None:
                    touched_thread_ids.add(tm.thread_id)

                work_session.commit()
            except Exception:
                work_session.rollback()
                logger.exception("Error clustering article %d; skipping", article_id)
            finally:
                work_session.close()

        # Score and tier all threads touched during this cycle
        for thread_id in touched_thread_ids:
            if stop_event.is_set():
                break
            score_session = session_factory()
            try:
                thread = score_session.get(Thread, thread_id)
                if thread is not None:
                    score_and_tier(score_session, thread, settings)
                    score_session.commit()
            except Exception:
                score_session.rollback()
                logger.exception("Error scoring thread %d; skipping", thread_id)
            finally:
                score_session.close()

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

        # Run consolidation pass when the corpus is fully drained (no articles
        # remained unassigned at the start of this cycle) or an explicit
        # recluster was requested.  Do NOT run when articles are still being
        # processed (full_drain is False and no recluster flag).
        full_drain = not article_ids
        should_consolidate = (full_drain or recluster_triggered) and not stop_event.is_set()
        if should_consolidate:
            trigger = "full-drain" if full_drain else "recluster-request"
            logger.info("Starting consolidation pass (trigger=%s)", trigger)
            consol_session = session_factory()
            try:
                result = run_consolidation_pass(
                    consol_session,
                    settings,
                    _make_llm_merge_fn(settings),
                    _make_relevance_gate_fn(settings),
                )
                consol_session.commit()
                logger.info(
                    "Consolidation pass complete (trigger=%s): merges=%d curated=%d pruned=%d",
                    trigger,
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
