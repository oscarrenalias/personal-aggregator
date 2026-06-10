"""Unit tests for ranker.py — happy path, retry on bad JSON, RankError after two failures.

litellm.completion is patched at the litellm module level (not via an import alias),
matching the acceptance criteria.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://placeholder:placeholder@localhost/placeholder")

from aggregator_summarize_rank.config import SummarizeRankSettings  # noqa: E402
from aggregator_summarize_rank.ranker import RankError, rank  # noqa: E402
from aggregator_summarize_rank.schema import RankResult  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides) -> SummarizeRankSettings:
    return SummarizeRankSettings(**overrides)


def _make_response(
    summary: str = "A clear summary.",
    topics: list[str] | None = None,
    score: int = 72,
    reason: str = "Matches stated interests.",
    model: str = "claude-sonnet-4-6",
    prompt_tokens: int = 120,
    completion_tokens: int = 60,
) -> SimpleNamespace:
    """Build a minimal litellm-response-shaped object."""
    if topics is None:
        topics = ["ai", "tech"]
    content = json.dumps({
        "summary": summary,
        "topics": topics,
        "importance_score": score,
        "importance_reason": reason,
    })
    message = SimpleNamespace(content=content, parsed=None)
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return SimpleNamespace(choices=[choice], usage=usage, model=model)


def _make_bad_response(content: str = "not valid json") -> SimpleNamespace:
    message = SimpleNamespace(content=content, parsed=None)
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(prompt_tokens=50, completion_tokens=10)
    return SimpleNamespace(choices=[choice], usage=usage, model="test-model")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestRankHappyPath:
    def test_returns_rank_result_and_usage_dict(self):
        settings = _make_settings()
        article = {"source_name": "TechCrunch", "clean_text": "x" * 500}

        with (
            patch("litellm.completion", return_value=_make_response()),
            patch("litellm.completion_cost", return_value=0.0012),
        ):
            result, usage = rank(article, "I like AI.", settings)

        assert isinstance(result, RankResult)
        assert result.summary == "A clear summary."
        assert result.importance_score == 72
        assert result.topics == ["ai", "tech"]

    def test_usage_dict_contains_required_keys(self):
        settings = _make_settings()
        article = {"clean_text": "x" * 500}

        with (
            patch("litellm.completion", return_value=_make_response()),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            _, usage = rank(article, "", settings)

        assert "model" in usage
        assert "prompt_tokens" in usage
        assert "completion_tokens" in usage
        assert "prompt_version" in usage

    def test_usage_dict_prompt_version_nonempty(self):
        settings = _make_settings()
        article = {"clean_text": "x" * 500}

        with (
            patch("litellm.completion", return_value=_make_response()),
            patch("litellm.completion_cost", side_effect=Exception("no cost")),
        ):
            _, usage = rank(article, "", settings)

        assert usage["prompt_version"]
        assert "cost" not in usage

    def test_cost_included_when_available(self):
        settings = _make_settings()
        article = {"clean_text": "x" * 500}

        with (
            patch("litellm.completion", return_value=_make_response()),
            patch("litellm.completion_cost", return_value=0.005),
        ):
            _, usage = rank(article, "", settings)

        assert usage["cost"] == pytest.approx(0.005)

    def test_completion_called_once_on_success(self):
        settings = _make_settings()
        article = {"clean_text": "x" * 500}

        with (
            patch("litellm.completion", return_value=_make_response()) as mock_completion,
            patch("litellm.completion_cost", return_value=0.001),
        ):
            _result, _usage = rank(article, "", settings)

        mock_completion.assert_called_once()


# ---------------------------------------------------------------------------
# Retry on bad JSON — first attempt fails parsing, second succeeds
# ---------------------------------------------------------------------------


class TestRetryOnBadJson:
    def test_retries_once_on_malformed_response(self):
        settings = _make_settings()
        article = {"clean_text": "x" * 500}

        with (
            patch(
                "litellm.completion",
                side_effect=[_make_bad_response(), _make_response()],
            ) as mock_completion,
            patch("litellm.completion_cost", return_value=0.001),
        ):
            result, _ = rank(article, "", settings)

        assert mock_completion.call_count == 2
        assert isinstance(result, RankResult)

    def test_retry_result_is_valid(self):
        settings = _make_settings()
        article = {"clean_text": "x" * 500}
        good = _make_response(summary="Retry succeeded", score=55)

        with (
            patch("litellm.completion", side_effect=[_make_bad_response(), good]),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            result, _ = rank(article, "", settings)

        assert result.summary == "Retry succeeded"
        assert result.importance_score == 55


# ---------------------------------------------------------------------------
# RankError after two consecutive bad responses
# ---------------------------------------------------------------------------


class TestRankError:
    def test_raises_rank_error_after_two_failures(self):
        settings = _make_settings()
        article = {"clean_text": "x" * 500}

        with (
            patch(
                "litellm.completion",
                side_effect=[_make_bad_response(), _make_bad_response()],
            ),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            with pytest.raises(RankError):
                rank(article, "", settings)

    def test_rank_error_called_exactly_twice(self):
        settings = _make_settings()
        article = {"clean_text": "x" * 500}

        with (
            patch("litellm.completion", side_effect=[_make_bad_response(), _make_bad_response()]) as mock_c,
            patch("litellm.completion_cost", return_value=0.001),
        ):
            with pytest.raises(RankError):
                rank(article, "", settings)

        assert mock_c.call_count == 2

    def test_litellm_exception_propagates_directly(self):
        """An exception from litellm.completion itself (not parsing) propagates uncaught."""
        settings = _make_settings()
        article = {"clean_text": "x" * 500}

        with patch("litellm.completion", side_effect=RuntimeError("API failure")):
            with pytest.raises(RuntimeError, match="API failure"):
                rank(article, "", settings)

    def test_rank_error_message_mentions_retry(self):
        settings = _make_settings()
        article = {"clean_text": "x" * 500}

        with (
            patch("litellm.completion", side_effect=[_make_bad_response(), _make_bad_response()]),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            with pytest.raises(RankError, match="retry"):
                rank(article, "", settings)
