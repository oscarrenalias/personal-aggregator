"""Unit tests for _resolve_artwork_url in generate.py.

Covers:
- Thread image found: first story segment with a header_image_url wins.
- Source fallback: no thread image → Source.default_image_url for first story's source.
- No image: returns None (not omitted, not raised) when both paths find nothing.
- String thread_id: coerced to int without error.
- No story segments: returns None immediately.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "postgresql://placeholder:placeholder@localhost/placeholder")


def _make_script(*thread_ids: int | str) -> dict:
    """Build a minimal script dict with story segments for each thread_id."""
    return {
        "segments": [{"type": "story", "thread_id": tid} for tid in thread_ids]
    }


def _mock_session_with_image(thread_id: int, image_url: str) -> MagicMock:
    """Session where execute() returns one row for thread_id with image_url."""
    row = MagicMock()
    row.thread_id = thread_id
    row.header_image_url = image_url

    session = MagicMock()
    session.execute.return_value.all.return_value = [row]
    return session


def _mock_session_no_image(source_default: str | None) -> MagicMock:
    """Session where no thread images exist; scalar() returns source_default."""
    session = MagicMock()
    session.execute.return_value.all.return_value = []
    session.scalar.return_value = source_default
    return session


# ── Three resolution paths ────────────────────────────────────────────────────

def test_thread_image_returned_for_first_story_with_image():
    """Returns header_image_url from the first story thread that has one."""
    from aggregator_podcast.generate import _resolve_artwork_url

    script = _make_script(10, 20)
    session = _mock_session_with_image(10, "https://cdn.example.com/img.jpg")

    result = _resolve_artwork_url(session, script)

    assert result == "https://cdn.example.com/img.jpg"
    # scalar() (source fallback) must NOT be called when a thread image resolves
    session.scalar.assert_not_called()


def test_second_story_image_used_when_first_story_has_none():
    """If the first story thread has no image but the second does, the second wins."""
    from aggregator_podcast.generate import _resolve_artwork_url

    script = _make_script(10, 20)

    # thread 20 has image; thread 10 does not
    row = MagicMock()
    row.thread_id = 20
    row.header_image_url = "https://cdn.example.com/second.jpg"

    session = MagicMock()
    session.execute.return_value.all.return_value = [row]

    result = _resolve_artwork_url(session, script)

    assert result == "https://cdn.example.com/second.jpg"


def test_source_fallback_fires_when_no_thread_has_image():
    """Falls back to Source.default_image_url when no thread has a header image."""
    from aggregator_podcast.generate import _resolve_artwork_url

    script = _make_script(10)
    session = _mock_session_no_image("https://source.example.com/logo.png")

    result = _resolve_artwork_url(session, script)

    assert result == "https://source.example.com/logo.png"


def test_returns_none_when_both_paths_find_nothing():
    """Returns None (not an exception, not an empty string) when no image resolves."""
    from aggregator_podcast.generate import _resolve_artwork_url

    script = _make_script(10)
    session = _mock_session_no_image(None)

    result = _resolve_artwork_url(session, script)

    assert result is None


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_no_story_segments_returns_none():
    """Returns None immediately when the script has no story-type segments."""
    from aggregator_podcast.generate import _resolve_artwork_url

    script = {
        "segments": [
            {"type": "intro", "text": "Welcome"},
            {"type": "outro", "text": "Goodbye"},
        ]
    }
    session = MagicMock()

    result = _resolve_artwork_url(session, script)

    assert result is None
    session.execute.assert_not_called()


def test_empty_segments_list_returns_none():
    """Returns None when segments list is empty."""
    from aggregator_podcast.generate import _resolve_artwork_url

    session = MagicMock()
    result = _resolve_artwork_url(session, {"segments": []})

    assert result is None
    session.execute.assert_not_called()


def test_string_thread_id_coerced_without_error():
    """String thread_id values are coerced to int; no TypeError or ValueError raised."""
    from aggregator_podcast.generate import _resolve_artwork_url

    # thread_ids as strings (as might come from JSON deserialisation)
    script = _make_script("42", "99")
    row = MagicMock()
    row.thread_id = 42
    row.header_image_url = "https://cdn.example.com/img.jpg"

    session = MagicMock()
    session.execute.return_value.all.return_value = [row]

    # Should not raise
    result = _resolve_artwork_url(session, script)

    assert result == "https://cdn.example.com/img.jpg"


def test_invalid_thread_id_skipped_gracefully():
    """Non-coercible thread_id values (e.g. None, 'abc') are skipped without error."""
    from aggregator_podcast.generate import _resolve_artwork_url

    script = {
        "segments": [
            {"type": "story", "thread_id": None},
            {"type": "story", "thread_id": "not-a-number"},
        ]
    }
    session = _mock_session_no_image(None)

    # No thread_ids collected → execute is never called; returns None
    result = _resolve_artwork_url(session, script)

    assert result is None


def test_missing_segments_key_returns_none():
    """Handles script dict without 'segments' key (e.g. legacy format) without error."""
    from aggregator_podcast.generate import _resolve_artwork_url

    session = MagicMock()
    result = _resolve_artwork_url(session, {})

    assert result is None
    session.execute.assert_not_called()
