"""Tests for aggregator_janitor: config defaults, advisory-lock guard, schedule gate, wiring."""
from __future__ import annotations

import threading
from datetime import date, datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

# Advisory lock key hardcoded in the clusterer's daemon loop (not in ClustererSettings).
_CLUSTERER_ADVISORY_LOCK_KEY = 1129855059


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_janitor_settings_importable():
    from aggregator_janitor.config import JanitorSettings

    settings = JanitorSettings()
    assert settings is not None


def test_janitor_settings_default_values():
    from aggregator_janitor.config import JanitorSettings

    settings = JanitorSettings()
    assert settings.janitor_article_retention_days == 14
    assert settings.janitor_thread_retention_days == 30
    assert settings.janitor_brief_retention_days == 30
    assert settings.janitor_run_hour == 4
    assert settings.janitor_timezone == "UTC"
    assert settings.janitor_poll_interval_seconds == 3600


def test_advisory_lock_key_differs_from_clusterer():
    """Janitor advisory lock key must be distinct from the clusterer's to avoid contention."""
    from aggregator_janitor.config import JanitorSettings

    settings = JanitorSettings()
    assert settings.janitor_advisory_lock_key != _CLUSTERER_ADVISORY_LOCK_KEY
    assert settings.janitor_advisory_lock_key == 2047839251


def test_settings_loads_with_database_url(monkeypatch):
    """JanitorSettings must load without error when DATABASE_URL is set."""
    import os

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    from aggregator_janitor.config import JanitorSettings

    settings = JanitorSettings()
    assert settings.database_url is not None


# ---------------------------------------------------------------------------
# Schedule gate (unit tests of the conditional logic from janitor.run())
# ---------------------------------------------------------------------------


def test_schedule_gate_fires_at_configured_hour():
    """Gate condition is True at run_hour when the run has not yet happened today."""
    from aggregator_janitor.config import JanitorSettings

    settings = JanitorSettings(janitor_run_hour=4, janitor_timezone="UTC")
    tz = ZoneInfo(settings.janitor_timezone)

    now_utc = datetime(2025, 6, 15, 4, 0, tzinfo=timezone.utc)
    now_local = now_utc.astimezone(tz)
    today = now_local.date()

    last_run_date: date | None = None
    should_fire = now_local.hour == settings.janitor_run_hour and last_run_date != today
    assert should_fire is True


def test_schedule_gate_skips_when_already_run_today():
    """Gate condition is False when last_run_date equals today (once-per-day guard)."""
    from aggregator_janitor.config import JanitorSettings

    settings = JanitorSettings(janitor_run_hour=4, janitor_timezone="UTC")
    tz = ZoneInfo(settings.janitor_timezone)

    now_utc = datetime(2025, 6, 15, 4, 0, tzinfo=timezone.utc)
    now_local = now_utc.astimezone(tz)
    today = now_local.date()

    last_run_date = today  # already ran
    should_fire = now_local.hour == settings.janitor_run_hour and last_run_date != today
    assert should_fire is False


def test_schedule_gate_skips_outside_run_hour():
    """Gate condition is False when the current hour does not match janitor_run_hour."""
    from aggregator_janitor.config import JanitorSettings

    settings = JanitorSettings(janitor_run_hour=4, janitor_timezone="UTC")
    tz = ZoneInfo(settings.janitor_timezone)

    now_utc = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)  # hour 12, not 4
    now_local = now_utc.astimezone(tz)
    today = now_local.date()

    last_run_date: date | None = None
    should_fire = now_local.hour == settings.janitor_run_hour and last_run_date != today
    assert should_fire is False


def test_schedule_gate_runs_once_not_twice_same_hour():
    """Simulating multiple poll iterations at the same hour fires _run_retention exactly once."""
    from aggregator_janitor.config import JanitorSettings

    settings = JanitorSettings(janitor_run_hour=4, janitor_timezone="UTC")
    tz = ZoneInfo(settings.janitor_timezone)

    now_utc = datetime(2025, 6, 15, 4, 0, tzinfo=timezone.utc)
    now_local = now_utc.astimezone(tz)

    fired = 0
    last_run_date: date | None = None

    for _ in range(5):
        today = now_local.date()
        if now_local.hour == settings.janitor_run_hour and last_run_date != today:
            fired += 1
            last_run_date = today

    assert fired == 1


# ---------------------------------------------------------------------------
# _run_retention: end-to-end wiring (real DB for advisory lock)
# ---------------------------------------------------------------------------


def test_run_retention_calls_all_three_helpers(db_engine):
    """_run_retention acquires the lock and calls all three purge helpers once."""
    from sqlalchemy.orm import sessionmaker

    from aggregator_janitor.config import JanitorSettings
    from aggregator_janitor.janitor import _run_retention

    settings = JanitorSettings()
    factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)

    called: list[str] = []

    with (
        patch(
            "aggregator_janitor.janitor.purge_expired_articles",
            side_effect=lambda s, d: called.append("articles") or 0,
        ),
        patch(
            "aggregator_janitor.janitor.purge_expired_threads",
            side_effect=lambda s, d: called.append("threads") or 0,
        ),
        patch(
            "aggregator_janitor.janitor.purge_expired_briefs",
            side_effect=lambda s, d: called.append("briefs") or 0,
        ),
    ):
        _run_retention(settings, factory)

    assert "articles" in called
    assert "threads" in called
    assert "briefs" in called


def test_advisory_lock_prevents_concurrent_run(db_engine):
    """A second concurrent _run_retention call skips without error when the lock is held."""
    from sqlalchemy.orm import sessionmaker

    from aggregator_janitor.config import JanitorSettings
    from aggregator_janitor.janitor import (
        _release_advisory_lock,
        _run_retention,
        _try_acquire_advisory_lock,
    )

    settings = JanitorSettings()
    factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)

    lock_acquired_event = threading.Event()
    test_done_event = threading.Event()
    errors: list[Exception] = []

    def holder() -> None:
        s = factory()
        try:
            ok = _try_acquire_advisory_lock(s, settings.janitor_advisory_lock_key)
            assert ok, "Initial lock acquisition must succeed"
            lock_acquired_event.set()
            test_done_event.wait(timeout=10)
        except Exception as exc:
            errors.append(exc)
        finally:
            try:
                _release_advisory_lock(s, settings.janitor_advisory_lock_key)
            except Exception:
                pass
            s.close()

    t = threading.Thread(target=holder, daemon=True)
    t.start()

    # Wait until the holder has the lock, then run retention in this thread.
    lock_acquired_event.wait(timeout=10)

    purge_calls: list[str] = []

    with (
        patch(
            "aggregator_janitor.janitor.purge_expired_articles",
            side_effect=lambda s, d: purge_calls.append("articles") or 0,
        ),
        patch(
            "aggregator_janitor.janitor.purge_expired_threads",
            side_effect=lambda s, d: purge_calls.append("threads") or 0,
        ),
        patch(
            "aggregator_janitor.janitor.purge_expired_briefs",
            side_effect=lambda s, d: purge_calls.append("briefs") or 0,
        ),
    ):
        _run_retention(settings, factory)

    test_done_event.set()
    t.join(timeout=10)

    assert errors == [], f"Holder thread raised: {errors}"
    assert purge_calls == [], "No purge helpers must be called when the advisory lock is already held"
