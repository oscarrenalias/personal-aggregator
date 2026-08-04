"""Regression tests for threads-view keyboard focus and member-article key actions.

Defect A: after selecting the Threads nav item via the real nav-selection path,
focus must land on #article-listview (not the nav Tree) so Enter/j/k are owned
by the list, not the Tree.

Defect B: after opening a thread member article via action_open_member_article,
v/m/s/n must act on that article — previously _selected_article was None
(nulled on thread/nav selection) and _selected_article_row was None (no list row),
so all four actions were no-ops.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from aggregator_tui.api_client import ArticleResponse
from aggregator_tui.app import AggregatorApp
from aggregator_tui.widgets.nav_sidebar import NavItem, NavSidebar
from textual.widgets import ListView

from .conftest import StubApiClient, make_article, make_thread


def _make_stub_with_article(article: ArticleResponse) -> StubApiClient:
    """Return a StubApiClient whose get_article always returns the given article."""
    stub = StubApiClient()

    async def _get_article(article_id: int) -> ArticleResponse:
        stub.calls.append(("get_article", article_id))
        return article

    stub.get_article = _get_article  # type: ignore[method-assign]
    return stub


# ---------------------------------------------------------------------------
# Defect A: threads nav selection moves focus to #article-listview
# ---------------------------------------------------------------------------


def test_threads_nav_selection_moves_focus_to_list(stub: StubApiClient) -> None:
    """After selecting Threads via the real nav-selection path, focus must be on
    #article-listview and _pane_focus_idx must be 1.
    Pre-fix: on_nav_sidebar_nav_item_selected never moved focus, so the nav Tree
    kept focus and _pane_focus_idx stayed 0."""
    stub.set_threads([make_thread(1)])

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            assert app._pane_focus_idx == 0  # nav Tree owns focus on mount

            # Fire the real nav-selection handler (same path as NavSidebar Tree click)
            app.post_message(NavSidebar.NavItemSelected(item=NavItem(kind="threads", label="Threads")))
            await pilot.pause(0.3)

            assert app._pane_focus_idx == 1
            assert isinstance(app.focused, ListView)

    asyncio.run(inner())


def test_enter_on_thread_opens_it_after_nav_selection(stub: StubApiClient) -> None:
    """After nav to Threads, pressing Enter on a highlighted thread must fire the
    open-thread handler (get_thread called).
    Pre-fix: Enter was consumed by the focused nav Tree (select_cursor) and never
    reached the ListView, so get_thread was never called."""
    stub.set_threads([make_thread(1)])

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)

            app.post_message(NavSidebar.NavItemSelected(item=NavItem(kind="threads", label="Threads")))
            await pilot.pause(0.3)
            assert app._pane_focus_idx == 1  # list owns focus after nav selection

            await pilot.press("j")
            await pilot.pause(0.1)
            assert app._selected_thread is not None
            assert app._selected_thread.id == 1

            await pilot.press("enter")
            await pilot.pause(0.2)

            get_thread_calls = [c for c in stub.calls if c[0] == "get_thread"]
            assert len(get_thread_calls) >= 1
            assert get_thread_calls[0][1] == 1

    asyncio.run(inner())


# ---------------------------------------------------------------------------
# Defect B: v/m/s act on member articles opened from a thread
# ---------------------------------------------------------------------------


def test_v_opens_member_article_url_in_browser() -> None:
    """After opening a thread member article via action_open_member_article,
    pressing 'v' must call webbrowser.open() with that article's URL.
    Pre-fix: _selected_article was None, so 'v' showed 'No URL available'."""
    member_article = make_article(42, url="http://example.com/member/42")
    stub2 = _make_stub_with_article(member_article)

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub2
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)

            # Simulate the real flow: reader_pane.on_option_list_option_selected
            # calls app.action_open_member_article(article_id)
            app.action_open_member_article(42)
            await pilot.pause(0.3)  # wait for _fetch_article to set _selected_article

            assert app._selected_article is not None
            assert app._selected_article.id == 42

            with patch("aggregator_tui.app.webbrowser.open") as mock_open:
                await pilot.press("v")
                await pilot.pause(0.1)

            mock_open.assert_called_once_with("http://example.com/member/42")

    asyncio.run(inner())


def test_m_marks_member_article_read() -> None:
    """After opening a thread member article, pressing 'm' must call mark_read.
    Pre-fix: _selected_article_row was None, so action_toggle_read returned early."""
    member_article = make_article(42, is_read=False)
    stub2 = _make_stub_with_article(member_article)

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub2
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)

            app.action_open_member_article(42)
            await pilot.pause(0.3)
            assert app._selected_article is not None

            await pilot.press("m")
            await pilot.pause(0.1)

            assert member_article.is_read is True
            assert ("mark_read", 42) in stub2.calls

    asyncio.run(inner())


def test_s_saves_member_article() -> None:
    """After opening a thread member article, pressing 's' must call save_article.
    Pre-fix: _selected_article_row was None, so action_toggle_save returned early."""
    member_article = make_article(42, is_saved=False)
    stub2 = _make_stub_with_article(member_article)

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub2
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)

            app.action_open_member_article(42)
            await pilot.pause(0.3)
            assert app._selected_article is not None

            await pilot.press("s")
            await pilot.pause(0.1)

            assert member_article.is_saved is True
            assert ("save_article", 42) in stub2.calls

    asyncio.run(inner())


# ---------------------------------------------------------------------------
# No regression on article-list v/m/s/n or Tab/Escape pane cycling
# ---------------------------------------------------------------------------


def test_tab_escape_pane_cycling_unaffected(stub: StubApiClient) -> None:
    """Tab/Escape pane cycling still works correctly after the focus-shift fix."""

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            assert app._pane_focus_idx == 0

            await pilot.press("tab")
            await pilot.pause(0.1)
            assert app._pane_focus_idx == 1

            await pilot.press("tab")
            await pilot.pause(0.1)
            assert app._pane_focus_idx == 2

            await pilot.press("escape")
            await pilot.pause(0.1)
            assert app._pane_focus_idx == 1

    asyncio.run(inner())


def test_v_still_works_for_normal_article_list(stub: StubApiClient) -> None:
    """Normal article-list v/m/s are unaffected by the member-article fallback."""
    from aggregator_tui.widgets.article_list import ArticleList

    stub.set_articles([make_article(1, url="http://example.com/1")])

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await app.query_one("#list-pane", ArticleList).load(view="all")
            await pilot.pause(0.1)

            await pilot.press("j")
            await pilot.pause(0.1)
            assert app._selected_article is not None
            assert app._selected_article_row is not None

            with patch("aggregator_tui.app.webbrowser.open") as mock_open:
                await pilot.press("v")
                await pilot.pause(0.1)

            mock_open.assert_called_once()

    asyncio.run(inner())
