"""Regression tests for known_facts consolidation in the periodic consolidation pass.

Covers:
- Thread over cap → recent facts preserved verbatim at tail, older replaced by stub condensed set.
- Thread at/under cap → untouched, LLM NOT called.
- Ordering preserved (recent verbatim remain at the end, in original order).
- Per-cycle bound (clusterer_max_facts_consolidations limits threads processed).
- Hysteresis: condensed result lands strictly below cap.
- Hard truncate: LLM over-returning is clipped to hard_limit.
- Change-guard: thread not re-condensed unless facts grew beyond condensed_len.
- known_facts_condensed_len recorded on success and on LLM failure (None return).
"""
from __future__ import annotations

from aggregator_clusterer.config import ClustererSettings
from aggregator_clusterer.consolidate import run_facts_consolidation_pass

from .conftest import make_thread


_DEFAULT = object()  # sentinel for "caller did not supply a result"


class _CountingStub:
    """Injectable stub that records calls and returns a fixed condensed list (or None).

    Pass ``result=None`` to simulate an LLM failure (returns None); omit ``result``
    or pass a list to simulate success.
    """

    def __init__(self, result: list[str] | None = _DEFAULT) -> None:  # type: ignore[assignment]
        self.calls: int = 0
        self._result: list[str] | None = (
            ["condensed A", "condensed B"] if result is _DEFAULT else result
        )

    def __call__(self, old_facts: list[str]) -> list[str] | None:
        self.calls += 1
        return self._result


_SETTINGS = ClustererSettings(
    clusterer_max_known_facts=10,
    clusterer_known_facts_keep_recent=5,
    clusterer_max_facts_consolidations=3,
    clusterer_facts_condensed_target_ratio=0.6,
)


class TestRunFactsConsolidationPass:
    def test_over_cap_recent_kept_verbatim_older_condensed(self, db_session):
        """Thread with > max_known_facts: last keep_recent facts stay verbatim at tail;
        older facts replaced by the condensed set; total <= max."""
        keep_recent = _SETTINGS.clusterer_known_facts_keep_recent  # 5
        all_facts = [f"fact-{i}" for i in range(15)]  # 15 > max=10
        recent_expected = all_facts[-keep_recent:]  # last 5 verbatim
        older_expected = all_facts[:-keep_recent]  # first 10 go to the stub

        condensed_result = ["condensed-1", "condensed-2"]
        stub = _CountingStub(result=condensed_result)

        thread = make_thread(db_session, title="Over-cap Thread", known_facts=all_facts)
        db_session.flush()

        count = run_facts_consolidation_pass(db_session, _SETTINGS, stub)
        db_session.flush()
        db_session.refresh(thread)

        assert count == 1
        assert stub.calls == 1

        facts_after = list(thread.known_facts or [])
        # Recent verbatim must be at the tail, in order
        assert facts_after[-keep_recent:] == recent_expected, (
            "Most-recent facts must be preserved verbatim at the tail"
        )
        # Head must be the condensed set
        assert facts_after[:-keep_recent] == condensed_result, (
            "Older facts must be replaced by the condensed set"
        )
        # Total within max
        assert len(facts_after) <= _SETTINGS.clusterer_max_known_facts

    def test_under_cap_thread_untouched_no_llm_call(self, db_session):
        """Thread at/under the cap is not touched and the LLM is never called."""
        facts = [f"fact-{i}" for i in range(_SETTINGS.clusterer_max_known_facts)]  # exactly at cap
        stub = _CountingStub()

        thread = make_thread(db_session, title="At-cap Thread", known_facts=facts)
        db_session.flush()

        count = run_facts_consolidation_pass(db_session, _SETTINGS, stub)
        db_session.flush()
        db_session.refresh(thread)

        assert count == 0
        assert stub.calls == 0
        assert list(thread.known_facts) == facts

    def test_empty_facts_thread_untouched(self, db_session):
        """Thread with no facts is not processed."""
        stub = _CountingStub()
        thread = make_thread(db_session, title="Empty Facts Thread", known_facts=[])
        db_session.flush()

        count = run_facts_consolidation_pass(db_session, _SETTINGS, stub)

        assert count == 0
        assert stub.calls == 0

    def test_ordering_recent_verbatim_at_tail_in_order(self, db_session):
        """Recent verbatim facts appear at the tail in their original insertion order."""
        keep_recent = _SETTINGS.clusterer_known_facts_keep_recent  # 5
        all_facts = [f"event-{chr(ord('A') + i)}" for i in range(12)]  # 12 > 10
        recent_expected = all_facts[-keep_recent:]

        stub = _CountingStub(result=["summary"])

        thread = make_thread(db_session, title="Ordering Thread", known_facts=all_facts)
        db_session.flush()

        run_facts_consolidation_pass(db_session, _SETTINGS, stub)
        db_session.flush()
        db_session.refresh(thread)

        facts_after = list(thread.known_facts or [])
        assert facts_after[-keep_recent:] == recent_expected, (
            "Recent facts must appear at the tail in original order"
        )

    def test_per_cycle_bound_limits_threads_condensed(self, db_session):
        """At most clusterer_max_facts_consolidations threads are processed per cycle."""
        max_consolidations = _SETTINGS.clusterer_max_facts_consolidations  # 3
        n_over_cap = 5  # more than the cap
        facts = [f"fact-{i}" for i in range(15)]

        stub = _CountingStub(result=["condensed"])
        threads = [
            make_thread(db_session, title=f"Bound Thread {j}", known_facts=facts)
            for j in range(n_over_cap)
        ]
        db_session.flush()

        count = run_facts_consolidation_pass(db_session, _SETTINGS, stub)

        assert count == max_consolidations, (
            "Should process at most clusterer_max_facts_consolidations threads per cycle"
        )
        assert stub.calls == max_consolidations

    def test_llm_exception_skips_thread_no_crash(self, db_session):
        """LLM exception for a thread is swallowed; that thread is skipped, others proceed."""
        facts = [f"fact-{i}" for i in range(15)]
        call_count = {"n": 0}

        def raising_stub(old_facts: list[str]) -> list[str] | None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated LLM failure")
            return ["ok condensed"]

        thread1 = make_thread(db_session, title="Fail Thread", known_facts=list(facts))
        thread2 = make_thread(db_session, title="Success Thread", known_facts=list(facts))
        db_session.flush()
        db_session.commit()

        count = run_facts_consolidation_pass(db_session, _SETTINGS, raising_stub)

        # thread1 failed (skipped), thread2 succeeded → count=1
        assert count == 1
        assert call_count["n"] == 2

    # ------------------------------------------------------------------
    # Hysteresis + hard-truncate
    # ------------------------------------------------------------------

    def test_hysteresis_condensed_ends_strictly_below_cap(self, db_session):
        """After condensation the thread's fact count is strictly below max_known_facts."""
        all_facts = [f"fact-{i}" for i in range(15)]  # 15 > max=10
        # Stub returns the target number of old-fact bullets (simulates ideal LLM)
        # target_total = int(10 * 0.6) = 6; target_older = max(1, 6-5) = 1 bullet
        stub = _CountingStub(result=["one condensed bullet"])

        thread = make_thread(db_session, title="Hysteresis Thread", known_facts=all_facts)
        db_session.flush()

        run_facts_consolidation_pass(db_session, _SETTINGS, stub)
        db_session.flush()
        db_session.refresh(thread)

        assert len(thread.known_facts) < _SETTINGS.clusterer_max_known_facts, (
            "Condensed thread must land strictly below cap to create a hysteresis gap"
        )

    def test_hard_truncate_when_llm_returns_too_many(self, db_session):
        """When the LLM returns more bullets than the hard limit allows, the combined
        list is truncated to max_known_facts - 1, keeping the most-recent facts."""
        all_facts = [f"fact-{i}" for i in range(15)]
        # Stub returns 8 bullets — condensed(8) + recent(5) = 13, which exceeds hard_limit=9
        stub = _CountingStub(result=[f"old-{i}" for i in range(8)])

        thread = make_thread(db_session, title="Overflow Thread", known_facts=all_facts)
        db_session.flush()

        run_facts_consolidation_pass(db_session, _SETTINGS, stub)
        db_session.flush()
        db_session.refresh(thread)

        hard_limit = max(_SETTINGS.clusterer_known_facts_keep_recent + 1, _SETTINGS.clusterer_max_known_facts - 1)
        assert len(thread.known_facts) <= hard_limit, (
            "Hard truncate must ensure result does not reach or exceed cap"
        )
        # Most-recent facts must be preserved at the tail
        keep_recent = _SETTINGS.clusterer_known_facts_keep_recent
        assert thread.known_facts[-keep_recent:] == all_facts[-keep_recent:]

    # ------------------------------------------------------------------
    # Change-guard
    # ------------------------------------------------------------------

    def test_condense_sets_known_facts_condensed_len(self, db_session):
        """After successful condensation known_facts_condensed_len is set on the thread."""
        all_facts = [f"fact-{i}" for i in range(15)]
        stub = _CountingStub(result=["condensed"])

        thread = make_thread(db_session, title="Len Track Thread", known_facts=all_facts)
        assert thread.known_facts_condensed_len is None

        db_session.flush()
        run_facts_consolidation_pass(db_session, _SETTINGS, stub)
        db_session.flush()
        db_session.refresh(thread)

        assert thread.known_facts_condensed_len is not None
        assert thread.known_facts_condensed_len == len(thread.known_facts)

    def test_change_guard_skips_thread_when_condensed_len_unchanged(self, db_session):
        """Thread is NOT re-condensed when its fact count has not grown beyond
        known_facts_condensed_len since the last condense."""
        # Thread over cap, but condensed_len equals current len → change-guard skips it
        facts = [f"fact-{i}" for i in range(12)]
        stub = _CountingStub(result=["re-condensed"])

        thread = make_thread(
            db_session,
            title="Guard Thread",
            known_facts=facts,
            known_facts_condensed_len=len(facts),  # guard: already recorded at this len
        )
        db_session.flush()

        count = run_facts_consolidation_pass(db_session, _SETTINGS, stub)

        assert count == 0, "Thread must be skipped by change-guard when condensed_len == current len"
        assert stub.calls == 0

    def test_change_guard_allows_recondense_after_growth(self, db_session):
        """Thread IS re-condensed when its fact count grew beyond the recorded condensed_len."""
        old_len = 8
        facts = [f"fact-{i}" for i in range(12)]  # 12 > old_len=8 and > cap=10
        stub = _CountingStub(result=["new condensed"])

        thread = make_thread(
            db_session,
            title="Grown Thread",
            known_facts=facts,
            known_facts_condensed_len=old_len,  # grew from 8 to 12 since last condense
        )
        db_session.flush()

        count = run_facts_consolidation_pass(db_session, _SETTINGS, stub)
        db_session.flush()
        db_session.refresh(thread)

        assert count == 1, "Thread must be recondensed after growing past recorded condensed_len"
        assert stub.calls == 1

    def test_llm_none_return_records_condensed_len_no_fact_change(self, db_session):
        """When the LLM stub returns None (fail-open signal), facts are unchanged and
        known_facts_condensed_len is set to current len to prevent perpetual retry."""
        facts = [f"fact-{i}" for i in range(12)]
        stub = _CountingStub(result=None)  # simulates LLM failure (None return)

        thread = make_thread(db_session, title="Fail-open Thread", known_facts=facts)
        db_session.flush()

        count = run_facts_consolidation_pass(db_session, _SETTINGS, stub)
        db_session.flush()
        db_session.refresh(thread)

        # facts must be unchanged (fail-open invariant)
        assert list(thread.known_facts) == facts, "Facts must be unchanged on LLM failure"
        # condensed_len must be recorded to prevent retry
        assert thread.known_facts_condensed_len == len(facts), (
            "condensed_len must record the attempt so the thread is not retried on next pass"
        )
        # not counted as a successful condensation
        assert count == 0
