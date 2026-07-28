"""Regression tests for known_facts consolidation in the periodic consolidation pass.

Covers:
- Thread over cap → recent facts preserved verbatim at tail, older replaced by stub condensed set.
- Thread at/under cap → untouched, LLM NOT called.
- Ordering preserved (recent verbatim remain at the end, in original order).
- Per-cycle bound (clusterer_max_facts_consolidations limits threads processed).
"""
from __future__ import annotations

from aggregator_clusterer.config import ClustererSettings
from aggregator_clusterer.consolidate import run_facts_consolidation_pass

from .conftest import make_thread


class _CountingStub:
    """Injectable stub that records calls and returns a fixed condensed list."""

    def __init__(self, result: list[str] | None = None) -> None:
        self.calls: int = 0
        self._result: list[str] = result if result is not None else ["condensed A", "condensed B"]

    def __call__(self, old_facts: list[str]) -> list[str]:
        self.calls += 1
        return self._result


_SETTINGS = ClustererSettings(
    clusterer_max_known_facts=10,
    clusterer_known_facts_keep_recent=5,
    clusterer_max_facts_consolidations=3,
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

        def raising_stub(old_facts: list[str]) -> list[str]:
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
