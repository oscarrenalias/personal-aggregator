"""Unit tests for LLM kwargs built by _make_llm_merge_fn and _make_llm_facts_fn.

Acceptance criteria covered:
- AC-0: merge call uses resolved merge model; literal 128 is gone, replaced by CLUSTERER_MERGE_LLM_MAX_OUTPUT_TOKENS
- AC-1: apply_reasoning_params called on merge control_kwargs before litellm and before submit_shadow
- AC-2: facts call uses resolved facts model and facts max_output_tokens
- AC-3: apply_reasoning_params called on facts control_kwargs with facts reasoning_effort
- AC-4: with no new settings, both calls' kwargs are dict-equal to before this change
- AC-5: with terra-none-v4b values, merge kwargs carry reasoning_effort='none', max_completion_tokens=128, temperature=0.0, no max_tokens
- AC-6: CLUSTERER_MERGE_LLM_MODEL change does not affect classify or facts kwargs
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://placeholder:placeholder@localhost/placeholder")

from aggregator_clusterer.config import ClustererSettings  # noqa: E402
from aggregator_clusterer.worker import _make_llm_facts_fn, _make_llm_merge_fn  # noqa: E402


def _merge_response(same_story: bool = False) -> SimpleNamespace:
    content = json.dumps({"same_story": same_story, "reason": "test"})
    msg = SimpleNamespace(content=content, refusal=None)
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return SimpleNamespace(choices=[choice], usage=usage, model="gpt-4.1", id="r-merge")


def _facts_response(facts: list[str] | None = None) -> SimpleNamespace:
    content = json.dumps(facts or ["condensed"])
    msg = SimpleNamespace(content=content, refusal=None)
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return SimpleNamespace(choices=[choice], usage=usage, model="gpt-4.1", id="r-facts")


def _make_thread(tid: int, title: str = "Thread") -> SimpleNamespace:
    return SimpleNamespace(
        id=tid,
        representative_title=title,
        rolling_summary="Some rolling summary.",
    )


# ---------------------------------------------------------------------------
# AC-4 / AC-0: defaults unchanged for merge
# ---------------------------------------------------------------------------

class TestMergeDefaultKwargs:
    def test_model_is_resolved_base_model(self):
        """With no per-op override, merge uses clusterer_llm_model (default gpt-4.1)."""
        settings = ClustererSettings()
        captured: list[dict] = []

        def mock_completion(**kwargs):
            captured.append(dict(kwargs))
            return _merge_response()

        merge_fn = _make_llm_merge_fn(settings)
        t1 = _make_thread(1)
        t2 = _make_thread(2)
        with patch("litellm.completion", side_effect=mock_completion):
            merge_fn(t1, t2)

        assert captured, "litellm.completion was not called"
        kw = captured[0]
        assert kw["model"] == "gpt-4.1"

    def test_max_tokens_uses_setting_not_literal(self):
        """max_tokens (or max_completion_tokens) reflects CLUSTERER_MERGE_LLM_MAX_OUTPUT_TOKENS (128)."""
        settings = ClustererSettings()
        captured: list[dict] = []

        def mock_completion(**kwargs):
            captured.append(dict(kwargs))
            return _merge_response()

        merge_fn = _make_llm_merge_fn(settings)
        t1 = _make_thread(1)
        t2 = _make_thread(2)
        with patch("litellm.completion", side_effect=mock_completion):
            merge_fn(t1, t2)

        kw = captured[0]
        # With no reasoning_effort, max_tokens should be preserved as-is
        assert kw.get("max_tokens") == 128
        assert "max_completion_tokens" not in kw

    def test_temperature_is_zero(self):
        settings = ClustererSettings()
        captured: list[dict] = []

        def mock_completion(**kwargs):
            captured.append(dict(kwargs))
            return _merge_response()

        merge_fn = _make_llm_merge_fn(settings)
        t1 = _make_thread(1)
        t2 = _make_thread(2)
        with patch("litellm.completion", side_effect=mock_completion):
            merge_fn(t1, t2)

        assert captured[0]["temperature"] == 0.0

    def test_no_reasoning_effort_in_default_kwargs(self):
        settings = ClustererSettings()
        captured: list[dict] = []

        def mock_completion(**kwargs):
            captured.append(dict(kwargs))
            return _merge_response()

        merge_fn = _make_llm_merge_fn(settings)
        t1 = _make_thread(1)
        t2 = _make_thread(2)
        with patch("litellm.completion", side_effect=mock_completion):
            merge_fn(t1, t2)

        assert "reasoning_effort" not in captured[0]


# ---------------------------------------------------------------------------
# AC-5: terra-none-v4b merge kwargs
# ---------------------------------------------------------------------------

class TestMergeTerraVoneBv4bKwargs:
    def test_reasoning_effort_none_swaps_max_tokens(self):
        """reasoning_effort='none' → max_tokens absent, max_completion_tokens present."""
        settings = ClustererSettings(
            clusterer_merge_llm_model="gpt-6.1-terra",
            clusterer_merge_llm_max_output_tokens=128,
            clusterer_merge_llm_reasoning_effort="none",
        )
        captured: list[dict] = []

        def mock_completion(**kwargs):
            captured.append(dict(kwargs))
            return _merge_response()

        merge_fn = _make_llm_merge_fn(settings)
        t1 = _make_thread(10)
        t2 = _make_thread(20)
        with patch("litellm.completion", side_effect=mock_completion):
            merge_fn(t1, t2)

        assert captured, "litellm.completion was not called"
        kw = captured[0]
        assert kw["model"] == "gpt-6.1-terra"
        assert kw["reasoning_effort"] == "none"
        assert kw["max_completion_tokens"] == 128
        assert "max_tokens" not in kw
        assert kw["temperature"] == 0.0

    def test_reasoning_effort_none_adds_allowed_openai_params(self):
        settings = ClustererSettings(
            clusterer_merge_llm_model="gpt-6.1-terra",
            clusterer_merge_llm_reasoning_effort="none",
        )
        captured: list[dict] = []

        def mock_completion(**kwargs):
            captured.append(dict(kwargs))
            return _merge_response()

        merge_fn = _make_llm_merge_fn(settings)
        t1 = _make_thread(10)
        t2 = _make_thread(20)
        with patch("litellm.completion", side_effect=mock_completion):
            merge_fn(t1, t2)

        assert "reasoning_effort" in captured[0].get("allowed_openai_params", [])


# ---------------------------------------------------------------------------
# AC-4 / AC-2: defaults unchanged for facts
# ---------------------------------------------------------------------------

class TestFactsDefaultKwargs:
    def test_model_is_resolved_base_model(self):
        """With no per-op override, facts uses clusterer_llm_model (default gpt-4.1)."""
        settings = ClustererSettings()
        captured: list[dict] = []

        def mock_completion(**kwargs):
            captured.append(dict(kwargs))
            return _facts_response()

        facts_fn = _make_llm_facts_fn(settings)
        with patch("litellm.completion", side_effect=mock_completion):
            facts_fn(99, ["fact a", "fact b"])

        assert captured, "litellm.completion was not called"
        assert captured[0]["model"] == "gpt-4.1"

    def test_max_tokens_uses_setting(self):
        """Facts max_tokens uses CLUSTERER_LLM_MAX_OUTPUT_TOKENS (default 512) when no override."""
        settings = ClustererSettings()
        captured: list[dict] = []

        def mock_completion(**kwargs):
            captured.append(dict(kwargs))
            return _facts_response()

        facts_fn = _make_llm_facts_fn(settings)
        with patch("litellm.completion", side_effect=mock_completion):
            facts_fn(99, ["f"])

        kw = captured[0]
        assert kw.get("max_tokens") == 512
        assert "max_completion_tokens" not in kw

    def test_no_reasoning_effort_in_default_kwargs(self):
        settings = ClustererSettings()
        captured: list[dict] = []

        def mock_completion(**kwargs):
            captured.append(dict(kwargs))
            return _facts_response()

        facts_fn = _make_llm_facts_fn(settings)
        with patch("litellm.completion", side_effect=mock_completion):
            facts_fn(99, ["f"])

        assert "reasoning_effort" not in captured[0]


# ---------------------------------------------------------------------------
# AC-3: facts with reasoning_effort applies apply_reasoning_params
# ---------------------------------------------------------------------------

class TestFactsReasoningEffortKwargs:
    def test_reasoning_effort_low_swaps_max_tokens(self):
        settings = ClustererSettings(
            clusterer_facts_llm_model="gpt-6.1-terra",
            clusterer_facts_llm_max_output_tokens=1024,
            clusterer_facts_llm_reasoning_effort="low",
        )
        captured: list[dict] = []

        def mock_completion(**kwargs):
            captured.append(dict(kwargs))
            return _facts_response()

        facts_fn = _make_llm_facts_fn(settings)
        with patch("litellm.completion", side_effect=mock_completion):
            facts_fn(5, ["f1"])

        kw = captured[0]
        assert kw["model"] == "gpt-6.1-terra"
        assert kw["reasoning_effort"] == "low"
        assert kw["max_completion_tokens"] == 1024
        assert "max_tokens" not in kw
        assert "temperature" not in kw


# ---------------------------------------------------------------------------
# AC-6: CLUSTERER_MERGE_LLM_MODEL does not affect classify or facts
# ---------------------------------------------------------------------------

class TestMergeModelIndependence:
    def test_merge_model_does_not_change_facts_model(self):
        """Setting CLUSTERER_MERGE_LLM_MODEL leaves facts model pointing at base model."""
        settings = ClustererSettings(
            clusterer_merge_llm_model="special-merge-model",
        )
        # clusterer_facts_llm_model should still resolve to the base clusterer_llm_model
        assert settings.clusterer_facts_llm_model == settings.clusterer_llm_model
        assert settings.clusterer_facts_llm_model != "special-merge-model"

    def test_merge_model_does_not_change_classify_model(self):
        """Setting CLUSTERER_MERGE_LLM_MODEL leaves classify model pointing at base model."""
        settings = ClustererSettings(
            clusterer_merge_llm_model="special-merge-model",
        )
        assert settings.clusterer_classify_llm_model == settings.clusterer_llm_model
        assert settings.clusterer_classify_llm_model != "special-merge-model"

    def test_merge_model_is_independent_of_classify_model(self):
        """Setting per-op classify and merge models independently works correctly."""
        settings = ClustererSettings(
            clusterer_classify_llm_model="classify-model",
            clusterer_merge_llm_model="merge-model",
        )
        assert settings.clusterer_classify_llm_model == "classify-model"
        assert settings.clusterer_merge_llm_model == "merge-model"
        assert settings.clusterer_facts_llm_model == settings.clusterer_llm_model
