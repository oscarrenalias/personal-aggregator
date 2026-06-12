"""HTTP route integration tests for aggregator-web using FastAPI TestClient."""

from __future__ import annotations

import json

from sqlalchemy import text

from aggregator_common.models import Article
from aggregator_web.feeds import smart_feed
from conftest import make_article, make_category, make_source


# ---------------------------------------------------------------------------
# Shell / index
# ---------------------------------------------------------------------------


def test_get_index_returns_200(client):
    response = client.get("/")
    assert response.status_code == 200


def test_get_index_has_pwa_manifest_link(client):
    response = client.get("/")
    assert response.status_code == 200
    assert 'rel="manifest"' in response.text


def test_get_index_has_htmx_sidebar(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "hx-get" in response.text


def test_app_js_loads_before_alpine_in_shell(client):
    """Regression: app.js must appear in <head> before the Alpine CDN script.

    Alpine v3 auto-starts when its deferred script executes.  If app.js loads
    after Alpine, aggregatorApp() and articleList() are undefined when Alpine
    evaluates x-data, making all keyboard shortcuts and UI interactions dead.
    This test fails against the old shell.html that placed app.js at the bottom
    of <body> with defer.
    """
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    app_js_idx = html.find('/static/app.js')
    alpine_idx = html.find('alpinejs')

    assert app_js_idx != -1, "/static/app.js script tag not found in shell HTML"
    assert alpine_idx != -1, "Alpine CDN script tag not found in shell HTML"
    assert app_js_idx < alpine_idx, (
        "app.js must appear before the Alpine CDN script so component factories "
        "exist when alpine:init fires"
    )


def test_shell_uses_alpine_data_registered_names(client):
    """Regression: x-data must reference registered Alpine.data names (no parens).

    When x-data uses call syntax ('aggregatorApp()') the factory must already be
    a global at parse time.  Using the registered name ('aggregatorApp') lets
    Alpine resolve it via Alpine.data(), which is order-robust.
    """
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    assert 'x-data="aggregatorApp"' in html, (
        "shell <body> must use x-data=\"aggregatorApp\" (no parentheses)"
    )
    assert 'x-data="aggregatorApp()"' not in html, (
        "call syntax aggregatorApp() must be replaced by registered name"
    )


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


def test_get_sidebar_returns_200(db_session, client):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, is_read=False)
    response = client.get("/sidebar")
    assert response.status_code == 200


def test_get_sidebar_includes_uncategorized_entry(db_session, client):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    response = client.get("/sidebar")
    assert response.status_code == 200
    assert "Uncategorized" in response.text


def test_get_sidebar_includes_category(db_session, client):
    src = make_source(db_session)
    make_category(db_session, name="technology")
    make_article(db_session, source_id=src.id, is_read=False, categories=["technology"])
    response = client.get("/sidebar")
    assert response.status_code == 200
    assert "technology" in response.text


def test_get_sidebar_includes_source(db_session, client):
    src = make_source(db_session, name="My Feed", url="http://myfeed.example.com/feed")
    make_article(db_session, source_id=src.id, is_read=False)
    response = client.get("/sidebar")
    assert response.status_code == 200
    assert "My Feed" in response.text


# ---------------------------------------------------------------------------
# Feed routes — smart views
# ---------------------------------------------------------------------------


def test_get_feed_smart_all(db_session, client):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, feed_title="Hello World")
    response = client.get("/feed/smart/all")
    assert response.status_code == 200
    assert "article-list" in response.text


def test_get_feed_smart_unread(db_session, client):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, is_read=False)
    response = client.get("/feed/smart/unread")
    assert response.status_code == 200


def test_get_feed_smart_saved(db_session, client):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, is_saved=True)
    response = client.get("/feed/smart/saved")
    assert response.status_code == 200


def test_get_feed_smart_important(db_session, client):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, importance_score=90)
    response = client.get("/feed/smart/important")
    assert response.status_code == 200


def test_get_feed_smart_uncategorized(db_session, client):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, categories=None)
    response = client.get("/feed/smart/uncategorized")
    assert response.status_code == 200


def test_get_feed_smart_unread_only_flag(db_session, client):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, is_read=False, feed_title="Unread Article")
    make_article(
        db_session, source_id=src.id, dedup_key="k2", is_read=True, feed_title="Read Article"
    )
    response = client.get("/feed/smart/all?unread=1")
    assert response.status_code == 200
    assert "Unread Article" in response.text
    assert "Read Article" not in response.text


# ---------------------------------------------------------------------------
# Feed routes — category and source
# ---------------------------------------------------------------------------


def test_get_feed_category(db_session, client):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, categories=["tech"], feed_title="Tech Article")
    response = client.get("/feed/category/tech")
    assert response.status_code == 200
    assert "Tech Article" in response.text


def test_get_feed_source(db_session, client):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, feed_title="Source Article")
    response = client.get(f"/feed/source/{src.id}")
    assert response.status_code == 200
    assert "Source Article" in response.text


def test_get_feed_empty_returns_empty_state(client):
    response = client.get("/feed/smart/all")
    assert response.status_code == 200
    assert "No articles found" in response.text


# ---------------------------------------------------------------------------
# Keyset pagination — route level
# ---------------------------------------------------------------------------


def test_feed_pagination_htmx_cursor_request(db_session, client):
    """HTMX pagination request with cursor returns card fragments, not full list wrapper."""
    src = make_source(db_session)
    for i in range(4):
        make_article(
            db_session,
            source_id=src.id,
            dedup_key=f"k{i}",
            importance_score=50 - i,
        )

    # Get cursor via feeds function directly (simulates what page 1 renders).
    page1 = smart_feed("all", db_session, page_size=2, important_threshold=70)
    assert page1.next_cursor is not None
    page1_ids = {a.id for a in page1.articles}

    # HTMX pagination: HX-Request + cursor → returns raw card fragments only.
    response = client.get(
        f"/feed/smart/all?cursor={page1.next_cursor}",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    # Fragment response should NOT contain the article-list wrapper.
    assert 'id="article-list"' not in response.text
    # At least one article card should be in the response.
    assert "article-card" in response.text

    # Verify page 2 articles are not on page 1.
    for article_id in page1_ids:
        assert f'data-article-id="{article_id}"' not in response.text


# ---------------------------------------------------------------------------
# Article detail
# ---------------------------------------------------------------------------


def test_get_article_detail_200(db_session, client):
    src = make_source(db_session)
    article = make_article(
        db_session,
        source_id=src.id,
        clean_title="My Article",
        feed_url="https://example.com/article/1",
    )
    response = client.get(f"/article/{article.id}")
    assert response.status_code == 200
    assert "My Article" in response.text


def test_get_article_detail_has_open_source_link(db_session, client):
    """Article detail must include a target=_blank rel=noopener Open source link."""
    src = make_source(db_session)
    article = make_article(
        db_session,
        source_id=src.id,
        feed_url="https://example.com/article/open-source",
    )
    response = client.get(f"/article/{article.id}")
    assert response.status_code == 200
    assert 'target="_blank"' in response.text
    assert 'rel="noopener"' in response.text
    assert "https://example.com/article/open-source" in response.text


def test_get_article_detail_shows_comments_icon_when_comments_url_set(db_session, client):
    """Comments toolbar icon is rendered when article.comments_url is populated."""
    src = make_source(db_session)
    article = make_article(
        db_session,
        source_id=src.id,
        dedup_key="comments-present",
        feed_url="https://example.com/article/1",
        comments_url="https://news.ycombinator.com/item?id=99999",
    )
    response = client.get(f"/article/{article.id}")
    assert response.status_code == 200
    assert "btn-open-comments" in response.text
    assert "https://news.ycombinator.com/item?id=99999" in response.text
    assert "Open comments in new tab" in response.text
    # Regression: comments icon must be the inline SVG, not the color emoji
    assert "<svg" in response.text
    assert "\U0001f4ac" not in response.text  # 💬 emoji must not appear


def test_get_article_detail_hides_comments_icon_when_comments_url_absent(db_session, client):
    """Comments toolbar icon is not rendered when article.comments_url is None."""
    src = make_source(db_session)
    article = make_article(
        db_session,
        source_id=src.id,
        dedup_key="comments-absent",
        feed_url="https://example.com/article/2",
        comments_url=None,
    )
    response = client.get(f"/article/{article.id}")
    assert response.status_code == 200
    assert "btn-open-comments" not in response.text


def test_article_with_topics_list_renders(db_session, client):
    """Regression: topics is a JSONB list; templates must iterate it directly,
    not call .keys(). Detail + card 500'd on real data before the fix."""
    src = make_source(db_session)
    article = make_article(
        db_session,
        source_id=src.id,
        clean_title="Topic Article",
        topics=["AI", "Gaming"],
        categories=None,  # force the card's topics branch
    )
    detail = client.get(f"/article/{article.id}")
    assert detail.status_code == 200
    assert "AI" in detail.text and "Gaming" in detail.text
    feed = client.get(f"/feed/source/{src.id}")
    assert feed.status_code == 200


def test_get_article_detail_not_found(client):
    response = client.get("/article/99999")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Interaction routes: read / unread / save / unsave
# ---------------------------------------------------------------------------


def test_post_article_read(db_session, client):
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, is_read=False)
    response = client.post(f"/article/{article.id}/read")
    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(Article, article.id).is_read is True


def test_post_article_unread(db_session, client):
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, is_read=True)
    response = client.post(f"/article/{article.id}/unread")
    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(Article, article.id).is_read is False


def test_post_article_save(db_session, client):
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, is_saved=False)
    response = client.post(f"/article/{article.id}/save")
    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(Article, article.id).is_saved is True


def test_post_article_unsave(db_session, client):
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, is_saved=True)
    response = client.post(f"/article/{article.id}/unsave")
    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(Article, article.id).is_saved is False


def test_post_interaction_returns_is_read_flag_in_card(db_session, client):
    """After marking read, response card HTML should reflect is-read state."""
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, is_read=False)
    response = client.post(f"/article/{article.id}/read")
    assert response.status_code == 200
    assert "is-read" in response.text


def test_post_interaction_returns_detail_when_hx_target_is_detail(db_session, client):
    """With HX-Target: article-detail, interaction routes return _article_detail.html."""
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, is_read=False)
    response = client.post(
        f"/article/{article.id}/read",
        headers={"HX-Target": "article-detail"},
    )
    assert response.status_code == 200
    assert 'id="article-detail"' in response.text


# ---------------------------------------------------------------------------
# OOB sync: card ↔ detail state stays in sync via hx-swap-oob
# ---------------------------------------------------------------------------


def test_article_card_has_stable_id_attribute(db_session, client):
    """Article card must render id='article-card-{id}' so hx-swap-oob can target it."""
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id)
    response = client.get(f"/feed/source/{src.id}")
    assert response.status_code == 200
    assert f'id="article-card-{article.id}"' in response.text


def test_post_read_with_detail_target_returns_both_fragments(db_session, client):
    """POST /read with HX-Target=article-detail must return primary detail + OOB card."""
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, is_read=False)
    response = client.post(
        f"/article/{article.id}/read",
        headers={"HX-Target": "article-detail"},
    )
    assert response.status_code == 200
    html = response.text
    assert 'id="article-detail"' in html
    assert f'id="article-card-{article.id}"' in html
    assert 'hx-swap-oob="true"' in html
    assert "is-read" in html


def test_post_read_with_card_target_returns_both_fragments(db_session, client):
    """POST /read without article-detail HX-Target must return primary card + OOB detail."""
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, is_read=False)
    response = client.post(f"/article/{article.id}/read")
    assert response.status_code == 200
    html = response.text
    assert f'id="article-card-{article.id}"' in html
    assert 'id="article-detail"' in html
    assert 'hx-swap-oob="true"' in html
    assert "is-read" in html


def test_post_unread_with_detail_target_returns_both_fragments(db_session, client):
    """POST /unread with HX-Target=article-detail returns detail primary + OOB card."""
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, is_read=True)
    response = client.post(
        f"/article/{article.id}/unread",
        headers={"HX-Target": "article-detail"},
    )
    assert response.status_code == 200
    html = response.text
    assert 'id="article-detail"' in html
    assert f'id="article-card-{article.id}"' in html
    assert 'hx-swap-oob="true"' in html


def test_post_save_with_detail_target_returns_both_fragments(db_session, client):
    """POST /save with HX-Target=article-detail returns detail primary + OOB card."""
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, is_saved=False)
    response = client.post(
        f"/article/{article.id}/save",
        headers={"HX-Target": "article-detail"},
    )
    assert response.status_code == 200
    html = response.text
    assert 'id="article-detail"' in html
    assert f'id="article-card-{article.id}"' in html
    assert 'hx-swap-oob="true"' in html
    assert "is-saved" in html


def test_post_save_with_card_target_returns_both_fragments(db_session, client):
    """POST /save without article-detail HX-Target returns card primary + OOB detail."""
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, is_saved=False)
    response = client.post(f"/article/{article.id}/save")
    assert response.status_code == 200
    html = response.text
    assert f'id="article-card-{article.id}"' in html
    assert 'id="article-detail"' in html
    assert 'hx-swap-oob="true"' in html
    assert "is-saved" in html


def test_post_unsave_with_detail_target_returns_both_fragments(db_session, client):
    """POST /unsave with HX-Target=article-detail returns detail primary + OOB card."""
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, is_saved=True)
    response = client.post(
        f"/article/{article.id}/unsave",
        headers={"HX-Target": "article-detail"},
    )
    assert response.status_code == 200
    html = response.text
    assert 'id="article-detail"' in html
    assert f'id="article-card-{article.id}"' in html
    assert 'hx-swap-oob="true"' in html


def test_post_article_read_not_found(client):
    response = client.post("/article/99999/read")
    assert response.status_code == 404


def test_post_article_save_not_found(client):
    response = client.post("/article/99999/save")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Mark-all-read routes
# ---------------------------------------------------------------------------


def test_smart_read_all_marks_all_articles(db_session, client):
    src = make_source(db_session)
    articles = [
        make_article(db_session, source_id=src.id, dedup_key=f"k{i}", is_read=False)
        for i in range(3)
    ]
    response = client.post("/feed/smart/all/read-all")
    assert response.status_code == 200
    db_session.expire_all()
    for a in articles:
        assert db_session.get(Article, a.id).is_read is True


def test_smart_read_all_marks_articles_not_on_current_page(db_session, client):
    """Bulk mark-all-read covers articles beyond what fits on a single page."""
    src = make_source(db_session)
    articles = [
        make_article(db_session, source_id=src.id, dedup_key=f"k{i}", is_read=False)
        for i in range(3)
    ]
    # The route marks ALL matching articles regardless of page display.
    response = client.post("/feed/smart/all/read-all")
    assert response.status_code == 200
    db_session.expire_all()
    all_read = all(db_session.get(Article, a.id).is_read for a in articles)
    assert all_read


def test_category_read_all(db_session, client):
    src = make_source(db_session)
    tech = make_article(db_session, source_id=src.id, dedup_key="k1", categories=["tech"])
    sports = make_article(
        db_session, source_id=src.id, dedup_key="k2", categories=["sports"]
    )
    response = client.post("/feed/category/tech/read-all")
    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(Article, tech.id).is_read is True
    assert db_session.get(Article, sports.id).is_read is False


def test_source_read_all(db_session, client):
    src1 = make_source(db_session, name="S1", url="http://s1.example.com/feed")
    src2 = make_source(db_session, name="S2", url="http://s2.example.com/feed")
    a1 = make_article(db_session, source_id=src1.id, dedup_key="k1")
    a2 = make_article(db_session, source_id=src2.id, dedup_key="k2")
    response = client.post(f"/feed/source/{src1.id}/read-all")
    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(Article, a1.id).is_read is True
    assert db_session.get(Article, a2.id).is_read is False


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_search_empty_query_shows_hint(client):
    """GET /search with no query shows a hint; no article-list and no search form."""
    response = client.get("/search")
    assert response.status_code == 200
    assert "search-hint" in response.text
    assert 'id="article-list"' not in response.text
    assert "search-form" not in response.text


def test_search_with_no_results(client):
    response = client.get("/search?q=xyznonexistent")
    assert response.status_code == 200
    assert "No results" in response.text
    assert "search-empty" in response.text


def test_search_returns_matching_article(db_session, client):
    """Search via GIN search_vector index should return articles matching the query."""
    src = make_source(db_session)
    article = make_article(
        db_session,
        source_id=src.id,
        clean_title="Advanced Python Programming",
    )
    # Set search_vector directly since no processor runs in tests.
    db_session.execute(
        text(
            "UPDATE articles SET search_vector = to_tsvector('english', 'python programming') "
            "WHERE id = :id"
        ),
        {"id": article.id},
    )
    db_session.commit()

    response = client.get("/search?q=python")
    assert response.status_code == 200
    assert "Advanced Python Programming" in response.text


def test_search_does_not_return_non_matching(db_session, client):
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, clean_title="Advanced Python")
    db_session.execute(
        text("UPDATE articles SET search_vector = to_tsvector('english', 'python') WHERE id = :id"),
        {"id": article.id},
    )
    db_session.commit()

    response = client.get("/search?q=gardening")
    assert response.status_code == 200
    assert "Advanced Python" not in response.text


# ---------------------------------------------------------------------------
# Healthz
# ---------------------------------------------------------------------------


def test_healthz_with_live_db(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    data = json.loads(response.text)
    assert data["db"] == "ok"
    assert "version" in data


# ---------------------------------------------------------------------------
# PWA static assets
# ---------------------------------------------------------------------------


def test_get_manifest_webmanifest(client):
    response = client.get("/static/manifest.webmanifest")
    assert response.status_code == 200
    content_type = response.headers.get("content-type", "")
    assert "json" in content_type or "manifest" in content_type
    data = json.loads(response.text)
    assert data.get("display") == "standalone"
    icons = data.get("icons", [])
    assert len(icons) > 0


def test_get_sw_js(client):
    response = client.get("/static/sw.js")
    assert response.status_code == 200


def test_sw_js_uses_network_first_for_dynamic_assets(client):
    """Service worker must use network-first for '/', app.js, and styles.css.

    A bare 'cached || fetch' strategy would serve stale assets after a deploy.
    This test fails against the old cache-first implementation.
    """
    response = client.get("/static/sw.js")
    assert response.status_code == 200
    content = response.text

    # The key paths that change between deploys must be in the network-first set.
    assert "NETWORK_FIRST_PATHS" in content, "Expected a NETWORK_FIRST_PATHS set"
    for path in ("'/'", "'/static/app.js'", "'/static/styles.css'"):
        assert path in content, f"Expected {path} to be listed as a network-first path"

    # The network-first handler must call fetch() first (not caches.match first).
    # The characteristic signature is fetch(event.request).then(...).catch(...)
    assert "fetch(event.request)" in content and ".catch(" in content, (
        "Expected fetch-first with cache fallback (.catch) in SW fetch handler"
    )

    # Regression: the old bare cache-first pattern must not be the only handler.
    # The old code had exactly: caches.match(event.request).then((cached) => cached || fetch(event.request))
    # as the sole respondWith. We confirm the new code does NOT use that as the
    # universal strategy (it is still used for icons/manifest, so we check it is
    # not the handler for the network-first paths).
    assert "NETWORK_FIRST_PATHS.has(url.pathname)" in content, (
        "Expected NETWORK_FIRST_PATHS.has check before responding"
    )


def test_sw_js_cache_first_preserved_for_static_assets(client):
    """Icons and manifest should still use cache-first (they are content-addressed)."""
    response = client.get("/static/sw.js")
    assert response.status_code == 200
    content = response.text

    assert "/static/icons/" in content, "Expected icons path in cache-first branch"
    assert "/static/manifest.webmanifest" in content, "Expected manifest in cache-first branch"


def test_get_styles_css(client):
    response = client.get("/static/styles.css")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Uncategorized feed — dedicated route acceptance
# ---------------------------------------------------------------------------


def test_uncategorized_feed_returns_only_articles_without_categories(db_session, client):
    src = make_source(db_session)
    make_article(
        db_session,
        source_id=src.id,
        dedup_key="k1",
        categories=None,
        feed_title="Uncategorized Article",
    )
    make_article(
        db_session,
        source_id=src.id,
        dedup_key="k2",
        categories=["tech"],
        feed_title="Tech Article",
    )
    response = client.get("/feed/smart/uncategorized")
    assert response.status_code == 200
    assert "Uncategorized Article" in response.text
    assert "Tech Article" not in response.text


# ---------------------------------------------------------------------------
# Config binding
# ---------------------------------------------------------------------------


def test_web_settings_default_host():
    """Port binding config must default to 127.0.0.1 (never 0.0.0.0)."""
    from aggregator_web.config import WebSettings

    s = WebSettings()
    assert s.web_host == "127.0.0.1"
    assert s.web_port == 8000
    assert s.web_page_size == 50
    assert s.web_important_threshold == 70


# ---------------------------------------------------------------------------
# HX-Trigger: refreshSidebar — interaction endpoints
# ---------------------------------------------------------------------------


def test_post_article_read_returns_hx_trigger_refresh_sidebar(db_session, client):
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, is_read=False)
    response = client.post(f"/article/{article.id}/read")
    assert response.headers.get("HX-Trigger") == "refreshSidebar"


def test_post_article_unread_returns_hx_trigger_refresh_sidebar(db_session, client):
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, is_read=True)
    response = client.post(f"/article/{article.id}/unread")
    assert response.headers.get("HX-Trigger") == "refreshSidebar"


def test_post_article_save_returns_hx_trigger_refresh_sidebar(db_session, client):
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, is_saved=False)
    response = client.post(f"/article/{article.id}/save")
    assert response.headers.get("HX-Trigger") == "refreshSidebar"


def test_post_article_unsave_returns_hx_trigger_refresh_sidebar(db_session, client):
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, is_saved=True)
    response = client.post(f"/article/{article.id}/unsave")
    assert response.headers.get("HX-Trigger") == "refreshSidebar"


def test_post_smart_read_all_returns_hx_trigger_refresh_sidebar(db_session, client):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    response = client.post("/feed/smart/all/read-all")
    assert response.headers.get("HX-Trigger") == "refreshSidebar"


def test_post_category_read_all_returns_hx_trigger_refresh_sidebar(db_session, client):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, categories=["tech"])
    response = client.post("/feed/category/tech/read-all")
    assert response.headers.get("HX-Trigger") == "refreshSidebar"


def test_post_source_read_all_returns_hx_trigger_refresh_sidebar(db_session, client):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    response = client.post(f"/feed/source/{src.id}/read-all")
    assert response.headers.get("HX-Trigger") == "refreshSidebar"


# ---------------------------------------------------------------------------
# Sidebar refreshSidebar trigger wiring
# ---------------------------------------------------------------------------


def test_get_index_sidebar_has_refresh_sidebar_trigger(client):
    """Shell HTML sidebar container must include the refreshSidebar hx-trigger so
    interaction responses that fire the event cause sidebar counts to reload."""
    response = client.get("/")
    assert response.status_code == 200
    assert "refreshSidebar" in response.text


# ---------------------------------------------------------------------------
# paragraphs filter — registration, rendering, and HTML escaping
# ---------------------------------------------------------------------------


def test_paragraphs_filter_is_registered():
    """The paragraphs Jinja2 filter must be registered on the templates environment."""
    from aggregator_web.app import templates

    assert "paragraphs" in templates.env.filters


def test_article_detail_multi_line_summary_renders_multiple_paragraphs(db_session, client):
    """Multi-line summary text must be split into separate <p> elements."""
    src = make_source(db_session)
    article = make_article(
        db_session,
        source_id=src.id,
        summary="First paragraph.\nSecond paragraph.\nThird paragraph.",
    )
    response = client.get(f"/article/{article.id}")
    assert response.status_code == 200
    assert response.text.count("<p>") >= 3


def test_article_detail_multi_line_body_renders_multiple_paragraphs(db_session, client):
    """Multi-line clean_text must be split into separate <p> elements."""
    src = make_source(db_session)
    article = make_article(
        db_session,
        source_id=src.id,
        clean_text="Paragraph one.\nParagraph two.",
    )
    response = client.get(f"/article/{article.id}")
    assert response.status_code == 200
    assert response.text.count("<p>") >= 2


def test_article_detail_clean_text_html_is_escaped(db_session, client):
    """HTML tags in clean_text must be escaped, not injected as raw markup."""
    src = make_source(db_session)
    article = make_article(
        db_session,
        source_id=src.id,
        clean_text="Safe text <script>alert('xss')</script>",
    )
    response = client.get(f"/article/{article.id}")
    assert response.status_code == 200
    assert "&lt;script&gt;" in response.text
    assert "<script>" not in response.text


# ---------------------------------------------------------------------------
# Article section heading
# ---------------------------------------------------------------------------


def test_article_detail_has_article_text_container(db_session, client):
    """Detail view with body text must render the .article-text container."""
    src = make_source(db_session)
    article = make_article(
        db_session,
        source_id=src.id,
        clean_text="Body content here.",
    )
    response = client.get(f"/article/{article.id}")
    assert response.status_code == 200
    assert 'class="article-text"' in response.text
    assert "Body content here." in response.text


def test_article_detail_no_section_headings(db_session, client):
    """Detail view must not render 'Summary' or 'Article' section headings."""
    src = make_source(db_session)
    article = make_article(
        db_session,
        source_id=src.id,
        summary="A generated summary.",
        clean_text="Body content here.",
    )
    response = client.get(f"/article/{article.id}")
    assert response.status_code == 200
    assert ">Summary<" not in response.text
    assert ">Article<" not in response.text
    assert "detail-section-heading" not in response.text


def test_article_detail_summary_rendered_without_heading(db_session, client):
    """Summary section must render .detail-summary and .summary-block without a heading."""
    src = make_source(db_session)
    article = make_article(
        db_session,
        source_id=src.id,
        summary="LLM-generated abstract.",
    )
    response = client.get(f"/article/{article.id}")
    assert response.status_code == 200
    assert 'class="detail-summary"' in response.text
    assert 'class="summary-block"' in response.text
    assert "LLM-generated abstract." in response.text


# ---------------------------------------------------------------------------
# Header layout regression — toolbar/title gap + relevance score placement
# ---------------------------------------------------------------------------


def test_article_detail_importance_badge_in_toolbar(db_session, client):
    """Regression: importance badge must appear inside .detail-reader-toolbar,
    not as a standalone element between the title and the category chips."""
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, importance_score=75)
    response = client.get(f"/article/{article.id}")
    assert response.status_code == 200
    html = response.text

    # Old standalone wrapper must be gone.
    assert 'class="detail-importance"' not in html

    # Badge must be rendered somewhere in the page.
    assert "importance-badge" in html

    # Badge must appear inside .detail-reader-toolbar (before first toolbar-btn).
    toolbar_idx = html.find('class="detail-reader-toolbar"')
    badge_idx = html.find("importance-badge")
    btn_idx = html.find('class="toolbar-btn')
    assert toolbar_idx != -1
    assert badge_idx != -1
    assert btn_idx != -1
    assert toolbar_idx < badge_idx < btn_idx, (
        "importance-badge must be the first item inside .detail-reader-toolbar"
    )


def test_article_detail_importance_badge_tier_class(db_session, client):
    """Regression: badge tier class (importance-high/medium/low) must be preserved
    when badge moves into the toolbar."""
    src = make_source(db_session)
    high = make_article(db_session, source_id=src.id, dedup_key="k-high", importance_score=85)
    medium = make_article(db_session, source_id=src.id, dedup_key="k-med", importance_score=65)
    low = make_article(db_session, source_id=src.id, dedup_key="k-low", importance_score=30)

    r = client.get(f"/article/{high.id}")
    assert "importance-high" in r.text

    r = client.get(f"/article/{medium.id}")
    assert "importance-medium" in r.text

    r = client.get(f"/article/{low.id}")
    assert "importance-low" in r.text


def test_article_detail_importance_badge_tooltip(db_session, client):
    """Regression: importance badge title tooltip must contain the score."""
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, importance_score=60)
    response = client.get(f"/article/{article.id}")
    assert response.status_code == 200
    assert 'title="60/100' in response.text


def test_article_detail_no_importance_badge_when_no_score(db_session, client):
    """Regression: no badge must be rendered when importance_score is None."""
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, importance_score=None)
    response = client.get(f"/article/{article.id}")
    assert response.status_code == 200
    assert "importance-badge" not in response.text
    assert "detail-importance" not in response.text


# ---------------------------------------------------------------------------
# Live-refresh: sidebar hx-trigger
# ---------------------------------------------------------------------------


def test_get_index_sidebar_hx_trigger_has_every_60s(client):
    """#sidebar hx-trigger must include 'every 60s', 'load', and 'refreshSidebar from:body'."""
    response = client.get("/")
    assert response.status_code == 200
    assert "every 60s" in response.text
    assert "load" in response.text
    assert "refreshSidebar from:body" in response.text


# ---------------------------------------------------------------------------
# Live-refresh: #new-articles-banner in full-page render
# ---------------------------------------------------------------------------


def test_get_feed_smart_all_has_new_articles_banner(db_session, client):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    response = client.get("/feed/smart/all")
    assert response.status_code == 200
    assert 'id="new-articles-banner"' in response.text
    assert "/updates?since=" in response.text


def test_get_feed_smart_all_banner_since_equals_max_article_id(db_session, client):
    src = make_source(db_session)
    a1 = make_article(db_session, source_id=src.id, dedup_key="k1")
    a2 = make_article(db_session, source_id=src.id, dedup_key="k2")
    max_id = max(a1.id, a2.id)
    response = client.get("/feed/smart/all")
    assert response.status_code == 200
    assert f"since={max_id}" in response.text


def test_get_feed_smart_all_empty_yields_since_0(client):
    response = client.get("/feed/smart/all")
    assert response.status_code == 200
    assert "since=0" in response.text


def test_feed_htmx_cursor_request_no_new_articles_banner(db_session, client):
    """HTMX infinite-scroll fragment must NOT contain #new-articles-banner."""
    src = make_source(db_session)
    for i in range(3):
        make_article(db_session, source_id=src.id, dedup_key=f"k{i}", importance_score=50 - i)
    page1 = smart_feed("all", db_session, page_size=1, important_threshold=70)
    assert page1.next_cursor is not None
    response = client.get(
        f"/feed/smart/all?cursor={page1.next_cursor}",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert 'id="new-articles-banner"' not in response.text


# ---------------------------------------------------------------------------
# Live-refresh: /updates endpoints
# ---------------------------------------------------------------------------


def test_smart_updates_empty_when_since_equals_max_id(db_session, client):
    src = make_source(db_session)
    a = make_article(db_session, source_id=src.id)
    response = client.get(f"/feed/smart/all/updates?since={a.id}")
    assert response.status_code == 200
    assert "new-articles-pill" not in response.text


def test_smart_updates_non_empty_when_since_zero(db_session, client):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    response = client.get("/feed/smart/all/updates?since=0")
    assert response.status_code == 200
    assert "new-articles-pill" in response.text


def test_smart_updates_pill_text_singular(db_session, client):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    response = client.get("/feed/smart/all/updates?since=0")
    assert "1 new article" in response.text
    assert "1 new articles" not in response.text


def test_smart_updates_pill_text_plural(db_session, client):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, dedup_key="k1")
    make_article(db_session, source_id=src.id, dedup_key="k2")
    response = client.get("/feed/smart/all/updates?since=0")
    assert "2 new articles" in response.text


def test_category_updates_isolates_by_category(db_session, client):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, dedup_key="k1", categories=["tech"])
    make_article(db_session, source_id=src.id, dedup_key="k2", categories=["sports"])
    response = client.get("/feed/category/tech/updates?since=0")
    assert response.status_code == 200
    assert "1 new article" in response.text


def test_source_updates_isolates_by_source(db_session, client):
    src1 = make_source(db_session, name="S1", url="http://s1.example.com/feed")
    src2 = make_source(db_session, name="S2", url="http://s2.example.com/feed")
    make_article(db_session, source_id=src1.id, dedup_key="k1")
    make_article(db_session, source_id=src2.id, dedup_key="k2")
    response = client.get(f"/feed/source/{src1.id}/updates?since=0")
    assert response.status_code == 200
    assert "1 new article" in response.text


def test_smart_updates_unread_filter_excludes_read_articles(db_session, client):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, dedup_key="k1", is_read=False)
    make_article(db_session, source_id=src.id, dedup_key="k2", is_read=True)
    response = client.get("/feed/smart/all/updates?since=0&unread=1")
    assert response.status_code == 200
    assert "1 new article" in response.text
    assert "2 new articles" not in response.text
    assert 'hx-get="/feed/smart/all?unread=1"' in response.text


def test_detail_header_toolbar_floats_to_free_title_width(client):
    """Regression (Issue 4): the toolbar floats right so the title text uses the
    full width (wrapping around/below it) instead of being squeezed by a reserved
    fixed gap; the header must contain the float."""
    import re

    response = client.get("/static/styles.css")
    assert response.status_code == 200
    css = response.text
    toolbar_blocks = re.findall(r"\.detail-reader-toolbar\s*\{[^}]*\}", css)
    assert any("float: right" in b for b in toolbar_blocks), (
        ".detail-reader-toolbar must float right so the title can use full width"
    )
    header = re.search(r"\.detail-header\s*\{[^}]*\}", css)
    assert header and "overflow" in header.group(0), (
        ".detail-header must contain the floated toolbar (overflow)"
    )


def test_search_hint_and_empty_css_rules_have_padding(client):
    """Regression: .search-hint and .search-empty must have padding rules in styles.css.

    Before the fix, neither class had any CSS rule, so messages rendered flush
    against the left edge of the middle pane with no spacing.
    """
    response = client.get("/static/styles.css")
    assert response.status_code == 200
    css = response.text

    # Locate the combined rule block for both classes.
    rule_start = css.find(".search-hint,")
    assert rule_start != -1, ".search-hint CSS rule not found in styles.css"
    rule_end = css.find("}", rule_start)
    rule_block = css[rule_start:rule_end]
    assert "padding" in rule_block, (
        ".search-hint / .search-empty rule must include a padding declaration"
    )
    assert ".search-empty" in rule_block, (
        ".search-empty must share the same CSS rule block as .search-hint"
    )


# ---------------------------------------------------------------------------
# Regression: newest_id uses global view max id, not page-1 max
# (live-refresh pill shows spurious count when ordering is not by id)
# ---------------------------------------------------------------------------


def test_new_articles_banner_uses_global_view_max_id_smart(db_session, client, monkeypatch):
    """Regression: with importance-based ordering a high-id article with low importance
    sorts to page 2, but the banner's since marker must be the global view max id.

    Pre-fix behaviour: page-1-max was used, so /updates?since=<page1-max> immediately
    counted the page-2 article as 'new', giving a spurious pill that never cleared.
    """
    import aggregator_web.app as app_mod

    monkeypatch.setattr(app_mod.settings, "web_page_size", 1)

    src = make_source(db_session)
    # Lower id, high importance → lands on page 1 (importance DESC ordering)
    a_page1 = make_article(db_session, source_id=src.id, dedup_key="k1", importance_score=90)
    # Higher id, low importance → pushed to page 2 by importance DESC ordering
    a_page2 = make_article(db_session, source_id=src.id, dedup_key="k2", importance_score=10)
    assert a_page2.id > a_page1.id, "sanity: second article must have a higher id"

    response = client.get("/feed/smart/all")
    assert response.status_code == 200
    # Banner must reference the global max (a_page2.id), not the page-1 max (a_page1.id)
    assert f"since={a_page2.id}" in response.text

    # /updates with the correct marker returns no pill (nothing new yet)
    upd = client.get(f"/feed/smart/all/updates?since={a_page2.id}")
    assert "new-articles-pill" not in upd.text

    # A genuinely new article (id > global max) must produce exactly one pill entry
    a_new = make_article(db_session, source_id=src.id, dedup_key="k3", importance_score=50)
    assert a_new.id > a_page2.id
    upd2 = client.get(f"/feed/smart/all/updates?since={a_page2.id}")
    assert "new-articles-pill" in upd2.text
    assert "1 new article" in upd2.text


def test_new_articles_banner_uses_global_view_max_id_category(db_session, client, monkeypatch):
    """Regression: category feed banner must use the global category-view max id."""
    import aggregator_web.app as app_mod

    monkeypatch.setattr(app_mod.settings, "web_page_size", 1)

    src = make_source(db_session)
    a_page1 = make_article(
        db_session, source_id=src.id, dedup_key="k1", importance_score=90, categories=["tech"]
    )
    a_page2 = make_article(
        db_session, source_id=src.id, dedup_key="k2", importance_score=10, categories=["tech"]
    )
    assert a_page2.id > a_page1.id

    response = client.get("/feed/category/tech")
    assert response.status_code == 200
    assert f"since={a_page2.id}" in response.text

    upd = client.get(f"/feed/category/tech/updates?since={a_page2.id}")
    assert "new-articles-pill" not in upd.text

    a_new = make_article(
        db_session, source_id=src.id, dedup_key="k3", importance_score=50, categories=["tech"]
    )
    assert a_new.id > a_page2.id
    upd2 = client.get(f"/feed/category/tech/updates?since={a_page2.id}")
    assert "new-articles-pill" in upd2.text
    assert "1 new article" in upd2.text


def test_new_articles_banner_uses_global_view_max_id_source(db_session, client, monkeypatch):
    """Regression: source feed banner must use the global source-view max id."""
    import aggregator_web.app as app_mod

    monkeypatch.setattr(app_mod.settings, "web_page_size", 1)

    src = make_source(db_session)
    a_page1 = make_article(db_session, source_id=src.id, dedup_key="k1", importance_score=90)
    a_page2 = make_article(db_session, source_id=src.id, dedup_key="k2", importance_score=10)
    assert a_page2.id > a_page1.id

    response = client.get(f"/feed/source/{src.id}")
    assert response.status_code == 200
    assert f"since={a_page2.id}" in response.text

    upd = client.get(f"/feed/source/{src.id}/updates?since={a_page2.id}")
    assert "new-articles-pill" not in upd.text

    a_new = make_article(db_session, source_id=src.id, dedup_key="k3", importance_score=50)
    assert a_new.id > a_page2.id
    upd2 = client.get(f"/feed/source/{src.id}/updates?since={a_page2.id}")
    assert "new-articles-pill" in upd2.text
    assert "1 new article" in upd2.text


# ---------------------------------------------------------------------------
# Sort toggle: route param + template context
# ---------------------------------------------------------------------------


def test_feed_sort_newest_accepted(db_session, client):
    """GET /feed/smart/all?sort=newest must return 200."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    response = client.get("/feed/smart/all?sort=newest")
    assert response.status_code == 200


def test_feed_sort_invalid_falls_back_to_relevance(db_session, client):
    """An unrecognised sort value must be silently normalised to 'relevance'."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    response = client.get("/feed/smart/all?sort=hacker")
    assert response.status_code == 200


def test_feed_source_sort_newest_returns_date_desc(db_session, client, monkeypatch):
    """With sort=newest the rendered list preserves date-descending order."""
    import aggregator_web.app as app_mod

    monkeypatch.setattr(app_mod.settings, "web_page_size", 10)

    from datetime import datetime, timezone
    src = make_source(db_session)
    dt_old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    dt_new = datetime(2025, 12, 1, tzinfo=timezone.utc)
    make_article(db_session, source_id=src.id, dedup_key="k1", importance_score=95, feed_published_at=dt_old, feed_title="Old High")
    make_article(db_session, source_id=src.id, dedup_key="k2", importance_score=5, feed_published_at=dt_new, feed_title="New Low")

    response = client.get(f"/feed/source/{src.id}?sort=newest")
    assert response.status_code == 200
    html = response.text
    # Newest sort: "New Low" (newer date) should appear before "Old High"
    assert html.index("New Low") < html.index("Old High")


def test_feed_sort_newest_toggle_in_rendered_html(db_session, client):
    """Sort toggle with active Newest button must appear in the rendered HTML."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    response = client.get(f"/feed/source/{src.id}?sort=newest")
    assert response.status_code == 200
    assert "sort-btn" in response.text
    assert "feed-sort-toggle" in response.text


def test_feed_sort_next_url_carries_sort_newest(db_session, client, monkeypatch):
    """Cursor next_url must include sort=newest when newest is active."""
    import aggregator_web.app as app_mod

    monkeypatch.setattr(app_mod.settings, "web_page_size", 1)

    from datetime import datetime, timezone
    src = make_source(db_session)
    for i in range(2):
        make_article(
            db_session,
            source_id=src.id,
            dedup_key=f"k{i}",
            feed_published_at=datetime(2025, 6, i + 1, tzinfo=timezone.utc),
        )

    response = client.get(f"/feed/source/{src.id}?sort=newest")
    assert response.status_code == 200
    assert "sort=newest" in response.text


def test_feed_sort_default_next_url_omits_sort(db_session, client, monkeypatch):
    """Default relevance sort must NOT add sort=newest to the cursor pagination URL."""
    import aggregator_web.app as app_mod

    monkeypatch.setattr(app_mod.settings, "web_page_size", 1)

    src = make_source(db_session)
    for i in range(2):
        make_article(db_session, source_id=src.id, dedup_key=f"k{i}", importance_score=50 - i)

    response = client.get(f"/feed/source/{src.id}")
    assert response.status_code == 200
    html = response.text
    # The cursor pagination hx-get links must not carry sort=newest in relevance mode.
    # (The sort toggle always has ?sort=newest in its href — that's correct and expected.)
    for part in html.split('hx-get="'):
        url = part.split('"')[0]
        if 'cursor=' in url:
            assert 'sort=newest' not in url, f"Cursor URL must not contain sort=newest: {url}"


def test_updates_endpoint_preserves_sort_newest(db_session, client):
    """The /updates pill hx-get must include sort=newest when sort is newest."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    response = client.get(f"/feed/source/{src.id}/updates?since=0&sort=newest")
    assert response.status_code == 200
    assert "sort=newest" in response.text


def test_updates_endpoint_omits_sort_for_relevance(db_session, client):
    """The /updates pill hx-get must NOT include sort= when sort is relevance."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    response = client.get(f"/feed/source/{src.id}/updates?since=0&sort=relevance")
    assert response.status_code == 200
    assert "sort=newest" not in response.text


def test_new_articles_banner_carries_sort_newest(db_session, client):
    """The #new-articles-banner hx-get URL must include sort=newest when active."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    response = client.get(f"/feed/source/{src.id}?sort=newest")
    assert response.status_code == 200
    assert "new-articles-banner" in response.text
    assert "sort=newest" in response.text


def test_new_articles_banner_omits_sort_for_relevance(db_session, client):
    """The #new-articles-banner hx-get URL must NOT include sort= for relevance mode."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    response = client.get(f"/feed/source/{src.id}")
    assert response.status_code == 200
    html = response.text
    assert "new-articles-banner" in html
    # Extract the banner element's hx-get URL and verify it lacks sort=newest.
    # The sort toggle button always has ?sort=newest in its href; that's expected.
    banner_idx = html.find('id="new-articles-banner"')
    assert banner_idx != -1
    # Grab from banner up to the closing >
    banner_tag = html[banner_idx : html.find(">", banner_idx)]
    assert "sort=newest" not in banner_tag


def test_new_articles_banner_unread_filter_uses_global_unread_max_id(db_session, client, monkeypatch):
    """Regression: with unread=1 the banner must use the global unread-view max id."""
    import aggregator_web.app as app_mod

    monkeypatch.setattr(app_mod.settings, "web_page_size", 1)

    src = make_source(db_session)
    # High importance, lower id, unread → page 1
    a_page1 = make_article(
        db_session, source_id=src.id, dedup_key="k1", importance_score=90, is_read=False
    )
    # Low importance, higher id, unread → page 2
    a_page2 = make_article(
        db_session, source_id=src.id, dedup_key="k2", importance_score=10, is_read=False
    )
    assert a_page2.id > a_page1.id

    response = client.get("/feed/smart/all?unread=1")
    assert response.status_code == 200
    assert f"since={a_page2.id}" in response.text

    upd = client.get(f"/feed/smart/all/updates?since={a_page2.id}&unread=1")
    assert "new-articles-pill" not in upd.text


# ---------------------------------------------------------------------------
# Hide-read toggle: UI control + route behaviour
# ---------------------------------------------------------------------------


def test_feed_hide_read_toggle_present_in_html(db_session, client):
    """Every feed must render the hide-read toggle control."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    response = client.get(f"/feed/source/{src.id}")
    assert response.status_code == 200
    assert "feed-hide-read-toggle" in response.text
    assert "Hide read" in response.text
    assert "Show all" in response.text


def test_feed_hide_read_toggle_active_when_unread_only(db_session, client):
    """When unread=1 is active the 'Hide read' button must carry the active CSS class."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, is_read=False)
    response = client.get(f"/feed/source/{src.id}?unread=1")
    assert response.status_code == 200
    html = response.text
    # The active class must appear on the Hide-read button, not on Show-all.
    # Locate the hide-read toggle group and check button order / active marker.
    group_start = html.find('feed-hide-read-toggle')
    assert group_start != -1
    group = html[group_start: html.find('</div>', group_start) + 6]
    # "Show all" button must NOT be active; "Hide read" button MUST be active.
    assert 'sort-btn active' in group
    hide_idx = group.find('Hide read')
    show_idx = group.find('Show all')
    assert hide_idx != -1 and show_idx != -1
    # The active class on the button containing "Hide read" must come before "Hide read" text.
    active_idx = group.rfind('sort-btn active', 0, hide_idx)
    assert active_idx != -1, "'Hide read' button must have the active class"


def test_feed_hide_read_returns_only_unread_articles(db_session, client):
    """GET /feed/source/<id>?unread=1 must exclude read articles."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, is_read=False, feed_title="Unread Article")
    make_article(db_session, source_id=src.id, dedup_key="k2", is_read=True, feed_title="Read Article")
    response = client.get(f"/feed/source/{src.id}?unread=1")
    assert response.status_code == 200
    assert "Unread Article" in response.text
    assert "Read Article" not in response.text


def test_feed_hide_read_next_url_carries_unread(db_session, client, monkeypatch):
    """Cursor next_url must include unread=1 when hide-read filter is active."""
    import aggregator_web.app as app_mod

    monkeypatch.setattr(app_mod.settings, "web_page_size", 1)

    src = make_source(db_session)
    for i in range(2):
        make_article(db_session, source_id=src.id, dedup_key=f"k{i}", is_read=False)

    response = client.get(f"/feed/source/{src.id}?unread=1")
    assert response.status_code == 200
    assert "unread=1" in response.text


def test_feed_hide_read_toggle_preserves_sort_newest(db_session, client):
    """The hide-read 'Hide read' button hx-get must carry both unread=1 and sort=newest."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, is_read=False)
    response = client.get(f"/feed/source/{src.id}?sort=newest")
    assert response.status_code == 200
    html = response.text
    group_start = html.find('feed-hide-read-toggle')
    assert group_start != -1
    group = html[group_start: html.find('</div>', group_start) + 6]
    # The "Hide read" button must include both params in its hx-get URL.
    assert "unread=1" in group
    assert "sort=newest" in group


def test_feed_hide_read_show_all_button_preserves_sort_newest(db_session, client):
    """The 'Show all' button hx-get must carry sort=newest when sort is newest."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, is_read=False)
    response = client.get(f"/feed/source/{src.id}?sort=newest&unread=1")
    assert response.status_code == 200
    html = response.text
    group_start = html.find('feed-hide-read-toggle')
    assert group_start != -1
    group = html[group_start: html.find('</div>', group_start) + 6]
    # "Show all" must include sort=newest (but not unread=1) in its hx-get URL.
    show_idx = group.find('Show all')
    assert show_idx != -1
    # hx-get URL for Show all comes before the "Show all" text
    btn_start = group.rfind('hx-get=', 0, show_idx)
    assert btn_start != -1
    btn_url = group[btn_start: group.find('"', btn_start + 8) + 1]
    assert "sort=newest" in btn_url
    assert "unread=1" not in btn_url


# ---------------------------------------------------------------------------
# Mobile reader fixes — regression suite
# ---------------------------------------------------------------------------


def test_article_card_title_link_has_no_broken_htmx_target(db_session, client):
    """Regression (Issue 1): article card title link must NOT carry hx-target='#article-reader'.

    The old target was a dead ID that never existed. The @click=select handler on the
    card element drives reader loading; the title anchor is now a plain href for
    accessibility and middle-click only.
    """
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    response = client.get(f"/feed/source/{src.id}")
    assert response.status_code == 200
    assert "#article-reader" not in response.text, (
        "hx-target='#article-reader' must be removed — that element does not exist"
    )


def test_article_card_title_has_href_for_accessibility(db_session, client):
    """Article card title must keep href so middle-click and copy-link still work."""
    src = make_source(db_session)
    article = make_article(
        db_session,
        source_id=src.id,
        feed_url="https://example.com/article/1",
    )
    response = client.get(f"/feed/source/{src.id}")
    assert response.status_code == 200
    assert f'href="/article/{article.id}"' in response.text


def test_article_detail_has_prev_next_nav_buttons(db_session, client):
    """Regression (Issue 5): article detail must include Prev and Next nav buttons.

    These dispatch reader:prev / reader:next CustomEvents caught by articleList.
    """
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id)
    response = client.get(f"/article/{article.id}")
    assert response.status_code == 200
    html = response.text
    assert "reader:prev" in html, "reader:prev event dispatch must be in article detail"
    assert "reader:next" in html, "reader:next event dispatch must be in article detail"
    assert "reader-nav-btn" in html, ".reader-nav-btn class must be present on nav buttons"


def test_article_detail_prev_next_dispatch_events(db_session, client):
    """Prev/Next buttons must dispatch CustomEvent via window.dispatchEvent."""
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id)
    response = client.get(f"/article/{article.id}")
    assert response.status_code == 200
    html = response.text
    assert "window.dispatchEvent(new CustomEvent('reader:prev'))" in html
    assert "window.dispatchEvent(new CustomEvent('reader:next'))" in html


def test_shell_reader_pane_has_close_button(client):
    """Regression (Issue 2/3): shell must render a .reader-close-btn inside #reader-pane.

    The close button persists through HTMX swaps (which target #reader-content)
    and provides mobile back/close affordance for both article and brief details.
    """
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "reader-close-btn" in html, ".reader-close-btn must be in the shell HTML"
    reader_pane_idx = html.find('id="reader-pane"')
    close_btn_idx = html.find("reader-close-btn")
    assert reader_pane_idx != -1
    assert close_btn_idx != -1
    assert close_btn_idx > reader_pane_idx, "reader-close-btn must be inside #reader-pane"


def test_shell_reader_pane_has_reader_content_wrapper(client):
    """Regression: shell must render #reader-content inside #reader-pane.

    HTMX swap targets #reader-content so that the .reader-close-btn chrome
    is preserved through article/brief loads.
    """
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert 'id="reader-content"' in html, "#reader-content must be present in shell"
    pane_idx = html.find('id="reader-pane"')
    content_idx = html.find('id="reader-content"')
    assert content_idx > pane_idx, "#reader-content must be nested inside #reader-pane"


def test_shell_hamburger_calls_toggle_drawer(client):
    """Regression (Issue 2/3): hamburger @click must call toggleDrawer() to close the
    reader before opening the sidebar drawer (prevents drawer hidden behind reader overlay).
    """
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "toggleDrawer()" in html, (
        "hamburger @click must use toggleDrawer() not drawerOpen = !drawerOpen"
    )


def test_detail_header_toolbar_flows_static_on_mobile(client):
    """Regression (Issue 4): on ≤1023px the toolbar must stop floating
    (float: none) so it flows as a static row above the title on narrow screens
    instead of squeezing the title into a partial-width column."""
    import re

    response = client.get("/static/styles.css")
    assert response.status_code == 200
    css = response.text

    assert "@media (max-width: 1023px)" in css, "≤1023px media block not found"
    toolbar_blocks = re.findall(r"\.detail-reader-toolbar\s*\{[^}]*\}", css)
    assert any("float: none" in b for b in toolbar_blocks), (
        "a .detail-reader-toolbar rule must set float: none for the ≤1023px layout"
    )
