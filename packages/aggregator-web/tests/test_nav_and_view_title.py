"""Tests for nav-store seeding, view_title per route, and heading presence vs fragment.

Covers:
  - Shell route injects initial_nav_key='smart/all' into the Alpine store init script.
  - Each smart-view route passes the correct view_title from VIEW_LABELS.
  - Category route passes view_title equal to the loaded category name.
  - Source route passes view_title equal to the loaded source name.
  - A full _article_list.html render (non-fragment) contains the feed-view-title heading.
  - A fragment request (HX-Request + cursor) omits the heading entirely.
  - HTML-special characters in category/source names are escaped in the heading.
"""
from __future__ import annotations

from aggregator_web.app import VIEW_LABELS
from aggregator_web.feeds import smart_feed
from conftest import make_article, make_category, make_source


# ---------------------------------------------------------------------------
# Shell route — initial_nav_key seeded into Alpine nav store
# ---------------------------------------------------------------------------


def test_shell_initial_nav_key_is_smart_all(client):
    """GET / embeds initial_nav_key='smart/all' in the Alpine store init script.

    shell.html renders:
      window.Alpine.store('nav', { current: '{{ initial_nav_key | default("smart/all") }}' });
    This test verifies the server-injected value is 'smart/all'.
    """
    response = client.get("/")
    assert response.status_code == 200
    assert "current: 'smart/all'" in response.text


# ---------------------------------------------------------------------------
# Smart-view routes — view_title matches VIEW_LABELS
# ---------------------------------------------------------------------------


def test_smart_all_view_title(db_session, client):
    """GET /feed/smart/all renders a heading with VIEW_LABELS['smart/all']."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    response = client.get("/feed/smart/all")
    assert response.status_code == 200
    assert "feed-view-title" in response.text
    assert VIEW_LABELS["smart/all"] in response.text


def test_smart_unread_view_title(db_session, client):
    """GET /feed/smart/unread renders a heading with VIEW_LABELS['smart/unread']."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, is_read=False)
    response = client.get("/feed/smart/unread")
    assert response.status_code == 200
    assert "feed-view-title" in response.text
    assert VIEW_LABELS["smart/unread"] in response.text


def test_smart_saved_view_title(db_session, client):
    """GET /feed/smart/saved renders a heading with VIEW_LABELS['smart/saved']."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, is_saved=True)
    response = client.get("/feed/smart/saved")
    assert response.status_code == 200
    assert "feed-view-title" in response.text
    assert VIEW_LABELS["smart/saved"] in response.text


def test_smart_important_view_title(db_session, client):
    """GET /feed/smart/important renders a heading with VIEW_LABELS['smart/important']."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, importance_score=90)
    response = client.get("/feed/smart/important")
    assert response.status_code == 200
    assert "feed-view-title" in response.text
    assert VIEW_LABELS["smart/important"] in response.text


def test_smart_uncategorized_view_title(db_session, client):
    """GET /feed/smart/uncategorized renders a heading with VIEW_LABELS['smart/uncategorized']."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, categories=None)
    response = client.get("/feed/smart/uncategorized")
    assert response.status_code == 200
    assert "feed-view-title" in response.text
    assert VIEW_LABELS["smart/uncategorized"] in response.text


# ---------------------------------------------------------------------------
# Category route — view_title = loaded category name
# ---------------------------------------------------------------------------


def test_category_view_title_equals_category_name(db_session, client):
    """GET /feed/category/{name} renders the category's DB name as the heading."""
    src = make_source(db_session)
    make_category(db_session, name="technology")
    make_article(db_session, source_id=src.id, categories=["technology"])
    response = client.get("/feed/category/technology")
    assert response.status_code == 200
    assert "feed-view-title" in response.text
    assert "technology" in response.text


def test_category_view_title_falls_back_to_url_name(db_session, client):
    """When the category row is absent view_title falls back to the URL path segment."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, categories=["orphan"])
    response = client.get("/feed/category/orphan")
    assert response.status_code == 200
    assert "feed-view-title" in response.text
    assert "orphan" in response.text


def test_category_view_title_html_chars_escaped(db_session, client):
    """HTML-special characters in a category name must be escaped in the heading."""
    src = make_source(db_session)
    make_category(db_session, name="tech & science")
    make_article(db_session, source_id=src.id, categories=["tech & science"])
    response = client.get("/feed/category/tech%20%26%20science")
    assert response.status_code == 200
    assert "feed-view-title" in response.text
    assert "&amp;" in response.text
    assert "tech & science".replace("&", "&amp;") in response.text


# ---------------------------------------------------------------------------
# Source route — view_title = source name
# ---------------------------------------------------------------------------


def test_source_view_title_equals_source_name(db_session, client):
    """GET /feed/source/{id} renders the source's name as the heading."""
    src = make_source(db_session, name="My RSS Feed")
    make_article(db_session, source_id=src.id)
    response = client.get(f"/feed/source/{src.id}")
    assert response.status_code == 200
    assert "feed-view-title" in response.text
    assert "My RSS Feed" in response.text


# ---------------------------------------------------------------------------
# Full render: heading present; Fragment render (HX-Request + cursor): heading absent
# ---------------------------------------------------------------------------


def test_full_render_contains_view_title_heading(db_session, client):
    """A full (non-fragment) feed response renders <h2 class="feed-view-title">."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    response = client.get("/feed/smart/all")
    assert response.status_code == 200
    assert '<h2 class="feed-view-title">' in response.text
    assert VIEW_LABELS["smart/all"] in response.text


def test_fragment_render_omits_view_title_heading(db_session, client, monkeypatch):
    """Infinite-scroll fragment (HX-Request + cursor) must not contain the heading.

    _render_feed short-circuits to raw card HTML when both hx_request and cursor
    are set, bypassing the _article_list.html template and therefore the heading.
    """
    import aggregator_web.app as app_mod
    monkeypatch.setattr(app_mod.settings, "web_page_size", 2)

    src = make_source(db_session)
    for i in range(4):
        make_article(db_session, source_id=src.id, dedup_key=f"k{i}", importance_score=50 - i)

    page1 = smart_feed("all", db_session, page_size=2, important_threshold=70)
    assert page1.next_cursor is not None, "Expected a next_cursor from page 1 (need 4 articles)"

    response = client.get(
        f"/feed/smart/all?cursor={page1.next_cursor}",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "feed-view-title" not in response.text
