import logging
import signal
import threading
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text

from aggregator_common.db import SessionFactory as _DefaultSessionFactory
from aggregator_common.retention import (
    purge_expired_articles,
    purge_expired_briefs,
    purge_expired_llm_calls,
    purge_expired_threads,
)

from .config import JanitorSettings

logger = logging.getLogger(__name__)


def _try_acquire_advisory_lock(session, key: int) -> bool:
    result = session.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
    return bool(result.scalar())


def _release_advisory_lock(session, key: int) -> None:
    session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


def _run_retention(settings: JanitorSettings, session_factory) -> None:
    """Acquire the advisory lock, run all three purge helpers, commit, and release."""
    session = session_factory()
    lock_acquired = False
    try:
        lock_acquired = _try_acquire_advisory_lock(session, settings.janitor_advisory_lock_key)
        if not lock_acquired:
            logger.info("Advisory lock held by another instance — skipping this run")
            return

        articles_deleted = purge_expired_articles(session, settings.janitor_article_retention_days)
        threads_deleted = purge_expired_threads(session, settings.janitor_thread_retention_days)
        briefs_deleted = purge_expired_briefs(session, settings.janitor_brief_retention_days)
        llm_calls_deleted = purge_expired_llm_calls(session, settings.janitor_llm_telemetry_retention_days)

        session.commit()
        logger.info(
            "Retention sweep complete: articles=%d threads=%d briefs=%d llm_calls=%d",
            articles_deleted,
            threads_deleted,
            briefs_deleted,
            llm_calls_deleted,
        )
    except Exception:
        session.rollback()
        logger.exception("Retention sweep failed")
    finally:
        if lock_acquired:
            try:
                _release_advisory_lock(session, settings.janitor_advisory_lock_key)
            except Exception:
                logger.exception("Failed to release advisory lock")
        session.close()


def run(settings: JanitorSettings, session_factory=None) -> None:
    if session_factory is None:
        session_factory = _DefaultSessionFactory

    stop_event = threading.Event()

    def _handle_signal(signum: int, _: object) -> None:
        logger.info("Signal %d received, stopping", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info("Janitor daemon starting")

    tz = ZoneInfo(settings.janitor_timezone)
    last_run_date: date | None = None

    while not stop_event.is_set():
        try:
            now_utc = datetime.now(timezone.utc)
            now_local = now_utc.astimezone(tz)
            today = now_local.date()

            if now_local.hour == settings.janitor_run_hour and last_run_date != today:
                logger.info("Scheduled hour reached — running retention sweep")
                _run_retention(settings, session_factory)
                last_run_date = today
        except Exception:
            logger.exception("Unexpected error in poll iteration")

        stop_event.wait(timeout=settings.janitor_poll_interval_seconds)

    logger.info("Janitor daemon stopped cleanly")
