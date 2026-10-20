"""Tests for aggregator_brief.shadow — spec AC 1-8 coverage.

AC 1: All arms succeed — correct row count, shared prompt_hash, valid payload shapes,
      raw_output not truncated.
AC 2: Each failure status row — timeout, api_error, refusal, parse_error.
AC 3: Deadline mid-loop — timeout row for running arm, skipped_deadline for later arms.
AC 4: Read-only arm session — SET TRANSACTION READ ONLY executed on each arm session.
AC 5: Manual brief origin skips shadow entirely.
AC 6: LLM_EXPERIMENT unset (decide returns None) → no shadow rows.
AC 7: Auto brief with experiment configured → run_shadow called.
AC 8: Shadow exception → logged, not re-raised, return value still True.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from aggregator_brief.config import BriefSettings
from aggregator_brief.generate import BriefLoopError, BriefRunResult
from aggregator_brief.schema import BriefSubmitSchema
from aggregator_common.experiment import Arm, Decision


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _settings(**overrides) -> BriefSettings:
    defaults: dict = {"brief_tool_max_calls": 5}
    defaults.update(overrides)
    return BriefSettings(**defaults)


def _valid_payload() -> BriefSubmitSchema:
    return BriefSubmitSchema(headline="Headline", intro="Intro.", topics=[])


def _make_decision(arms=None) -> Decision:
    if arms is None:
        arms = [Arm(name="arm-a", model="gpt-shadow-a", params={})]
    return Decision(name="test-exp", key="brief.generate", ref_id="99", arms=arms)


def _make_result(
    turns: int = 2,
    model: str = "gpt-ctrl",
    payload: BriefSubmitSchema | None = None,
    seed_messages: list | None = None,
) -> BriefRunResult:
    return BriefRunResult(
        seed_messages=seed_messages or [{"role": "user", "content": "seed"}],
        payload=payload or _valid_payload(),
        turns=turns,
        forced_submit=False,
        corrective_turn=False,
        request_ids=["req-ctrl-1"],
        model=model,
    )


def _mock_arm_session():
    sess = MagicMock()
    sess.execute = MagicMock()
    sess.rollback = MagicMock()
    sess.close = MagicMock()
    factory = MagicMock(return_value=sess)
    return factory, sess


# ──────────────────────────────────────────────────────────────────────────────
# AC 1: All arms succeed — row count, shared prompt_hash, payload shape, no truncation
# ──────────────────────────────────────────────────────────────────────────────


class TestAllArmsSucceed:
    def test_row_count_is_control_plus_arms(self):
        arms = [
            Arm(name="arm-a", model="gpt-shadow-a", params={}),
            Arm(name="arm-b", model="gpt-shadow-b", params={}),
        ]
        decision = _make_decision(arms=arms)
        result = _make_result()
        factory, _ = _mock_arm_session()
        captured: list = []

        arm_result = _make_result(turns=1, model="gpt-shadow-a")

        with patch("aggregator_brief.shadow.run_brief_loop", return_value=arm_result), \
             patch("aggregator_brief.shadow.write_item", side_effect=lambda rows, **kw: captured.extend(rows)):
            from aggregator_brief.shadow import _run_shadow
            _run_shadow(result, decision, _settings(), factory)

        assert len(captured) == 3
        assert captured[0].arm == "control"
        assert captured[1].arm == "arm-a"
        assert captured[2].arm == "arm-b"

    def test_all_rows_share_same_prompt_hash(self):
        arms = [
            Arm(name="arm-a", model="gpt-shadow-a", params={}),
            Arm(name="arm-b", model="gpt-shadow-b", params={}),
        ]
        decision = _make_decision(arms=arms)
        result = _make_result()
        factory, _ = _mock_arm_session()
        captured: list = []

        with patch("aggregator_brief.shadow.run_brief_loop", return_value=_make_result(turns=1)), \
             patch("aggregator_brief.shadow.write_item", side_effect=lambda rows, **kw: captured.extend(rows)):
            from aggregator_brief.shadow import _run_shadow
            _run_shadow(result, decision, _settings(), factory)

        hashes = {r.prompt_hash for r in captured}
        assert len(hashes) == 1

    def test_ok_rows_have_valid_payload_shape(self):
        decision = _make_decision()
        result = _make_result()
        factory, _ = _mock_arm_session()
        captured: list = []

        with patch("aggregator_brief.shadow.run_brief_loop", return_value=_make_result(turns=1)), \
             patch("aggregator_brief.shadow.write_item", side_effect=lambda rows, **kw: captured.extend(rows)):
            from aggregator_brief.shadow import _run_shadow
            _run_shadow(result, decision, _settings(), factory)

        for row in captured:
            assert row.status == "ok"
            assert "payload" in row.output
            assert "turns" in row.output
            assert "forced_submit" in row.output
            assert "corrective_turn" in row.output

    def test_write_item_called_with_truncate_raw_false(self):
        """write_item must receive truncate_raw=False so large payloads are not clipped."""
        decision = _make_decision()
        result = _make_result()
        factory, _ = _mock_arm_session()
        write_calls: list = []

        with patch("aggregator_brief.shadow.run_brief_loop", return_value=_make_result(turns=1)), \
             patch("aggregator_brief.shadow.write_item", side_effect=lambda rows, **kw: write_calls.append(kw)):
            from aggregator_brief.shadow import _run_shadow
            _run_shadow(result, decision, _settings(), factory)

        assert write_calls, "write_item should have been called"
        assert write_calls[0].get("truncate_raw") is False


# ──────────────────────────────────────────────────────────────────────────────
# AC 2: Failure status rows — timeout, api_error, refusal, parse_error
# ──────────────────────────────────────────────────────────────────────────────


class TestFailureStatusRows:
    def _run_with_side_effect(self, side_effect):
        decision = _make_decision()
        result = _make_result()
        factory, _ = _mock_arm_session()
        captured: list = []

        with patch("aggregator_brief.shadow.run_brief_loop", side_effect=side_effect), \
             patch("aggregator_brief.shadow.write_item", side_effect=lambda rows, **kw: captured.extend(rows)):
            from aggregator_brief.shadow import _run_shadow
            _run_shadow(result, decision, _settings(), factory)

        return captured

    def test_briefloouperror_timeout_gives_timeout_row(self):
        exc = BriefLoopError("deadline", status="timeout", turns=1, request_ids=["r1"])
        rows = self._run_with_side_effect(exc)
        arm_row = next(r for r in rows if r.arm == "arm-a")
        assert arm_row.status == "timeout"
        assert arm_row.error is not None

    def test_brieflooperror_parse_error_gives_parse_error_row(self):
        exc = BriefLoopError("invalid payload", status="parse_error", turns=2)
        rows = self._run_with_side_effect(exc)
        arm_row = next(r for r in rows if r.arm == "arm-a")
        assert arm_row.status == "parse_error"

    def test_arm_timeout_error_gives_timeout_row(self):
        import aggregator_brief.shadow as shadow_mod
        exc = shadow_mod._ArmTimeoutError("timeout!")
        rows = self._run_with_side_effect(exc)
        arm_row = next(r for r in rows if r.arm == "arm-a")
        assert arm_row.status == "timeout"

    def test_arm_api_error_gives_api_error_row(self):
        import aggregator_brief.shadow as shadow_mod
        exc = shadow_mod._ArmApiError("connection refused")
        rows = self._run_with_side_effect(exc)
        arm_row = next(r for r in rows if r.arm == "arm-a")
        assert arm_row.status == "api_error"

    def test_arm_refusal_error_gives_refusal_row(self):
        import aggregator_brief.shadow as shadow_mod
        exc = shadow_mod._ArmRefusalError("content_filter triggered")
        rows = self._run_with_side_effect(exc)
        arm_row = next(r for r in rows if r.arm == "arm-a")
        assert arm_row.status == "refusal"

    def test_unexpected_exception_gives_api_error_row(self):
        rows = self._run_with_side_effect(RuntimeError("unexpected boom"))
        arm_row = next(r for r in rows if r.arm == "arm-a")
        assert arm_row.status == "api_error"

    def test_failure_row_has_error_text(self):
        exc = BriefLoopError("something broke", status="parse_error", turns=1)
        rows = self._run_with_side_effect(exc)
        arm_row = next(r for r in rows if r.arm == "arm-a")
        assert arm_row.error is not None
        assert len(arm_row.error) > 0


# ──────────────────────────────────────────────────────────────────────────────
# AC 3: Deadline mid-loop — correct arm skipping behaviour
# ──────────────────────────────────────────────────────────────────────────────


class TestDeadlineMidLoop:
    def test_deadline_before_first_arm_writes_only_control_row(self):
        """When clock >= deadline before arm 0, all arms are skipped."""
        arms = [
            Arm(name="arm-a", model="gpt-a", params={}),
            Arm(name="arm-b", model="gpt-b", params={}),
        ]
        decision = _make_decision(arms=arms)
        result = _make_result()
        factory, _ = _mock_arm_session()
        captured: list = []

        # First call sets deadline=0+10=10; second call (pre-arm-0 check) returns 20 >= 10 → skip.
        clock_vals = iter([0.0, 20.0])
        clock = lambda: next(clock_vals)  # noqa: E731

        with patch("aggregator_brief.shadow.run_brief_loop") as mock_loop, \
             patch("aggregator_brief.shadow.write_item", side_effect=lambda rows, **kw: captured.extend(rows)):
            from aggregator_brief.shadow import _run_shadow
            _run_shadow(result, decision, _settings(), factory, clock=clock, deadline_seconds=10.0)

        assert len(captured) == 1
        assert captured[0].arm == "control"
        mock_loop.assert_not_called()

    def test_deadline_after_first_arm_skips_remaining_arms(self):
        """When arm-a consumes the deadline, arm-b must not run."""
        arms = [
            Arm(name="arm-a", model="gpt-a", params={}),
            Arm(name="arm-b", model="gpt-b", params={}),
        ]
        decision = _make_decision(arms=arms)
        result = _make_result()
        factory, _ = _mock_arm_session()
        captured: list = []

        # call 1 (init): 0.0 → deadline = 10.0
        # call 2 (pre-arm-a): 5.0 < 10.0 → proceed
        # call 3 (post-arm-a): 15.0 >= 10.0 → skip arm-b
        clock_vals = iter([0.0, 5.0, 15.0])
        clock = lambda: next(clock_vals)  # noqa: E731

        with patch("aggregator_brief.shadow.run_brief_loop", return_value=_make_result(turns=1)) as mock_loop, \
             patch("aggregator_brief.shadow.write_item", side_effect=lambda rows, **kw: captured.extend(rows)):
            from aggregator_brief.shadow import _run_shadow
            _run_shadow(result, decision, _settings(), factory, clock=clock, deadline_seconds=10.0)

        arm_names = [r.arm for r in captured]
        assert "control" in arm_names
        assert "arm-a" in arm_names
        assert "arm-b" not in arm_names
        assert mock_loop.call_count == 1

    def test_write_item_still_called_when_deadline_reached(self):
        """Rows collected before deadline hit must still be persisted."""
        decision = _make_decision()
        result = _make_result()
        factory, _ = _mock_arm_session()
        write_called = []

        clock_vals = iter([0.0, 20.0])
        clock = lambda: next(clock_vals)  # noqa: E731

        with patch("aggregator_brief.shadow.run_brief_loop"), \
             patch("aggregator_brief.shadow.write_item", side_effect=lambda rows, **kw: write_called.append(rows)):
            from aggregator_brief.shadow import _run_shadow
            _run_shadow(result, decision, _settings(), factory, clock=clock, deadline_seconds=10.0)

        assert write_called, "write_item should still be called with control row"


# ──────────────────────────────────────────────────────────────────────────────
# AC 4: Read-only arm session
# ──────────────────────────────────────────────────────────────────────────────


class TestReadOnlyArmSession:
    def test_set_transaction_read_only_executed_on_arm_session(self):
        decision = _make_decision()
        result = _make_result()
        factory, arm_sess = _mock_arm_session()

        with patch("aggregator_brief.shadow.run_brief_loop", return_value=_make_result(turns=1)), \
             patch("aggregator_brief.shadow.write_item"):
            from aggregator_brief.shadow import _run_shadow
            _run_shadow(result, decision, _settings(), factory)

        executed_stmts = [str(c.args[0]) for c in arm_sess.execute.call_args_list]
        assert any("READ ONLY" in s for s in executed_stmts)

    def test_arm_session_rolled_back_and_closed_on_success(self):
        decision = _make_decision()
        result = _make_result()
        factory, arm_sess = _mock_arm_session()

        with patch("aggregator_brief.shadow.run_brief_loop", return_value=_make_result(turns=1)), \
             patch("aggregator_brief.shadow.write_item"):
            from aggregator_brief.shadow import _run_shadow
            _run_shadow(result, decision, _settings(), factory)

        arm_sess.rollback.assert_called()
        arm_sess.close.assert_called()

    def test_arm_session_rolled_back_and_closed_on_failure(self):
        decision = _make_decision()
        result = _make_result()
        factory, arm_sess = _mock_arm_session()

        with patch("aggregator_brief.shadow.run_brief_loop", side_effect=RuntimeError("boom")), \
             patch("aggregator_brief.shadow.write_item"):
            from aggregator_brief.shadow import _run_shadow
            _run_shadow(result, decision, _settings(), factory)

        arm_sess.rollback.assert_called()
        arm_sess.close.assert_called()


# ──────────────────────────────────────────────────────────────────────────────
# AC 8: Shadow outer wrapper — never raises
# ──────────────────────────────────────────────────────────────────────────────


class TestRunShadowWrapper:
    def test_run_shadow_does_not_raise_on_inner_exception(self):
        """run_shadow must swallow all exceptions from _run_shadow."""
        decision = _make_decision()
        result = _make_result()
        factory, _ = _mock_arm_session()

        with patch("aggregator_brief.shadow._run_shadow", side_effect=RuntimeError("inner boom")):
            from aggregator_brief.shadow import run_shadow
            run_shadow(result, decision, _settings(), factory)  # must not raise


# ──────────────────────────────────────────────────────────────────────────────
# AC 5, 6, 7, 8 (loop wiring): _run_one_iteration shadow integration
# ──────────────────────────────────────────────────────────────────────────────


class TestLoopShadowWiring:
    """Tests for _run_one_iteration shadow wiring in loop.py."""

    def _make_brief_mock(self, origin: str = "auto", brief_id: int = 1):
        brief = MagicMock()
        brief.id = brief_id
        brief.origin = origin
        brief.model = "gpt-ctrl"
        brief.headline = "Test Headline"
        brief.intro = "Test Intro"
        brief.generated_at = datetime.now(timezone.utc)
        brief.topics = []
        return brief

    def _make_session_factory(self, brief):
        session = MagicMock()
        session.get = MagicMock(return_value=brief)
        session.commit = MagicMock()
        session.rollback = MagicMock()
        session.close = MagicMock()
        factory = MagicMock(return_value=session)
        return factory, session

    def test_auto_brief_calls_run_shadow_on_success(self):
        """AC 7: auto brief with experiment configured → run_shadow called."""
        brief = self._make_brief_mock(origin="auto")
        factory, _ = self._make_session_factory(brief)
        decision = _make_decision()
        run_result = _make_result()

        with patch("aggregator_brief.loop.reap_stale_brief_claims", return_value=0), \
             patch("aggregator_brief.loop._maybe_enqueue_auto_brief", return_value=False), \
             patch("aggregator_brief.loop.claim_brief", return_value=brief), \
             patch("aggregator_brief.loop.experiment.decide", return_value=decision), \
             patch("aggregator_brief.loop.generate_brief", return_value=run_result), \
             patch("aggregator_brief.loop.complete_brief"), \
             patch("aggregator_brief.loop.run_shadow") as mock_shadow:
            from aggregator_brief.loop import _run_one_iteration
            result = _run_one_iteration(_settings(), factory, "worker-1")

        assert result is True
        mock_shadow.assert_called_once()
        assert mock_shadow.call_args.args[0] is run_result
        assert mock_shadow.call_args.args[1] is decision

    def test_manual_brief_does_not_call_run_shadow(self):
        """AC 5: manual brief origin → decide not called, run_shadow not called."""
        brief = self._make_brief_mock(origin="manual")
        factory, _ = self._make_session_factory(brief)
        run_result = _make_result()

        with patch("aggregator_brief.loop.reap_stale_brief_claims", return_value=0), \
             patch("aggregator_brief.loop._maybe_enqueue_auto_brief", return_value=False), \
             patch("aggregator_brief.loop.claim_brief", return_value=brief), \
             patch("aggregator_brief.loop.experiment.decide") as mock_decide, \
             patch("aggregator_brief.loop.generate_brief", return_value=run_result), \
             patch("aggregator_brief.loop.complete_brief"), \
             patch("aggregator_brief.loop.run_shadow") as mock_shadow:
            from aggregator_brief.loop import _run_one_iteration
            result = _run_one_iteration(_settings(), factory, "worker-1")

        assert result is True
        mock_decide.assert_not_called()
        mock_shadow.assert_not_called()

    def test_generation_failure_does_not_call_run_shadow(self):
        """AC 5/8: if generation fails, run_shadow must not be called."""
        brief = self._make_brief_mock(origin="auto")
        factory, _ = self._make_session_factory(brief)
        decision = _make_decision()

        with patch("aggregator_brief.loop.reap_stale_brief_claims", return_value=0), \
             patch("aggregator_brief.loop._maybe_enqueue_auto_brief", return_value=False), \
             patch("aggregator_brief.loop.claim_brief", return_value=brief), \
             patch("aggregator_brief.loop.experiment.decide", return_value=decision), \
             patch("aggregator_brief.loop.generate_brief", side_effect=RuntimeError("LLM failed")), \
             patch("aggregator_brief.loop.fail_brief"), \
             patch("aggregator_brief.loop.run_shadow") as mock_shadow:
            from aggregator_brief.loop import _run_one_iteration
            result = _run_one_iteration(_settings(), factory, "worker-1")

        assert result is False
        mock_shadow.assert_not_called()

    def test_shadow_exception_not_reraised_returns_true(self):
        """AC 8: exception from run_shadow must not propagate; return value is still True."""
        brief = self._make_brief_mock(origin="auto")
        factory, _ = self._make_session_factory(brief)
        decision = _make_decision()
        run_result = _make_result()

        with patch("aggregator_brief.loop.reap_stale_brief_claims", return_value=0), \
             patch("aggregator_brief.loop._maybe_enqueue_auto_brief", return_value=False), \
             patch("aggregator_brief.loop.claim_brief", return_value=brief), \
             patch("aggregator_brief.loop.experiment.decide", return_value=decision), \
             patch("aggregator_brief.loop.generate_brief", return_value=run_result), \
             patch("aggregator_brief.loop.complete_brief"), \
             patch("aggregator_brief.loop.run_shadow", side_effect=RuntimeError("shadow boom")):
            from aggregator_brief.loop import _run_one_iteration
            result = _run_one_iteration(_settings(), factory, "worker-1")

        assert result is True

    def test_auto_brief_no_shadow_when_decide_returns_none(self):
        """AC 6: experiment.decide returns None → run_shadow not called."""
        brief = self._make_brief_mock(origin="auto")
        factory, _ = self._make_session_factory(brief)
        run_result = _make_result()

        with patch("aggregator_brief.loop.reap_stale_brief_claims", return_value=0), \
             patch("aggregator_brief.loop._maybe_enqueue_auto_brief", return_value=False), \
             patch("aggregator_brief.loop.claim_brief", return_value=brief), \
             patch("aggregator_brief.loop.experiment.decide", return_value=None), \
             patch("aggregator_brief.loop.generate_brief", return_value=run_result), \
             patch("aggregator_brief.loop.complete_brief"), \
             patch("aggregator_brief.loop.run_shadow") as mock_shadow:
            from aggregator_brief.loop import _run_one_iteration
            result = _run_one_iteration(_settings(), factory, "worker-1")

        assert result is True
        mock_shadow.assert_not_called()
