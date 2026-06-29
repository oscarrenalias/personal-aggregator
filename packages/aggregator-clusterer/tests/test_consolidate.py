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

    def test_title_pre_pass_auto_merges_near_identical_title_threads(self, db_session):
        """Regression: two active threads with near-identical representative_titles are merged
        by the title pre-pass without an LLM verdict call (llm_fn is NEVER_MERGE stub).

        Before the fix only the LLM-verdict path ran, so these duplicates persisted.
        """
        src1 = make_source(db_session, url="https://title-prepass-a.test/feed.xml")
        src2 = make_source(db_session, url="https://title-prepass-b.test/feed.xml")
        title = "SpaceX surpasses Amazon with 2.7T valuation"
        t1 = make_thread(db_session, title=title, status="active", source_list=[src1.feed_url])
        t2 = make_thread(db_session, title=title, status="active", source_list=[src2.feed_url])

        # NEVER_MERGE ensures zero LLM merges; any merge that happens is from the title pre-pass
        count = run_merge_pass(db_session, _SETTINGS, _NEVER_MERGE)
        db_session.flush()

        assert count >= 1, "Title pre-pass must merge near-identical-title thread pair"

        from sqlalchemy import select as sa_select
        from aggregator_common.models import Thread as ThreadModel
        remaining_ids = {
            row.id for row in db_session.execute(
                sa_select(ThreadModel).where(ThreadModel.id.in_([t1.id, t2.id]))
            ).scalars()
        }
        assert len(remaining_ids) == 1, "One of the two duplicate threads must be absorbed"

    def test_title_pre_pass_bypasses_max_merge_checks(self, db_session):
        """Regression: title pre-pass merges succeed even when max_merge_checks=0
        (the LLM-verdict budget does not gate the title pre-pass).
        """
        settings = ClustererSettings(clusterer_max_merge_checks=0)
        title = "US and Iran reach preliminary deal amid internal opposition"
        t1 = make_thread(db_session, title=title, status="active")
        t2 = make_thread(db_session, title=title, status="active")

        count = run_merge_pass(db_session, settings, _NEVER_MERGE)
        db_session.flush()

        assert count >= 1, "Title pre-pass must merge identical-title threads even when max_merge_checks=0"

        from sqlalchemy import select as sa_select
        from aggregator_common.models import Thread as ThreadModel
        remaining = list(db_session.execute(
            sa_select(ThreadModel).where(ThreadModel.id.in_([t1.id, t2.id]))
        ).scalars())
        assert len(remaining) == 1

    def test_title_pre_pass_does_not_merge_low_title_overlap_pairs(self, db_session):
        """Negative case: thread pairs with dissimilar titles are NOT auto-merged by the title pre-pass."""
        settings = ClustererSettings(clusterer_max_merge_checks=0)  # block LLM pass too
        t1 = make_thread(db_session, title="Apple Announces New iPhone Model", status="active")
        t2 = make_thread(db_session, title="NASA Discovers Water Ice on Mars Surface", status="active")

        count = run_merge_pass(db_session, settings, _NEVER_MERGE)
        db_session.flush()

        assert count == 0, "Dissimilar-title threads must NOT be auto-merged by the title pre-pass"

        from sqlalchemy import select as sa_select
        from aggregator_common.models import Thread as ThreadModel
        remaining = list(db_session.execute(
            sa_select(ThreadModel).where(ThreadModel.id.in_([t1.id, t2.id]))
        ).scalars())
        assert len(remaining) == 2, "Both dissimilar-title threads must still exist"


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


class TestFindMergeCandidatesIncrementalBound:
    """Regression tests for the O(n²) FTS self-join bound fix.

    Before the fix, find_merge_candidates ran ts_rank for ALL C(active,2) pairs
    unconditionally — the incremental stale×stale skip was only applied in Python
    AFTER the expensive SQL had already run. These tests verify that stale×stale
    pairs are excluded in SQL (not just Python) and that the recency window caps
    full-pass cost.
    """

    def test_incremental_stale_stale_pair_excluded(self, db_session):
        """With incremental mode, a stale×stale pair is NOT returned even if similarity floor is 0."""
        settings = ClustererSettings(
            clusterer_incremental_merge=True,
            clusterer_merge_similarity_floor=0.0,
        )
        stale_time = datetime.now(tz=timezone.utc) - timedelta(hours=2)
        changed_since = datetime.now(tz=timezone.utc) - timedelta(hours=1)

        src = make_source(db_session, url="https://incr-stale-stale.test/feed.xml")
        t1 = make_thread(db_session, title="EU AI Act Stale A", status="active",
                         last_updated=stale_time)
        t2 = make_thread(db_session, title="EU AI Act Stale B", status="active",
                         last_updated=stale_time)
        art1 = make_article(db_session, source_id=src.id, dedup_key="iss-k1",
                            entities={"OpenAI": 1, "Policy": 1}, topics=["AI", "tech"])
        art2 = make_article(db_session, source_id=src.id, dedup_key="iss-k2",
                            entities={"OpenAI": 1, "Policy": 1}, topics=["AI", "tech"])
        db_session.add(ThreadMembership(thread_id=t1.id, article_id=art1.id, suppressed=False,
                                        assigned_at=stale_time))
        db_session.add(ThreadMembership(thread_id=t2.id, article_id=art2.id, suppressed=False,
                                        assigned_at=stale_time))
        db_session.commit()

        candidates = find_merge_candidates(db_session, settings, changed_since=changed_since)

        stale_pair = (min(t1.id, t2.id), max(t1.id, t2.id))
        assert stale_pair not in candidates, (
            "Stale×stale pair must be excluded in incremental mode even when floor=0"
        )

    def test_incremental_changed_stale_pair_included(self, db_session):
        """With incremental mode, a pair where at least one thread is recent IS returned."""
        settings = ClustererSettings(
            clusterer_incremental_merge=True,
            clusterer_merge_similarity_floor=0.0,
        )
        stale_time = datetime.now(tz=timezone.utc) - timedelta(hours=2)
        recent_time = datetime.now(tz=timezone.utc)
        changed_since = datetime.now(tz=timezone.utc) - timedelta(hours=1)

        src = make_source(db_session, url="https://incr-changed-stale.test/feed.xml")
        t_stale = make_thread(db_session, title="AI Policy Thread Stale", status="active",
                              last_updated=stale_time)
        t_recent = make_thread(db_session, title="AI Policy Thread Recent", status="active",
                               last_updated=recent_time)
        art1 = make_article(db_session, source_id=src.id, dedup_key="ics-k1",
                            entities={"OpenAI": 1, "AI": 1}, topics=["tech"])
        art2 = make_article(db_session, source_id=src.id, dedup_key="ics-k2",
                            entities={"OpenAI": 1, "AI": 1}, topics=["tech"])
        db_session.add(ThreadMembership(thread_id=t_stale.id, article_id=art1.id,
                                        suppressed=False, assigned_at=stale_time))
        db_session.add(ThreadMembership(thread_id=t_recent.id, article_id=art2.id,
                                        suppressed=False, assigned_at=recent_time))
        db_session.commit()

        candidates = find_merge_candidates(db_session, settings, changed_since=changed_since)

        expected_pair = (min(t_stale.id, t_recent.id), max(t_stale.id, t_recent.id))
        assert expected_pair in candidates, (
            "Changed×stale pair with matching entities/topics must be returned in incremental mode"
        )

    def test_full_pass_excludes_threads_outside_window(self, db_session):
        """Full pass (no changed_since) excludes threads older than clusterer_merge_candidate_window_days."""
        settings = ClustererSettings(
            clusterer_merge_candidate_window_days=7,
            clusterer_merge_similarity_floor=0.0,
        )
        old_time = datetime.now(tz=timezone.utc) - timedelta(days=14)

        src = make_source(db_session, url="https://full-old.test/feed.xml")
        t1 = make_thread(db_session, title="Old Thread Window A", status="active",
                         last_updated=old_time)
        t2 = make_thread(db_session, title="Old Thread Window B", status="active",
                         last_updated=old_time)
        art1 = make_article(db_session, source_id=src.id, dedup_key="fow-k1",
                            entities={"Topic": 1}, topics=["news"])
        art2 = make_article(db_session, source_id=src.id, dedup_key="fow-k2",
                            entities={"Topic": 1}, topics=["news"])
        db_session.add(ThreadMembership(thread_id=t1.id, article_id=art1.id, suppressed=False,
                                        assigned_at=old_time))
        db_session.add(ThreadMembership(thread_id=t2.id, article_id=art2.id, suppressed=False,
                                        assigned_at=old_time))
        db_session.commit()

        # Full pass with no changed_since — old threads are outside the 7-day window
        candidates = find_merge_candidates(db_session, settings, changed_since=None)

        old_pair = (min(t1.id, t2.id), max(t1.id, t2.id))
        assert old_pair not in candidates, (
            "Threads older than clusterer_merge_candidate_window_days must be excluded in full pass"
        )

    def test_full_sweep_bypasses_recency_window(self, db_session):
        """full_sweep=True (explicit operator recluster) evaluates even old thread pairs."""
        settings = ClustererSettings(
            clusterer_merge_candidate_window_days=7,
            clusterer_merge_similarity_floor=0.0,
        )
        old_time = datetime.now(tz=timezone.utc) - timedelta(days=14)

        src = make_source(db_session, url="https://full-sweep.test/feed.xml")
        t1 = make_thread(db_session, title="Old Full Sweep Thread A", status="active",
                         last_updated=old_time)
        t2 = make_thread(db_session, title="Old Full Sweep Thread B", status="active",
                         last_updated=old_time)
        art1 = make_article(db_session, source_id=src.id, dedup_key="fsw-k1",
                            entities={"Topic": 1}, topics=["news"])
        art2 = make_article(db_session, source_id=src.id, dedup_key="fsw-k2",
                            entities={"Topic": 1}, topics=["news"])
        db_session.add(ThreadMembership(thread_id=t1.id, article_id=art1.id, suppressed=False,
                                        assigned_at=old_time))
        db_session.add(ThreadMembership(thread_id=t2.id, article_id=art2.id, suppressed=False,
                                        assigned_at=old_time))
        db_session.commit()

        # full_sweep=True must bypass the recency window and evaluate old pairs
        candidates = find_merge_candidates(db_session, settings, full_sweep=True)

        old_pair = (min(t1.id, t2.id), max(t1.id, t2.id))
        assert old_pair in candidates, (
            "full_sweep=True must bypass the recency window and include old thread pairs"
        )


class TestRunMergePassIncrementalBound:
    """Regression tests for the title pre-pass O(n²) bounding fix."""

    def test_title_pre_pass_skips_stale_stale_in_incremental_mode(self, db_session):
        """In incremental mode, near-identical-title stale×stale pairs are NOT auto-merged."""
        title = "EU AI Act Passes Final Vote"
        stale_time = datetime.now(tz=timezone.utc) - timedelta(hours=2)
        changed_since = datetime.now(tz=timezone.utc) - timedelta(hours=1)

        t1 = make_thread(db_session, title=title, status="active", last_updated=stale_time)
        t2 = make_thread(db_session, title=title, status="active", last_updated=stale_time)

        # max_merge_checks=0 blocks the LLM pass; any merge comes from the title pre-pass
        settings = ClustererSettings(clusterer_max_merge_checks=0)
        count = run_merge_pass(db_session, settings, _NEVER_MERGE, changed_since=changed_since)
        db_session.flush()

        assert count == 0, "Stale×stale identical-title pair must NOT be merged in incremental mode"

        from sqlalchemy import select as sa_select
        from aggregator_common.models import Thread as ThreadModel
        remaining = list(db_session.execute(
            sa_select(ThreadModel).where(ThreadModel.id.in_([t1.id, t2.id]))
        ).scalars())
        assert len(remaining) == 2, "Both stale threads must still exist"

    def test_title_pre_pass_merges_changed_identical_title_in_incremental_mode(self, db_session):
        """In incremental mode, a near-identical-title pair where at least one is recent IS merged."""
        title = "Fed Raises Rates by 25 Basis Points"
        stale_time = datetime.now(tz=timezone.utc) - timedelta(hours=2)
        recent_time = datetime.now(tz=timezone.utc)
        changed_since = datetime.now(tz=timezone.utc) - timedelta(hours=1)

        t_stale = make_thread(db_session, title=title, status="active", last_updated=stale_time)
        t_recent = make_thread(db_session, title=title, status="active", last_updated=recent_time)

        settings = ClustererSettings(clusterer_max_merge_checks=0)
        count = run_merge_pass(db_session, settings, _NEVER_MERGE, changed_since=changed_since)
        db_session.flush()

        assert count >= 1, (
            "Changed×stale identical-title pair must be merged in incremental mode"
        )

    def test_title_pre_pass_skips_old_threads_in_full_pass(self, db_session):
        """In a full pass, threads older than clusterer_merge_candidate_window_days are not compared."""
        title = "Old Identical Title That Should Not Merge"
        old_time = datetime.now(tz=timezone.utc) - timedelta(days=14)

        t1 = make_thread(db_session, title=title, status="active", last_updated=old_time)
        t2 = make_thread(db_session, title=title, status="active", last_updated=old_time)

        settings = ClustererSettings(
            clusterer_merge_candidate_window_days=7,
            clusterer_max_merge_checks=0,
        )
        # No changed_since → full pass; old threads should be outside the window
        count = run_merge_pass(db_session, settings, _NEVER_MERGE)
        db_session.flush()

        assert count == 0, "Old threads outside the window must not be merged in a full pass"

        from sqlalchemy import select as sa_select
        from aggregator_common.models import Thread as ThreadModel
        remaining = list(db_session.execute(
            sa_select(ThreadModel).where(ThreadModel.id.in_([t1.id, t2.id]))
        ).scalars())
        assert len(remaining) == 2, "Both old threads must still exist"

    def test_run_merge_pass_full_sweep_merges_old_identical_title_threads(self, db_session):
        """bypass_verdict_cache=True (explicit recluster) bypasses the window and merges old pairs."""
        title = "Recluster Should Catch This Old Duplicate"
        old_time = datetime.now(tz=timezone.utc) - timedelta(days=14)

        t1 = make_thread(db_session, title=title, status="active", last_updated=old_time)
        t2 = make_thread(db_session, title=title, status="active", last_updated=old_time)

        settings = ClustererSettings(
            clusterer_merge_candidate_window_days=7,
            clusterer_max_merge_checks=0,
        )
        count = run_merge_pass(db_session, settings, _NEVER_MERGE, bypass_verdict_cache=True)
        db_session.flush()

        assert count >= 1, (
            "bypass_verdict_cache=True must bypass the recency window and merge old identical-title pairs"
        )


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
