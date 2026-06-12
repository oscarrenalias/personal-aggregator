"""Unit tests for prompt.py — message structure, system variant, truncation, fallback chain."""

from __future__ import annotations

import pytest

from aggregator_summarize_rank.config import SummarizeRankSettings
from aggregator_summarize_rank.prompt import (
    INTEREST_PROFILE_MAX_CHARS,
    _build_category_instruction,
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

    def test_prompt_version_is_1_2_0(self):
        assert PROMPT_VERSION == "1.2.0"


class _Cat:
    """Minimal _CategoryEntry stand-in for tests."""
    def __init__(self, name: str, description: str | None = None):
        self.name = name
        self.description = description


class TestBuildCategoryInstruction:
    def test_category_with_description_included(self):
        cats = [_Cat("Tech", "Technology news")]
        text = _build_category_instruction(cats)
        assert "Tech" in text
        assert "Technology news" in text

    def test_category_without_description_name_only(self):
        cats = [_Cat("Sports")]
        text = _build_category_instruction(cats)
        assert "Sports" in text

    def test_multiple_categories_all_present(self):
        cats = [_Cat("Tech", "Technology"), _Cat("Science"), _Cat("Politics", "Political news")]
        text = _build_category_instruction(cats)
        assert "Tech" in text
        assert "Science" in text
        assert "Politics" in text

    def test_instruction_mentions_exact_names(self):
        cats = [_Cat("AI")]
        text = _build_category_instruction(cats)
        assert "exact names" in text.lower() or "using the exact" in text.lower()


class TestBuildMessagesWithCategories:
    def test_no_categories_omits_category_step(self, default_settings):
        msgs = build_messages({"clean_text": "x" * 300}, "", default_settings)
        assert "category" not in msgs[0]["content"].lower() or "5." not in msgs[0]["content"]

    def test_with_categories_adds_instruction_to_system(self, default_settings):
        cats = [_Cat("Tech", "Technology articles"), _Cat("Science")]
        msgs = build_messages({"clean_text": "x" * 300}, "", default_settings, enabled_categories=cats)
        assert "Tech" in msgs[0]["content"]
        assert "Science" in msgs[0]["content"]

    def test_with_categories_still_two_messages(self, default_settings):
        cats = [_Cat("AI")]
        msgs = build_messages({"clean_text": "x" * 300}, "", default_settings, enabled_categories=cats)
        assert len(msgs) == 2

    def test_empty_categories_list_treated_as_no_categories(self, default_settings):
        msgs_none = build_messages({"clean_text": "x" * 300}, "", default_settings, enabled_categories=None)
        msgs_empty = build_messages({"clean_text": "x" * 300}, "", default_settings, enabled_categories=[])
        assert msgs_none[0]["content"] == msgs_empty[0]["content"]


class TestLowPriorityProfileGuidance:
    """Regression tests: profiled prompt must carry low-priority/deprioritize guidance."""

    def test_profiled_system_prompt_mentions_deprioritize(self, default_settings):
        msgs = build_messages({"clean_text": "x" * 300}, "I like AI. Deprioritize sports.", default_settings)
        system = msgs[0]["content"].lower()
        assert "deprioritize" in system or "low-priority" in system or "de-prioritize" in system

    def test_profiled_system_prompt_mentions_lower_score_for_negative_signals(self, default_settings):
        msgs = build_messages({"clean_text": "x" * 300}, "I like AI.", default_settings)
        system = msgs[0]["content"].lower()
        assert "lower" in system or "0-30" in system

    def test_neutral_system_prompt_unchanged(self, default_settings):
        msgs = build_messages({"clean_text": "x" * 300}, "", default_settings)
        system = msgs[0]["content"].lower()
        assert "deprioritize" not in system and "low-priority" not in system

    def test_profiled_system_mentions_both_priorities_and_deprioritizations(self, default_settings):
        msgs = build_messages({"clean_text": "x" * 300}, "I like Python.", default_settings)
        system = msgs[0]["content"].lower()
        assert "deprioritize" in system or "de-prioritize" in system or "deprioritization" in system


class TestCategoryInstructionStrengthened:
    """Regression tests: category instruction must enforce clear-match and honor exclusions."""

    def test_instruction_requires_clear_match(self):
        cats = [_Cat("SoftwareEngineering", "Software engineering news. EXCLUDES video games.")]
        text = _build_category_instruction(cats)
        assert "clearly matches" in text.lower() or "clear match" in text.lower()

    def test_instruction_honors_exclusions(self):
        cats = [_Cat("SoftwareEngineering", "Software engineering news. EXCLUDES video games.")]
        text = _build_category_instruction(cats)
        assert "exclusion" in text.lower() or "excludes" in text.lower()

    def test_instruction_permits_zero_categories(self):
        cats = [_Cat("AI"), _Cat("Science")]
        text = _build_category_instruction(cats)
        assert "zero" in text.lower() or "no categor" in text.lower() or "nothing clearly fits" in text.lower()

    def test_instruction_discourages_over_assignment(self):
        cats = [_Cat("Tech", "Technology articles")]
        text = _build_category_instruction(cats)
        assert "over-assign" in text.lower() or "do not over" in text.lower() or "pad" in text.lower()

    def test_instruction_still_uses_exact_names(self):
        cats = [_Cat("AI")]
        text = _build_category_instruction(cats)
        assert "exact name" in text.lower()

    def test_category_with_exclusion_preserved_in_output(self):
        cats = [_Cat("SoftwareEngineering", "Software engineering. EXCLUDES video games.")]
        text = _build_category_instruction(cats)
        assert "EXCLUDES video games" in text
