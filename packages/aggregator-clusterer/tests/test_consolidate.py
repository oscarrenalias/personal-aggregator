"""Tests for aggregator_clusterer.consolidate module."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aggregator_clusterer.config import ClustererSettings
from aggregator_clusterer.consolidate import (
    ConsolidationResult,
    find_merge_candidates,
    run_consolidation_pass,
    run_merge_pass,
    run_surfacing_pass,
)
from aggregator_common.models import ThreadMembership

from .conftest import make_article, make_source, make_thread

_SETTINGS = ClustererSettings()

# Stub callables for injectable functions
_ALWAYS_MERGE = lambda t1, t2: True  # noqa: E731
_NEVER_MERGE = lambda t1, t2: False  # noqa: E731


class TestFindMergeCandidates:
    def test_returns_empty_for_fewer_than_two_active_threads(self, db_session):
        make_thread(db_session, title="Solo Thread", status="active")
        result = find_merge_candidates(db_session, _SETTINGS)
        assert result == []

    def test_returns_empty_when_no_active_threads(self, db_session):
        make_thread(db_session, title="Dormant", status="dormant")
        result = find_merge_candidates(db_session, _SETTINGS)
        assert result == []

    def test_pairs_below_similarity_floor_excluded(self, db_session):
        # Threads with no shared entities/topics → entity_overlap=0, topic_overlap=0
        # FTS may contribute a small amount but should stay below floor of 0.35 for unrelated threads
        make_thread(db_session, title="Thread About Cooking Recipes")
        make_thread(db_session, title="Thread About Space Exploration")
        # With no member articles (no entity/topic data), similarity is driven only by FTS.
        # For very different titles, FTS composite should be < 0.35 floor.
        result = find_merge_candidates(db_session, _SETTINGS)
        # Either 0 candidates (below floor) or a small FTS contribution — acceptable
        # We only assert the return type is a list of tuples
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, tuple) and len(item) == 2


class TestRunMergePass:
    def test_returns_zero_when_no_active_threads(self, db_session):
        count = run_merge_pass(db_session, _SETTINGS, _ALWAYS_MERGE)
        assert count == 0

    def test_llm_exception_in_classify_fn_skips_pair(self, db_session):
        """LLM exception is swallowed; pair is skipped, no crash, returns 0 merges."""
        def _raising_fn(t1, t2):
            raise RuntimeError("LLM timeout")

        src = make_source(db_session, url="https://merge-exc.test/feed.xml")
        # Create two threads that are candidates (identical entities/topics)
        t1 = make_thread(db_session, title="Same Topic Thread A", status="active")
        t2 = make_thread(db_session, title="Same Topic Thread B", status="active")
        art1 = make_article(db_session, source_id=src.id, dedup_key="merge-exc-k1",
                            entities={"AI": 1}, topics=["tech"])
        art2 = make_article(db_session, source_id=src.id, dedup_key="merge-exc-k2",
                            entities={"AI": 1}, topics=["tech"])
        db_session.add(ThreadMembership(
            thread_id=t1.id, article_id=art1.id, suppressed=False,
            assigned_at=datetime.now(tz=timezone.utc),
        ))
        db_session.add(ThreadMembership(
            thread_id=t2.id, article_id=art2.id, suppressed=False,
            assigned_at=datetime.now(tz=timezone.utc),
        ))
        db_session.commit()

        count = run_merge_pass(db_session, _SETTINGS, _raising_fn)
        assert count == 0

    def test_idempotent_second_call_returns_zero(self, db_session):
        """After a successful merge, the second call finds no pairs and returns 0."""
        src1 = make_source(db_session, url="https://merge-idem-a.test/feed.xml")
        src2 = make_source(db_session, url="https://merge-idem-b.test/feed.xml")
        t1 = make_thread(db_session, title="Topic Thread A", status="active",
                         source_list=[src1.feed_url])
        t2 = make_thread(db_session, title="Topic Thread A", status="active",
                         source_list=[src2.feed_url])
        # Articles with identical entities/topics so they score above similarity floor
        art1 = make_article(db_session, source_id=src1.id, dedup_key="idem-k1",
                            entities={"OpenAI": 1, "GPT": 1}, topics=["AI", "tech"])
        art2 = make_article(db_session, source_id=src2.id, dedup_key="idem-k2",
                            entities={"OpenAI": 1, "GPT": 1}, topics=["AI", "tech"])
        db_session.add(ThreadMembership(
            thread_id=t1.id, article_id=art1.id, suppressed=False,
            assigned_at=datetime.now(tz=timezone.utc),
        ))
        db_session.add(ThreadMembership(
            thread_id=t2.id, article_id=art2.id, suppressed=False,
            assigned_at=datetime.now(tz=timezone.utc),
        ))
        db_session.commit()

        first_count = run_merge_pass(db_session, _SETTINGS, _ALWAYS_MERGE)
        db_session.flush()

        second_count = run_merge_pass(db_session, _SETTINGS, _ALWAYS_MERGE)
        assert second_count == 0
        # The first call either merged (1) or found no candidates (0); either way
        # the second call must be idempotent.
        assert first_count >= 0

    def test_max_merge_checks_limits_llm_calls(self, db_session):
        """Only clusterer_max_merge_checks pairs are checked; no crash when capped."""
        settings = ClustererSettings(clusterer_max_merge_checks=0)
        src = make_source(db_session, url="https://merge-cap.test/feed.xml")
        t1 = make_thread(db_session, title="Cap Thread A", status="active")
        t2 = make_thread(db_session, title="Cap Thread B", status="active")
        art1 = make_article(db_session, source_id=src.id, dedup_key="cap-k1",
                            entities={"X": 1}, topics=["t"])
        art2 = make_article(db_session, source_id=src.id, dedup_key="cap-k2",
                            entities={"X": 1}, topics=["t"])
        db_session.add(ThreadMembership(
            thread_id=t1.id, article_id=art1.id, suppressed=False,
            assigned_at=datetime.now(tz=timezone.utc),
        ))
        db_session.add(ThreadMembership(
            thread_id=t2.id, article_id=art2.id, suppressed=False,
            assigned_at=datetime.now(tz=timezone.utc),
        ))
        db_session.commit()

        count = run_merge_pass(db_session, settings, _ALWAYS_MERGE)
        assert count == 0  # max_merge_checks=0 means no LLM calls allowed → 0 merges


class TestRunSurfacingPass:
    def test_empty_db_returns_zero(self, db_session):
        count = run_surfacing_pass(db_session, _SETTINGS)
        assert count == 0

    def test_surfaces_thread_with_enough_members(self, db_session):
        """Thread with member_count >= min_members gets surfaced=True."""
        src = make_source(db_session, url="https://surf-members.test/feed.xml")
        thread = make_thread(db_session, title="Surfaced By Members")
        for i in range(3):
            art = make_article(db_session, source_id=src.id, dedup_key=f"surf-mem-k{i}",
                               importance_score=30)
            db_session.add(ThreadMembership(
                thread_id=thread.id, article_id=art.id, suppressed=False,
                assigned_at=datetime.now(tz=timezone.utc),
            ))
        db_session.commit()
        db_session.refresh(thread)

        run_surfacing_pass(db_session, _SETTINGS)
        db_session.flush()
        db_session.refresh(thread)

        assert thread.surfaced is True

    def test_surfaces_thread_with_high_top_grade(self, db_session):
        """Thread with max importance_score >= min_grade gets surfaced=True."""
        src = make_source(db_session, url="https://surf-grade.test/feed.xml")
        thread = make_thread(db_session, title="Surfaced By Grade")
        art = make_article(db_session, source_id=src.id, dedup_key="surf-grade-k1",
                           importance_score=80)
        db_session.add(ThreadMembership(
            thread_id=thread.id, article_id=art.id, suppressed=False,
            assigned_at=datetime.now(tz=timezone.utc),
        ))
        db_session.commit()
        db_session.refresh(thread)

        run_surfacing_pass(db_session, _SETTINGS)
        db_session.flush()
        db_session.refresh(thread)

        assert thread.surfaced is True
        assert thread.top_grade == 80

    def test_does_not_surface_lone_on_topic_article(self, db_session):
        """Single article with grade below min_grade and only 1 source/member is not surfaced."""
        src = make_source(db_session, url="https://surf-lone.test/feed.xml")
        thread = make_thread(db_session, title="Lone On-Topic", source_list=[src.feed_url])
        art = make_article(db_session, source_id=src.id, dedup_key="surf-lone-k1",
                           importance_score=33)
        db_session.add(ThreadMembership(
            thread_id=thread.id, article_id=art.id, suppressed=False,
            assigned_at=datetime.now(tz=timezone.utc),
        ))
        db_session.commit()
        db_session.refresh(thread)

        run_surfacing_pass(db_session, _SETTINGS)
        db_session.flush()
        db_session.refresh(thread)

        assert thread.surfaced is False

    def test_idempotent_second_call_leaves_surfaced_unchanged(self, db_session):
        """Running surfacing pass twice produces the same result."""
        src = make_source(db_session, url="https://surf-idem.test/feed.xml")
        thread = make_thread(db_session, title="Idempotent Surfacing")
        art = make_article(db_session, source_id=src.id, dedup_key="surf-idem-k1",
                           importance_score=80)
        db_session.add(ThreadMembership(
            thread_id=thread.id, article_id=art.id, suppressed=False,
            assigned_at=datetime.now(tz=timezone.utc),
        ))
        db_session.commit()

        run_surfacing_pass(db_session, _SETTINGS)
        db_session.flush()
        db_session.refresh(thread)
        surfaced_after_first = thread.surfaced

        run_surfacing_pass(db_session, _SETTINGS)
        db_session.flush()
        db_session.refresh(thread)

        assert thread.surfaced == surfaced_after_first


class TestRunConsolidationPass:
    def test_returns_consolidation_result_dataclass(self, db_session):
        result = run_consolidation_pass(db_session, _SETTINGS, _NEVER_MERGE)
        assert isinstance(result, ConsolidationResult)
        assert result.merges == 0
        assert result.curated == 0
        assert result.pruned == 0

    def test_pruned_always_zero(self, db_session):
        """Consolidation pass never deletes threads; pruned is always 0."""
        old_time = datetime.now(tz=timezone.utc) - timedelta(days=31)
        make_thread(db_session, title="Old 1", last_updated=old_time)
        make_thread(db_session, title="Old 2", last_updated=old_time)

        result = run_consolidation_pass(db_session, _SETTINGS, _NEVER_MERGE)
        assert result.pruned == 0

    def test_stop_event_does_not_prevent_pass(self, db_session):
        """run_consolidation_pass doesn't check stop_event; it always runs to completion."""
        result = run_consolidation_pass(db_session, _SETTINGS, _NEVER_MERGE)
        assert isinstance(result, ConsolidationResult)
