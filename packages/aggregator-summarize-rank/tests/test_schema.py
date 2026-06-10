"""Unit tests for schema.py — RankResult clamping, truncation, and PROMPT_VERSION."""

from __future__ import annotations

import pytest

from aggregator_summarize_rank.schema import PROMPT_VERSION, RankResult


class TestRankResultConstruction:
    def test_valid_construction(self):
        r = RankResult(
            summary="A short summary.",
            topics=["ai", "tech"],
            importance_score=50,
            importance_reason="Relevant to stated interests.",
        )
        assert r.summary == "A short summary."
        assert r.topics == ["ai", "tech"]
        assert r.importance_score == 50
        assert r.importance_reason == "Relevant to stated interests."

    def test_all_fields_present(self):
        r = RankResult(summary="s", topics=[], importance_score=0, importance_reason="r")
        assert hasattr(r, "summary")
        assert hasattr(r, "topics")
        assert hasattr(r, "importance_score")
        assert hasattr(r, "importance_reason")


class TestScoreClamping:
    def test_score_below_zero_clamped_to_zero(self):
        r = RankResult(summary="s", topics=[], importance_score=-5, importance_reason="r")
        assert r.importance_score == 0

    def test_score_above_hundred_clamped_to_hundred(self):
        r = RankResult(summary="s", topics=[], importance_score=150, importance_reason="r")
        assert r.importance_score == 100

    def test_score_at_zero_unchanged(self):
        r = RankResult(summary="s", topics=[], importance_score=0, importance_reason="r")
        assert r.importance_score == 0

    def test_score_at_hundred_unchanged(self):
        r = RankResult(summary="s", topics=[], importance_score=100, importance_reason="r")
        assert r.importance_score == 100

    def test_score_midrange_unchanged(self):
        r = RankResult(summary="s", topics=[], importance_score=72, importance_reason="r")
        assert r.importance_score == 72


class TestTopicsTruncation:
    def test_topics_list_of_seven_truncated_to_five(self):
        topics = ["a", "b", "c", "d", "e", "f", "g"]
        r = RankResult(summary="s", topics=topics, importance_score=50, importance_reason="r")
        assert len(r.topics) == 5
        assert r.topics == ["a", "b", "c", "d", "e"]

    def test_topics_list_of_five_unchanged(self):
        topics = ["a", "b", "c", "d", "e"]
        r = RankResult(summary="s", topics=topics, importance_score=50, importance_reason="r")
        assert len(r.topics) == 5

    def test_empty_topics_unchanged(self):
        r = RankResult(summary="s", topics=[], importance_score=50, importance_reason="r")
        assert r.topics == []


class TestPromptVersion:
    def test_prompt_version_nonempty(self):
        assert PROMPT_VERSION
        assert len(PROMPT_VERSION) > 0

    def test_prompt_version_is_string(self):
        assert isinstance(PROMPT_VERSION, str)
