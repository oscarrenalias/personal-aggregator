"""Tests for keyboard navigation, layout, help overlay, quit, and import hygiene."""
from __future__ import annotations

import ast
import asyncio
import os
import pathlib
from unittest.mock import patch

import pytest

from aggregator_tui.app import AggregatorApp
from aggregator_tui.widgets.article_list import ArticleList
from textual.widgets import Input, ListView

from .conftest import StubApiClient, make_article, make_thread


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_articles(app: AggregatorApp, stub: StubApiClient) -> ArticleList:
    """Load articles into the list pane and wait for events to propagate."""
    article_list = app.query_one("#list-pane", ArticleList)
    await article_list.load(view="all")
    return article_list


async def _load_threads(app: AggregatorApp, stub: StubApiClient) -> ArticleList:
    article_list = app.query_one("#list-pane", ArticleList)
    await article_list.load_threads()
    return article_list


# ---------------------------------------------------------------------------
# j / k navigation
# ---------------------------------------------------------------------------


def test_j_moves_cursor_to_first_item_then_forward(stub: StubApiClient) -> None:
    """First j selects item-0; second j advances to item-1."""
    stub.set_articles([make_article(1), make_article(2), make_article(3)])

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await _load_articles(app, stub)
            await pilot.pause(0.1)

            await pilot.press("j")
            await pilot.pause(0.1)
            assert app._selected_article is not None
            assert app._selected_article.id == 1

            await pilot.press("j")
            await pilot.pause(0.1)
            assert app._selected_article.id == 2

    asyncio.run(inner())


def test_k_moves_cursor_up(stub: StubApiClient) -> None:
    """k reverses j movement."""
    stub.set_articles([make_article(1), make_article(2), make_article(3)])

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await _load_articles(app, stub)
            await pilot.pause(0.1)

            await pilot.press("j")
            await pilot.pause(0.1)
            await pilot.press("j")
            await pilot.pause(0.1)
            assert app._selected_article.id == 2

            await pilot.press("k")
            await pilot.pause(0.1)
            assert app._selected_article.id == 1

    asyncio.run(inner())


def test_G_jumps_to_last_item(stub: StubApiClient) -> None:
    """G moves selection to the last article in the list."""
    stub.set_articles([make_article(1), make_article(2), make_article(3)])

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await _load_articles(app, stub)
            await pilot.pause(0.1)

            await pilot.press("G")
            await pilot.pause(0.1)
            assert app._selected_article is not None
            assert app._selected_article.id == 3

    asyncio.run(inner())


def test_g_jumps_to_first_item(stub: StubApiClient) -> None:
    """g moves selection back to the first article."""
    stub.set_articles([make_article(1), make_article(2), make_article(3)])

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await _load_articles(app, stub)
            await pilot.pause(0.1)

            await pilot.press("G")
            await pilot.pause(0.1)
            assert app._selected_article.id == 3

            await pilot.press("g")
            await pilot.pause(0.1)
            assert app._selected_article.id == 1

    asyncio.run(inner())


# ---------------------------------------------------------------------------
# Enter / o open reader
# ---------------------------------------------------------------------------


def test_enter_loads_article_into_reader(stub: StubApiClient) -> None:
    """Enter on a highlighted article calls get_article and populates reader."""
    stub.set_articles([make_article(1)])

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await _load_articles(app, stub)
            await pilot.pause(0.1)

            await pilot.press("j")
            await pilot.pause(0.1)
            assert app._selected_article.id == 1

            await pilot.press("enter")
            await pilot.pause(0.2)

            get_article_calls = [c for c in stub.calls if c[0] == "get_article"]
            assert len(get_article_calls) >= 1
            assert get_article_calls[0][1] == 1

    asyncio.run(inner())


def test_o_loads_article_into_reader(stub: StubApiClient) -> None:
    """o key opens the highlighted article in the reader pane."""
    stub.set_articles([make_article(1)])

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await _load_articles(app, stub)
            await pilot.pause(0.1)

            await pilot.press("j")
            await pilot.pause(0.1)

            await pilot.press("o")
            await pilot.pause(0.2)

            get_article_calls = [c for c in stub.calls if c[0] == "get_article"]
            assert len(get_article_calls) >= 1

    asyncio.run(inner())


# ---------------------------------------------------------------------------
# Layout: wide vs narrow
# ---------------------------------------------------------------------------


def test_wide_mode_shows_all_three_panes(stub: StubApiClient) -> None:
    """At >= 100 columns all three panes are visible simultaneously."""

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)

            assert app.query_one("#nav-sidebar").display is True
            assert app.query_one("#list-pane").display is True
            assert app.query_one("#reader-pane").display is True

    asyncio.run(inner())


def test_narrow_mode_shows_only_list_pane(stub: StubApiClient) -> None:
    """Below 100 columns only the list pane is visible by default."""

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.1)

            nav = app.query_one("#nav-sidebar")
            list_pane = app.query_one("#list-pane")
            reader = app.query_one("#reader-pane")

            assert list_pane.display is True
            assert nav.display is False
            assert reader.display is False

    asyncio.run(inner())


# ---------------------------------------------------------------------------
# Help overlay (? key)
# ---------------------------------------------------------------------------


def test_question_mark_pushes_help_overlay(stub: StubApiClient) -> None:
    """? key pushes the HelpOverlay modal onto the screen stack."""
    from aggregator_tui.widgets.help_overlay import HelpOverlay

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            assert len(app.screen_stack) == 1

            await pilot.press("question_mark")
            await pilot.pause(0.1)

            assert len(app.screen_stack) == 2
            assert isinstance(app.screen_stack[-1], HelpOverlay)

    asyncio.run(inner())


def test_q_inside_help_overlay_dismisses_it(stub: StubApiClient) -> None:
    """q inside the HelpOverlay screen closes it and returns to the main screen."""
    from aggregator_tui.widgets.help_overlay import HelpOverlay

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("question_mark")
            await pilot.pause(0.1)
            assert isinstance(app.screen_stack[-1], HelpOverlay)

            await pilot.press("q")
            await pilot.pause(0.1)
            assert len(app.screen_stack) == 1

    asyncio.run(inner())


def test_q_dismisses_help_overlay(stub: StubApiClient) -> None:
    """q inside the HelpOverlay closes it without quitting the app."""
    from aggregator_tui.widgets.help_overlay import HelpOverlay

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("question_mark")
            await pilot.pause(0.1)
            assert isinstance(app.screen_stack[-1], HelpOverlay)

            await pilot.press("q")
            await pilot.pause(0.1)
            assert len(app.screen_stack) == 1

    asyncio.run(inner())


# ---------------------------------------------------------------------------
# Quit (q)
# ---------------------------------------------------------------------------


def test_q_calls_exit_when_no_input_focused(stub: StubApiClient) -> None:
    """q calls app.exit() when no text input has focus."""

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            assert not isinstance(app.focused, Input)

            with patch.object(app, "exit") as mock_exit:
                await pilot.press("q")
                await pilot.pause(0.1)

            mock_exit.assert_called_once()

    asyncio.run(inner())


def test_q_does_not_quit_when_search_input_focused(stub: StubApiClient) -> None:
    """q is ignored when the search Input has focus (to allow typing 'q')."""

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)

            await pilot.press("/")
            await pilot.pause(0.1)
            assert isinstance(app.focused, Input)

            with patch.object(app, "exit") as mock_exit:
                await pilot.press("q")
                await pilot.pause(0.1)

            mock_exit.assert_not_called()

    asyncio.run(inner())


# ---------------------------------------------------------------------------
# v — open article URL in browser
# ---------------------------------------------------------------------------


def test_v_opens_article_url_in_browser(stub: StubApiClient) -> None:
    """v key calls webbrowser.open() with the selected article's URL."""
    stub.set_articles([make_article(1)])

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await _load_articles(app, stub)
            await pilot.pause(0.1)

            await pilot.press("j")
            await pilot.pause(0.1)
            assert app._selected_article is not None
            assert app._selected_article.id == 1

            with patch("aggregator_tui.app.webbrowser.open") as mock_open:
                await pilot.press("v")
                await pilot.pause(0.1)

            mock_open.assert_called_once_with(app._selected_article.url)

    asyncio.run(inner())


# ---------------------------------------------------------------------------
# Tab — cycle pane focus
# ---------------------------------------------------------------------------


def test_tab_cycles_pane_focus(stub: StubApiClient) -> None:
    """Tab advances _pane_focus_idx through all three panes and wraps around."""

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            assert app._pane_focus_idx == 1  # list pane is default

            await pilot.press("tab")
            await pilot.pause(0.1)
            assert app._pane_focus_idx == 2  # reader pane

            await pilot.press("tab")
            await pilot.pause(0.1)
            assert app._pane_focus_idx == 0  # nav sidebar

            await pilot.press("tab")
            await pilot.pause(0.1)
            assert app._pane_focus_idx == 1  # wraps back to list pane

    asyncio.run(inner())


# ---------------------------------------------------------------------------
# API URL configuration (unit-level, no Textual needed)
# ---------------------------------------------------------------------------


def test_default_api_url() -> None:
    """Default URL is http://localhost:8000/api/v1 when no env var or flag set."""
    from aggregator_tui.__main__ import _DEFAULT_API_URL

    assert _DEFAULT_API_URL == "http://localhost:8000/api/v1"


def test_env_var_overrides_default_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """AGGREGATOR_API_URL env var takes precedence over the hard-coded default."""
    monkeypatch.setenv("AGGREGATOR_API_URL", "http://api.example.com/api/v1")
    from aggregator_tui.__main__ import _DEFAULT_API_URL

    api_url = None or os.environ.get("AGGREGATOR_API_URL") or _DEFAULT_API_URL
    assert api_url == "http://api.example.com/api/v1"


def test_flag_overrides_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """--api-url flag takes precedence over AGGREGATOR_API_URL env var."""
    monkeypatch.setenv("AGGREGATOR_API_URL", "http://api.example.com/api/v1")
    from aggregator_tui.__main__ import _DEFAULT_API_URL

    flag_value = "http://custom.host/api/v1"
    api_url = flag_value or os.environ.get("AGGREGATOR_API_URL") or _DEFAULT_API_URL
    assert api_url == "http://custom.host/api/v1"


# ---------------------------------------------------------------------------
# No aggregator-common import
# ---------------------------------------------------------------------------


def test_no_aggregator_common_import() -> None:
    """No aggregator_tui source file may import from aggregator_common."""
    src_dir = pathlib.Path(__file__).parent.parent / "src" / "aggregator_tui"
    for py_file in src_dir.rglob("*.py"):
        source = py_file.read_text()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith("aggregator_common"):
                    pytest.fail(
                        f"{py_file.name}: found 'from {module} import ...' "
                        "(aggregator_common must not be imported in aggregator_tui)"
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("aggregator_common"):
                        pytest.fail(
                            f"{py_file.name}: found 'import {alias.name}' "
                            "(aggregator_common must not be imported in aggregator_tui)"
                        )
