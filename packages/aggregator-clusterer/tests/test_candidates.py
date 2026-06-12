"""Tests for aggregator_clusterer.candidates module."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aggregator_clusterer.candidates import get_candidates
from aggregator_clusterer.config import ClustererSettings
from aggregator_common.models import ClassificationLabel, ThreadMembership

from .conftest import make_article, make_source, make_thread

_SETTINGS = ClustererSettings()


class TestGetCandidates:
    def test_empty_threads_returns_empty(self, db_session):
        src = make_source(db_session, url="https://cand-empty.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="k1")
        result = get_candidates(db_session, article, _SETTINGS)
        assert result == []

    def test_article_no_entities_topics_zero_overlap(self, db_session):
        src = make_source(db_session, url="https://cand-zero.test/feed.xml")
        article = make_article(
            db_session, source_id=src.id, dedup_key="k1",
            topics=None, entities=None,
        )
        make_thread(db_session, title="Some thread about things")
        result = get_candidates(db_session, article, _SETTINGS)
        assert len(result) == 1
        assert result[0].signals["entity_overlap"] == 0.0
        assert result[0].signals["topic_overlap"] == 0.0

    def test_url_match_signal_set_true(self, db_session):
        src = make_source(db_session, url="https://cand-url.test/feed.xml")
        article_url = "https://cand-url.test/article/1"
        existing = make_article(
            db_session, source_id=src.id, dedup_key="k1",
            raw_payload={"link": article_url},
        )
        thread = make_thread(db_session, title="Existing thread")
        db_session.add(ThreadMembership(
            thread_id=thread.id,
            article_id=existing.id,
            classification_label=ClassificationLabel.new_thread.value,
            suppressed=False,
            assigned_at=datetime.now(tz=timezone.utc),
        ))
        db_session.commit()

        new_art = make_article(
            db_session, source_id=src.id, dedup_key="k2",
            raw_payload={"link": article_url},
        )
        result = get_candidates(db_session, new_art, _SETTINGS)
        assert len(result) >= 1
        url_signals = [c.signals["url_match"] for c in result if c.thread_id == thread.id]
        assert url_signals == [True]

    def test_results_capped_at_max_candidates(self, db_session):
        src = make_source(db_session, url="https://cand-cap.test/feed.xml")
        settings = ClustererSettings(clusterer_max_candidate_threads=3)
        now = datetime.now(tz=timezone.utc)
        for i in range(10):
            make_thread(db_session, title=f"Thread {i}", last_updated=now)
        article = make_article(db_session, source_id=src.id, dedup_key="k1")
        result = get_candidates(db_session, article, settings)
        assert len(result) <= 3

    def test_results_sorted_descending_by_composite_score(self, db_session):
        src = make_source(db_session, url="https://cand-sort.test/feed.xml")
        now = datetime.now(tz=timezone.utc)
        for i in range(3):
            make_thread(db_session, title=f"Thread {i}", last_updated=now)
        article = make_article(db_session, source_id=src.id, dedup_key="k1")
        result = get_candidates(db_session, article, _SETTINGS)
        scores = [c.composite_score for c in result]
        assert scores == sorted(scores, reverse=True)

    def test_fast_window_excludes_old_threads(self, db_session):
        src = make_source(db_session, url="https://cand-win.test/feed.xml")
        settings = ClustererSettings(clusterer_candidate_window_hours_fast=48)
        # Thread last updated 73 hours ago — outside 48h fast window
        old_time = datetime.now(tz=timezone.utc) - timedelta(hours=73)
        old_thread = make_thread(db_session, title="Old thread", last_updated=old_time)
        # Article with recent feed_published_at uses the fast window
        new_art = make_article(
            db_session, source_id=src.id, dedup_key="k1",
            feed_published_at=datetime.now(tz=timezone.utc),
        )
        result = get_candidates(db_session, new_art, settings)
        thread_ids = [c.thread_id for c in result]
        assert old_thread.id not in thread_ids
