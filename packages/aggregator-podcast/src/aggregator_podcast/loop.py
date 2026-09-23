import logging
import os
import signal
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aggregator_common.db import SessionFactory as _DefaultSessionFactory
from aggregator_common.models import PodcastEpisode
from aggregator_common.podcast_claim import (
    claim_podcast,
    complete_podcast,
    fail_podcast,
    reap_stale_podcast_claims,
)

from .config import PodcastSettings
from .generate import generate_podcast
from .tts import generate_audio

logger = logging.getLogger(__name__)


def _maybe_enqueue_auto_episode(session, settings: PodcastSettings, now_utc: datetime) -> bool:
    """Insert a pending auto episode for today if past the generation hour and none exists yet.

    Uses INSERT ... ON CONFLICT DO NOTHING on the partial unique index
    (podcast_episodes_date_auto) so concurrent workers stay race-safe.
    """
    tz = ZoneInfo(settings.podcast_timezone)
    now_local = now_utc.astimezone(tz)
    if now_local.hour < settings.podcast_generation_hour:
        return False

    today = now_local.date()

    stmt = (
        pg_insert(PodcastEpisode)
        .values(
            date=today,
            origin="auto",
            status="pending",
        )
        .on_conflict_do_nothing(
            index_elements=["date"],
            index_where=text("origin = 'auto'"),
        )
    )
    result = session.execute(stmt)
    inserted = result.rowcount > 0
    if inserted:
        logger.info("Enqueued auto podcast episode for %s", today.isoformat())
    return inserted


def _run_one_iteration(
    settings: PodcastSettings,
    session_factory,
    worker_id: str,
) -> bool:
    """Run one poll cycle: reap → daily-trigger → claim → generate → complete/fail.

    Returns True if an episode was processed (claimed and either completed or failed).
    """
    session = session_factory()
    episode_id: int | None = None

    try:
        now = datetime.now(timezone.utc)
        reaped = reap_stale_podcast_claims(session, settings.podcast_claim_lease_seconds, now)
        if reaped:
            logger.info("Reaped %d stale podcast claim(s)", reaped)
        _maybe_enqueue_auto_episode(session, settings, now)
        episode = claim_podcast(session, worker_id, now)
        if episode is None:
            session.commit()
            return False
        episode_id = episode.id
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
        episode = session.get(PodcastEpisode, episode_id)
        if episode is None:
            logger.error("PodcastEpisode %d not found after claim", episode_id)
            return False

        script_json, _ = generate_podcast(episode, settings, session)

        audio_dir = Path(settings.podcast_audio_dir)
        audio_path, audio_size_bytes = generate_audio(script_json, audio_dir, settings)

        duration_seconds = script_json.get("duration_estimate_seconds", 0)

        complete_podcast(
            session,
            episode_id,
            script_json=script_json,
            audio_path=audio_path,
            audio_size_bytes=audio_size_bytes,
            duration_seconds=duration_seconds,
            llm_model=settings.podcast_llm_model,
            tts_model=settings.podcast_tts_model,
            tts_voice=settings.podcast_tts_voice,
        )

        session.commit()
        logger.info("PodcastEpisode %d completed successfully", episode_id)
        return True

    except Exception as exc:
        session.rollback()
        logger.exception("PodcastEpisode %d generation failed", episode_id)

        fail_session = session_factory()
        try:
            fail_podcast(fail_session, episode_id, str(exc))
            fail_session.commit()
        except Exception:
            fail_session.rollback()
            logger.exception("Failed to mark PodcastEpisode %d as failed", episode_id)
        finally:
            fail_session.close()

        return False
    finally:
        session.close()


def run_once(settings: PodcastSettings, session_factory=None) -> None:
    if session_factory is None:
        session_factory = _DefaultSessionFactory
    worker_id = f"podcast-{socket.gethostname()}-{os.getpid()}"
    logger.info("podcast run_once (worker_id=%s)", worker_id)
    _run_one_iteration(settings, session_factory, worker_id)


def run(settings: PodcastSettings, session_factory=None) -> None:
    if session_factory is None:
        session_factory = _DefaultSessionFactory

    worker_id = f"podcast-{socket.gethostname()}-{os.getpid()}"
    stop_event = threading.Event()

    def _handle_signal(signum: int, _: object) -> None:
        logger.info("Signal %d received, stopping", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info("Podcast daemon starting (worker_id=%s)", worker_id)

    while not stop_event.is_set():
        try:
            _run_one_iteration(settings, session_factory, worker_id)
        except Exception:
            logger.exception("Unexpected error in poll iteration")
        stop_event.wait(timeout=settings.podcast_poll_interval_seconds)

    logger.info("Podcast daemon stopped cleanly")
