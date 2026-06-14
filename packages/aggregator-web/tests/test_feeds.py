"""Tests for aggregator_web.feeds — unit tests using a live Postgres container."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aggregator_common.state import ArticleStatus
from aggregator_web.feeds import (
    Cursor,
    category_feed,
    category_feed_count,
    category_feed_max_id,
    get_sidebar_counts,
    smart_feed,
    smart_feed_count,
    smart_feed_max_id,
    source_feed,
    source_feed_count,
    source_feed_max_id,
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

    assert counts.smart["all"].count == 2
    assert counts.smart["unread"].count == 2
    assert counts.smart["saved"].count == 1
    assert counts.smart["important"].count == 1
    assert counts.smart["uncategorized"].count == 1  # k2 has no categories


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


# ---------------------------------------------------------------------------
# smart_feed_count / category_feed_count / source_feed_count
# ---------------------------------------------------------------------------


def test_smart_feed_count_zero_when_no_articles_newer_than_since(db_session):
    src = make_source(db_session)
    a = make_article(db_session, source_id=src.id)
    count = smart_feed_count("all", db_session, since=a.id, important_threshold=70)
    assert count == 0


def test_smart_feed_count_articles_newer_than_since(db_session):
    src = make_source(db_session)
    older = make_article(db_session, source_id=src.id, dedup_key="k1")
    make_article(db_session, source_id=src.id, dedup_key="k2")
    count = smart_feed_count("all", db_session, since=older.id, important_threshold=70)
    assert count == 1


def test_smart_feed_count_unread_only(db_session):
    src = make_source(db_session)
    anchor = make_article(db_session, source_id=src.id, dedup_key="k0")
    make_article(db_session, source_id=src.id, dedup_key="k1", is_read=False)
    make_article(db_session, source_id=src.id, dedup_key="k2", is_read=True)
    count = smart_feed_count("all", db_session, since=anchor.id, important_threshold=70, unread_only=True)
    assert count == 1


def test_category_feed_count_only_counts_matching_category(db_session):
    src = make_source(db_session)
    anchor = make_article(db_session, source_id=src.id, dedup_key="k0")
    make_article(db_session, source_id=src.id, dedup_key="k1", categories=["tech"])
    make_article(db_session, source_id=src.id, dedup_key="k2", categories=["sports"])
    count = category_feed_count("tech", db_session, since=anchor.id)
    assert count == 1


def test_source_feed_count_only_counts_matching_source(db_session):
    src1 = make_source(db_session, name="S1", url="http://s1.example.com/feed")
    src2 = make_source(db_session, name="S2", url="http://s2.example.com/feed")
    anchor = make_article(db_session, source_id=src1.id, dedup_key="k0")
    make_article(db_session, source_id=src1.id, dedup_key="k1")
    make_article(db_session, source_id=src2.id, dedup_key="k2")
    count = source_feed_count(src1.id, db_session, since=anchor.id)
    assert count == 1


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


# ---------------------------------------------------------------------------
# smart_feed_max_id / category_feed_max_id / source_feed_max_id
# ---------------------------------------------------------------------------


def test_smart_feed_max_id_returns_global_max_not_page_max(db_session):
    """The max-id helper must return the maximum id across the entire view,
    including articles that would appear on later pages due to importance ordering."""
    src = make_source(db_session)
    a_high_imp = make_article(db_session, source_id=src.id, dedup_key="k1", importance_score=90)
    # Lower importance → later page when ordering by importance DESC, but higher id
    a_low_imp = make_article(db_session, source_id=src.id, dedup_key="k2", importance_score=10)
    assert a_low_imp.id > a_high_imp.id

    result = smart_feed_max_id("all", db_session, important_threshold=70)
    assert result == a_low_imp.id


def test_smart_feed_max_id_zero_when_empty(db_session):
    result = smart_feed_max_id("all", db_session, important_threshold=70)
    assert result == 0


def test_smart_feed_max_id_unread_only_excludes_read_articles(db_session):
    src = make_source(db_session)
    unread = make_article(db_session, source_id=src.id, dedup_key="k1", is_read=False)
    read = make_article(db_session, source_id=src.id, dedup_key="k2", is_read=True)
    assert read.id > unread.id

    result = smart_feed_max_id("all", db_session, important_threshold=70, unread_only=True)
    assert result == unread.id


def test_category_feed_max_id_returns_global_category_max(db_session):
    src = make_source(db_session)
    a1 = make_article(
        db_session, source_id=src.id, dedup_key="k1", importance_score=90, categories=["tech"]
    )
    # Higher id, lower importance — would be on page 2 with small page size
    a2 = make_article(
        db_session, source_id=src.id, dedup_key="k2", importance_score=10, categories=["tech"]
    )
    assert a2.id > a1.id

    result = category_feed_max_id("tech", db_session)
    assert result == a2.id


def test_category_feed_max_id_zero_when_empty(db_session):
    result = category_feed_max_id("nonexistent", db_session)
    assert result == 0


def test_source_feed_max_id_returns_global_source_max(db_session):
    src = make_source(db_session)
    a1 = make_article(db_session, source_id=src.id, dedup_key="k1", importance_score=90)
    a2 = make_article(db_session, source_id=src.id, dedup_key="k2", importance_score=10)
    assert a2.id > a1.id

    result = source_feed_max_id(src.id, db_session)
    assert result == a2.id


def test_source_feed_max_id_zero_when_empty(db_session):
    src = make_source(db_session)
    result = source_feed_max_id(src.id, db_session)
    assert result == 0


# ---------------------------------------------------------------------------
# Sort: "newest" order
# ---------------------------------------------------------------------------


def test_source_feed_newest_sort_strict_date_desc(db_session):
    """Newest sort returns articles in strict feed_published_at DESC order."""
    src = make_source(db_session)
    dt_old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    dt_mid = datetime(2025, 6, 1, tzinfo=timezone.utc)
    dt_new = datetime(2025, 12, 1, tzinfo=timezone.utc)

    # Create with varying importance scores and dates to test that sort is by date, not score
    make_article(db_session, source_id=src.id, dedup_key="k1", importance_score=90, feed_published_at=dt_old)
    make_article(db_session, source_id=src.id, dedup_key="k2", importance_score=10, feed_published_at=dt_new)
    make_article(db_session, source_id=src.id, dedup_key="k3", importance_score=50, feed_published_at=dt_mid)

    page = source_feed(src.id, db_session, page_size=10, sort="newest")
    dates = [a.feed_published_at for a in page.articles]
    assert dates == sorted(dates, reverse=True), "Articles must be in descending date order"
    assert dates[0] == dt_new


def test_source_feed_newest_vs_relevance_differ(db_session):
    """Newest and relevance orderings differ when dates and scores diverge."""
    src = make_source(db_session)
    dt_old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    dt_new = datetime(2025, 12, 1, tzinfo=timezone.utc)

    # High score but old date — first in relevance, second in newest
    high_score_old = make_article(db_session, source_id=src.id, dedup_key="k1", importance_score=95, feed_published_at=dt_old)
    # Low score but new date — second in relevance, first in newest
    low_score_new = make_article(db_session, source_id=src.id, dedup_key="k2", importance_score=5, feed_published_at=dt_new)

    page_relevance = source_feed(src.id, db_session, page_size=10, sort="relevance")
    page_newest = source_feed(src.id, db_session, page_size=10, sort="newest")

    assert page_relevance.articles[0].id == high_score_old.id
    assert page_newest.articles[0].id == low_score_new.id


def test_newest_sort_cursor_pagination_no_gaps_no_dupes(db_session):
    """Regression: cursor pagination in 'newest' mode must yield strict date-desc
    order across page boundaries with no duplicate or missing articles.

    This test seeds articles with mixed importance_score and feed_published_at,
    paginates with page_size=2, and asserts:
      - All article IDs are seen exactly once.
      - The resulting date sequence is non-increasing (desc) across pages.
    """
    src = make_source(db_session)
    base = datetime(2025, 6, 1, tzinfo=timezone.utc)

    # 6 articles: dates span 6 days, scores are deliberately inverted so
    # relevance order would produce a completely different sequence.
    articles = []
    for i in range(6):
        a = make_article(
            db_session,
            source_id=src.id,
            dedup_key=f"k{i}",
            importance_score=i * 10,          # 0,10,20,30,40,50
            feed_published_at=datetime(2025, 6, 6 - i, tzinfo=timezone.utc),  # desc: day 6→1
        )
        articles.append(a)

    all_ids = []
    all_dates = []
    cursor = None
    while True:
        page = source_feed(src.id, db_session, page_size=2, cursor=cursor, sort="newest")
        for a in page.articles:
            all_ids.append(a.id)
            all_dates.append(a.feed_published_at)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert len(all_ids) == 6, f"Expected 6 articles, got {len(all_ids)}"
    assert len(set(all_ids)) == 6, "Duplicate articles across pages"
    # Dates must be non-increasing (DESC order)
    for i in range(len(all_dates) - 1):
        assert all_dates[i] >= all_dates[i + 1], (
            f"Date order broken at index {i}: {all_dates[i]} < {all_dates[i + 1]}"
        )


def test_newest_sort_cursor_encodes_sort_mode(db_session):
    """Cursor generated by newest sort must encode sort='newest' for correct continuation."""
    src = make_source(db_session)
    for i in range(3):
        make_article(
            db_session,
            source_id=src.id,
            dedup_key=f"k{i}",
            feed_published_at=datetime(2025, 6, i + 1, tzinfo=timezone.utc),
        )

    page = source_feed(src.id, db_session, page_size=2, sort="newest")
    assert page.next_cursor is not None

    decoded = Cursor.decode(page.next_cursor)
    assert decoded.sort == "newest"


def test_relevance_sort_cursor_encodes_relevance(db_session):
    """Cursor generated by relevance sort must encode sort='relevance'."""
    src = make_source(db_session)
    for i in range(3):
        make_article(db_session, source_id=src.id, dedup_key=f"k{i}", importance_score=50 - i)

    page = source_feed(src.id, db_session, page_size=2, sort="relevance")
    assert page.next_cursor is not None

    decoded = Cursor.decode(page.next_cursor)
    assert decoded.sort == "relevance"


def test_cursor_backward_compat_defaults_to_relevance():
    """Old cursors without 'o' key must decode to sort='relevance'."""
    import base64
    import json

    # Simulate an old-style cursor token (no "o" key)
    old_payload = {"s": 75, "p": "2025-06-01T12:00:00+00:00", "i": 42}
    token = base64.urlsafe_b64encode(json.dumps(old_payload).encode()).decode()

    decoded = Cursor.decode(token)
    assert decoded.sort == "relevance"
    assert decoded.importance_score == 75
    assert decoded.id == 42


def test_newest_sort_null_dates_come_last(db_session):
    """Articles with NULL feed_published_at sort after dated ones in newest mode."""
    src = make_source(db_session)
    dt = datetime(2025, 6, 1, tzinfo=timezone.utc)

    with_date = make_article(db_session, source_id=src.id, dedup_key="k1", feed_published_at=dt)
    no_date = make_article(db_session, source_id=src.id, dedup_key="k2", feed_published_at=None)

    page = source_feed(src.id, db_session, page_size=10, sort="newest")
    ids = [a.id for a in page.articles]
    assert ids.index(with_date.id) < ids.index(no_date.id), (
        "Article with date must appear before NULL-date article in newest sort"
    )
