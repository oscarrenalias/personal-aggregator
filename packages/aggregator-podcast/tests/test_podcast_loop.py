"""Tests for the aggregator-podcast service end-to-end."""
from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from aggregator_common.models import PodcastEpisode
from aggregator_common.podcast_claim import (
    claim_podcast,
    complete_podcast,
    fail_podcast,
    reap_stale_podcast_claims,
)
from aggregator_common.queries import list_podcast_episodes
from aggregator_common.retention import purge_expired_podcast_episodes


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_episode(
    session: Session,
    *,
    episode_date: date,
    status: str = "pending",
    origin: str = "auto",
    audio_path: str | None = None,
    claimed_by: str | None = None,
    claimed_at: datetime | None = None,
) -> PodcastEpisode:
    ep = PodcastEpisode(
        date=episode_date,
        origin=origin,
        status=status,
        audio_path=audio_path,
        claimed_by=claimed_by,
        claimed_at=claimed_at,
    )
    session.add(ep)
    session.flush()
    return ep


# ── Enqueue tests ─────────────────────────────────────────────────────────────

def test_auto_enqueue_creates_one_row(session_factory):
    """_maybe_enqueue_auto_episode inserts exactly one row for today."""
    from aggregator_podcast.config import PodcastSettings
    from aggregator_podcast.loop import _maybe_enqueue_auto_episode

    settings = PodcastSettings(
        DATABASE_URL=os.environ["DATABASE_URL"],
        podcast_generation_hour=0,  # always past the hour
        podcast_timezone="UTC",
    )
    now = datetime.now(timezone.utc)
    session = session_factory()
    try:
        _maybe_enqueue_auto_episode(session, settings, now)
        session.commit()

        rows = session.query(PodcastEpisode).all()
        assert len(rows) == 1
        assert rows[0].status == "pending"
        assert rows[0].origin == "auto"
        assert rows[0].date == now.date()
    finally:
        session.close()


def test_auto_enqueue_no_duplicate(session_factory):
    """Calling _maybe_enqueue_auto_episode twice on the same day does not create a duplicate row (ON CONFLICT DO NOTHING)."""
    from aggregator_podcast.config import PodcastSettings
    from aggregator_podcast.loop import _maybe_enqueue_auto_episode

    settings = PodcastSettings(
        DATABASE_URL=os.environ["DATABASE_URL"],
        podcast_generation_hour=0,
        podcast_timezone="UTC",
    )
    now = datetime.now(timezone.utc)

    session = session_factory()
    try:
        _maybe_enqueue_auto_episode(session, settings, now)
        session.commit()

        # Second call for the same date must not insert a duplicate
        _maybe_enqueue_auto_episode(session, settings, now)
        session.commit()

        rows = session.query(PodcastEpisode).all()
        assert len(rows) == 1, "ON CONFLICT DO NOTHING must prevent duplicate rows"
    finally:
        session.close()


def test_auto_enqueue_skips_before_generation_hour(session_factory):
    """_maybe_enqueue_auto_episode returns False when the local hour is before the generation hour."""
    from aggregator_podcast.config import PodcastSettings
    from aggregator_podcast.loop import _maybe_enqueue_auto_episode

    settings = PodcastSettings(
        DATABASE_URL=os.environ["DATABASE_URL"],
        podcast_generation_hour=23,  # far in the future
        podcast_timezone="UTC",
    )
    # Use a time where hour < 23
    now = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)

    session = session_factory()
    try:
        inserted = _maybe_enqueue_auto_episode(session, settings, now)
        session.commit()
        assert inserted is False

        rows = session.query(PodcastEpisode).all()
        assert len(rows) == 0
    finally:
        session.close()


# ── claim_podcast ─────────────────────────────────────────────────────────────

def test_claim_podcast_transitions_to_generating(db_session):
    """claim_podcast sets status='generating' and populates claimed_by/claimed_at."""
    today = date.today()
    ep = _make_episode(db_session, episode_date=today, status="pending")
    db_session.commit()

    now = datetime.now(timezone.utc)
    claimed = claim_podcast(db_session, "worker-1", now)

    assert claimed is not None
    assert claimed.id == ep.id
    assert claimed.status == "generating"
    assert claimed.claimed_by == "worker-1"
    assert claimed.claimed_at == now


def test_claim_podcast_returns_none_on_empty_table(db_session):
    """claim_podcast returns None when there are no pending rows."""
    result = claim_podcast(db_session, "worker-1", datetime.now(timezone.utc))
    assert result is None


# ── complete_podcast ──────────────────────────────────────────────────────────

def test_complete_podcast_sets_ready_and_output_fields(db_session):
    """complete_podcast sets status='ready' and populates all output fields including episode_theme."""
    today = date.today()
    ep = _make_episode(db_session, episode_date=today, status="generating", claimed_by="w1")
    db_session.commit()

    script = {"segments": [], "episode_theme": "tech news today", "duration_estimate_seconds": 300}
    complete_podcast(
        db_session,
        ep.id,
        script_json=script,
        audio_path="/data/podcasts/2026-09-23.mp3",
        audio_size_bytes=1234567,
        duration_seconds=300,
        llm_model="gpt-4",
        tts_model="gpt-4o-mini-tts",
        tts_voice="marin",
    )
    db_session.commit()

    db_session.expire(ep)
    db_session.refresh(ep)
    assert ep.status == "ready"
    assert ep.script_json == script
    assert ep.audio_path == "/data/podcasts/2026-09-23.mp3"
    assert ep.audio_size_bytes == 1234567
    assert ep.duration_seconds == 300
    assert ep.llm_model == "gpt-4"
    assert ep.tts_model == "gpt-4o-mini-tts"
    assert ep.tts_voice == "marin"
    assert ep.claimed_by is None
    assert ep.claimed_at is None
    assert ep.generated_at is not None
    assert ep.episode_theme == "tech news today"


def test_complete_podcast_writes_episode_theme_from_script_json(db_session):
    """complete_podcast populates episode_theme from script_json, never from a separate arg."""
    today = date.today()
    ep = _make_episode(db_session, episode_date=today, status="generating", claimed_by="w1")
    db_session.commit()

    theme = "AI and geopolitics reshape the week's biggest headlines."
    script = {"segments": [], "episode_theme": theme, "duration_estimate_seconds": 500}
    complete_podcast(
        db_session, ep.id,
        script_json=script,
        audio_path="/data/podcasts/ep.mp3",
        audio_size_bytes=5000000,
        duration_seconds=500,
        llm_model="gpt-4",
        tts_model="gpt-4o-mini-tts",
        tts_voice="marin",
    )
    db_session.commit()

    db_session.expire(ep)
    db_session.refresh(ep)
    assert ep.episode_theme == theme


def test_complete_podcast_empty_theme_stored_as_null(db_session):
    """An empty string episode_theme in script_json is stored as NULL, not as ''."""
    today = date.today()
    ep = _make_episode(db_session, episode_date=today, status="generating", claimed_by="w1")
    db_session.commit()

    script = {"segments": [], "episode_theme": "   ", "duration_estimate_seconds": 300}
    complete_podcast(
        db_session, ep.id,
        script_json=script,
        audio_path="/data/podcasts/ep.mp3",
        audio_size_bytes=1000,
        duration_seconds=300,
        llm_model="gpt-4",
        tts_model="gpt-4o-mini-tts",
        tts_voice="marin",
    )
    db_session.commit()

    db_session.expire(ep)
    db_session.refresh(ep)
    assert ep.episode_theme is None


def test_complete_podcast_missing_theme_stored_as_null(db_session):
    """A missing episode_theme key in script_json leaves the column NULL."""
    today = date.today()
    ep = _make_episode(db_session, episode_date=today, status="generating", claimed_by="w1")
    db_session.commit()

    script = {"segments": [], "duration_estimate_seconds": 300}  # no episode_theme key
    complete_podcast(
        db_session, ep.id,
        script_json=script,
        audio_path="/data/podcasts/ep.mp3",
        audio_size_bytes=1000,
        duration_seconds=300,
        llm_model="gpt-4",
        tts_model="gpt-4o-mini-tts",
        tts_voice="marin",
    )
    db_session.commit()

    db_session.expire(ep)
    db_session.refresh(ep)
    assert ep.episode_theme is None


# ── fail_podcast ──────────────────────────────────────────────────────────────

def test_fail_podcast_sets_failed_and_error(db_session):
    """fail_podcast sets status='failed' and stores the error message."""
    today = date.today()
    ep = _make_episode(db_session, episode_date=today, status="generating", claimed_by="w1")
    db_session.commit()

    fail_podcast(db_session, ep.id, "Something went wrong")
    db_session.commit()

    db_session.expire(ep)
    db_session.refresh(ep)
    assert ep.status == "failed"
    assert ep.error == "Something went wrong"
    assert ep.claimed_by is None
    assert ep.claimed_at is None


# ── reap_stale_podcast_claims ─────────────────────────────────────────────────

def test_reap_stale_podcast_claims_resets_stale_row(db_session):
    """reap_stale_podcast_claims resets a generating row whose claimed_at is past the lease."""
    today = date.today()
    stale_claimed_at = datetime.now(timezone.utc) - timedelta(seconds=1000)
    ep = _make_episode(
        db_session,
        episode_date=today,
        status="generating",
        claimed_by="old-worker",
        claimed_at=stale_claimed_at,
    )
    db_session.commit()

    now = datetime.now(timezone.utc)
    reaped = reap_stale_podcast_claims(db_session, lease_seconds=600, now=now)
    db_session.commit()

    assert reaped == 1
    db_session.expire(ep)
    db_session.refresh(ep)
    assert ep.status == "pending"
    assert ep.claimed_by is None
    assert ep.claimed_at is None


def test_reap_does_not_touch_recent_claims(db_session):
    """reap_stale_podcast_claims ignores generating rows whose claimed_at is within the lease."""
    today = date.today()
    recent_claimed_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    ep = _make_episode(
        db_session,
        episode_date=today,
        status="generating",
        claimed_by="active-worker",
        claimed_at=recent_claimed_at,
    )
    db_session.commit()

    now = datetime.now(timezone.utc)
    reaped = reap_stale_podcast_claims(db_session, lease_seconds=600, now=now)
    db_session.commit()

    assert reaped == 0
    db_session.expire(ep)
    db_session.refresh(ep)
    assert ep.status == "generating"


# ── list_podcast_episodes ─────────────────────────────────────────────────────

def test_list_podcast_episodes_returns_only_ready(db_session):
    """list_podcast_episodes only returns episodes with status='ready'."""
    today = date.today()
    _make_episode(db_session, episode_date=today, status="pending")
    _make_episode(db_session, episode_date=today - timedelta(days=1), status="failed")
    ready = _make_episode(db_session, episode_date=today - timedelta(days=2), status="ready")
    db_session.commit()

    episodes, cursor = list_podcast_episodes(db_session)
    assert len(episodes) == 1
    assert episodes[0].id == ready.id
    assert cursor is None


def test_list_podcast_episodes_cursor_pagination(db_session):
    """list_podcast_episodes paginates correctly using keyset cursors."""
    base = date.today() - timedelta(days=10)
    for i in range(5):
        ep = _make_episode(
            db_session,
            episode_date=base + timedelta(days=i),
            status="ready",
        )
    db_session.commit()

    # Fetch page 1 with limit=2
    page1, cursor1 = list_podcast_episodes(db_session, limit=2)
    assert len(page1) == 2
    assert cursor1 is not None

    # Fetch page 2 using cursor
    page2, cursor2 = list_podcast_episodes(db_session, limit=2, cursor=cursor1)
    assert len(page2) == 2

    # Fetch page 3
    page3, cursor3 = list_podcast_episodes(db_session, limit=2, cursor=cursor2)
    assert len(page3) == 1
    assert cursor3 is None

    # All IDs unique and no overlap
    all_ids = [ep.id for ep in page1 + page2 + page3]
    assert len(set(all_ids)) == 5


# ── purge_expired_podcast_episodes ───────────────────────────────────────────

def test_purge_expired_podcast_episodes_deletes_old_rows(db_session):
    """purge_expired_podcast_episodes removes rows older than retention_days."""
    old_date = date.today() - timedelta(days=15)
    recent_date = date.today() - timedelta(days=1)

    old_ep = _make_episode(db_session, episode_date=old_date, status="ready")
    recent_ep = _make_episode(db_session, episode_date=recent_date, status="ready")
    db_session.commit()

    deleted = purge_expired_podcast_episodes(db_session, retention_days=7)
    db_session.commit()

    assert deleted == 1
    remaining = db_session.query(PodcastEpisode).all()
    assert len(remaining) == 1
    assert remaining[0].id == recent_ep.id


def test_purge_expired_podcast_episodes_removes_audio_file(db_session):
    """purge_expired_podcast_episodes deletes the associated audio file when it exists."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        audio_path = f.name
        f.write(b"fake audio data")

    try:
        old_date = date.today() - timedelta(days=15)
        _make_episode(
            db_session,
            episode_date=old_date,
            status="ready",
            audio_path=audio_path,
        )
        db_session.commit()

        assert Path(audio_path).exists()

        deleted = purge_expired_podcast_episodes(db_session, retention_days=7)
        db_session.commit()

        assert deleted == 1
        assert not Path(audio_path).exists()
    finally:
        if Path(audio_path).exists():
            os.remove(audio_path)


def test_purge_expired_podcast_episodes_no_audio_file(db_session):
    """purge_expired_podcast_episodes handles rows with audio_path=None gracefully."""
    old_date = date.today() - timedelta(days=15)
    _make_episode(db_session, episode_date=old_date, status="ready", audio_path=None)
    db_session.commit()

    deleted = purge_expired_podcast_episodes(db_session, retention_days=7)
    db_session.commit()

    assert deleted == 1
    assert db_session.query(PodcastEpisode).count() == 0


def test_purge_expired_podcast_episodes_retention_zero_purges_all(db_session):
    """With retention_days=0, all episodes older than today are purged."""
    yesterday = date.today() - timedelta(days=1)
    two_days_ago = date.today() - timedelta(days=2)
    _make_episode(db_session, episode_date=yesterday, status="ready")
    _make_episode(db_session, episode_date=two_days_ago, status="ready")
    db_session.commit()

    deleted = purge_expired_podcast_episodes(db_session, retention_days=0)
    db_session.commit()

    assert deleted == 2
    assert db_session.query(PodcastEpisode).count() == 0


def test_purge_expired_podcast_episodes_empty_table(db_session):
    """purge_expired_podcast_episodes returns 0 on an empty table."""
    deleted = purge_expired_podcast_episodes(db_session, retention_days=7)
    assert deleted == 0


# ── Continuity block ──────────────────────────────────────────────────────────

def test_continuity_block_empty_when_count_zero(db_session):
    """_build_continuity_block returns '' when continuity_count=0."""
    from aggregator_podcast.generate import _build_continuity_block

    result = _build_continuity_block(db_session, continuity_count=0)
    assert result == ""


def test_continuity_block_empty_when_no_ready_episodes(db_session):
    """_build_continuity_block returns '' when no ready episodes exist."""
    from aggregator_podcast.generate import _build_continuity_block

    # Add a pending episode — should not be included
    _make_episode(db_session, episode_date=date.today(), status="pending")
    db_session.commit()

    result = _build_continuity_block(db_session, continuity_count=2)
    assert result == ""


def test_continuity_block_contains_thread_ids_from_prior_episodes(db_session):
    """_build_continuity_block references thread_ids from prior ready episode script_json."""
    from aggregator_podcast.generate import _build_continuity_block

    script = {
        "episode_theme": "Tech daily",
        "segments": [
            {"type": "story", "thread_id": 42, "headline": "AI advances", "topic_category": "tech"},
            {"type": "story", "thread_id": 99, "headline": "Climate update", "topic_category": "environment"},
            {"type": "intro", "thread_id": None, "headline": "", "topic_category": ""},
        ],
    }
    ep = _make_episode(
        db_session,
        episode_date=date.today() - timedelta(days=1),
        status="ready",
    )
    ep.script_json = script
    ep.episode_theme = "Tech daily"
    db_session.commit()

    result = _build_continuity_block(db_session, continuity_count=2)

    assert "thread_id=42" in result
    assert "thread_id=99" in result
    assert "AI advances" in result
    assert "Climate update" in result


def test_continuity_block_fallback_on_bad_json(db_session, caplog):
    """_build_continuity_block handles malformed script_json gracefully (no crash)."""
    from aggregator_podcast.generate import _build_continuity_block

    ep = _make_episode(
        db_session,
        episode_date=date.today() - timedelta(days=1),
        status="ready",
    )
    ep.script_json = None  # No script; code should handle gracefully
    db_session.commit()

    # Should not raise
    result = _build_continuity_block(db_session, continuity_count=2)
    # Result is a string (possibly empty-ish since no story segments)
    assert isinstance(result, str)


# ── _measure_audio_duration ───────────────────────────────────────────────────

def test_measure_audio_duration_uses_ffprobe_output():
    """_measure_audio_duration returns the ffprobe-reported duration rounded to seconds."""
    from unittest.mock import patch, MagicMock
    from aggregator_podcast.loop import _measure_audio_duration

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "695.8\n"

    with patch("aggregator_podcast.loop.subprocess.run", return_value=mock_result) as mock_run:
        duration = _measure_audio_duration("/data/podcasts/ep.mp3", fallback=828)

    assert duration == 696
    mock_run.assert_called_once()


def test_measure_audio_duration_falls_back_on_ffprobe_nonzero(caplog):
    """_measure_audio_duration falls back to estimate when ffprobe exits non-zero."""
    import logging
    from unittest.mock import patch, MagicMock
    from aggregator_podcast.loop import _measure_audio_duration

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""

    with patch("aggregator_podcast.loop.subprocess.run", return_value=mock_result):
        with caplog.at_level(logging.WARNING, logger="aggregator_podcast.loop"):
            duration = _measure_audio_duration("/data/podcasts/ep.mp3", fallback=828)

    assert duration == 828
    assert any("non-zero" in r.message for r in caplog.records)


# ── complete_podcast: artwork_url normalisation ───────────────────────────────

def test_complete_podcast_stores_artwork_url_from_script_json(db_session):
    """complete_podcast persists artwork_url from script_json onto the episode row."""
    today = date.today()
    ep = _make_episode(db_session, episode_date=today, status="generating", claimed_by="w1")
    db_session.commit()

    script = {
        "segments": [],
        "episode_theme": "test",
        "duration_estimate_seconds": 300,
        "artwork_url": "https://cdn.example.com/cover.jpg",
    }
    complete_podcast(
        db_session, ep.id,
        script_json=script,
        audio_path="/data/podcasts/ep.mp3",
        audio_size_bytes=1000,
        duration_seconds=300,
        llm_model="gpt-4",
        tts_model="gpt-4o-mini-tts",
        tts_voice="marin",
    )
    db_session.commit()

    db_session.expire(ep)
    db_session.refresh(ep)
    assert ep.artwork_url == "https://cdn.example.com/cover.jpg"


def test_complete_podcast_empty_string_artwork_url_stored_as_null(db_session):
    """An empty-string artwork_url in script_json is normalised to NULL, not ''."""
    today = date.today()
    ep = _make_episode(db_session, episode_date=today, status="generating", claimed_by="w1")
    db_session.commit()

    script = {
        "segments": [],
        "episode_theme": "test",
        "duration_estimate_seconds": 300,
        "artwork_url": "",
    }
    complete_podcast(
        db_session, ep.id,
        script_json=script,
        audio_path="/data/podcasts/ep.mp3",
        audio_size_bytes=1000,
        duration_seconds=300,
        llm_model="gpt-4",
        tts_model="gpt-4o-mini-tts",
        tts_voice="marin",
    )
    db_session.commit()

    db_session.expire(ep)
    db_session.refresh(ep)
    assert ep.artwork_url is None


def test_complete_podcast_whitespace_artwork_url_stored_as_null(db_session):
    """A whitespace-only artwork_url is treated the same as empty string → NULL."""
    today = date.today()
    ep = _make_episode(db_session, episode_date=today, status="generating", claimed_by="w1")
    db_session.commit()

    script = {
        "segments": [],
        "episode_theme": "test",
        "duration_estimate_seconds": 300,
        "artwork_url": "   ",
    }
    complete_podcast(
        db_session, ep.id,
        script_json=script,
        audio_path="/data/podcasts/ep.mp3",
        audio_size_bytes=1000,
        duration_seconds=300,
        llm_model="gpt-4",
        tts_model="gpt-4o-mini-tts",
        tts_voice="marin",
    )
    db_session.commit()

    db_session.expire(ep)
    db_session.refresh(ep)
    assert ep.artwork_url is None


def test_complete_podcast_missing_artwork_url_key_stored_as_null(db_session):
    """A missing artwork_url key in script_json (legacy episodes) leaves the column NULL."""
    today = date.today()
    ep = _make_episode(db_session, episode_date=today, status="generating", claimed_by="w1")
    db_session.commit()

    script = {
        "segments": [],
        "episode_theme": "no artwork key here",
        "duration_estimate_seconds": 300,
        # deliberately no artwork_url key
    }
    complete_podcast(
        db_session, ep.id,
        script_json=script,
        audio_path="/data/podcasts/ep.mp3",
        audio_size_bytes=1000,
        duration_seconds=300,
        llm_model="gpt-4",
        tts_model="gpt-4o-mini-tts",
        tts_voice="marin",
    )
    db_session.commit()

    db_session.expire(ep)
    db_session.refresh(ep)
    # Must not raise; column defaults to NULL
    assert ep.artwork_url is None


def test_measure_audio_duration_falls_back_on_exception(caplog):
    """_measure_audio_duration falls back to estimate when ffprobe raises (e.g. not installed)."""
    import logging
    from unittest.mock import patch
    from aggregator_podcast.loop import _measure_audio_duration

    with patch("aggregator_podcast.loop.subprocess.run", side_effect=FileNotFoundError("ffprobe")):
        with caplog.at_level(logging.WARNING, logger="aggregator_podcast.loop"):
            duration = _measure_audio_duration("/data/podcasts/ep.mp3", fallback=600)

    assert duration == 600
    assert any("ffprobe" in r.message for r in caplog.records)
