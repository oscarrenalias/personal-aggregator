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


def test_search_empty_query_returns_form(client):
    response = client.get("/search")
    assert response.status_code == 200
    assert "search-form" in response.text
    # No results section when query is empty — _article_list.html (id="article-list") is not rendered.
    # The form itself always contains hx-target="#article-list", so only check for the id attribute.
    assert 'id="article-list"' not in response.text


def test_search_with_no_results(client):
    response = client.get("/search?q=xyznonexistent")
    assert response.status_code == 200
    assert "No results" in response.text


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
