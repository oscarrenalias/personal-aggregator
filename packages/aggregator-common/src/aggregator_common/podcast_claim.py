from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import PodcastEpisode


def claim_podcast(session: Session, worker_id: str, now: datetime) -> PodcastEpisode | None:
    stmt = (
        select(PodcastEpisode)
        .where(PodcastEpisode.status == "pending")
        .where(PodcastEpisode.claimed_at.is_(None))
        .order_by(PodcastEpisode.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    episode = session.scalars(stmt).first()
    if episode is None:
        return None
    episode.status = "generating"
    episode.claimed_by = worker_id
    episode.claimed_at = now
    session.flush()
    return episode


def complete_podcast(
    session: Session,
    episode_id: int,
    script_json: dict,
    audio_path: str,
    audio_size_bytes: int,
    duration_seconds: int,
    llm_model: str,
    tts_model: str,
    tts_voice: str,
) -> None:
    episode = session.get(PodcastEpisode, episode_id)
    if episode is None:
        raise ValueError(f"PodcastEpisode {episode_id} not found")
    episode.status = "ready"
    episode.script_json = script_json
    episode.audio_path = audio_path
    episode.audio_size_bytes = audio_size_bytes
    episode.duration_seconds = duration_seconds
    episode.llm_model = llm_model
    episode.tts_model = tts_model
    episode.tts_voice = tts_voice
    episode.generated_at = datetime.now(timezone.utc)
    episode.claimed_by = None
    episode.claimed_at = None
    # Derive theme from script_json so the column and the JSON never diverge.
    theme = (script_json.get("episode_theme") or "").strip()
    episode.episode_theme = theme or None
    # Derive artwork_url from script_json; empty string treated as NULL.
    artwork_url = (script_json.get("artwork_url") or "").strip()
    episode.artwork_url = artwork_url or None
    session.flush()


def fail_podcast(session: Session, episode_id: int, error: str) -> None:
    episode = session.get(PodcastEpisode, episode_id)
    if episode is None:
        raise ValueError(f"PodcastEpisode {episode_id} not found")
    episode.status = "failed"
    episode.error = error
    episode.claimed_by = None
    episode.claimed_at = None
    session.flush()


def reap_stale_podcast_claims(
    session: Session,
    lease_seconds: float,
    now: datetime,
) -> int:
    cutoff = now - timedelta(seconds=lease_seconds)
    stmt = (
        select(PodcastEpisode)
        .where(PodcastEpisode.status == "generating")
        .where(PodcastEpisode.claimed_at.is_not(None))
        .where(PodcastEpisode.claimed_at < cutoff)
        .with_for_update(skip_locked=True)
    )
    episodes = list(session.scalars(stmt).all())
    for episode in episodes:
        episode.status = "pending"
        episode.claimed_by = None
        episode.claimed_at = None
    session.flush()
    return len(episodes)
