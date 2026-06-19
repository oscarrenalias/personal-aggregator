"""Tests for aggregator_clusterer.candidates module."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aggregator_clusterer.candidates import get_candidates
from aggregator_clusterer.config import ClustererSettings
from aggregator_clusterer.dedup import check_duplicate
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

    def test_title_match_guarantees_candidate_with_zero_entity_topic_overlap(self, db_session):
        """Regression: thread with matching representative_title is a candidate even with zero entity/topic overlap.

        Before the fix, a thread with title_jaccard=1.0 but zero entity/topic/fts signals
        could fall outside the candidate cap, so the classifier would never see it and
        would create a duplicate new_thread instead.
        """
        src = make_source(db_session, url="https://cand-title-match.test/feed.xml")
        title = "US and Iran reach preliminary deal amid internal opposition"
        article = make_article(
            db_session, source_id=src.id, dedup_key="title-match-art",
            clean_title=title,
            entities=None,
            topics=None,
        )
        thread = make_thread(db_session, title=title)

        result = get_candidates(db_session, article, _SETTINGS)

        thread_ids = [c.thread_id for c in result]
        assert thread.id in thread_ids, "Title-matching thread must be a candidate"
        match = next(c for c in result if c.thread_id == thread.id)
        assert "title_jaccard" in match.signals
        assert match.signals["title_jaccard"] >= _SETTINGS.clusterer_title_jaccard_threshold

    def test_title_match_guaranteed_even_beyond_normal_cap(self, db_session):
        """Regression: title-matching thread is included even when cap=1 and another thread scores higher."""
        settings = ClustererSettings(clusterer_max_candidate_threads=1)
        src = make_source(db_session, url="https://cand-cap-title.test/feed.xml")
        title = "SpaceX surpasses Amazon with record valuation"
        now = datetime.now(tz=timezone.utc)

        # Thread that would normally win the cap slot via entity/topic overlap
        high_overlap_thread = make_thread(db_session, title="High Overlap Thread", last_updated=now)
        art1 = make_article(db_session, source_id=src.id, dedup_key="cap-title-a1",
                            entities=["SpaceX", "Amazon"], topics=["finance"])
        db_session.add(ThreadMembership(
            thread_id=high_overlap_thread.id, article_id=art1.id,
            classification_label=ClassificationLabel.new_thread.value,
            suppressed=False, assigned_at=now,
        ))
        db_session.commit()

        # Thread with matching title but zero entity/topic overlap
        title_match_thread = make_thread(db_session, title=title, last_updated=now)

        article = make_article(
            db_session, source_id=src.id, dedup_key="cap-title-art",
            clean_title=title, entities=None, topics=None,
        )

        result = get_candidates(db_session, article, settings)
        thread_ids = [c.thread_id for c in result]
        assert title_match_thread.id in thread_ids, "Title-matching thread must survive cap truncation"

    def test_title_jaccard_signal_present_for_all_candidates(self, db_session):
        """title_jaccard key must appear in every candidate's signals dict."""
        src = make_source(db_session, url="https://cand-tjsig.test/feed.xml")
        now = datetime.now(tz=timezone.utc)
        for i in range(3):
            make_thread(db_session, title=f"Thread About Something {i}", last_updated=now)
        article = make_article(db_session, source_id=src.id, dedup_key="tjsig-art")

        result = get_candidates(db_session, article, _SETTINGS)
        assert result, "Expected at least one candidate"
        for c in result:
            assert "title_jaccard" in c.signals

    def test_title_match_not_detected_by_dedup_but_surfaced_by_candidates(self, db_session):
        """Integration: article matching thread's representative_title is not caught by check_duplicate
        (which only scans member-article titles) but IS surfaced by get_candidates after the fix.

        This is the exact failure mode from the live Pi evidence: identical representative_title
        threads created because the matching thread was never a candidate.
        """
        src = make_source(db_session, url="https://integ-title.test/feed.xml")
        title = "Claude Fable 5 remains suspended under US export controls"

        # Thread with matching representative_title but NO member articles
        thread = make_thread(db_session, title=title)

        # New article with the same title, no entity/topic overlap
        article = make_article(
            db_session, source_id=src.id, dedup_key="integ-title-art",
            clean_title=title, entities=None, topics=None,
        )

        # dedup only checks member-article titles; the thread has none → returns None
        dedup_result = check_duplicate(db_session, article, _SETTINGS)
        assert dedup_result is None, "check_duplicate should not catch representative_title match"

        # candidates MUST include the thread after the fix
        candidates = get_candidates(db_session, article, _SETTINGS)
        candidate_ids = [c.thread_id for c in candidates]
        assert thread.id in candidate_ids, (
            "Thread with matching representative_title must be a candidate so the "
            "classifier can route the article to same_thread (not create new_thread)"
        )

    def test_aggregate_entity_overlap_union_outranks_latest_member_alone(self, db_session):
        """Union of all thread members' entities produces a higher overlap than the latest (drifted) member alone.

        Article entities: {A, B}
        Thread member 1 (earlier): entities {A, B}  — overlaps with article
        Thread member 2 (latest/drifted): entities {C, D} — no overlap with article

        Single-latest jaccard = 0/4 = 0.0
        Aggregate union jaccard = 2/4 = 0.5
        """
        src = make_source(db_session, url="https://agg-ov.test/feed.xml")
        article = make_article(
            db_session, source_id=src.id, dedup_key="agg-art",
            entities=["entityA", "entityB"],
        )
        thread = make_thread(db_session, title="Multi-member thread")

        earlier = make_article(
            db_session, source_id=src.id, dedup_key="agg-m1",
            entities=["entityA", "entityB"],
        )
        latest = make_article(
            db_session, source_id=src.id, dedup_key="agg-m2",
            entities=["entityC", "entityD"],
        )

        now = datetime.now(tz=timezone.utc)
        db_session.add(ThreadMembership(
            thread_id=thread.id,
            article_id=earlier.id,
            classification_label=ClassificationLabel.new_thread.value,
            suppressed=False,
            assigned_at=now - timedelta(hours=2),
        ))
        db_session.add(ThreadMembership(
            thread_id=thread.id,
            article_id=latest.id,
            classification_label=ClassificationLabel.same_thread_new_fact.value,
            suppressed=False,
            assigned_at=now,
        ))
        db_session.commit()

        result = get_candidates(db_session, article, _SETTINGS)
        match = next((c for c in result if c.thread_id == thread.id), None)
        assert match is not None

        # Aggregate union: jaccard({A,B}, {A,B,C,D}) = 2/4 = 0.5
        # Single-latest: jaccard({A,B}, {C,D}) = 0/4 = 0.0
        assert match.signals["entity_overlap"] > 0.0
        assert abs(match.signals["entity_overlap"] - 0.5) < 1e-9
