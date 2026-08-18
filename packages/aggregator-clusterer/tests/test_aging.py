"""Regression tests for thread lifecycle aging (active→dormant→archived) and reactivation.

Tests:
1. active→dormant when last_updated older than dormant_age_days
2. dormant→archived when last_updated older than dormant_age_days + archive_age_days
3. Recent/active thread NOT transitioned
4. A thread idle beyond both thresholds jumps to archived in one cycle
5. Aging is idempotent
6. Reactivation: assigning an article to a dormant thread sets status back to active
7. Merging into a dormant thread reactivates it
8. Surfacing/merge passes still operate only on active threads
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aggregator_clusterer.classification import ClassificationResult
from aggregator_clusterer.config import ClustererSettings
from aggregator_clusterer.consolidate import (
    run_aging_pass,
    run_merge_pass,
    run_surfacing_pass,
)
from aggregator_clusterer.upsert import process_classification
from aggregator_common.management import merge_threads
from aggregator_common.models import ClassificationLabel, ThreadMembership

from .conftest import make_article, make_source, make_thread

_SETTINGS = ClustererSettings()

_DORMANT_DAYS = _SETTINGS.clusterer_dormant_age_days   # 7
_ARCHIVE_DAYS = _SETTINGS.clusterer_archive_age_days   # 30


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


class TestAgingTransitions:
    def test_active_thread_becomes_dormant_when_old_enough(self, db_session):
        """Thread idle beyond dormant_age_days transitions active→dormant."""
        last_updated = _now() - timedelta(days=_DORMANT_DAYS + 1)
        thread = make_thread(db_session, title="Old Thread", last_updated=last_updated, status="active")

        count = run_aging_pass(db_session, _SETTINGS)

        db_session.refresh(thread)
        assert thread.status == "dormant"
        assert count >= 1

    def test_dormant_thread_becomes_archived_when_old_enough(self, db_session):
        """Thread idle beyond dormant+archive days transitions dormant→archived."""
        last_updated = _now() - timedelta(days=_DORMANT_DAYS + _ARCHIVE_DAYS + 1)
        thread = make_thread(db_session, title="Ancient Thread", last_updated=last_updated, status="dormant")

        count = run_aging_pass(db_session, _SETTINGS)

        db_session.refresh(thread)
        assert thread.status == "archived"
        assert count >= 1

    def test_recent_active_thread_not_transitioned(self, db_session):
        """Thread updated within dormant_age_days stays active."""
        last_updated = _now() - timedelta(days=_DORMANT_DAYS - 2)
        thread = make_thread(db_session, title="Fresh Thread", last_updated=last_updated, status="active")

        count = run_aging_pass(db_session, _SETTINGS)

        db_session.refresh(thread)
        assert thread.status == "active"
        assert count == 0

    def test_active_thread_jumps_to_archived_in_one_cycle(self, db_session):
        """Thread idle far beyond the combined threshold reaches archived in one cycle.

        active→dormant runs first, then dormant→archived catches it in the same pass.
        """
        last_updated = _now() - timedelta(days=_DORMANT_DAYS + _ARCHIVE_DAYS + 1)
        thread = make_thread(db_session, title="Very Old Thread", last_updated=last_updated, status="active")

        count = run_aging_pass(db_session, _SETTINGS)

        db_session.refresh(thread)
        assert thread.status == "archived"
        # One count from active→dormant, one from dormant→archived
        assert count == 2

    def test_aging_pass_is_idempotent(self, db_session):
        """Running the aging pass twice with no new eligible threads is a no-op on the second run."""
        last_updated = _now() - timedelta(days=_DORMANT_DAYS + 1)
        thread = make_thread(db_session, title="Idle Thread", last_updated=last_updated, status="active")

        run_aging_pass(db_session, _SETTINGS)
        db_session.commit()
        db_session.refresh(thread)
        assert thread.status == "dormant"

        count2 = run_aging_pass(db_session, _SETTINGS)
        assert count2 == 0

    def test_aging_does_not_touch_archived_threads(self, db_session):
        """Archived threads are not re-transitioned by the aging pass."""
        last_updated = _now() - timedelta(days=_DORMANT_DAYS + _ARCHIVE_DAYS + 5)
        thread = make_thread(db_session, title="Already Archived", last_updated=last_updated, status="archived")

        count = run_aging_pass(db_session, _SETTINGS)

        db_session.refresh(thread)
        assert thread.status == "archived"
        # count == 0 because the thread was already archived — no active→dormant or dormant→archived change
        assert count == 0


class TestReactivation:
    def test_assigning_article_to_dormant_thread_reactivates_it(self, db_session):
        """Assigning a new article to a dormant thread sets its status back to active."""
        src = make_source(db_session, url="https://reactivation.test/feed.xml")
        last_updated = _now() - timedelta(days=_DORMANT_DAYS + 2)
        dormant_thread = make_thread(
            db_session,
            title="Dormant Story",
            last_updated=last_updated,
            status="dormant",
            source_list=[src.id],
        )
        article = make_article(
            db_session,
            source_id=src.id,
            dedup_key="reactivation-k1",
            feed_title="Dormant Story Update",
        )

        result = ClassificationResult(
            label=ClassificationLabel.same_thread_new_fact,
            thread_id=dormant_thread.id,
            confidence=0.9,
            new_facts=["New development"],
            reason="reactivation test",
        )
        process_classification(db_session, article, result, _SETTINGS)
        db_session.flush()

        db_session.refresh(dormant_thread)
        assert dormant_thread.status == "active"

    def test_merge_reactivates_dormant_kept_thread(self, db_session):
        """Merging another thread into a dormant kept thread reactivates it to active."""
        last_updated = _now() - timedelta(days=_DORMANT_DAYS + 2)
        dormant_keep = make_thread(
            db_session, title="Dormant Keep", last_updated=last_updated, status="dormant"
        )
        active_absorb = make_thread(db_session, title="Active Absorb", status="active")

        merge_threads(db_session, dormant_keep.id, active_absorb.id)
        db_session.flush()

        db_session.refresh(dormant_keep)
        assert dormant_keep.status == "active"

    def test_merge_reactivates_archived_kept_thread(self, db_session):
        """Merging into an archived thread reactivates it to active."""
        last_updated = _now() - timedelta(days=_DORMANT_DAYS + _ARCHIVE_DAYS + 5)
        archived_keep = make_thread(
            db_session, title="Archived Keep", last_updated=last_updated, status="archived"
        )
        active_absorb = make_thread(db_session, title="Active Absorb 2", status="active")

        merge_threads(db_session, archived_keep.id, active_absorb.id)
        db_session.flush()

        db_session.refresh(archived_keep)
        assert archived_keep.status == "active"


class TestActivePasses:
    def test_surfacing_pass_ignores_dormant_threads(self, db_session):
        """run_surfacing_pass only processes active threads; dormant are excluded."""
        src = make_source(db_session, url="https://surfacing-aging.test/feed.xml")
        dormant = make_thread(
            db_session,
            title="Dormant Thread",
            last_updated=_now() - timedelta(days=_DORMANT_DAYS + 2),
            status="dormant",
        )
        art = make_article(db_session, source_id=src.id, dedup_key="surf-aging-k1", importance_score=90)
        db_session.add(
            ThreadMembership(
                thread_id=dormant.id,
                article_id=art.id,
                suppressed=False,
                assigned_at=_now(),
            )
        )
        db_session.commit()

        run_surfacing_pass(db_session, _SETTINGS)

        db_session.refresh(dormant)
        assert dormant.surfaced is False

    def test_merge_pass_ignores_dormant_threads(self, db_session):
        """run_merge_pass only considers active threads; dormant are not merge candidates."""
        dormant = make_thread(
            db_session,
            title="Dormant Merge Thread",
            last_updated=_now() - timedelta(days=_DORMANT_DAYS + 2),
            status="dormant",
        )
        _active = make_thread(db_session, title="Active Merge Thread", status="active")

        _NEVER_MERGE = lambda t1, t2: False  # noqa: E731
        merges = run_merge_pass(db_session, _SETTINGS, _NEVER_MERGE)
        # With only one active thread, find_merge_candidates returns [] (needs >= 2 active)
        assert merges == 0

        db_session.refresh(dormant)
        assert dormant.status == "dormant"  # untouched by merge pass
