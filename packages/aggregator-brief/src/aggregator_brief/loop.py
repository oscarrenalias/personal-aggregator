import logging
import os
import signal
import socket
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aggregator_common.brief_claim import (
    claim_brief,
    complete_brief,
    fail_brief,
    reap_stale_brief_claims,
)
from aggregator_common.db import SessionFactory as _DefaultSessionFactory
from aggregator_common.models import Brief

from .config import BriefSettings
from .generate import generate_brief

logger = logging.getLogger(__name__)


def _compute_period(settings: BriefSettings, now_utc: datetime) -> tuple[datetime, datetime]:
    tz = ZoneInfo(settings.brief_timezone)
    now_local = now_utc.astimezone(tz)
    day_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_local = day_start_local + timedelta(days=1)
    return day_start_local.astimezone(timezone.utc), day_end_local.astimezone(timezone.utc)


def _maybe_enqueue_auto_brief(session, settings: BriefSettings, now_utc: datetime) -> bool:
    """Insert a pending auto brief for today if past the generation hour and none exists yet.

    Uses INSERT ... ON CONFLICT DO NOTHING on the partial unique index
    (uq_briefs_period_start_auto) so concurrent workers stay race-safe.
    """
    tz = ZoneInfo(settings.brief_timezone)
    local_hour = now_utc.astimezone(tz).hour
    if local_hour < settings.brief_generation_hour:
        return False

    period_start, period_end = _compute_period(settings, now_utc)

    stmt = (
        pg_insert(Brief)
        .values(
            status="pending",
            period_start=period_start,
            period_end=period_end,
            origin="auto",
        )
        .on_conflict_do_nothing(
            index_elements=["period_start"],
            index_where=text("origin = 'auto'"),
        )
    )
    result = session.execute(stmt)
    inserted = result.rowcount > 0
    if inserted:
        logger.info(
            "Enqueued auto brief for period %s → %s",
            period_start.isoformat(),
            period_end.isoformat(),
        )
    return inserted


def _prune_old_briefs(session, settings: BriefSettings, now_utc: datetime) -> int:
    cutoff = now_utc - timedelta(days=settings.brief_retention_days)
    result = session.execute(delete(Brief).where(Brief.created_at < cutoff))
    return result.rowcount


def _run_one_iteration(
    settings: BriefSettings,
    session_factory,
    worker_id: str,
) -> bool:
    """Run one poll cycle: reap → daily-trigger → claim → generate → complete/fail.

    Returns True if a brief was processed (claimed and either completed or failed).
    """
    session = session_factory()
    brief_id: int | None = None

    try:
        now = datetime.now(timezone.utc)
        reaped = reap_stale_brief_claims(session, settings.brief_claim_lease_seconds, now)
        if reaped:
            logger.info("Reaped %d stale brief claim(s)", reaped)
        _maybe_enqueue_auto_brief(session, settings, now)
        brief = claim_brief(session, worker_id, now)
        if brief is None:
            session.commit()
            return False
        brief_id = brief.id
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Error during reap/trigger/claim cycle")
        return False
    finally:
        session.close()

    # Generation runs in its own session so the claim transaction is already committed.
    session = session_factory()
    try:
        brief = session.get(Brief, brief_id)
        if brief is None:
            logger.error("Brief %d not found after claim", brief_id)
            return False

        generate_brief(session, brief, settings)

        complete_brief(
            session,
            brief_id,
            model=brief.model or settings.brief_llm_model,
            headline=brief.headline or "",
            intro=brief.intro or "",
            generated_at=brief.generated_at or datetime.now(timezone.utc),
        )

        pruned = _prune_old_briefs(session, settings, datetime.now(timezone.utc))
        if pruned:
            logger.info("Pruned %d old brief(s)", pruned)

        session.commit()
        logger.info("Brief %d completed successfully", brief_id)
        return True

    except Exception as exc:
        session.rollback()
        logger.exception("Brief %d generation failed", brief_id)

        fail_session = session_factory()
        try:
            fail_brief(fail_session, brief_id, str(exc))
            fail_session.commit()
        except Exception:
            fail_session.rollback()
            logger.exception("Failed to mark brief %d as failed", brief_id)
        finally:
            fail_session.close()

        return False
    finally:
        session.close()


def run_once(settings: BriefSettings, session_factory=None) -> None:
    if session_factory is None:
        session_factory = _DefaultSessionFactory
    worker_id = f"brief-{socket.gethostname()}-{os.getpid()}"
    logger.info("brief run_once (worker_id=%s)", worker_id)
    _run_one_iteration(settings, session_factory, worker_id)


def run(settings: BriefSettings, session_factory=None) -> None:
    if session_factory is None:
        session_factory = _DefaultSessionFactory

    worker_id = f"brief-{socket.gethostname()}-{os.getpid()}"
    stop_event = threading.Event()

    def _handle_signal(signum: int, _: object) -> None:
        logger.info("Signal %d received, stopping", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info("Brief daemon starting (worker_id=%s)", worker_id)

    while not stop_event.is_set():
        try:
            _run_one_iteration(settings, session_factory, worker_id)
        except Exception:
            logger.exception("Unexpected error in poll iteration")
        stop_event.wait(timeout=settings.brief_poll_interval_seconds)

    logger.info("Brief daemon stopped cleanly")
