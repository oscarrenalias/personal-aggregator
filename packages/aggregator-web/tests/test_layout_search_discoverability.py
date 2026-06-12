"""Tests for web-ui-reader-layout-and-search-discoverability changes.

Covers eight areas:
  1. Search affordance in /sidebar
  2. Article card toolbar (icon-only controls with aria-labels)
  3. Article detail reader — exactly one Open-source control
  4. Importance badge tooltip — reason in title attribute, no inline element
  5. styles.css max-width rule for .article-detail
  6. Keyboard shortcuts overlay in GET /
  7. Scroll-reset logic present in app.js
  8. Sidebar search unification and mark-read-and-next shortcut
"""

from __future__ import annotations

from conftest import make_article, make_source


# ---------------------------------------------------------------------------
# 1. Search affordance in /sidebar
# ---------------------------------------------------------------------------


def test_sidebar_has_search_input(db_session, client):
    """Sidebar must include a real search input with id=sidebar-search-input."""
    make_source(db_session)
    response = client.get("/sidebar")
    assert response.status_code == 200
    assert 'id="sidebar-search-input"' in response.text


def test_sidebar_search_input_wires_to_search_route(db_session, client):
    """Sidebar search input must use hx-get='/search' to trigger live search."""
    make_source(db_session)
    response = client.get("/sidebar")
    assert response.status_code == 200
    assert 'hx-get="/search"' in response.text


def test_sidebar_search_appears_before_smart_views(db_session, client):
    """Search input must appear before the Smart Views section in DOM order."""
    make_source(db_session)
    response = client.get("/sidebar")
    assert response.status_code == 200
    search_pos = response.text.find('id="sidebar-search-input"')
    smart_views_pos = response.text.find("Smart Views")
    assert search_pos != -1 and smart_views_pos != -1
    assert search_pos < smart_views_pos


# ---------------------------------------------------------------------------
# 2. Article card toolbar — icon-only controls with aria-labels
# ---------------------------------------------------------------------------


def test_article_card_has_card_actions_group(db_session, client):
    """Article card must render a .card-actions element containing action buttons."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, is_read=False)
    response = client.get("/feed/smart/all")
    assert response.status_code == 200
    assert "card-actions" in response.text


def test_article_card_read_button_has_aria_label(db_session, client):
    """Unread article card read button must carry an aria-label."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, is_read=False)
    response = client.get("/feed/smart/all")
    assert response.status_code == 200
    assert 'aria-label="Mark as read"' in response.text


def test_article_card_read_button_label_reflects_read_state(db_session, client):
    """Read article card read button must show 'Mark as unread'."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, is_read=True)
    response = client.get("/feed/smart/all")
    assert response.status_code == 200
    assert 'aria-label="Mark as unread"' in response.text


def test_article_card_save_button_has_aria_label(db_session, client):
    """Unsaved article card save button must carry an accessible label."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, is_saved=False)
    response = client.get("/feed/smart/all")
    assert response.status_code == 200
    assert 'aria-label="Save article"' in response.text


def test_post_article_read_card_renders_is_read_class(db_session, client):
    """After marking read, the re-rendered card must carry the is-read class."""
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, is_read=False)
    response = client.post(f"/article/{article.id}/read")
    assert response.status_code == 200
    assert "is-read" in response.text


# ---------------------------------------------------------------------------
# 3. Article detail — exactly one Open-source control
# ---------------------------------------------------------------------------


def test_article_detail_has_exactly_one_open_source_control(db_session, client):
    """Reader detail must contain exactly one Open-source link (no duplicates)."""
    src = make_source(db_session)
    article = make_article(
        db_session,
        source_id=src.id,
        feed_url="https://example.com/article/dedup-check",
    )
    response = client.get(f"/article/{article.id}")
    assert response.status_code == 200
    assert response.text.count("btn-open-source") == 1


def test_article_detail_open_source_control_has_accessible_label(db_session, client):
    """The Open-source control must carry an aria-label."""
    src = make_source(db_session)
    article = make_article(
        db_session,
        source_id=src.id,
        feed_url="https://example.com/article/a11y-check",
    )
    response = client.get(f"/article/{article.id}")
    assert response.status_code == 200
    assert "Open article source" in response.text


# ---------------------------------------------------------------------------
# 4. Importance badge — reason in title attribute, no inline element
# ---------------------------------------------------------------------------


def test_article_detail_no_importance_reason_element(db_session, client):
    """Detail view must not render a standalone .importance-reason element."""
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, importance_score=80)
    response = client.get(f"/article/{article.id}")
    assert response.status_code == 200
    assert "importance-reason" not in response.text


def test_importance_badge_contains_reason_in_title(db_session, client):
    """importance_reason must appear in the badge title attribute, not as a visible element."""
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, importance_score=80)
    article.importance_reason = "Highly relevant to your interests"
    db_session.commit()
    db_session.refresh(article)

    response = client.get(f"/article/{article.id}")
    assert response.status_code == 200
    assert "Highly relevant to your interests" in response.text
    assert "importance-reason" not in response.text


def test_importance_badge_shows_numeric_score(db_session, client):
    """Importance badge must display the numeric score."""
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, importance_score=75)
    response = client.get(f"/article/{article.id}")
    assert response.status_code == 200
    assert "75" in response.text
    assert "importance-badge" in response.text


def test_importance_badge_high_tier_class(db_session, client):
    """Score >= 80 must render importance-high class."""
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, importance_score=85)
    response = client.get(f"/article/{article.id}")
    assert response.status_code == 200
    assert "importance-high" in response.text


# ---------------------------------------------------------------------------
# 5. styles.css — max-width rule for .article-detail
# ---------------------------------------------------------------------------


def test_styles_css_article_detail_has_max_width_rule(client):
    """.article-detail rule in styles.css must include a max-width declaration."""
    response = client.get("/static/styles.css")
    assert response.status_code == 200
    text = response.text
    # Find .article-detail selector
    selector_pos = text.find(".article-detail")
    assert selector_pos != -1, ".article-detail selector not found in styles.css"
    # max-width must appear within a reasonable distance after the selector
    block_end = text.find("}", selector_pos)
    block = text[selector_pos:block_end + 1] if block_end != -1 else text[selector_pos:selector_pos + 300]
    assert "max-width" in block, "max-width not found in .article-detail rule block"


# ---------------------------------------------------------------------------
# 6. Keyboard shortcuts overlay in GET /
# ---------------------------------------------------------------------------


def test_index_has_shortcuts_overlay_element(client):
    """Shell HTML must include the keyboard shortcuts overlay element."""
    response = client.get("/")
    assert response.status_code == 200
    assert "shortcuts-overlay" in response.text


def test_index_shortcuts_overlay_uses_x_show_show_help(client):
    """Shortcuts overlay must be toggled via x-show=\"showHelp\"."""
    response = client.get("/")
    assert response.status_code == 200
    assert 'x-show="showHelp"' in response.text


def test_index_shortcuts_overlay_has_close_button(client):
    """Overlay close button must carry aria-label=\"Close keyboard shortcuts\"."""
    response = client.get("/")
    assert response.status_code == 200
    assert 'aria-label="Close keyboard shortcuts"' in response.text


def test_index_shortcuts_overlay_lists_all_shortcut_keys(client):
    """Shortcuts overlay must list all expected shortcut keys including n."""
    response = client.get("/")
    assert response.status_code == 200
    for key in ("<kbd>j</kbd>", "<kbd>k</kbd>", "<kbd>v</kbd>", "<kbd>m</kbd>", "<kbd>n</kbd>", "<kbd>/</kbd>", "<kbd>?</kbd>"):
        assert key in response.text, f"shortcut key {key!r} not found in shortcuts overlay"


# ---------------------------------------------------------------------------
# 7. Scroll-reset wired to reader pane in app.js
# ---------------------------------------------------------------------------


def test_app_js_reader_pane_scroll_reset_present(client):
    """app.js must contain scroll-reset logic (scrollTop = 0) wired to reader-pane."""
    response = client.get("/static/app.js")
    assert response.status_code == 200
    assert "scrollTop" in response.text
    assert "reader-pane" in response.text


def test_app_js_scroll_reset_uses_after_swap_event(client):
    """Scroll reset must be triggered on the htmx:afterSwap event."""
    response = client.get("/static/app.js")
    assert response.status_code == 200
    assert "htmx:afterSwap" in response.text
    assert "scrollTop = 0" in response.text


# ---------------------------------------------------------------------------
# 8. Sidebar search unification and mark-read-and-next shortcut
# ---------------------------------------------------------------------------


def test_sidebar_search_input_has_required_htmx_attrs(db_session, client):
    """Sidebar search input must carry name='q', hx-get, hx-target, and hx-preserve."""
    make_source(db_session)
    response = client.get("/sidebar")
    assert response.status_code == 200
    html = response.text
    assert 'id="sidebar-search-input"' in html
    assert 'name="q"' in html
    assert 'hx-get="/search"' in html
    assert 'hx-target="#article-list"' in html
    assert 'hx-preserve="true"' in html


def test_sidebar_has_no_search_button(db_session, client):
    """Sidebar must not contain the old .sidebar-search-btn button element."""
    make_source(db_session)
    response = client.get("/sidebar")
    assert response.status_code == 200
    assert "sidebar-search-btn" not in response.text


def test_search_with_query_has_no_old_search_form(client):
    """GET /search?q=<term> must not render id='search-input' or class='search-form'."""
    response = client.get("/search?q=python")
    assert response.status_code == 200
    assert 'id="search-input"' not in response.text
    assert 'class="search-form"' not in response.text


def test_article_list_template_has_keydown_n_shortcut(db_session, client):
    """Feed page must include @keydown.n.window wired to markReadAndNext()."""
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    response = client.get("/feed/smart/all")
    assert response.status_code == 200
    assert "@keydown.n.window" in response.text


def test_app_js_defines_mark_read_and_next(client):
    """app.js must define the markReadAndNext function."""
    response = client.get("/static/app.js")
    assert response.status_code == 200
    assert "markReadAndNext" in response.text


def test_index_shortcuts_overlay_includes_n_shortcut(client):
    """Shortcuts overlay must list the n shortcut (mark read and next)."""
    response = client.get("/")
    assert response.status_code == 200
    assert "<kbd>n</kbd>" in response.text
