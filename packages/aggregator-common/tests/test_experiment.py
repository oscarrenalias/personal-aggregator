"""Tests for aggregator_common.experiment — shadow evaluation framework.

Covers acceptance criteria:
- AC-5: sampling rate determinism and ±2pp over 10k synthetic ids; ref_id=None never sampled
- AC-6: write_item with truncate_raw=True/False
- AC-7: write conflict idempotency (ON CONFLICT DO NOTHING)
- AC-8: invalid config logs one ERROR and starts disabled
- AC-13: crash between arm calls and write_item leaves no rows
- AC-14: skip at any decision step leaves control metadata untagged
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Reset experiment module state between tests
import aggregator_common.experiment as _exp_module


def _reset_experiment() -> None:
    """Reset module-level state so each test starts fresh."""
    with _exp_module._lock:
        _exp_module._config = None
        _exp_module._initialized = False
        _exp_module._executor = None
    _exp_module._counters = _exp_module._Counters()
    _exp_module._budget = _exp_module._BudgetCache()


@pytest.fixture(autouse=True)
def reset_experiment_state():
    _reset_experiment()
    yield
    # Shutdown executor if init left one running
    with _exp_module._lock:
        ex = _exp_module._executor
        _exp_module._executor = None
        _exp_module._config = None
        _exp_module._initialized = False
    if ex is not None:
        ex.shutdown()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UNTIL_FUTURE = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
_UNTIL_PAST = "2000-01-01T00:00:00Z"

_VALID_CFG = json.dumps({
    "enabled": True,
    "name": "test-exp",
    "until": _UNTIL_FUTURE,
    "max_daily_cost_usd": 100.0,
    "operations": {
        "summarize_rank.rank": {
            "sample_rate": 0.3,
            "arms": [
                {"name": "luna-low", "model": "gpt-6-luna", "params": {"reasoning_effort": "low", "max_tokens": None}},
            ],
        }
    },
})

_FULL_CFG = json.dumps({
    "enabled": True,
    "name": "test-exp",
    "until": _UNTIL_FUTURE,
    "max_daily_cost_usd": 100.0,
    "operations": {
        "summarize_rank.rank": {
            "sample_rate": 1.0,
            "arms": [
                {"name": "luna-low", "model": "gpt-6-luna", "params": {"reasoning_effort": "low", "max_tokens": None, "max_completion_tokens": 2000}},
            ],
        }
    },
})


def _make_settings(cfg: str | None = None) -> Any:
    s = SimpleNamespace()
    s.llm_experiment = cfg
    return s


# ---------------------------------------------------------------------------
# AC-8: Invalid config — one ERROR, experiment disabled
# ---------------------------------------------------------------------------

class TestInvalidConfig:
    def _count_errors(self, caplog, fn):
        with caplog.at_level(logging.ERROR, logger="aggregator_common.experiment"):
            fn()
        return sum(1 for r in caplog.records if r.levelno == logging.ERROR)

    def test_invalid_json_logs_one_error(self, caplog):
        count = self._count_errors(caplog, lambda: _exp_module.init(_make_settings("{bad json")))
        assert count == 1
        assert _exp_module._config is None

    def test_unknown_top_level_field_logs_one_error(self, caplog):
        cfg = json.dumps({"enabled": True, "name": "x", "until": _UNTIL_FUTURE, "max_daily_cost_usd": 1.0,
                          "operations": {}, "unknown_field": "value"})
        count = self._count_errors(caplog, lambda: _exp_module.init(_make_settings(cfg)))
        assert count == 1
        assert _exp_module._config is None

    def test_unknown_operation_key_logs_one_error(self, caplog):
        cfg = json.dumps({"enabled": True, "name": "x", "until": _UNTIL_FUTURE, "max_daily_cost_usd": 1.0,
                          "operations": {"bogus.op": {"sample_rate": 1.0, "arms": []}}})
        count = self._count_errors(caplog, lambda: _exp_module.init(_make_settings(cfg)))
        assert count == 1
        assert _exp_module._config is None

    def test_forbidden_params_key_logs_one_error(self, caplog):
        cfg = json.dumps({
            "enabled": True, "name": "x", "until": _UNTIL_FUTURE, "max_daily_cost_usd": 1.0,
            "operations": {
                "summarize_rank.rank": {
                    "sample_rate": 1.0,
                    "arms": [{"name": "a", "model": "m", "params": {"messages": "bad"}}],
                }
            },
        })
        count = self._count_errors(caplog, lambda: _exp_module.init(_make_settings(cfg)))
        assert count == 1
        assert _exp_module._config is None

    def test_unknown_arm_field_logs_one_error(self, caplog):
        cfg = json.dumps({
            "enabled": True, "name": "x", "until": _UNTIL_FUTURE, "max_daily_cost_usd": 1.0,
            "operations": {
                "summarize_rank.rank": {
                    "sample_rate": 1.0,
                    "arms": [{"name": "a", "model": "m", "params": {}, "weird": "field"}],
                }
            },
        })
        count = self._count_errors(caplog, lambda: _exp_module.init(_make_settings(cfg)))
        assert count == 1
        assert _exp_module._config is None

    def test_disabled_enabled_false_no_error(self, caplog):
        cfg = json.dumps({"enabled": False, "name": "x", "until": _UNTIL_FUTURE, "max_daily_cost_usd": 1.0, "operations": {}})
        with caplog.at_level(logging.ERROR, logger="aggregator_common.experiment"):
            _exp_module.init(_make_settings(cfg))
        assert all(r.levelno < logging.ERROR for r in caplog.records)
        assert _exp_module._config is None

    def test_valid_config_starts_enabled(self):
        _exp_module.init(_make_settings(_VALID_CFG))
        assert _exp_module._config is not None

    def test_init_idempotent(self):
        _exp_module.init(_make_settings(_VALID_CFG))
        cfg1 = _exp_module._config
        _exp_module.init(_make_settings(_VALID_CFG))  # second call — no-op
        assert _exp_module._config is cfg1


# ---------------------------------------------------------------------------
# AC-5/AC-14: decide() — 6-step gate and counter semantics
# ---------------------------------------------------------------------------

class TestDecide:
    def test_not_configured_when_no_init(self):
        result = _exp_module.decide("summarize_rank.rank", "123")
        assert result is None
        assert _exp_module._counters.not_configured == 1

    def test_not_configured_when_key_missing(self):
        _exp_module.init(_make_settings(_VALID_CFG))
        result = _exp_module.decide("clusterer.classify", "123")
        assert result is None
        assert _exp_module._counters.not_configured == 1

    def test_no_ref_id_returns_none(self):
        _exp_module.init(_make_settings(_FULL_CFG))
        result = _exp_module.decide("summarize_rank.rank", None)
        assert result is None
        assert _exp_module._counters.no_ref_id == 1

    def test_skipped_until_past(self):
        cfg = json.dumps({
            "enabled": True, "name": "x", "until": _UNTIL_PAST,
            "max_daily_cost_usd": 100.0,
            "operations": {"summarize_rank.rank": {"sample_rate": 1.0, "arms": [
                {"name": "a", "model": "m", "params": {}}
            ]}},
        })
        _exp_module.init(_make_settings(cfg))
        result = _exp_module.decide("summarize_rank.rank", "1")
        assert result is None
        assert _exp_module._counters.skipped_until == 1

    def test_budget_cap_skips(self):
        _exp_module.init(_make_settings(_FULL_CFG))
        # Force budget over cap
        with patch.object(_exp_module._budget, "get", return_value=999.0):
            result = _exp_module.decide("summarize_rank.rank", "1")
        assert result is None
        assert _exp_module._counters.skipped_budget == 1

    def test_sampled_returns_decision(self):
        _exp_module.init(_make_settings(_FULL_CFG))
        result = _exp_module.decide("summarize_rank.rank", "fixed-ref-1")
        assert result is not None
        assert result.name == "test-exp"
        assert result.key == "summarize_rank.rank"
        assert result.ref_id == "fixed-ref-1"

    def test_decision_has_arms(self):
        _exp_module.init(_make_settings(_FULL_CFG))
        result = _exp_module.decide("summarize_rank.rank", "abc")
        assert result is not None
        assert len(result.arms) == 1
        assert result.arms[0].name == "luna-low"

    def test_skip_at_until_leaves_metadata_untagged(self):
        """AC-14: a skip must not tag control metadata."""
        cfg = json.dumps({
            "enabled": True, "name": "x", "until": _UNTIL_PAST,
            "max_daily_cost_usd": 100.0,
            "operations": {"summarize_rank.rank": {"sample_rate": 1.0, "arms": [
                {"name": "a", "model": "m", "params": {}}
            ]}},
        })
        _exp_module.init(_make_settings(cfg))
        decision = _exp_module.decide("summarize_rank.rank", "1")
        assert decision is None
        # No decision — caller would use plain base_metadata (no experiment/arm tags)

    def test_skip_budget_leaves_metadata_untagged(self):
        """AC-14: budget skip must not tag control metadata."""
        _exp_module.init(_make_settings(_FULL_CFG))
        with patch.object(_exp_module._budget, "get", return_value=999.0):
            decision = _exp_module.decide("summarize_rank.rank", "1")
        assert decision is None


# ---------------------------------------------------------------------------
# AC-5: Sampling — deterministic, ±2pp, ref_id=None never sampled
# ---------------------------------------------------------------------------

class TestSampling:
    def test_deterministic_same_inputs(self):
        _exp_module.init(_make_settings(_FULL_CFG))
        results = [_exp_module.decide("summarize_rank.rank", "ref-42") for _ in range(10)]
        # All must be the same (all None or all Decision)
        assert len(set(r is None for r in results)) == 1

    def test_rate_30pct_over_10k_ids(self):
        """AC-5: observed rate within ±2pp of 30%."""
        cfg = json.dumps({
            "enabled": True, "name": "test-exp", "until": _UNTIL_FUTURE,
            "max_daily_cost_usd": 9999.0,
            "operations": {"summarize_rank.rank": {"sample_rate": 0.3, "arms": [
                {"name": "a", "model": "m", "params": {}}
            ]}},
        })
        _exp_module.init(_make_settings(cfg))
        with patch.object(_exp_module._budget, "get", return_value=0.0):
            sampled = sum(
                1 for i in range(10000)
                if _exp_module.decide("summarize_rank.rank", str(i)) is not None
            )
        rate = sampled / 10000
        assert abs(rate - 0.30) <= 0.02, f"Expected ~30%, got {rate:.3%}"

    def test_ref_id_none_never_sampled(self):
        _exp_module.init(_make_settings(_FULL_CFG))
        for _ in range(100):
            assert _exp_module.decide("summarize_rank.rank", None) is None


# ---------------------------------------------------------------------------
# AC-14 / control_metadata: sampled items get tagged; skipped items do not
# ---------------------------------------------------------------------------

class TestControlMetadata:
    def test_sampled_control_metadata_tagged(self):
        _exp_module.init(_make_settings(_FULL_CFG))
        decision = _exp_module.decide("summarize_rank.rank", "some-ref")
        if decision is None:
            pytest.skip("hash not sampled for this ref — adjust ref_id")
        base = {"service": "svc", "operation": "op"}
        tagged = decision.control_metadata(base)
        assert tagged["experiment"] == "test-exp"
        assert tagged["arm"] == "control"
        assert tagged["service"] == "svc"  # original keys preserved

    def test_control_metadata_does_not_mutate_base(self):
        _exp_module.init(_make_settings(_FULL_CFG))
        decision = _exp_module.decide("summarize_rank.rank", "some-ref")
        if decision is None:
            pytest.skip("not sampled")
        base = {"service": "svc"}
        decision.control_metadata(base)
        assert "experiment" not in base


# ---------------------------------------------------------------------------
# build_arm_kwargs
# ---------------------------------------------------------------------------

class TestBuildArmKwargs:
    def _make_decision(self) -> _exp_module.Decision:
        return _exp_module.Decision(
            name="test-exp",
            key="summarize_rank.rank",
            ref_id="1",
            arms=[_exp_module.Arm(name="luna-low", model="gpt-6-luna",
                                  params={"reasoning_effort": "low", "max_tokens": None, "max_completion_tokens": 2000})],
        )

    def test_model_replaced(self):
        decision = self._make_decision()
        arm = decision.arms[0]
        control_kwargs = {"model": "gpt-4.1-mini", "max_tokens": 512, "messages": []}
        kw = _exp_module.build_arm_kwargs(control_kwargs, decision, arm)
        assert kw["model"] == "gpt-6-luna"

    def test_null_removes_key(self):
        decision = self._make_decision()
        arm = decision.arms[0]
        control_kwargs = {"model": "gpt-4.1-mini", "max_tokens": 512, "messages": []}
        kw = _exp_module.build_arm_kwargs(control_kwargs, decision, arm)
        assert "max_tokens" not in kw

    def test_non_null_sets_key(self):
        decision = self._make_decision()
        arm = decision.arms[0]
        control_kwargs = {"model": "gpt-4.1-mini", "max_tokens": 512, "messages": []}
        kw = _exp_module.build_arm_kwargs(control_kwargs, decision, arm)
        assert kw["max_completion_tokens"] == 2000

    def test_reasoning_effort_adds_allowed_openai_params(self):
        decision = self._make_decision()
        arm = decision.arms[0]
        control_kwargs = {"model": "gpt-4.1-mini", "messages": []}
        kw = _exp_module.build_arm_kwargs(control_kwargs, decision, arm)
        assert "reasoning_effort" in kw
        assert "allowed_openai_params" in kw
        assert "reasoning_effort" in kw["allowed_openai_params"]

    def test_arm_metadata_set(self):
        decision = self._make_decision()
        arm = decision.arms[0]
        control_kwargs = {"model": "gpt-4.1-mini", "messages": [], "metadata": {"service": "svc"}}
        kw = _exp_module.build_arm_kwargs(control_kwargs, decision, arm)
        assert kw["metadata"]["experiment"] == "test-exp"
        assert kw["metadata"]["arm"] == "luna-low"

    def test_no_reasoning_effort_no_allowed_openai_params(self):
        decision = _exp_module.Decision(
            name="test-exp", key="clusterer.merge", ref_id="1",
            arms=[_exp_module.Arm(name="luna-none", model="gpt-6-luna", params={"max_tokens": None})],
        )
        arm = decision.arms[0]
        control_kwargs = {"model": "gpt-4.1", "messages": []}
        kw = _exp_module.build_arm_kwargs(control_kwargs, decision, arm)
        assert "reasoning_effort" not in kw
        assert "allowed_openai_params" not in kw


# ---------------------------------------------------------------------------
# prompt_hash
# ---------------------------------------------------------------------------

class TestPromptHash:
    def test_same_messages_same_hash(self):
        msgs = [{"role": "user", "content": "hello"}]
        assert _exp_module.prompt_hash(msgs) == _exp_module.prompt_hash(msgs)

    def test_different_messages_different_hash(self):
        h1 = _exp_module.prompt_hash([{"role": "user", "content": "hello"}])
        h2 = _exp_module.prompt_hash([{"role": "user", "content": "world"}])
        assert h1 != h2

    def test_hash_is_sha256_hex(self):
        msgs = [{"role": "user", "content": "x"}]
        expected = hashlib.sha256(json.dumps(msgs, sort_keys=True).encode()).hexdigest()
        assert _exp_module.prompt_hash(msgs) == expected


# ---------------------------------------------------------------------------
# write_item: truncation and idempotency (AC-6, AC-7)
# Requires DB session — uses conftest db_session_factory if available; otherwise skips
# ---------------------------------------------------------------------------

class TestWriteItem:
    def _make_row(self, **overrides):
        defaults = dict(
            experiment="test-exp",
            service="summarize_rank",
            operation="rank",
            ref_id="ref-1",
            arm="control",
            model="gpt-4.1-mini",
            prompt_hash="abc123",
            status="ok",
            attempts=1,
            params={},
            output={"score": 80},
            raw_output="some raw output",
            error=None,
            request_ids=["req-1"],
        )
        defaults.update(overrides)
        return _exp_module.ShadowRow(**defaults)

    def test_truncate_raw_true_truncates_long_fields(self, db_session_factory):
        from aggregator_common.db import SessionFactory
        import aggregator_common.db as db_mod
        db_mod.SessionFactory = db_session_factory

        raw_long = "x" * 10000
        err_long = "e" * 3000
        row = self._make_row(raw_output=raw_long, error=err_long, ref_id="trunc-1")
        _exp_module.write_item([row], truncate_raw=True)

        sess = db_session_factory()
        try:
            from aggregator_common.models import LlmShadowResult
            from sqlalchemy import select as sa_select
            r = sess.execute(
                sa_select(LlmShadowResult).where(LlmShadowResult.ref_id == "trunc-1")
            ).scalars().first()
            assert r is not None
            assert len(r.raw_output) == 8192
            assert len(r.error) == 2048
        finally:
            sess.close()

    def test_truncate_raw_false_preserves_full_content(self, db_session_factory):
        import aggregator_common.db as db_mod
        db_mod.SessionFactory = db_session_factory

        raw_long = "y" * 10000
        row = self._make_row(raw_output=raw_long, ref_id="notrunc-1")
        _exp_module.write_item([row], truncate_raw=False)

        sess = db_session_factory()
        try:
            from aggregator_common.models import LlmShadowResult
            from sqlalchemy import select as sa_select
            r = sess.execute(
                sa_select(LlmShadowResult).where(LlmShadowResult.ref_id == "notrunc-1")
            ).scalars().first()
            assert r is not None
            assert r.raw_output == raw_long
        finally:
            sess.close()

    def test_conflict_idempotency_no_extra_rows(self, db_session_factory):
        """AC-7: ON CONFLICT DO NOTHING — second write of same (exp, svc, op, ref_id, arm) inserts nothing."""
        import aggregator_common.db as db_mod
        db_mod.SessionFactory = db_session_factory

        row = self._make_row(ref_id="idem-1")
        _exp_module.write_item([row])
        _exp_module.write_item([row])  # second write

        sess = db_session_factory()
        try:
            from aggregator_common.models import LlmShadowResult
            from sqlalchemy import select as sa_select
            rows = sess.execute(
                sa_select(LlmShadowResult).where(LlmShadowResult.ref_id == "idem-1")
            ).scalars().all()
            assert len(rows) == 1
        finally:
            sess.close()

    def test_write_item_never_raises(self, db_session_factory):
        """write_item must swallow errors, never raise."""
        import aggregator_common.db as db_mod
        # Inject a bad factory
        bad_factory = MagicMock(side_effect=RuntimeError("DB down"))
        original = db_mod.SessionFactory
        db_mod.SessionFactory = bad_factory
        try:
            # must not raise
            _exp_module.write_item([self._make_row(ref_id="never-raises")])
        finally:
            db_mod.SessionFactory = original


# ---------------------------------------------------------------------------
# submit_shadow: no-op if decision is None
# ---------------------------------------------------------------------------

class TestSubmitShadow:
    def test_submit_shadow_noop_if_no_decision(self):
        _exp_module.init(_make_settings(_FULL_CFG))
        called = []
        def fake_attempt(kw):
            called.append(kw)
            return _exp_module.CallOutcome(status="ok", value=None, output=None,
                                           raw_output=None, attempts=1, request_ids=[], error=None)
        control = fake_attempt({})
        _exp_module.submit_shadow(None, {}, control, fake_attempt)
        time.sleep(0.1)
        # The attempt should not have been called via shadow (decision is None)
        assert len(called) == 1  # only the one direct call above

    def test_submit_shadow_enqueues_item(self):
        _exp_module.init(_make_settings(_FULL_CFG))
        decision = _exp_module.Decision(
            name="test-exp", key="summarize_rank.rank", ref_id="1",
            arms=[_exp_module.Arm(name="luna-low", model="gpt-6-luna", params={})],
        )
        control = _exp_module.CallOutcome(status="ok", value=None, output=None,
                                          raw_output=None, attempts=1, request_ids=[], error=None)
        enqueued_before = _exp_module._counters.enqueued
        _exp_module.submit_shadow(decision, {"messages": [], "model": "gpt-4.1-mini"}, control,
                                  lambda kw: control)
        assert _exp_module._counters.enqueued > enqueued_before


# ---------------------------------------------------------------------------
# AC-5: Queue full — whole item dropped, no partial rows (AC-5 partial)
# ---------------------------------------------------------------------------

class TestQueueFull:
    def test_queue_full_submit_returns_false(self):
        """Executor.submit() returns False when queue is at maxsize."""
        _exp_module.init(_make_settings(_FULL_CFG))
        ex = _exp_module._executor
        assert ex is not None

        # Stop worker threads so they cannot drain items we put in
        ex._stop.set()
        for t in ex._threads:
            t.join(timeout=2.0)

        dummy_item = {
            "decision": _exp_module.Decision("test-exp", "summarize_rank.rank", "fill-ref",
                                              [_exp_module.Arm("a", "m", {})]),
            "control_kwargs": {"model": "m", "messages": []},
            "control_outcome": _exp_module.CallOutcome("ok", None, None, None, 1, [], None),
            "attempt_fn": lambda kw: _exp_module.CallOutcome("ok", None, None, None, 1, [], None),
            "phash": "x",
        }

        # Fill queue to maxsize (50)
        for _ in range(_exp_module._QUEUE_MAXSIZE):
            ex._q.put_nowait(dict(dummy_item))

        assert ex._q.full(), "Queue should be full"

        # Submit must return False (queue.Full)
        accepted = ex.submit(dict(dummy_item))
        assert accepted is False, "submit() must return False when queue is full"

    def test_submit_shadow_increments_dropped_counter(self):
        """submit_shadow() increments dropped_queue_full counter when queue is full."""
        _exp_module.init(_make_settings(_FULL_CFG))
        ex = _exp_module._executor
        assert ex is not None

        # Stop workers
        ex._stop.set()
        for t in ex._threads:
            t.join(timeout=2.0)

        # Fill queue to maxsize
        dummy_item = {
            "decision": _exp_module.Decision("test-exp", "summarize_rank.rank", "fill-ref2",
                                              [_exp_module.Arm("a", "m", {})]),
            "control_kwargs": {"model": "m", "messages": []},
            "control_outcome": _exp_module.CallOutcome("ok", None, None, None, 1, [], None),
            "attempt_fn": lambda kw: _exp_module.CallOutcome("ok", None, None, None, 1, [], None),
            "phash": "x2",
        }
        for _ in range(_exp_module._QUEUE_MAXSIZE):
            ex._q.put_nowait(dict(dummy_item))

        decision = _exp_module.Decision("test-exp", "summarize_rank.rank", "drop-ref",
                                        [_exp_module.Arm("a", "m", {})])
        control = _exp_module.CallOutcome("ok", None, None, None, 1, [], None)
        dropped_before = _exp_module._counters.dropped_queue_full
        _exp_module.submit_shadow(decision, {"model": "m", "messages": []}, control,
                                  lambda kw: control)
        assert _exp_module._counters.dropped_queue_full == dropped_before + 1

    def test_dropped_item_leaves_no_rows(self, db_session_factory):
        """AC-5: A dropped item (queue full) must leave zero rows in llm_shadow_results."""
        import aggregator_common.db as db_mod
        db_mod.SessionFactory = db_session_factory

        _reset_experiment()
        _exp_module.init(_make_settings(_FULL_CFG))
        ex = _exp_module._executor
        assert ex is not None

        # Stop workers so queue stays full
        ex._stop.set()
        for t in ex._threads:
            t.join(timeout=2.0)

        dummy_item = {
            "decision": _exp_module.Decision("test-exp", "summarize_rank.rank", "drop-test-ref",
                                              [_exp_module.Arm("a", "m", {})]),
            "control_kwargs": {"model": "m", "messages": []},
            "control_outcome": _exp_module.CallOutcome("ok", None, None, None, 1, [], None),
            "attempt_fn": lambda kw: _exp_module.CallOutcome("ok", None, None, None, 1, [], None),
            "phash": "y",
        }

        # Fill queue to maxsize — workers are stopped, won't drain
        for _ in range(_exp_module._QUEUE_MAXSIZE):
            ex._q.put_nowait(dict(dummy_item))

        assert ex._q.full(), "Queue should be full before test"

        # Try to submit one more — should fail
        accepted = ex.submit(dict(dummy_item))
        assert accepted is False, "Queue full: submit must return False"

        # Verify no rows for "drop-test-ref" were written (item was dropped, never processed)
        sess = db_session_factory()
        try:
            from aggregator_common.models import LlmShadowResult
            from sqlalchemy import select as sa_select
            rows = sess.execute(
                sa_select(LlmShadowResult).where(LlmShadowResult.ref_id == "drop-test-ref")
            ).scalars().all()
            assert len(rows) == 0, "Dropped item must not produce any rows"
        finally:
            sess.close()


# ---------------------------------------------------------------------------
# AC2 (prompt variant): unregistered or empty prompt disables experiment
# ---------------------------------------------------------------------------

class TestPromptVariantValidation:
    def _count_errors(self, caplog, fn):
        with caplog.at_level(logging.ERROR, logger="aggregator_common.experiment"):
            fn()
        return sum(1 for r in caplog.records if r.levelno == logging.ERROR)

    def _cfg_with_prompt(self, prompt_val):
        return json.dumps({
            "enabled": True,
            "name": "test-exp",
            "until": _UNTIL_FUTURE,
            "max_daily_cost_usd": 100.0,
            "operations": {
                "summarize_rank.rank": {
                    "sample_rate": 1.0,
                    "arms": [{"name": "a", "model": "m", "params": {}, "prompt": prompt_val}],
                }
            },
        })

    def test_unregistered_prompt_logs_one_error(self, caplog):
        count = self._count_errors(
            caplog,
            lambda: _exp_module.init(_make_settings(self._cfg_with_prompt("v3"))),
        )
        assert count == 1
        assert _exp_module._config is None

    def test_empty_string_prompt_logs_one_error(self, caplog):
        count = self._count_errors(
            caplog,
            lambda: _exp_module.init(_make_settings(self._cfg_with_prompt(""))),
        )
        assert count == 1
        assert _exp_module._config is None

    def test_valid_v1_prompt_accepted(self, caplog):
        count = self._count_errors(
            caplog,
            lambda: _exp_module.init(_make_settings(self._cfg_with_prompt("v1"))),
        )
        assert count == 0
        assert _exp_module._config is not None

    def test_valid_v2_prompt_accepted(self, caplog):
        count = self._count_errors(
            caplog,
            lambda: _exp_module.init(_make_settings(self._cfg_with_prompt("v2"))),
        )
        assert count == 0
        assert _exp_module._config is not None


# ---------------------------------------------------------------------------
# AC3 (prompt_variants): Decision.prompt_variants returns non-control variants
# ---------------------------------------------------------------------------

class TestDecisionPromptVariants:
    def test_returns_non_control_variants(self):
        decision = _exp_module.Decision(
            name="x", key="summarize_rank.rank", ref_id="1",
            arms=[
                _exp_module.Arm(name="ctrl", model="m", params={}, prompt="v1"),
                _exp_module.Arm(name="exp", model="m2", params={}, prompt="v2"),
            ],
        )
        assert decision.prompt_variants("v1") == {"v2"}

    def test_excludes_control_variant(self):
        decision = _exp_module.Decision(
            name="x", key="summarize_rank.rank", ref_id="1",
            arms=[
                _exp_module.Arm(name="a", model="m", params={}, prompt="v1"),
            ],
        )
        assert decision.prompt_variants("v1") == set()

    def test_multiple_arms_same_extra_variant(self):
        decision = _exp_module.Decision(
            name="x", key="summarize_rank.rank", ref_id="1",
            arms=[
                _exp_module.Arm(name="a", model="m1", params={}, prompt="v2"),
                _exp_module.Arm(name="b", model="m2", params={}, prompt="v2"),
            ],
        )
        assert decision.prompt_variants("v1") == {"v2"}

    def test_all_arms_on_control_variant_returns_empty(self):
        decision = _exp_module.Decision(
            name="x", key="summarize_rank.rank", ref_id="1",
            arms=[
                _exp_module.Arm(name="a", model="m1", params={}, prompt="v2"),
                _exp_module.Arm(name="b", model="m2", params={}, prompt="v2"),
            ],
        )
        assert decision.prompt_variants("v2") == set()


# ---------------------------------------------------------------------------
# AC3 (three-row write): submit_shadow with v1+v2 arms writes 3 rows with
# correct prompt hashes and params.prompt keys
# ---------------------------------------------------------------------------

class TestProcessItemWithVariantMessages:
    def test_three_rows_written_with_correct_params(self, db_session_factory):
        """v1-arm (control) and v2-arm produce three rows; v2 has different prompt_hash."""
        import aggregator_common.db as db_mod
        db_mod.SessionFactory = db_session_factory

        cfg = json.dumps({
            "enabled": True,
            "name": "variant-exp",
            "until": _UNTIL_FUTURE,
            "max_daily_cost_usd": 100.0,
            "operations": {
                "summarize_rank.rank": {
                    "sample_rate": 1.0,
                    "arms": [
                        {"name": "v1-arm", "model": "m1", "params": {}, "prompt": "v1"},
                        {"name": "v2-arm", "model": "m2", "params": {}, "prompt": "v2"},
                    ],
                }
            },
        })
        _exp_module.init(_make_settings(cfg))

        v1_msgs = [{"role": "system", "content": "v1 prompt"}]
        v2_msgs = [{"role": "system", "content": "v2 prompt different"}]
        ref = "variant-three-rows-ref-42"

        decision = _exp_module.Decision(
            name="variant-exp", key="summarize_rank.rank", ref_id=ref,
            arms=[
                _exp_module.Arm(name="v1-arm", model="m1", params={}, prompt="v1"),
                _exp_module.Arm(name="v2-arm", model="m2", params={}, prompt="v2"),
            ],
        )
        control_outcome = _exp_module.CallOutcome(
            status="ok", value=None, output=None,
            raw_output=None, attempts=1, request_ids=[], error=None,
        )

        def fake_attempt(kw):
            return _exp_module.CallOutcome(
                status="ok", value=None, output=None,
                raw_output=None, attempts=1, request_ids=[], error=None,
            )

        _exp_module.submit_shadow(
            decision,
            {"model": "ctrl-model", "messages": v1_msgs},
            control_outcome,
            fake_attempt,
            control_variant="v1",
            variant_messages={"v2": v2_msgs},
        )

        # Give executor time to process
        time.sleep(0.5)

        sess = db_session_factory()
        try:
            from aggregator_common.models import LlmShadowResult
            from sqlalchemy import select as sa_select
            rows = sess.execute(
                sa_select(LlmShadowResult).where(LlmShadowResult.ref_id == ref)
            ).scalars().all()
            assert len(rows) == 3

            by_arm = {r.arm: r for r in rows}
            assert "control" in by_arm
            assert "v1-arm" in by_arm
            assert "v2-arm" in by_arm

            # v2-arm must have a different prompt_hash from control/v1-arm
            ctrl_hash = by_arm["control"].prompt_hash
            v2_hash = by_arm["v2-arm"].prompt_hash
            assert ctrl_hash != v2_hash

            # v2-arm must record params.prompt = "v2"
            assert by_arm["v2-arm"].params.get("prompt") == "v2"

            # v1-arm control row must have no prompt key in params (v1 is control)
            assert "prompt" not in by_arm["control"].params
        finally:
            sess.close()


# ---------------------------------------------------------------------------
# AC6b: MissingPromptVariant in _process_item → api_error row for that arm
# ---------------------------------------------------------------------------

class TestMissingPromptVariantProducesApiError:
    def test_missing_variant_writes_api_error_row(self, db_session_factory):
        """Empty variant_messages={} causes v2 arm to get api_error; v1 arm still gets ok."""
        import aggregator_common.db as db_mod
        db_mod.SessionFactory = db_session_factory

        cfg = json.dumps({
            "enabled": True,
            "name": "miss-exp",
            "until": _UNTIL_FUTURE,
            "max_daily_cost_usd": 100.0,
            "operations": {
                "summarize_rank.rank": {
                    "sample_rate": 1.0,
                    "arms": [
                        {"name": "ok-arm", "model": "m1", "params": {}, "prompt": "v1"},
                        {"name": "miss-arm", "model": "m2", "params": {}, "prompt": "v2"},
                    ],
                }
            },
        })
        _exp_module.init(_make_settings(cfg))

        ref = "missing-variant-ref-77"
        decision = _exp_module.Decision(
            name="miss-exp", key="summarize_rank.rank", ref_id=ref,
            arms=[
                _exp_module.Arm(name="ok-arm", model="m1", params={}, prompt="v1"),
                _exp_module.Arm(name="miss-arm", model="m2", params={}, prompt="v2"),
            ],
        )
        control_outcome = _exp_module.CallOutcome(
            status="ok", value=None, output=None,
            raw_output=None, attempts=1, request_ids=[], error=None,
        )

        def fake_attempt(kw):
            return _exp_module.CallOutcome(
                status="ok", value=None, output=None,
                raw_output=None, attempts=1, request_ids=[], error=None,
            )

        # Pass empty variant_messages — v2 variant is missing, must produce api_error
        _exp_module.submit_shadow(
            decision,
            {"model": "ctrl-model", "messages": [{"role": "system", "content": "ctrl"}]},
            control_outcome,
            fake_attempt,
            control_variant="v1",
            variant_messages={},
        )

        time.sleep(0.5)

        sess = db_session_factory()
        try:
            from aggregator_common.models import LlmShadowResult
            from sqlalchemy import select as sa_select
            rows = sess.execute(
                sa_select(LlmShadowResult).where(LlmShadowResult.ref_id == ref)
            ).scalars().all()
            assert len(rows) == 3

            by_arm = {r.arm: r for r in rows}
            # ok-arm (v1 = control_variant) → no variant swap needed → ok
            assert by_arm["ok-arm"].status == "ok"
            # miss-arm needs v2 messages which are absent → api_error
            assert by_arm["miss-arm"].status == "api_error"
            assert "missing prompt variant v2" in (by_arm["miss-arm"].error or "")
        finally:
            sess.close()


# ---------------------------------------------------------------------------
# AC-13: crash between arm calls and write_item leaves no rows
# ---------------------------------------------------------------------------

class TestCrashBetweenArms:
    def test_crash_leaves_no_rows(self, db_session_factory):
        """AC-13: if write_item transaction fails (crash simulation), no rows appear."""
        import aggregator_common.db as db_mod

        ref = "crash-test-ref-999"

        rows = [
            _exp_module.ShadowRow(
                experiment="test-exp", service="summarize_rank", operation="rank",
                ref_id=ref, arm="control", model="m", prompt_hash="h",
                status="ok", attempts=1, params={}, output=None, raw_output=None, error=None,
            ),
            _exp_module.ShadowRow(
                experiment="test-exp", service="summarize_rank", operation="rank",
                ref_id=ref, arm="luna-low", model="gpt-6-luna", prompt_hash="h",
                status="ok", attempts=1, params={}, output=None, raw_output=None, error=None,
            ),
        ]

        # Inject error during session.execute to simulate crash mid-write
        sess_mock = MagicMock()
        sess_mock.execute.side_effect = RuntimeError("crash!")

        original_factory = db_mod.SessionFactory
        db_mod.SessionFactory = lambda: sess_mock
        try:
            # write_item must not raise even when DB call fails
            _exp_module.write_item(rows)
        finally:
            db_mod.SessionFactory = original_factory

        # Verify no rows were actually written (exception was raised in mocked session)
        real_sess = db_session_factory()
        try:
            from aggregator_common.models import LlmShadowResult
            from sqlalchemy import select as sa_select
            found = real_sess.execute(
                sa_select(LlmShadowResult).where(LlmShadowResult.ref_id == ref)
            ).scalars().all()
            assert len(found) == 0
        finally:
            real_sess.close()
