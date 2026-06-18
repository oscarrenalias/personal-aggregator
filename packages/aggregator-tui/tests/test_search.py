"""Tests for the search flow: / key, query execution, empty results, and errors."""
from __future__ import annotations

import asyncio

from aggregator_tui.api_client import ApiError
from aggregator_tui.app import AggregatorApp
from aggregator_tui.widgets.article_list import ArticleList
from textual.widgets import Input, Static

from .conftest import StubApiClient, make_article


# ---------------------------------------------------------------------------
# / activation
# ---------------------------------------------------------------------------


def test_slash_shows_and_focuses_search_input(stub: StubApiClient) -> None:
    """/ key makes the search input visible and transfers focus to it."""

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)

            search_input = app.query_one("#search-input", Input)
            assert search_input.display is False

            await pilot.press("/")
            await pilot.pause(0.1)

            assert search_input.display is True
            assert isinstance(app.focused, Input)

    asyncio.run(inner())


# ---------------------------------------------------------------------------
# Search execution
# ---------------------------------------------------------------------------


def test_enter_in_search_calls_search_articles(stub: StubApiClient) -> None:
    """Typing a query and pressing Enter calls search_articles with that query."""
    stub.set_search_results([make_article(1, title="Found Article")])

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)

            await pilot.press("/")
            await pilot.pause(0.1)

            await pilot.press("t", "e", "s", "t")
            await pilot.press("enter")
            await pilot.pause(0.2)

            search_calls = [c for c in stub.calls if c[0] == "search_articles"]
            assert len(search_calls) == 1
            assert search_calls[0][1]["q"] == "test"

    asyncio.run(inner())


def test_search_with_results_populates_list(stub: StubApiClient) -> None:
    """When search_articles returns articles they appear in the article list."""
    stub.set_search_results([make_article(1, title="Match"), make_article(2, title="Also Match")])

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)

            await pilot.press("/")
            await pilot.pause(0.1)
            await pilot.press("m", "a", "t", "c", "h")
            await pilot.press("enter")
            await pilot.pause(0.2)

            article_list = app.query_one("#list-pane", ArticleList)
            assert len(article_list._articles) == 2

    asyncio.run(inner())


# ---------------------------------------------------------------------------
# Empty results
# ---------------------------------------------------------------------------


def test_empty_search_shows_no_results_placeholder(stub: StubApiClient) -> None:
    """When search returns no items the 'No results' placeholder is visible."""
    stub.set_search_results([])

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)

            await pilot.press("/")
            await pilot.pause(0.1)
            await pilot.press("n", "o", "n", "e")
            await pilot.press("enter")
            await pilot.pause(0.2)

            placeholder = app.query_one("#no-results-placeholder", Static)
            assert placeholder.display is True

    asyncio.run(inner())


def test_empty_search_hides_list_view(stub: StubApiClient) -> None:
    """When search returns no items the article listview is hidden."""
    stub.set_search_results([])

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)

            await pilot.press("/")
            await pilot.pause(0.1)
            await pilot.press("q", "u", "e", "r", "y")
            await pilot.press("enter")
            await pilot.pause(0.2)

            from textual.widgets import ListView
            listview = app.query_one("#article-listview", ListView)
            assert listview.display is False

    asyncio.run(inner())


# ---------------------------------------------------------------------------
# Search error
# ---------------------------------------------------------------------------


def test_search_api_error_shows_in_status_bar(stub: StubApiClient) -> None:
    """An ApiError from search_articles is routed to the status bar (no crash)."""
    stub._search_error = ApiError("Search service unavailable", status_code=503)

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)

            await pilot.press("/")
            await pilot.pause(0.1)
            await pilot.press("e", "r", "r")
            await pilot.press("enter")
            await pilot.pause(0.2)

            status = str(app.query_one("#status-bar")._Static__content)
            assert "Search error" in status
            assert "Search service unavailable" in status

    asyncio.run(inner())


def test_search_api_error_does_not_crash_app(stub: StubApiClient) -> None:
    """The app remains usable after a search ApiError."""
    stub._search_error = ApiError("Network failure")

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)

            await pilot.press("/")
            await pilot.pause(0.1)
            await pilot.press("t", "e", "s", "t")
            await pilot.press("enter")
            await pilot.pause(0.2)

            # App is still running — can still query widgets
            assert app.query_one("#list-pane", ArticleList) is not None

    asyncio.run(inner())


# ---------------------------------------------------------------------------
# Escape deactivates search
# ---------------------------------------------------------------------------


def test_escape_hides_search_input(stub: StubApiClient) -> None:
    """Escape after / deactivates the search input and hides it."""

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)

            await pilot.press("/")
            await pilot.pause(0.1)
            assert app.query_one("#search-input", Input).display is True

            await pilot.press("escape")
            await pilot.pause(0.1)

            assert app.query_one("#search-input", Input).display is False

    asyncio.run(inner())


def test_escape_restores_list_focus_after_search(stub: StubApiClient) -> None:
    """After Escape the search mode flag is cleared."""

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)

            await pilot.press("/")
            await pilot.pause(0.1)
            assert app._in_search_mode is True

            await pilot.press("escape")
            await pilot.pause(0.1)

            assert app._in_search_mode is False

    asyncio.run(inner())
