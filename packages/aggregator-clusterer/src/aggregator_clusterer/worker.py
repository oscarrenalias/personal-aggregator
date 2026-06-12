import logging
import os
import signal
import socket
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from aggregator_common.db import SessionFactory as _DefaultSessionFactory
from aggregator_common.models import Article, Thread, ThreadMembership
from aggregator_common.queries import list_unassigned_ready_articles
from aggregator_clusterer.candidates import get_candidates
from aggregator_clusterer.classification import classify_article
from aggregator_clusterer.config import ClustererSettings
from aggregator_clusterer.dedup import check_duplicate
from aggregator_clusterer.scoring import score_and_tier
from aggregator_clusterer.upsert import process_classification

logger = logging.getLogger(__name__)

# Stable advisory lock key for the clusterer daemon ('CRLS' hex → 1129855059)
_ADVISORY_LOCK_KEY = 1129855059


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
            fetch_session.commit()
        finally:
            fetch_session.close()

        article_ids = [a.id for a in articles]

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
        flag_session = session_factory()
        try:
            triggered = _check_and_clear_recluster_flag(flag_session)
            flag_session.commit()
            if triggered:
                logger.info("recluster flag detected and reset; immediate cycle was triggered")
        except Exception:
            flag_session.rollback()
            logger.exception("Error checking recluster flag")
        finally:
            flag_session.close()

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
