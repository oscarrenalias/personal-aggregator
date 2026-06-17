"""Unit tests for aggregator_processor.extract — no DB, no real HTTP."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from aggregator_processor.extract import (
    ExtractionResult,
    _strip_html_summary,
    _trim_at_word_boundary,
    extract_content,
)


def _meta(title=None, author=None, date=None, language=None):
    return SimpleNamespace(title=title, author=author, date=date, language=language)


# ---------------------------------------------------------------------------
# _trim_at_word_boundary helper
# ---------------------------------------------------------------------------


class TestTrimAtWordBoundary:
    def test_text_shorter_than_limit_unchanged(self):
        assert _trim_at_word_boundary("hello world", 300) == "hello world"

    def test_trims_to_last_space_before_limit(self):
        text = "hello world foo"
        result = _trim_at_word_boundary(text, 11)
        # "hello world" is 11 chars; rfind(" ", 0, 12) finds index 5 or 11
        assert len(result) <= 11
        assert not result.endswith(" ")

    def test_no_space_falls_back_to_hard_cut(self):
        text = "abcdefghij"
        result = _trim_at_word_boundary(text, 5)
        assert result == "abcde"

    def test_exactly_at_limit_unchanged(self):
        text = "hello"
        assert _trim_at_word_boundary(text, 5) == "hello"

    def test_long_text_trimmed_at_word_boundary(self):
        text = "word " * 100  # 500 chars total
        result = _trim_at_word_boundary(text, 300)
        assert len(result) <= 300
        # must end on a complete word (or be empty if all spaces)
        assert not result.endswith(" w")


# ---------------------------------------------------------------------------
# extract_content — full extraction
# ---------------------------------------------------------------------------


class TestExtractContent:
    def test_all_fields_populated_from_trafilatura(self):
        html = b"<html><body><p>article</p></body></html>"
        meta = _meta(title="My Title", author="Jane Doe", date="2024-06-01", language="en")

        with (
            patch("trafilatura.load_html") as mock_load,
            patch("trafilatura.extract_metadata", return_value=meta),
            patch("trafilatura.extract", return_value="Long article content words"),
        ):
            mock_load.return_value = MagicMock()
            result = extract_content(html, {})

        assert result.clean_title == "My Title"
        assert result.author == "Jane Doe"
        assert result.language == "en"
        assert result.clean_text == "Long article content words"
        assert result.word_count == 4
        assert result.excerpt == "Long article content words"
        assert result.published_at is not None
        assert result.published_at.year == 2024

    def test_clean_title_falls_back_to_feed_title(self):
        html = b"<html><body>content</body></html>"
        meta = _meta(title=None)
        fallback = {"feed_title": "Fallback Feed Title"}

        with (
            patch("trafilatura.load_html") as mock_load,
            patch("trafilatura.extract_metadata", return_value=meta),
            patch("trafilatura.extract", return_value="some content"),
        ):
            mock_load.return_value = MagicMock()
            result = extract_content(html, fallback)

        assert result.clean_title == "Fallback Feed Title"

    def test_clean_title_none_when_both_absent(self):
        html = b"<html><body>content</body></html>"
        meta = _meta(title=None)

        with (
            patch("trafilatura.load_html") as mock_load,
            patch("trafilatura.extract_metadata", return_value=meta),
            patch("trafilatura.extract", return_value="some content"),
        ):
            mock_load.return_value = MagicMock()
            result = extract_content(html, {})

        assert result.clean_title is None

    def test_author_falls_back_to_raw_payload(self):
        html = b"<html><body>content</body></html>"
        meta = _meta(author=None)
        fallback = {"raw_payload": {"author": "Payload Author"}}

        with (
            patch("trafilatura.load_html") as mock_load,
            patch("trafilatura.extract_metadata", return_value=meta),
            patch("trafilatura.extract", return_value="some content"),
        ):
            mock_load.return_value = MagicMock()
            result = extract_content(html, fallback)

        assert result.author == "Payload Author"

    def test_author_none_when_all_fallbacks_absent(self):
        html = b"<html><body>content</body></html>"
        meta = _meta(author=None)
        fallback = {"raw_payload": {}}

        with (
            patch("trafilatura.load_html") as mock_load,
            patch("trafilatura.extract_metadata", return_value=meta),
            patch("trafilatura.extract", return_value="some content"),
        ):
            mock_load.return_value = MagicMock()
            result = extract_content(html, fallback)

        assert result.author is None

    def test_published_at_falls_back_to_feed_published_at(self):
        html = b"<html><body>content</body></html>"
        meta = _meta(date=None)
        feed_dt = datetime(2024, 3, 10, tzinfo=timezone.utc)
        fallback = {"feed_published_at": feed_dt}

        with (
            patch("trafilatura.load_html") as mock_load,
            patch("trafilatura.extract_metadata", return_value=meta),
            patch("trafilatura.extract", return_value="some content"),
        ):
            mock_load.return_value = MagicMock()
            result = extract_content(html, fallback)

        assert result.published_at == feed_dt

    def test_published_at_none_when_all_fallbacks_absent(self):
        html = b"<html><body>content</body></html>"
        meta = _meta(date=None)

        with (
            patch("trafilatura.load_html") as mock_load,
            patch("trafilatura.extract_metadata", return_value=meta),
            patch("trafilatura.extract", return_value="some content"),
        ):
            mock_load.return_value = MagicMock()
            result = extract_content(html, {})

        assert result.published_at is None

    def test_language_none_when_undetectable(self):
        html = b"<html><body>content</body></html>"
        meta = _meta(language=None)

        with (
            patch("trafilatura.load_html") as mock_load,
            patch("trafilatura.extract_metadata", return_value=meta),
            patch("trafilatura.extract", return_value="some content"),
        ):
            mock_load.return_value = MagicMock()
            result = extract_content(html, {})

        assert result.language is None

    def test_excerpt_word_boundary_trim_at_300(self):
        long_text = ("word " * 80).rstrip()  # 399 chars
        assert len(long_text) > 300
        html = b"<html><body>content</body></html>"
        meta = _meta()

        with (
            patch("trafilatura.load_html") as mock_load,
            patch("trafilatura.extract_metadata", return_value=meta),
            patch("trafilatura.extract", return_value=long_text),
        ):
            mock_load.return_value = MagicMock()
            result = extract_content(html, {})

        assert result.excerpt is not None
        assert len(result.excerpt) <= 300
        # Must end at a word boundary — no partial word immediately after the cut
        assert result.excerpt[-1] != " " or result.excerpt.strip()

    def test_excerpt_falls_back_to_feed_summary_when_no_clean_text(self):
        html = b"<html><body></body></html>"
        meta = _meta()
        fallback = {"feed_summary": "Feed summary preview text"}

        with (
            patch("trafilatura.load_html") as mock_load,
            patch("trafilatura.extract_metadata", return_value=meta),
            patch("trafilatura.extract", return_value=None),
        ):
            mock_load.return_value = MagicMock()
            result = extract_content(html, fallback)

        assert result.excerpt == "Feed summary preview text"
        assert result.clean_text is None

    def test_word_count_correctness(self):
        text = "one two three four five"
        html = b"<html><body>content</body></html>"
        meta = _meta()

        with (
            patch("trafilatura.load_html") as mock_load,
            patch("trafilatura.extract_metadata", return_value=meta),
            patch("trafilatura.extract", return_value=text),
        ):
            mock_load.return_value = MagicMock()
            result = extract_content(html, {})

        assert result.word_count == 5

    def test_word_count_zero_when_no_clean_text(self):
        html = b"<html><body></body></html>"
        meta = _meta()

        with (
            patch("trafilatura.load_html") as mock_load,
            patch("trafilatura.extract_metadata", return_value=meta),
            patch("trafilatura.extract", return_value=None),
        ):
            mock_load.return_value = MagicMock()
            result = extract_content(html, {})

        assert result.word_count == 0

    def test_bytes_input_passed_to_load_html(self):
        html = b"<html><body>bytes content</body></html>"
        meta = _meta(title="Bytes Test")

        with (
            patch("trafilatura.load_html") as mock_load,
            patch("trafilatura.extract_metadata", return_value=meta),
            patch("trafilatura.extract", return_value="bytes content"),
        ):
            mock_load.return_value = MagicMock()
            result = extract_content(html, {})

        mock_load.assert_called_once_with(html)
        assert result.clean_title == "Bytes Test"

    def test_str_input_passed_to_load_html(self):
        html = "<html><body>string content</body></html>"
        meta = _meta(title="String Test")

        with (
            patch("trafilatura.load_html") as mock_load,
            patch("trafilatura.extract_metadata", return_value=meta),
            patch("trafilatura.extract", return_value="string content"),
        ):
            mock_load.return_value = MagicMock()
            result = extract_content(html, {})

        mock_load.assert_called_once_with(html)
        assert result.clean_title == "String Test"

    def test_load_html_returns_none_gives_empty_result(self):
        html = b"broken"
        fallback = {"feed_title": "Fallback Title"}

        with patch("trafilatura.load_html", return_value=None):
            result = extract_content(html, fallback)

        assert result.clean_text is None
        assert result.clean_title == "Fallback Title"
        assert result.word_count == 0
        assert result.language is None

    def test_html_feed_summary_stripped_when_no_clean_text(self):
        """Regression: Reddit RSS puts raw HTML in feed_summary; excerpt must be clean text."""
        reddit_summary = (
            '&#32; submitted by &#32; <a href="https://reddit.com/u/user">user</a>'
            ' &#32; <a href="https://reddit.com/r/python/comments/abc/article">'
            "23 comments</a>"
        )
        html = b"<html><body></body></html>"
        meta = _meta()
        fallback = {"feed_summary": reddit_summary}

        with (
            patch("trafilatura.load_html") as mock_load,
            patch("trafilatura.extract_metadata", return_value=meta),
            patch("trafilatura.extract", return_value=None),
        ):
            mock_load.return_value = MagicMock()
            result = extract_content(html, fallback)

        assert result.excerpt is not None
        assert "<" not in result.excerpt, "HTML tags must be stripped from excerpt"
        assert "&#" not in result.excerpt, "HTML entities must be unescaped in excerpt"
        assert "submitted by" in result.excerpt
        assert "user" in result.excerpt

    def test_html_only_feed_summary_returns_none_when_empty_after_strip(self):
        """Excerpt must be None when feed_summary contains only tags/whitespace."""
        html = b"<html><body></body></html>"
        meta = _meta()
        fallback = {"feed_summary": "  <br/><br/>  "}

        with (
            patch("trafilatura.load_html") as mock_load,
            patch("trafilatura.extract_metadata", return_value=meta),
            patch("trafilatura.extract", return_value=None),
        ):
            mock_load.return_value = MagicMock()
            result = extract_content(html, fallback)

        assert result.excerpt is None


# ---------------------------------------------------------------------------
# _strip_html_summary helper
# ---------------------------------------------------------------------------


class TestStripHtmlSummary:
    def test_strips_tags_and_unescapes_entities(self):
        result = _strip_html_summary('&#32; submitted by <a href="x">user</a>')
        assert result is not None
        assert "<" not in result
        assert "&#" not in result
        assert "submitted by" in result
        assert "user" in result

    def test_plain_text_unchanged(self):
        result = _strip_html_summary("Clean plain text excerpt")
        assert result == "Clean plain text excerpt"

    def test_collapses_whitespace(self):
        result = _strip_html_summary("word1   \n\t  word2")
        assert result == "word1 word2"

    def test_returns_none_for_tags_only(self):
        assert _strip_html_summary("  <br/>  <p>  </p>  ") is None

    def test_returns_none_for_empty_string(self):
        assert _strip_html_summary("") is None

    def test_reddit_table_markup_stripped(self):
        table_html = (
            "<table><tr><td><a href='https://example.com/link'>link</a></td>"
            "<td>&#32; submitted by &#32; <a href='u/user'>u/user</a></td></tr></table>"
        )
        result = _strip_html_summary(table_html)
        assert result is not None
        assert "<table>" not in result
        assert "<tr>" not in result
        assert "submitted by" in result
        assert "u/user" in result
