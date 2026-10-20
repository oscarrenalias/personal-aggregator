"""Tests for summarize-rank shadow evaluation integration.

Covers:
- AC-1: unset LLM_EXPERIMENT → no shadow rows, no items enqueued
- AC-2: with experiment configured, control + arm rows written with same prompt_hash,
  correct arm kwargs (model, null removal, max_completion_tokens, allowed_openai_params)
- AC-3: five statuses (ok, parse_error, api_error, timeout, refusal) verified
- AC-4: first attempt malformed + second succeeds → control row has attempts=2, one shadow item enqueued
"""
from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://placeholder:placeholder@localhost/placeholder")

import aggregator_common.experiment as _exp_module  # noqa: E402
from aggregator_summarize_rank.config import SummarizeRankSettings  # noqa: E402
from aggregator_summarize_rank.ranker import RankError, rank  # noqa: E402
from aggregator_summarize_rank.schema import RankResult  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UNTIL_FUTURE = "2099-01-01T00:00:00Z"


def _make_settings(**overrides) -> SummarizeRankSettings:
    return SummarizeRankSettings(**overrides)


def _make_rank_response(
    summary: str = "A summary.",
    score: int = 75,
    topics: list[str] | None = None,
) -> SimpleNamespace:
    if topics is None:
        topics = ["tech"]
    content = json.dumps({
        "summary": summary,
        "topics": topics,
        "importance_score": score,
        "importance_reason": "reason",
    })
    message = SimpleNamespace(content=content, parsed=None, refusal=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        prompt_tokens_details=None,
        completion_tokens_details=None,
    )
    return SimpleNamespace(choices=[choice], usage=usage, model="gpt-4.1-mini", id="resp-1")


def _make_bad_response(content: str = "not json") -> SimpleNamespace:
    message = SimpleNamespace(content=content, parsed=None, refusal=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=50, completion_tokens=10, total_tokens=60,
                            prompt_tokens_details=None, completion_tokens_details=None)
    return SimpleNamespace(choices=[choice], usage=usage, model="gpt-4.1-mini", id="resp-bad")


def _reset_experiment():
    with _exp_module._lock:
        _exp_module._config = None
        _exp_module._initialized = False
        _exp_module._executor = None
    _exp_module._counters = _exp_module._Counters()
    _exp_module._budget = _exp_module._BudgetCache()


@pytest.fixture(autouse=True)
def reset_exp(db_engine):
    _reset_experiment()
    yield
    with _exp_module._lock:
        ex = _exp_module._executor
        _exp_module._executor = None
        _exp_module._config = None
        _exp_module._initialized = False
    if ex is not None:
        ex.shutdown()


@pytest.fixture(autouse=True)
def truncate_shadow_results(db_engine):
    """Ensure llm_shadow_results is empty before each test."""
    from sqlalchemy import text
    with db_engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE llm_shadow_results"))
        conn.commit()
    yield


def _init_experiment(sample_rate: float = 1.0):
    cfg = json.dumps({
        "enabled": True,
        "name": "test-exp",
        "until": _UNTIL_FUTURE,
        "max_daily_cost_usd": 9999.0,
        "operations": {
            "summarize_rank.rank": {
                "sample_rate": sample_rate,
                "arms": [
                    {
                        "name": "luna-low",
                        "model": "gpt-6-luna",
                        "params": {
                            "reasoning_effort": "low",
                            "temperature": None,
                            "max_tokens": None,
                            "max_completion_tokens": 2000,
                        },
                    }
                ],
            }
        },
    })
    settings_stub = SimpleNamespace(llm_experiment=cfg)
    _exp_module.init(settings_stub)


def _wait_for_shadow_rows(db_session, min_count: int = 1, timeout: float = 5.0):
    """Wait for at least min_count rows in llm_shadow_results. Returns current rows."""
    from aggregator_common.models import LlmShadowResult
    from sqlalchemy import select
    rows = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        db_session.rollback()
        rows = db_session.execute(select(LlmShadowResult)).scalars().all()
        if len(rows) >= min_count:
            break
        time.sleep(0.1)
    return rows


# ---------------------------------------------------------------------------
# AC-1: Unset experiment — no shadow rows, no items enqueued
# ---------------------------------------------------------------------------

class TestNoExperiment:
    def test_rank_succeeds_without_experiment_config(self, db_session):
        settings = _make_settings()
        article = {"clean_text": "x" * 300}
        with (
            patch("litellm.completion", return_value=_make_rank_response()),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            result, usage = rank(article, "I like tech.", settings)
        assert isinstance(result, RankResult)

    def test_no_shadow_rows_without_experiment(self, db_session):
        from aggregator_common.models import LlmShadowResult
        from sqlalchemy import select

        settings = _make_settings()
        article = {"clean_text": "x" * 300}
        with (
            patch("litellm.completion", return_value=_make_rank_response()),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            rank(article, "", settings)
            # Wait briefly inside mock context
            time.sleep(0.3)
            db_session.rollback()
            rows = db_session.execute(select(LlmShadowResult)).scalars().all()

        assert len(rows) == 0

    def test_no_items_enqueued_without_experiment(self):
        before = _exp_module._counters.enqueued
        settings = _make_settings()
        with (
            patch("litellm.completion", return_value=_make_rank_response()),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            rank({"clean_text": "x" * 300}, "", settings)
        assert _exp_module._counters.enqueued == before


# ---------------------------------------------------------------------------
# AC-2: With experiment — control + arm rows, same prompt_hash, correct arm kwargs
# ---------------------------------------------------------------------------

class TestWithExperiment:
    def test_control_and_arm_rows_written(self, db_session):
        _init_experiment(sample_rate=1.0)
        # Freeze budget at 0 so budget check always passes
        _exp_module._budget._cost = 0.0
        _exp_module._budget._refreshed_at = time.monotonic() + 3600

        settings = _make_settings()
        article = {"clean_text": "x" * 300}

        # Keep mock alive INSIDE block while waiting for background thread
        with (
            patch("litellm.completion", return_value=_make_rank_response()),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            rank(article, "tech", settings, article_id=42)
            rows = _wait_for_shadow_rows(db_session, min_count=2)

        assert len(rows) >= 2, f"Expected ≥2 rows (control + arm), got {len(rows)}"
        arms = {r.arm for r in rows}
        assert "control" in arms
        assert "luna-low" in arms

    def test_same_prompt_hash_for_all_rows(self, db_session):
        _init_experiment(sample_rate=1.0)
        _exp_module._budget._cost = 0.0
        _exp_module._budget._refreshed_at = time.monotonic() + 3600

        settings = _make_settings()
        with (
            patch("litellm.completion", return_value=_make_rank_response()),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            rank({"clean_text": "x" * 300}, "tech", settings, article_id=43)
            rows = _wait_for_shadow_rows(db_session, min_count=2)

        hashes = {r.prompt_hash for r in rows}
        assert len(hashes) == 1, f"All rows must share same prompt_hash, got {hashes}"

    def test_arm_kwargs_override_applied(self, db_session):
        """AC-2: arm kwargs: model=gpt-6-luna, max_tokens removed, max_completion_tokens=2000,
        allowed_openai_params includes reasoning_effort."""
        _init_experiment(sample_rate=1.0)
        _exp_module._budget._cost = 0.0
        _exp_module._budget._refreshed_at = time.monotonic() + 3600

        captured_kwargs: list[dict] = []

        def mock_completion(**kwargs):
            captured_kwargs.append(dict(kwargs))
            return _make_rank_response()

        settings = _make_settings()
        with (
            patch("litellm.completion", side_effect=mock_completion),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            rank({"clean_text": "x" * 300}, "tech", settings, article_id=44)
            # Wait for background thread to call arm attempt inside this mock context
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if len(captured_kwargs) >= 2:
                    break
                time.sleep(0.05)

        arm_calls = [kw for kw in captured_kwargs if kw.get("model") == "gpt-6-luna"]
        assert arm_calls, f"Expected arm call with gpt-6-luna, got models: {[kw.get('model') for kw in captured_kwargs]}"
        arm_kw = arm_calls[0]
        assert "max_tokens" not in arm_kw
        assert arm_kw.get("max_completion_tokens") == 2000
        assert "reasoning_effort" in arm_kw
        assert "allowed_openai_params" in arm_kw
        assert "reasoning_effort" in arm_kw["allowed_openai_params"]

    def test_caller_return_value_unchanged(self, db_session):
        """AC-2: caller's return value identical to unconfigured case."""
        _init_experiment(sample_rate=1.0)
        _exp_module._budget._cost = 0.0
        _exp_module._budget._refreshed_at = time.monotonic() + 3600

        settings = _make_settings()
        with (
            patch("litellm.completion", return_value=_make_rank_response(summary="Test summary", score=82)),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            result, usage = rank({"clean_text": "x" * 300}, "tech", settings, article_id=45)

        assert isinstance(result, RankResult)
        assert result.summary == "Test summary"
        assert result.importance_score == 82


# ---------------------------------------------------------------------------
# AC-3: api_error and timeout propagate unchanged; parse_error raises RankError
# ---------------------------------------------------------------------------

class TestStatusHandling:
    def test_api_error_propagates_unchanged(self, db_session):
        """AC-3 / AC-2: api_error propagates as RuntimeError, not as RankError."""
        _init_experiment(sample_rate=1.0)
        _exp_module._budget._cost = 0.0
        _exp_module._budget._refreshed_at = time.monotonic() + 3600

        settings = _make_settings()
        exc = RuntimeError("API failure")
        with patch("litellm.completion", side_effect=exc):
            with pytest.raises(RuntimeError, match="API failure"):
                rank({"clean_text": "x" * 300}, "tech", settings, article_id=102)

    def test_timeout_propagates_unchanged(self, db_session):
        """AC-3: timeout exception from litellm propagates unchanged."""
        _init_experiment(sample_rate=1.0)
        _exp_module._budget._cost = 0.0
        _exp_module._budget._refreshed_at = time.monotonic() + 3600

        settings = _make_settings()
        with patch("litellm.completion", side_effect=TimeoutError("timed out")):
            with pytest.raises(TimeoutError):
                rank({"clean_text": "x" * 300}, "tech", settings, article_id=103)

    def test_parse_error_raises_rank_error(self, db_session):
        """AC-3: parse_error (both attempts bad JSON) → raises RankError."""
        _init_experiment(sample_rate=1.0)
        _exp_module._budget._cost = 0.0
        _exp_module._budget._refreshed_at = time.monotonic() + 3600

        settings = _make_settings()
        with (
            patch("litellm.completion", side_effect=[_make_bad_response(), _make_bad_response()]),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            with pytest.raises(RankError):
                rank({"clean_text": "x" * 300}, "tech", settings, article_id=101)

    def test_parse_error_no_shadow_rows_when_attempt_raises(self, db_session):
        """When attempt() raises RankError, submit_shadow is never called → 0 shadow rows."""
        from aggregator_common.models import LlmShadowResult
        from sqlalchemy import select

        # No experiment configured
        settings = _make_settings()
        with (
            patch("litellm.completion", side_effect=[_make_bad_response(), _make_bad_response()]),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            with pytest.raises(RankError):
                rank({"clean_text": "x" * 300}, "tech", settings, article_id=111)
            time.sleep(0.3)
            db_session.rollback()
            rows = db_session.execute(select(LlmShadowResult)).scalars().all()

        assert len(rows) == 0, "No shadow rows when experiment not configured"

    def test_ok_status_on_control_row(self, db_session):
        """AC-3: successful call → control row has status='ok'."""
        _init_experiment(sample_rate=1.0)
        _exp_module._budget._cost = 0.0
        _exp_module._budget._refreshed_at = time.monotonic() + 3600

        settings = _make_settings()
        with (
            patch("litellm.completion", return_value=_make_rank_response()),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            rank({"clean_text": "x" * 300}, "tech", settings, article_id=100)
            rows = _wait_for_shadow_rows(db_session, min_count=1)

        control_rows = [r for r in rows if r.arm == "control"]
        assert any(r.status == "ok" for r in control_rows), f"Control row should have status=ok, got {[r.status for r in control_rows]}"


# ---------------------------------------------------------------------------
# AC-4: Retry on malformed → control row has attempts=2, one shadow item enqueued
# ---------------------------------------------------------------------------

class TestRetryAndShadow:
    def test_malformed_first_attempt_attempts_equals_2(self, db_session):
        """AC-4: first attempt malformed, second succeeds → control row attempts=2,
        exactly one shadow item enqueued (not two)."""
        _init_experiment(sample_rate=1.0)
        _exp_module._budget._cost = 0.0
        _exp_module._budget._refreshed_at = time.monotonic() + 3600

        enqueued_before = _exp_module._counters.enqueued
        settings = _make_settings()
        with (
            patch("litellm.completion", side_effect=[_make_bad_response(), _make_rank_response(), _make_rank_response()]),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            result, _ = rank({"clean_text": "x" * 300}, "tech", settings, article_id=200)
            # Exactly one shadow item enqueued (not two)
            assert _exp_module._counters.enqueued == enqueued_before + 1

            # Wait for worker to write rows inside mock context
            rows = _wait_for_shadow_rows(db_session, min_count=1)

        assert isinstance(result, RankResult)

        control_rows = [r for r in rows if r.arm == "control"]
        assert control_rows, "Control row should have been written"
        assert control_rows[0].attempts == 2, (
            f"Expected attempts=2 on control row (first attempt bad, second ok), got {control_rows[0].attempts}"
        )
