"""Unit tests for prompt.py — message structure, system variant, truncation, fallback chain."""

from __future__ import annotations

import pytest

from aggregator_summarize_rank.config import SummarizeRankSettings
from aggregator_summarize_rank.prompt import (
    INTEREST_PROFILE_MAX_CHARS,
    build_messages,
    get_prompt_version,
)
from aggregator_summarize_rank.schema import PROMPT_VERSION


@pytest.fixture
def default_settings():
    import os
    os.environ.setdefault("DATABASE_URL", "postgresql://placeholder:placeholder@localhost/placeholder")
    return SummarizeRankSettings()


class TestMessageStructure:
    def test_returns_exactly_two_messages(self, default_settings):
        msgs = build_messages({"clean_text": "x" * 300}, "", default_settings)
        assert len(msgs) == 2

    def test_first_message_is_system(self, default_settings):
        msgs = build_messages({"clean_text": "x" * 300}, "", default_settings)
        assert msgs[0]["role"] == "system"
        assert "content" in msgs[0]

    def test_second_message_is_user(self, default_settings):
        msgs = build_messages({"clean_text": "x" * 300}, "", default_settings)
        assert msgs[1]["role"] == "user"
        assert "content" in msgs[1]


class TestSystemVariantSelection:
    def test_with_profile_uses_interest_profile_prompt(self, default_settings):
        msgs = build_messages({"clean_text": "x" * 300}, "I like AI and robotics.", default_settings)
        assert "interest profile" in msgs[0]["content"].lower()

    def test_without_profile_uses_neutral_prompt(self, default_settings):
        msgs = build_messages({"clean_text": "x" * 300}, "", default_settings)
        assert "general reader" in msgs[0]["content"].lower()

    def test_whitespace_only_profile_uses_neutral_prompt(self, default_settings):
        msgs = build_messages({"clean_text": "x" * 300}, "   \n  ", default_settings)
        assert "general reader" in msgs[0]["content"].lower()

    def test_profile_text_included_in_user_message(self, default_settings):
        profile = "I enjoy deep-sea biology and machine learning."
        msgs = build_messages({"clean_text": "x" * 300}, profile, default_settings)
        assert profile in msgs[1]["content"]

    def test_profile_truncated_at_max_chars(self, default_settings):
        profile = "X" * (INTEREST_PROFILE_MAX_CHARS + 500)
        msgs = build_messages({"clean_text": "x" * 300}, profile, default_settings)
        assert "X" * INTEREST_PROFILE_MAX_CHARS in msgs[1]["content"]
        assert "X" * (INTEREST_PROFILE_MAX_CHARS + 1) not in msgs[1]["content"]


class TestContentTruncation:
    def test_content_at_exact_max_chars_not_truncated(self, default_settings):
        content = "a" * default_settings.llm_max_input_chars
        msgs = build_messages({"clean_text": content}, "", default_settings)
        assert "a" * default_settings.llm_max_input_chars in msgs[1]["content"]

    def test_content_exceeding_max_chars_is_truncated(self, default_settings):
        content = "b" * (default_settings.llm_max_input_chars + 1000)
        msgs = build_messages({"clean_text": content}, "", default_settings)
        assert "b" * (default_settings.llm_max_input_chars + 1) not in msgs[1]["content"]
        assert "b" * default_settings.llm_max_input_chars in msgs[1]["content"]

    def test_truncation_at_exact_boundary(self, default_settings):
        limit = default_settings.llm_max_input_chars
        content = "c" * (limit * 2)
        msgs = build_messages({"clean_text": content}, "", default_settings)
        user_content = msgs[1]["content"]
        # Content in user message should contain exactly limit 'c's
        article_section = user_content.split("Article content:\n", 1)[1]
        assert len(article_section) == limit


class TestFallbackChain:
    def test_clean_text_used_when_present(self, default_settings):
        article = {
            "clean_text": "clean text content",
            "excerpt": "excerpt content",
            "feed_summary": "feed summary content",
        }
        msgs = build_messages(article, "", default_settings)
        assert "clean text content" in msgs[1]["content"]

    def test_excerpt_used_when_clean_text_absent(self, default_settings):
        article = {
            "clean_text": None,
            "excerpt": "excerpt content",
            "feed_summary": "feed summary content",
        }
        msgs = build_messages(article, "", default_settings)
        assert "excerpt content" in msgs[1]["content"]
        assert "feed summary content" not in msgs[1]["content"]

    def test_feed_summary_used_as_final_fallback(self, default_settings):
        article = {
            "clean_text": None,
            "excerpt": None,
            "feed_summary": "feed summary content",
        }
        msgs = build_messages(article, "", default_settings)
        assert "feed summary content" in msgs[1]["content"]

    def test_empty_content_when_all_absent(self, default_settings):
        article = {"clean_text": None, "excerpt": None, "feed_summary": None}
        msgs = build_messages(article, "", default_settings)
        assert "Article content:" in msgs[1]["content"]


class TestSourceAndTitle:
    def test_source_name_in_user_message(self, default_settings):
        msgs = build_messages({"source_name": "TechCrunch", "clean_text": "x" * 300}, "", default_settings)
        assert "TechCrunch" in msgs[1]["content"]

    def test_unknown_source_fallback(self, default_settings):
        msgs = build_messages({"source_name": None, "clean_text": "x" * 300}, "", default_settings)
        assert "Unknown Source" in msgs[1]["content"]

    def test_title_fallback_chain(self, default_settings):
        article = {"clean_title": None, "feed_title": "Feed Title", "clean_text": "x" * 300}
        msgs = build_messages(article, "", default_settings)
        assert "Feed Title" in msgs[1]["content"]

    def test_no_title_fallback(self, default_settings):
        article = {"clean_title": None, "feed_title": None, "title": None, "clean_text": "x" * 300}
        msgs = build_messages(article, "", default_settings)
        assert "(no title)" in msgs[1]["content"]


class TestGetPromptVersion:
    def test_equals_prompt_version_constant(self):
        assert get_prompt_version() == PROMPT_VERSION

    def test_returns_string(self):
        assert isinstance(get_prompt_version(), str)
