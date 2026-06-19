"""Tests for forward-compatible response decoding.

The API may add response fields over time (a backwards-compatible change); the
client must ignore unknown keys rather than crash on an unexpected kwarg.
"""
from __future__ import annotations

from aggregator_tui.api_client import ArticleResponse, _decode


def test_decode_ignores_unknown_fields() -> None:
    # An API response carrying a field the client doesn't model (e.g. a future
    # addition) must not raise.
    article = _decode(
        ArticleResponse,
        {
            "id": 1,
            "source_id": 2,
            "is_read": False,
            "is_saved": False,
            "title": "Hello",
            "image_url": "https://img.example.com/hero.jpg",  # not (yet) a client field
            "some_future_field": "ignored",
        },
    )
    assert article.id == 1
    assert article.title == "Hello"
    # unknown keys were dropped, not attached
    assert not hasattr(article, "some_future_field")
    assert not hasattr(article, "image_url")


def test_decode_populates_known_fields_and_defaults_missing() -> None:
    article = _decode(ArticleResponse, {"id": 5, "source_id": 1, "is_read": True, "is_saved": False})
    assert article.id == 5
    assert article.is_read is True
    assert article.title is None  # optional, defaulted
