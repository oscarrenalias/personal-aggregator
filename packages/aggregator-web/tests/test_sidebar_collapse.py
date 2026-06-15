"""Regression tests: sidebar section collapse state survives HTMX swaps.

Root cause (B-16fa8319): the categories and sources sections in _sidebar.html
used per-section Alpine x-data components that initialised collapsed state from
localStorage on component creation.  Because the sidebar element uses
hx-swap=innerHTML and every web response emits HX-Trigger: refreshSidebar,
marking an article read (and every 60s poll) destroyed and re-created those
Alpine components.  The server always renders sections expanded, so every swap
reset collapsed state.

Fix: collapsed state moved to a global Alpine.store('sidebar', ...) in app.js.
A global store survives HTMX innerHTML swaps; the swapped-in markup reads the
already-initialised store value immediately.  x-cloak is also added to the
collapsible <ul> elements so there is no expand flash while Alpine processes
the newly-swapped markup.
"""

from __future__ import annotations

from pathlib import Path

_APP_JS = (
    Path(__file__).parent.parent
    / "src"
    / "aggregator_web"
    / "static"
    / "app.js"
)


# ---------------------------------------------------------------------------
# app.js — Alpine store registration
# ---------------------------------------------------------------------------


def test_app_js_registers_sidebar_alpine_store():
    """app.js must register a global Alpine store named 'sidebar'.

    Pre-fix: no store — collapsed state lived only in per-section x-data
    components destroyed by every sidebar swap.
    Post-fix: Alpine.store('sidebar', ...) registered in alpine:init, making
    the state swap-proof.
    """
    source = _APP_JS.read_text()
    assert "Alpine.store('sidebar'" in source, (
        "app.js must register Alpine.store('sidebar', ...) in the alpine:init "
        "handler so collapse state survives HTMX sidebar swaps"
    )


def test_app_js_sidebar_store_tracks_categories_and_sources():
    source = _APP_JS.read_text()
    assert "categoriesCollapsed" in source
    assert "sourcesCollapsed" in source


def test_app_js_sidebar_store_persists_to_localstorage():
    source = _APP_JS.read_text()
    assert "sidebar.categories.collapsed" in source
    assert "sidebar.sources.collapsed" in source


# ---------------------------------------------------------------------------
# /sidebar route — markup contract
# ---------------------------------------------------------------------------


def test_sidebar_categories_ul_has_x_cloak(client, db_session):
    """The categories <ul> must carry x-cloak to prevent expand flash on swap.

    Pre-fix: no x-cloak on #categories-list; the list briefly rendered expanded
    on every sidebar swap before Alpine processed x-show=!collapsed.
    """
    from conftest import make_category, make_source

    make_source(db_session)
    make_category(db_session, name="Tech")

    resp = client.get("/sidebar")
    assert resp.status_code == 200
    html = resp.text

    assert 'id="categories-list"' in html, "categories-list ul not found in sidebar HTML"
    # Verify x-cloak appears on the same element as categories-list
    idx = html.find('id="categories-list"')
    line_start = html.rfind("\n", 0, idx)
    line_end = html.find(">", idx)
    element_fragment = html[line_start:line_end]
    assert "x-cloak" in element_fragment, (
        "#categories-list <ul> must have x-cloak to suppress expand flash during swap"
    )


def test_sidebar_sources_ul_has_x_cloak(client, db_session):
    """The sources <ul> must carry x-cloak to prevent expand flash on swap."""
    from conftest import make_source

    make_source(db_session)

    resp = client.get("/sidebar")
    assert resp.status_code == 200
    html = resp.text

    assert 'id="sources-list"' in html, "sources-list ul not found in sidebar HTML"
    idx = html.find('id="sources-list"')
    line_start = html.rfind("\n", 0, idx)
    line_end = html.find(">", idx)
    element_fragment = html[line_start:line_end]
    assert "x-cloak" in element_fragment, (
        "#sources-list <ul> must have x-cloak to suppress expand flash during swap"
    )


def test_sidebar_uses_store_for_categories_collapse(client, db_session):
    """Categories section must bind collapse state to $store.sidebar, not local x-data.

    Pre-fix: section had x-data="{ collapsed: localStorage.getItem(...) }" — the
    component state was destroyed on every swap.
    Post-fix: section references $store.sidebar.categoriesCollapsed which lives
    in the global Alpine store and survives swaps.
    """
    from conftest import make_category, make_source

    make_source(db_session)
    make_category(db_session, name="Tech")

    resp = client.get("/sidebar")
    assert resp.status_code == 200
    html = resp.text

    assert "$store.sidebar.categoriesCollapsed" in html, (
        "Categories section must reference $store.sidebar.categoriesCollapsed "
        "so collapse state survives sidebar HTMX swaps"
    )


def test_sidebar_uses_store_for_sources_collapse(client, db_session):
    """Sources section must bind collapse state to $store.sidebar, not local x-data."""
    from conftest import make_source

    make_source(db_session)

    resp = client.get("/sidebar")
    assert resp.status_code == 200
    html = resp.text

    assert "$store.sidebar.sourcesCollapsed" in html, (
        "Sources section must reference $store.sidebar.sourcesCollapsed "
        "so collapse state survives sidebar HTMX swaps"
    )


def test_sidebar_no_per_section_xdata_for_collapsed(client, db_session):
    """Sections must NOT use per-section x-data with local collapsed state.

    The broken pattern was:
      x-data="{ collapsed: localStorage.getItem('sidebar.categories.collapsed') === 'true', ... }"
    This is destroyed on every HTMX swap.  The fix moves state to a global store.
    """
    from conftest import make_category, make_source

    make_source(db_session)
    make_category(db_session, name="Tech")

    resp = client.get("/sidebar")
    assert resp.status_code == 200
    html = resp.text

    assert "localStorage.getItem('sidebar.categories.collapsed')" not in html, (
        "Categories section must not use per-section x-data localStorage collapsed "
        "state — this gets destroyed on every HTMX sidebar swap"
    )
    assert "localStorage.getItem('sidebar.sources.collapsed')" not in html, (
        "Sources section must not use per-section x-data localStorage collapsed "
        "state — this gets destroyed on every HTMX sidebar swap"
    )


def test_sidebar_toggle_calls_store_method(client, db_session):
    """Toggle buttons must call $store.sidebar.toggle(...), not a local toggle()."""
    from conftest import make_category, make_source

    make_source(db_session)
    make_category(db_session, name="Tech")

    resp = client.get("/sidebar")
    assert resp.status_code == 200
    html = resp.text

    assert "$store.sidebar.toggle('categoriesCollapsed')" in html
    assert "$store.sidebar.toggle('sourcesCollapsed')" in html
