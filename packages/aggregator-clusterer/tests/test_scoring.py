"""Tests for aggregator_clusterer.scoring module."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aggregator_clusterer.config import ClustererSettings
from aggregator_clusterer.scoring import _time_sensitivity, score_and_tier, update_thread_scores
from aggregator_common.models import ClassificationLabel, Thread, ThreadMembership

from .conftest import make_article, make_source, make_thread

_SETTINGS = ClustererSettings()
_NOW = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class TestTimeSensitivity:
    def test_zero_hours_returns_one(self):
        ts = _time_sensitivity(datetime.now(tz=timezone.utc))
        assert ts == 1.0

    def test_at_peak_hours_returns_one(self):
        # Use 5.9h to stay safely inside the <= 6h peak range
        last_updated = datetime.now(tz=timezone.utc) - timedelta(hours=5, minutes=54)
        ts = _time_sensitivity(last_updated)
        assert ts == 1.0

    def test_midpoint_decay(self):
        # At 39h: span=66, (39-6)/66 = 0.5
        last_updated = datetime.now(tz=timezone.utc) - timedelta(hours=39)
        ts = _time_sensitivity(last_updated)
        assert abs(ts - 0.5) < 0.01

    def test_at_floor_hours_returns_zero(self):
        last_updated = datetime.now(tz=timezone.utc) - timedelta(hours=72)
        ts = _time_sensitivity(last_updated)
        assert ts == 0.0

    def test_beyond_floor_hours_returns_zero(self):
        last_updated = datetime.now(tz=timezone.utc) - timedelta(hours=100)
        ts = _time_sensitivity(last_updated)
        assert ts == 0.0


class TestUpdateThreadScores:
    def test_sets_all_five_score_fields(self):
        thread = Thread(
            representative_title="Test",
            first_seen=_NOW,
            last_updated=_NOW,
        )
        update_thread_scores(
            thread,
            relevance=0.5,
            novelty=0.3,
            importance=0.8,
            diversity=0.6,
            time_sensitivity=0.9,
            tier="must_know",
            tier_reason="High importance across 2 sources",
        )
        assert thread.relevance_score == 0.5
        assert thread.novelty_score == 0.3
        assert thread.importance_score == 0.8
        assert thread.diversity_score == 0.6
        assert thread.time_sensitivity_score == 0.9
        assert thread.tier == "must_know"
        assert thread.tier_reason == "High importance across 2 sources"


class TestScoreAndTier:
    def _make_high_score_thread(self, db_session):
        src = make_source(db_session, url="https://score-hi.test/feed.xml")
        thread = make_thread(
            db_session,
            last_updated=datetime.now(tz=timezone.utc),
            confidence=1.0,
            source_diversity=1.0,
            source_list=[src.id],
        )
        article = make_article(
            db_session, source_id=src.id, dedup_key="k1",
            importance_score=100,
        )
        db_session.add(ThreadMembership(
            thread_id=thread.id,
            article_id=article.id,
            classification_label=ClassificationLabel.same_thread_new_fact.value,
            suppressed=False,
            assigned_at=datetime.now(tz=timezone.utc),
        ))
        db_session.commit()
        db_session.refresh(thread)
        return thread

    def test_must_know_tier_when_all_inputs_high(self, db_session):
        thread = self._make_high_score_thread(db_session)
        score_and_tier(db_session, thread, _SETTINGS)
        assert thread.tier == "must_know"

    def test_low_noise_tier_when_all_inputs_zero(self, db_session):
        src = make_source(db_session, url="https://score-lo.test/feed.xml")
        old_time = datetime.now(tz=timezone.utc) - timedelta(hours=80)
        thread = make_thread(
            db_session, last_updated=old_time,
            confidence=0.0, source_diversity=0.0,
        )
        # Article with no importance score
        article = make_article(db_session, source_id=src.id, dedup_key="k1", importance_score=None)
        db_session.add(ThreadMembership(
            thread_id=thread.id,
            article_id=article.id,
            classification_label=ClassificationLabel.same_thread_background_only.value,
            suppressed=True,
            assigned_at=datetime.now(tz=timezone.utc),
        ))
        db_session.commit()
        db_session.refresh(thread)
        score_and_tier(db_session, thread, _SETTINGS)
        assert thread.tier == "low_noise"

    def test_tier_reason_non_empty_for_must_know(self, db_session):
        thread = self._make_high_score_thread(db_session)
        score_and_tier(db_session, thread, _SETTINGS)
        assert thread.tier_reason is not None and len(thread.tier_reason) > 0

    def test_novelty_fraction_from_new_fact_labels(self, db_session):
        src = make_source(db_session, url="https://score-nov.test/feed.xml")
        thread = make_thread(db_session, last_updated=datetime.now(tz=timezone.utc))
        art1 = make_article(db_session, source_id=src.id, dedup_key="k1", importance_score=50)
        art2 = make_article(db_session, source_id=src.id, dedup_key="k2", importance_score=50)
        # One new_fact, one duplicate → novelty fraction = 0.5
        db_session.add(ThreadMembership(
            thread_id=thread.id,
            article_id=art1.id,
            classification_label=ClassificationLabel.same_thread_new_fact.value,
            suppressed=False,
            assigned_at=datetime.now(tz=timezone.utc),
        ))
        db_session.add(ThreadMembership(
            thread_id=thread.id,
            article_id=art2.id,
            classification_label=ClassificationLabel.same_thread_duplicate.value,
            suppressed=True,
            assigned_at=datetime.now(tz=timezone.utc),
        ))
        db_session.commit()
        db_session.refresh(thread)
        score_and_tier(db_session, thread, _SETTINGS)
        assert abs(thread.novelty_score - 0.5) < 0.01

    def test_aging_sets_dormant_when_seven_days_old(self, db_session):
        old_time = datetime.now(tz=timezone.utc) - timedelta(days=8)
        thread = make_thread(db_session, last_updated=old_time, status="active")
        score_and_tier(db_session, thread, _SETTINGS)
        assert thread.status == "dormant"

    def test_aging_sets_archived_when_thirty_days_old(self, db_session):
        very_old = datetime.now(tz=timezone.utc) - timedelta(days=31)
        thread = make_thread(db_session, last_updated=very_old, status="active")
        score_and_tier(db_session, thread, _SETTINGS)
        assert thread.status == "archived"

    def test_recent_thread_status_unchanged(self, db_session):
        recent = datetime.now(tz=timezone.utc) - timedelta(hours=1)
        thread = make_thread(db_session, last_updated=recent, status="active")
        score_and_tier(db_session, thread, _SETTINGS)
        assert thread.status == "active"
