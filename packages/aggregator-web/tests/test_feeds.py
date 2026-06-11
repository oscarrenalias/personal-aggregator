"""Tests for aggregator_web.feeds — unit tests using a live Postgres container."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aggregator_common.state import ArticleStatus
from aggregator_web.feeds import (
    Cursor,
    category_feed,
    get_sidebar_counts,
    smart_feed,
    source_feed,
)
from conftest import make_article, make_category, make_source


# ---------------------------------------------------------------------------
# smart_feed
# ---------------------------------------------------------------------------


def test_smart_feed_all_returns_ready_articles(db_session):
    src = make_source(db_session)
    a1 = make_article(db_session, source_id=src.id, dedup_key="k1")
    a2 = make_article(db_session, source_id=src.id, dedup_key="k2")
    page = smart_feed("all", db_session, page_size=10, important_threshold=70)
    ids = {a.id for a in page.articles}
    assert a1.id in ids
    assert a2.id in ids


def test_smart_feed_excludes_non_ready(db_session):
    src = make_source(db_session)
    ready = make_article(db_session, source_id=src.id, dedup_key="k1")
    pending = make_article(
        db_session,
        source_id=src.id,
        dedup_key="k2",
        status=ArticleStatus.pending_processing,
    )
    page = smart_feed("all", db_session, page_size=10, important_threshold=70)
    ids = {a.id for a in page.articles}
    assert ready.id in ids
    assert pending.id not in ids


def test_smart_feed_excludes_hidden(db_session):
    src = make_source(db_session)
    visible = make_article(db_session, source_id=src.id, dedup_key="k1")
    hidden = make_article(db_session, source_id=src.id, dedup_key="k2", is_hidden=True)
    page = smart_feed("all", db_session, page_size=10, important_threshold=70)
    ids = {a.id for a in page.articles}
    assert visible.id in ids
    assert hidden.id not in ids


def test_smart_feed_unread(db_session):
    src = make_source(db_session)
    unread = make_article(db_session, source_id=src.id, dedup_key="k1", is_read=False)
    read = make_article(db_session, source_id=src.id, dedup_key="k2", is_read=True)
    page = smart_feed("unread", db_session, page_size=10, important_threshold=70)
    ids = {a.id for a in page.articles}
    assert unread.id in ids
    assert read.id not in ids


def test_smart_feed_saved(db_session):
    src = make_source(db_session)
    saved = make_article(db_session, source_id=src.id, dedup_key="k1", is_saved=True)
    unsaved = make_article(db_session, source_id=src.id, dedup_key="k2", is_saved=False)
    page = smart_feed("saved", db_session, page_size=10, important_threshold=70)
    ids = {a.id for a in page.articles}
    assert saved.id in ids
    assert unsaved.id not in ids


def test_smart_feed_important(db_session):
    src = make_source(db_session)
    important = make_article(db_session, source_id=src.id, dedup_key="k1", importance_score=80)
    unimportant = make_article(
        db_session, source_id=src.id, dedup_key="k2", importance_score=50
    )
    page = smart_feed("important", db_session, page_size=10, important_threshold=70)
    ids = {a.id for a in page.articles}
    assert important.id in ids
    assert unimportant.id not in ids


def test_smart_feed_uncategorized_null_categories(db_session):
    src = make_source(db_session)
    no_cats = make_article(db_session, source_id=src.id, dedup_key="k1", categories=None)
    categorized = make_article(
        db_session, source_id=src.id, dedup_key="k2", categories=["tech"]
    )
    page = smart_feed("uncategorized", db_session, page_size=10, important_threshold=70)
    ids = {a.id for a in page.articles}
    assert no_cats.id in ids
    assert categorized.id not in ids


def test_smart_feed_uncategorized_empty_categories(db_session):
    src = make_source(db_session)
    empty_cats = make_article(db_session, source_id=src.id, dedup_key="k1", categories=[])
    categorized = make_article(
        db_session, source_id=src.id, dedup_key="k2", categories=["tech"]
    )
    page = smart_feed("uncategorized", db_session, page_size=10, important_threshold=70)
    ids = {a.id for a in page.articles}
    assert empty_cats.id in ids
    assert categorized.id not in ids


def test_smart_feed_unread_only_flag(db_session):
    src = make_source(db_session)
    unread = make_article(db_session, source_id=src.id, dedup_key="k1", is_read=False)
    read = make_article(db_session, source_id=src.id, dedup_key="k2", is_read=True)
    page = smart_feed("all", db_session, page_size=10, important_threshold=70, unread_only=True)
    ids = {a.id for a in page.articles}
    assert unread.id in ids
    assert read.id not in ids


def test_smart_feed_unread_only_does_not_double_filter_unread_view(db_session):
    """unread_only=True on the 'unread' view should not break anything."""
    src = make_source(db_session)
    unread = make_article(db_session, source_id=src.id, dedup_key="k1", is_read=False)
    read = make_article(db_session, source_id=src.id, dedup_key="k2", is_read=True)
    page = smart_feed("unread", db_session, page_size=10, important_threshold=70, unread_only=True)
    ids = {a.id for a in page.articles}
    assert unread.id in ids
    assert read.id not in ids


# ---------------------------------------------------------------------------
# category_feed
# ---------------------------------------------------------------------------


def test_category_feed_containment(db_session):
    src = make_source(db_session)
    tech = make_article(
        db_session, source_id=src.id, dedup_key="k1", categories=["tech", "news"]
    )
    sports = make_article(db_session, source_id=src.id, dedup_key="k2", categories=["sports"])
    both = make_article(
        db_session, source_id=src.id, dedup_key="k3", categories=["tech", "sports"]
    )
    page = category_feed("tech", db_session, page_size=10)
    ids = {a.id for a in page.articles}
    assert tech.id in ids
    assert both.id in ids
    assert sports.id not in ids


def test_category_feed_unread_only(db_session):
    src = make_source(db_session)
    unread = make_article(
        db_session, source_id=src.id, dedup_key="k1", categories=["tech"], is_read=False
    )
    read = make_article(
        db_session, source_id=src.id, dedup_key="k2", categories=["tech"], is_read=True
    )
    page = category_feed("tech", db_session, page_size=10, unread_only=True)
    ids = {a.id for a in page.articles}
    assert unread.id in ids
    assert read.id not in ids


# ---------------------------------------------------------------------------
# source_feed
# ---------------------------------------------------------------------------


def test_source_feed_filters_by_source(db_session):
    src1 = make_source(db_session, name="Source 1", url="http://s1.example.com/feed")
    src2 = make_source(db_session, name="Source 2", url="http://s2.example.com/feed")
    a1 = make_article(db_session, source_id=src1.id, dedup_key="k1")
    a2 = make_article(db_session, source_id=src2.id, dedup_key="k2")
    page = source_feed(src1.id, db_session, page_size=10)
    ids = {a.id for a in page.articles}
    assert a1.id in ids
    assert a2.id not in ids


def test_source_feed_unread_only(db_session):
    src = make_source(db_session)
    unread = make_article(db_session, source_id=src.id, dedup_key="k1", is_read=False)
    read = make_article(db_session, source_id=src.id, dedup_key="k2", is_read=True)
    page = source_feed(src.id, db_session, page_size=10, unread_only=True)
    ids = {a.id for a in page.articles}
    assert unread.id in ids
    assert read.id not in ids


# ---------------------------------------------------------------------------
# Keyset pagination
# ---------------------------------------------------------------------------


def test_keyset_pagination_no_overlap(db_session):
    src = make_source(db_session)
    # Create 5 articles with distinct importance scores for deterministic ordering.
    for i in range(5):
        make_article(
            db_session,
            source_id=src.id,
            dedup_key=f"k{i}",
            importance_score=50 - i,
        )

    page1 = smart_feed("all", db_session, page_size=3, important_threshold=70)
    assert len(page1.articles) == 3
    assert page1.next_cursor is not None

    page2 = smart_feed(
        "all", db_session, page_size=3, important_threshold=70, cursor=page1.next_cursor
    )
    assert len(page2.articles) == 2
    assert page2.next_cursor is None

    page1_ids = {a.id for a in page1.articles}
    page2_ids = {a.id for a in page2.articles}
    assert page1_ids.isdisjoint(page2_ids), "pages overlap"


def test_keyset_pagination_page2_follows_page1(db_session):
    """Every article on page 2 has an importance_score <= the minimum on page 1."""
    src = make_source(db_session)
    for i in range(6):
        make_article(
            db_session,
            source_id=src.id,
            dedup_key=f"k{i}",
            importance_score=100 - i * 10,
        )

    page1 = smart_feed("all", db_session, page_size=3, important_threshold=70)
    page2 = smart_feed(
        "all", db_session, page_size=3, important_threshold=70, cursor=page1.next_cursor
    )

    p1_min = min(a.importance_score for a in page1.articles)
    p2_max = max(a.importance_score for a in page2.articles)
    assert p2_max <= p1_min


def test_no_next_cursor_when_all_fit(db_session):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, dedup_key="k1")
    page = smart_feed("all", db_session, page_size=10, important_threshold=70)
    assert page.next_cursor is None


# ---------------------------------------------------------------------------
# Cursor encode/decode
# ---------------------------------------------------------------------------


def test_cursor_encode_decode_round_trip():
    ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    c = Cursor(importance_score=75, feed_published_at=ts, id=42)
    decoded = Cursor.decode(c.encode())
    assert decoded.importance_score == 75
    assert decoded.feed_published_at == ts
    assert decoded.id == 42


def test_cursor_encode_decode_null_fields():
    c = Cursor(importance_score=None, feed_published_at=None, id=99)
    decoded = Cursor.decode(c.encode())
    assert decoded.importance_score is None
    assert decoded.feed_published_at is None
    assert decoded.id == 99


# ---------------------------------------------------------------------------
# get_sidebar_counts
# ---------------------------------------------------------------------------


def test_get_sidebar_counts_smart_views(db_session):
    src = make_source(db_session)
    # Unread + ready: counts in all smart views where applicable.
    make_article(
        db_session,
        source_id=src.id,
        dedup_key="k1",
        is_read=False,
        importance_score=80,
        categories=["tech"],
    )
    make_article(
        db_session,
        source_id=src.id,
        dedup_key="k2",
        is_read=False,
        is_saved=True,
        categories=None,
    )
    # Read article — should not appear in any count.
    make_article(db_session, source_id=src.id, dedup_key="k3", is_read=True)

    counts = get_sidebar_counts(db_session, important_threshold=70)

    assert counts.smart["all"] == 2
    assert counts.smart["unread"] == 2
    assert counts.smart["saved"] == 1
    assert counts.smart["important"] == 1
    assert counts.smart["uncategorized"] == 1  # k2 has no categories


def test_get_sidebar_counts_sources(db_session):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, dedup_key="k1", is_read=False)
    make_article(db_session, source_id=src.id, dedup_key="k2", is_read=False)
    make_article(db_session, source_id=src.id, dedup_key="k3", is_read=True)

    counts = get_sidebar_counts(db_session, important_threshold=70)
    assert counts.sources[src.id] == 2


def test_get_sidebar_counts_categories(db_session):
    src = make_source(db_session)
    make_category(db_session, name="tech")
    make_article(
        db_session,
        source_id=src.id,
        dedup_key="k1",
        is_read=False,
        categories=["tech"],
    )
    make_article(
        db_session,
        source_id=src.id,
        dedup_key="k2",
        is_read=True,
        categories=["tech"],
    )

    counts = get_sidebar_counts(db_session, important_threshold=70)
    assert counts.categories["tech"] == 1


def test_get_sidebar_counts_disabled_source_excluded(db_session):
    enabled = make_source(db_session, name="Enabled", url="http://e.example.com/feed")
    disabled = make_source(
        db_session, name="Disabled", url="http://d.example.com/feed", enabled=False
    )
    make_article(db_session, source_id=enabled.id, dedup_key="k1", is_read=False)
    make_article(db_session, source_id=disabled.id, dedup_key="k2", is_read=False)

    counts = get_sidebar_counts(db_session, important_threshold=70)
    assert enabled.id in counts.sources
    assert disabled.id not in counts.sources
