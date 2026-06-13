"""Tests for aggregator_clusterer.scoring module."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aggregator_clusterer.config import ClustererSettings
from aggregator_clusterer.scoring import _source_diversity, _time_sensitivity, score_and_tier, update_thread_scores
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
        # Two sources and two articles so the single-source gate does not fire.
        src1 = make_source(db_session, url="https://score-hi-a.test/feed.xml")
        src2 = make_source(db_session, url="https://score-hi-b.test/feed.xml")
        thread = make_thread(
            db_session,
            last_updated=datetime.now(tz=timezone.utc),
            confidence=1.0,
            source_diversity=1.0,
            source_list=[src1.feed_url, src2.feed_url],
        )
        art1 = make_article(db_session, source_id=src1.id, dedup_key="hi-k1", importance_score=100)
        art2 = make_article(db_session, source_id=src2.id, dedup_key="hi-k2", importance_score=100)
        for art in (art1, art2):
            db_session.add(ThreadMembership(
                thread_id=thread.id,
                article_id=art.id,
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


class TestSourceDiversity:
    def test_singleton_returns_zero(self):
        assert _source_diversity(1, _SETTINGS) == 0.0

    def test_saturated_returns_one(self):
        assert _source_diversity(4, _SETTINGS) == 1.0

    def test_intermediate_two_sources(self):
        val = _source_diversity(2, _SETTINGS)
        assert abs(val - 1 / 3) < 0.001

    def test_above_saturation_clamped_to_one(self):
        assert _source_diversity(10, _SETTINGS) == 1.0


class TestSingleSourceGate:
    def test_single_source_high_importance_capped_to_worth_tracking(self, db_session):
        """Gate fires on 1-source thread with composite >= must_know; high importance → worth_tracking."""
        src = make_source(db_session, url="https://gate-sshi.test/feed.xml")
        thread = make_thread(
            db_session,
            last_updated=datetime.now(tz=timezone.utc),
            confidence=1.0,
            source_list=[src.feed_url],
        )
        article = make_article(
            db_session, source_id=src.id, dedup_key="gate-sshi-k1",
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
        score_and_tier(db_session, thread, _SETTINGS)
        assert thread.tier == "worth_tracking"
        assert "[single-source gate" in (thread.tier_reason or "")

    def test_single_member_low_importance_capped_to_deep_read(self, db_session):
        """Gate fires on 1-member thread; importance < threshold → deep_read."""
        src1 = make_source(db_session, url="https://gate-smlo-a.test/feed.xml")
        src2 = make_source(db_session, url="https://gate-smlo-b.test/feed.xml")
        src3 = make_source(db_session, url="https://gate-smlo-c.test/feed.xml")
        src4 = make_source(db_session, url="https://gate-smlo-d.test/feed.xml")
        # 4 sources → source_count=4 >= 2, so source gate won't fire
        # 1 article → len(articles)=1 < 2, so member gate fires
        thread = make_thread(
            db_session,
            last_updated=datetime.now(tz=timezone.utc),
            confidence=1.0,
            source_list=[src1.feed_url, src2.feed_url, src3.feed_url, src4.feed_url],
        )
        # importance_score=74 → importance=0.74 < must_know_threshold=0.75
        # novelty=1.0, diversity=1.0, ts=1.0 → composite ≈ 0.896 → must_know before gate
        article = make_article(
            db_session, source_id=src1.id, dedup_key="gate-smlo-k1",
            importance_score=74,
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
        score_and_tier(db_session, thread, _SETTINGS)
        assert thread.tier in ("deep_read", "low_noise")
        assert "[single-source gate" in (thread.tier_reason or "")

    def test_two_source_two_member_thread_reaches_must_know(self, db_session):
        """2-source, 2-member thread with high composite → must_know with no gate note."""
        src1 = make_source(db_session, url="https://gate-2s2m-a.test/feed.xml")
        src2 = make_source(db_session, url="https://gate-2s2m-b.test/feed.xml")
        thread = make_thread(
            db_session,
            last_updated=datetime.now(tz=timezone.utc),
            confidence=1.0,
            source_list=[src1.feed_url, src2.feed_url],
        )
        art1 = make_article(db_session, source_id=src1.id, dedup_key="gate-2s2m-k1", importance_score=100)
        art2 = make_article(db_session, source_id=src2.id, dedup_key="gate-2s2m-k2", importance_score=100)
        for art in (art1, art2):
            db_session.add(ThreadMembership(
                thread_id=thread.id,
                article_id=art.id,
                classification_label=ClassificationLabel.same_thread_new_fact.value,
                suppressed=False,
                assigned_at=datetime.now(tz=timezone.utc),
            ))
        db_session.commit()
        db_session.refresh(thread)
        score_and_tier(db_session, thread, _SETTINGS)
        assert thread.tier == "must_know"
        assert "[single-source gate" not in (thread.tier_reason or "")
