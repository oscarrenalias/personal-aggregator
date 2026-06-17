"""Regression tests for consolidation throttle and dirty flag logic.

These tests cover the fix that prevents an idle clusterer from making LLM calls
every poll cycle. Key behaviours verified:
  - _set_dirty_flag / _mark_consolidation_done / _check_should_consolidate DB helpers
  - Idle cycles (no articles) produce no consolidation and zero LLM calls
  - After new content is clustered, consolidation runs at most once per MIN_INTERVAL
  - A second drain within the window does NOT re-run consolidation
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from aggregator_clusterer.config import ClustererSettings
from aggregator_clusterer.worker import (
    _check_should_consolidate,
    _mark_consolidation_done,
    _set_dirty_flag,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_cluster_state(
    session: Session,
    *,
    dirty: bool = False,
    last_consolidated_at: datetime | None = None,
    recluster_requested: bool = False,
) -> None:
    session.execute(
        text(
            "INSERT INTO cluster_state "
            "(id, recluster_requested, dirty, last_consolidated_at) "
            "VALUES (true, :rr, :dirty, :lca) "
            "ON CONFLICT (id) DO UPDATE "
            "SET recluster_requested = :rr, dirty = :dirty, last_consolidated_at = :lca"
        ),
        {
            "rr": recluster_requested,
            "dirty": dirty,
            "lca": last_consolidated_at,
        },
    )
    session.flush()


def _settings(min_interval_minutes: int = 10) -> ClustererSettings:
    return ClustererSettings(
        clusterer_consolidation_min_interval_minutes=min_interval_minutes
    )


# ---------------------------------------------------------------------------
# _set_dirty_flag
# ---------------------------------------------------------------------------

class TestSetDirtyFlag:
    def test_creates_row_when_absent(self, db_session):
        _set_dirty_flag(db_session)
        db_session.flush()
        row = db_session.execute(
            text("SELECT dirty FROM cluster_state WHERE id = true")
        ).one_or_none()
        assert row is not None
        assert row.dirty is True

    def test_sets_dirty_when_row_exists(self, db_session):
        _insert_cluster_state(db_session, dirty=False)
        _set_dirty_flag(db_session)
        db_session.flush()
        row = db_session.execute(
            text("SELECT dirty FROM cluster_state WHERE id = true")
        ).one()
        assert row.dirty is True

    def test_idempotent_when_already_dirty(self, db_session):
        _insert_cluster_state(db_session, dirty=True)
        _set_dirty_flag(db_session)
        db_session.flush()
        row = db_session.execute(
            text("SELECT dirty FROM cluster_state WHERE id = true")
        ).one()
        assert row.dirty is True


# ---------------------------------------------------------------------------
# _mark_consolidation_done
# ---------------------------------------------------------------------------

class TestMarkConsolidationDone:
    def test_clears_dirty_and_sets_timestamp(self, db_session):
        _insert_cluster_state(db_session, dirty=True)
        _mark_consolidation_done(db_session)
        db_session.flush()
        row = db_session.execute(
            text("SELECT dirty, last_consolidated_at FROM cluster_state WHERE id = true")
        ).one()
        assert row.dirty is False
        assert row.last_consolidated_at is not None

    def test_no_error_when_row_absent(self, db_session):
        # Should silently update 0 rows without crashing.
        _mark_consolidation_done(db_session)
        db_session.flush()


# ---------------------------------------------------------------------------
# _check_should_consolidate
# ---------------------------------------------------------------------------

class TestCheckShouldConsolidate:
    def test_returns_false_when_no_row(self, db_session):
        should, _ = _check_should_consolidate(db_session, _settings())
        assert not should

    def test_returns_false_when_not_dirty(self, db_session):
        _insert_cluster_state(db_session, dirty=False)
        should, _ = _check_should_consolidate(db_session, _settings())
        assert not should

    def test_returns_true_when_dirty_and_never_consolidated(self, db_session):
        # last_consolidated_at = NULL → treated as epoch → always >= min_interval
        _insert_cluster_state(db_session, dirty=True, last_consolidated_at=None)
        should, lca = _check_should_consolidate(db_session, _settings(min_interval_minutes=10))
        assert should
        assert lca is None

    def test_returns_false_when_dirty_but_within_window(self, db_session):
        # Last consolidation 2 minutes ago, window is 10 minutes → too soon
        recent = datetime.now(tz=timezone.utc) - timedelta(minutes=2)
        _insert_cluster_state(db_session, dirty=True, last_consolidated_at=recent)
        should, _ = _check_should_consolidate(db_session, _settings(min_interval_minutes=10))
        assert not should

    def test_returns_true_when_dirty_and_past_window(self, db_session):
        # Last consolidation 15 minutes ago, window is 10 minutes → should run
        old = datetime.now(tz=timezone.utc) - timedelta(minutes=15)
        _insert_cluster_state(db_session, dirty=True, last_consolidated_at=old)
        should, lca = _check_should_consolidate(db_session, _settings(min_interval_minutes=10))
        assert should
        assert lca is not None

    def test_returns_false_exactly_at_boundary(self, db_session):
        # Exactly at the boundary: elapsed == min_interval → should run (>=)
        # but set to 9m 59s → should NOT run
        just_under = datetime.now(tz=timezone.utc) - timedelta(seconds=599)
        _insert_cluster_state(db_session, dirty=True, last_consolidated_at=just_under)
        should, _ = _check_should_consolidate(db_session, _settings(min_interval_minutes=10))
        assert not should


# ---------------------------------------------------------------------------
# Integration: idle clusterer makes zero LLM calls
# ---------------------------------------------------------------------------

class TestIdleClustererMakesNoLLMCalls:
    """Verify that an idle clusterer (no articles to process) makes zero LLM calls
    and does not trigger consolidation."""

    def test_no_articles_dirty_never_set_no_consolidation(self, db_session):
        """With no articles, dirty is never set → _check_should_consolidate returns False.

        This is the core invariant: an idle clusterer (corpus fully drained, no new
        articles arriving) never triggers consolidation and therefore makes zero LLM calls.
        The cluster_state row is absent when no article has ever been assigned, so the
        check short-circuits to False.
        """
        settings = _settings(min_interval_minutes=10)
        # No cluster_state row inserted → row is absent.
        should, _ = _check_should_consolidate(db_session, settings)
        assert not should

    def test_dirty_cleared_after_consolidation_prevents_second_run(self, db_session):
        """After consolidation marks dirty=false, a subsequent check must return False."""
        old = datetime.now(tz=timezone.utc) - timedelta(minutes=20)
        _insert_cluster_state(db_session, dirty=True, last_consolidated_at=old)

        settings = _settings(min_interval_minutes=10)

        # First check: should consolidate
        should, _ = _check_should_consolidate(db_session, settings)
        assert should

        # Simulate consolidation completing
        _mark_consolidation_done(db_session)
        db_session.flush()

        # Second check within the window: dirty=false → must NOT consolidate
        should2, _ = _check_should_consolidate(db_session, settings)
        assert not should2

    def test_second_drain_within_window_does_not_retrigger(self, db_session):
        """After consolidation, a drained cycle within MIN_INTERVAL must not re-run."""
        # Consolidation ran 2 minutes ago → within 10-minute window
        recent = datetime.now(tz=timezone.utc) - timedelta(minutes=2)
        _insert_cluster_state(db_session, dirty=False, last_consolidated_at=recent)

        settings = _settings(min_interval_minutes=10)

        # Even if dirty was re-set now, the window prevents consolidation.
        _set_dirty_flag(db_session)
        db_session.flush()
        should, _ = _check_should_consolidate(db_session, settings)
        assert not should
