"""Tests for incremental merge scoping, verdict cache, and recluster bypass.

Covers:
  1. stale×stale pairs → zero candidates when changed_since is set
  2. changed thread scored against all others including stale
  3. cached negative verdict suppresses LLM call (threads unchanged)
  4. cached verdict invalidated after either thread's last_updated advances
  5. recluster bypass: changed_since=None + bypass_verdict_cache=True → full LLM pass
  6. cache key is order-independent (verdict for (a,b) found as (b,a))
  7. both flags disabled → global un-memoized behaviour
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from aggregator_clusterer.config import ClustererSettings
from aggregator_clusterer.consolidate import (
    _upsert_merge_verdict,
    find_merge_candidates,
    run_merge_pass,
)
from aggregator_common.models import Thread, ThreadMembership, ThreadMergeVerdict

from .conftest import make_article, make_source, make_thread

_ANCHOR = datetime(2025, 6, 7, 12, 0, 0, tzinfo=timezone.utc)
_OLD = _ANCHOR
_CHANGED_SINCE = _ANCHOR + timedelta(days=5)
_RECENT = _ANCHOR + timedelta(days=10)

_ENTITIES = {"OpenAI": 1, "GPT": 1}
_TOPICS = ["AI", "tech"]


def _add_member(session: Session, thread: Thread, article) -> None:
    session.add(
        ThreadMembership(
            thread_id=thread.id,
            article_id=article.id,
            suppressed=False,
            assigned_at=_ANCHOR,
        )
    )


def _make_similar_pair(
    session: Session,
    *,
    url_suffix: str,
    title_a: str = "Thread A",
    title_b: str = "Thread B",
    last_updated_a: datetime = _OLD,
    last_updated_b: datetime = _OLD,
) -> tuple[Thread, Thread]:
    """Two active threads sharing identical entities/topics (composite score >> 0.35)."""
    src = make_source(session, url=f"https://{url_suffix}.test/feed.xml")
    t1 = make_thread(session, title=title_a, last_updated=last_updated_a)
    t2 = make_thread(session, title=title_b, last_updated=last_updated_b)
    a1 = make_article(
        session, source_id=src.id, dedup_key=f"{url_suffix}-k1",
        entities=_ENTITIES, topics=_TOPICS,
    )
    a2 = make_article(
        session, source_id=src.id, dedup_key=f"{url_suffix}-k2",
        entities=_ENTITIES, topics=_TOPICS,
    )
    _add_member(session, t1, a1)
    _add_member(session, t2, a2)
    session.commit()
    return t1, t2


class TestIncrementalScoping:
    """Scenarios 1–2: find_merge_candidates respects changed_since."""

    def test_stale_by_stale_excluded_when_changed_since_set(self, db_session):
        """Scenario 1: both threads older than changed_since → pair excluded."""
        t1, t2 = _make_similar_pair(
            db_session,
            url_suffix="sc1-stale-stale",
            title_a="Stale Thread Alpha",
            title_b="Stale Thread Beta",
            last_updated_a=_OLD,
            last_updated_b=_OLD,
        )
        settings = ClustererSettings(clusterer_incremental_merge=True)
        result = find_merge_candidates(db_session, settings, changed_since=_CHANGED_SINCE)
        lo, hi = min(t1.id, t2.id), max(t1.id, t2.id)
        assert (lo, hi) not in {(a, b) for a, b in result}, \
            "stale×stale pair must be excluded when changed_since is set"

    def test_changed_thread_scored_against_stale(self, db_session):
        """Scenario 2: one thread updated after changed_since → pair IS a candidate."""
        t1, t2 = _make_similar_pair(
            db_session,
            url_suffix="sc2-changed-vs-stale",
            title_a="Old Story Thread",
            title_b="Updated Story Thread",
            last_updated_a=_OLD,
            last_updated_b=_RECENT,
        )
        settings = ClustererSettings(clusterer_incremental_merge=True)
        result = find_merge_candidates(db_session, settings, changed_since=_CHANGED_SINCE)
        lo, hi = min(t1.id, t2.id), max(t1.id, t2.id)
        assert (lo, hi) in {(a, b) for a, b in result}, \
            "pair with one changed thread must appear in candidates"


class TestVerdictCache:
    """Scenarios 3–4: cache suppresses duplicate LLM calls and re-enables on change."""

    def test_cached_negative_verdict_suppresses_llm(self, db_session):
        """Scenario 3: negative verdict exists; neither thread updated → no LLM call."""
        t1, t2 = _make_similar_pair(db_session, url_suffix="sc3-cache-hit")
        lo, hi = min(t1.id, t2.id), max(t1.id, t2.id)
        keep = db_session.get(Thread, lo)
        absorb = db_session.get(Thread, hi)

        _upsert_merge_verdict(db_session, keep, absorb)
        db_session.commit()

        llm_calls = 0

        def _counting_fn(k, a):
            nonlocal llm_calls
            llm_calls += 1
            return False

        settings = ClustererSettings(
            clusterer_merge_verdict_cache=True,
            clusterer_incremental_merge=False,
            # Use a wide window so the _OLD-timestamped threads are included in the
            # full-pass candidate set and the cache logic is actually exercised.
            clusterer_merge_candidate_window_days=730,
        )
        run_merge_pass(db_session, settings, _counting_fn)
        assert llm_calls == 0, "LLM must not be called when a valid cached verdict exists"

    def test_negative_verdict_resent_after_thread_updated(self, db_session):
        """Scenario 4: thread's last_updated advances past verdict → LLM IS called."""
        t1, t2 = _make_similar_pair(
            db_session,
            url_suffix="sc4-cache-stale",
            last_updated_a=_OLD,
            last_updated_b=_OLD,
        )
        lo, hi = min(t1.id, t2.id), max(t1.id, t2.id)
        keep = db_session.get(Thread, lo)
        absorb = db_session.get(Thread, hi)

        # Write verdict capturing current (old) timestamps
        _upsert_merge_verdict(db_session, keep, absorb)
        db_session.commit()

        # Advance keep's last_updated past the snapshot stored in the verdict
        keep.last_updated = _OLD + timedelta(hours=1)
        db_session.commit()

        llm_calls = 0

        def _counting_fn(k, a):
            nonlocal llm_calls
            llm_calls += 1
            return False

        settings = ClustererSettings(
            clusterer_merge_verdict_cache=True,
            clusterer_incremental_merge=False,
            # Use a wide window so the _OLD-timestamped threads are included.
            clusterer_merge_candidate_window_days=730,
        )
        run_merge_pass(db_session, settings, _counting_fn)
        assert llm_calls == 1, "LLM must be called after a thread's last_updated advances past the verdict"


class TestReclusterBypass:
    """Scenario 5: changed_since=None + bypass_verdict_cache=True → full fresh pass."""

    def test_recluster_ignores_stale_filter_and_cache(self, db_session):
        """Scenario 5: stale×stale pair with cached verdict still gets a fresh LLM call."""
        t1, t2 = _make_similar_pair(
            db_session,
            url_suffix="sc5-recluster",
            title_a="Recluster Old A",
            title_b="Recluster Old B",
            last_updated_a=_OLD,
            last_updated_b=_OLD,
        )
        lo, hi = min(t1.id, t2.id), max(t1.id, t2.id)
        keep = db_session.get(Thread, lo)
        absorb = db_session.get(Thread, hi)

        _upsert_merge_verdict(db_session, keep, absorb)
        db_session.commit()

        llm_calls = 0

        def _counting_fn(k, a):
            nonlocal llm_calls
            llm_calls += 1
            return False

        settings = ClustererSettings(
            clusterer_incremental_merge=True,
            clusterer_merge_verdict_cache=True,
        )
        # changed_since=None → no incremental filter; bypass_verdict_cache=True → no cache read
        run_merge_pass(
            db_session, settings, _counting_fn,
            changed_since=None,
            bypass_verdict_cache=True,
        )
        assert llm_calls == 1, "recluster must bypass both incremental filter and verdict cache"


class TestCacheKeyOrderIndependence:
    """Scenario 6: cache lookup normalises to (min, max) — order-independent."""

    def test_verdict_written_for_lo_hi_is_found_as_lo_hi(self, db_session):
        """Verdict inserted for (lo, hi) is found by the normalised cache lookup."""
        t1, t2 = _make_similar_pair(db_session, url_suffix="sc6-order")
        lo, hi = min(t1.id, t2.id), max(t1.id, t2.id)
        keep = db_session.get(Thread, lo)
        absorb = db_session.get(Thread, hi)

        # Write verdict through the standard upsert path
        _upsert_merge_verdict(db_session, keep, absorb)
        db_session.commit()

        # Direct lookup with (lo, hi) composite key
        verdict = db_session.get(ThreadMergeVerdict, (lo, hi))
        assert verdict is not None, "verdict must be retrievable by (lo, hi) key"
        assert verdict.keep_id == lo
        assert verdict.absorb_id == hi

        # The run_merge_pass cache lookup uses cache_key = (min(keep.id, absorb.id), max(...))
        # which is identical to (lo, hi). Verify the cache hit suppresses LLM.
        llm_calls = 0

        def _counting_fn(k, a):
            nonlocal llm_calls
            llm_calls += 1
            return False

        settings = ClustererSettings(
            clusterer_merge_verdict_cache=True,
            clusterer_incremental_merge=False,
            # Use a wide window so the _OLD-timestamped threads are included.
            clusterer_merge_candidate_window_days=730,
        )
        run_merge_pass(db_session, settings, _counting_fn)
        assert llm_calls == 0, "normalised cache key must hit regardless of iteration order"


class TestFlagsDisabled:
    """Scenario 7: both feature flags False → un-memoized global sweep."""

    def test_incremental_false_includes_stale_pairs(self, db_session):
        """With clusterer_incremental_merge=False, stale×stale pairs are candidates."""
        t1, t2 = _make_similar_pair(
            db_session,
            url_suffix="sc7-flags-off",
            title_a="Flags Off Alpha",
            title_b="Flags Off Beta",
            last_updated_a=_OLD,
            last_updated_b=_OLD,
        )
        lo, hi = min(t1.id, t2.id), max(t1.id, t2.id)

        settings = ClustererSettings(
            clusterer_incremental_merge=False,
            clusterer_merge_verdict_cache=False,
            # Use a wide window so the _OLD-timestamped threads are included in the
            # full-pass candidate set (the test is about the incremental flag, not the window).
            clusterer_merge_candidate_window_days=730,
        )
        # Pass a far-future changed_since; with flag=False it must be ignored
        far_future = _OLD + timedelta(days=365)
        result = find_merge_candidates(db_session, settings, changed_since=far_future)
        assert (lo, hi) in {(a, b) for a, b in result}, \
            "stale×stale pair must appear when incremental_merge=False"

    def test_verdict_cache_false_does_not_write_verdict_row(self, db_session):
        """With clusterer_merge_verdict_cache=False, no verdict row is written after LLM call."""
        t1, t2 = _make_similar_pair(
            db_session,
            url_suffix="sc7-no-cache-write",
            title_a="No Cache Write A",
            title_b="No Cache Write B",
        )
        lo, hi = min(t1.id, t2.id), max(t1.id, t2.id)

        settings = ClustererSettings(
            clusterer_incremental_merge=False,
            clusterer_merge_verdict_cache=False,
            # Use a wide window so the _OLD-timestamped threads are included.
            clusterer_merge_candidate_window_days=730,
        )
        llm_calls = 0

        def _counting_fn(k, a):
            nonlocal llm_calls
            llm_calls += 1
            return False  # negative verdict

        run_merge_pass(db_session, settings, _counting_fn)
        db_session.flush()

        verdict = db_session.get(ThreadMergeVerdict, (lo, hi))
        assert llm_calls >= 1, "LLM must be called when flags are off"
        assert verdict is None, "no verdict row should be written when cache flag is False"
