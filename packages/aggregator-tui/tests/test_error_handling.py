"""Tests for error-handling paths: ApiError in writes and reads routes to the status bar."""
from __future__ import annotations

import asyncio

from aggregator_tui.api_client import ApiError
from aggregator_tui.app import AggregatorApp
from aggregator_tui.widgets.article_list import ArticleList

from .conftest import StubApiClient, make_article, make_thread


def _status(app: AggregatorApp) -> str:
    return str(app.query_one("#status-bar")._Static__content)


# ---------------------------------------------------------------------------
# Write errors surface in the status bar
# ---------------------------------------------------------------------------


def test_mark_read_error_shows_in_status_bar(stub: StubApiClient) -> None:
    """ApiError from mark_read appears in the status bar."""
    article = make_article(1, is_read=False)
    stub.set_articles([article])
    stub._mark_read_error = ApiError("Server error", status_code=500)

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await app.query_one("#list-pane", ArticleList).load(view="all")
            await pilot.pause(0.1)

            await pilot.press("j")
            await pilot.pause(0.1)
            await pilot.press("m")
            await pilot.pause(0.1)

            status = _status(app)
            assert "Error" in status
            assert len(status.strip()) > 0

    asyncio.run(inner())


def test_save_error_shows_in_status_bar(stub: StubApiClient) -> None:
    """ApiError from save_article appears in the status bar."""
    article = make_article(1, is_saved=False)
    stub.set_articles([article])
    stub._save_error = ApiError("Save failed", status_code=503)

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await app.query_one("#list-pane", ArticleList).load(view="all")
            await pilot.pause(0.1)

            await pilot.press("j")
            await pilot.pause(0.1)
            await pilot.press("s")
            await pilot.pause(0.1)

            assert "Error" in _status(app)

    asyncio.run(inner())


def test_dismiss_thread_error_shows_in_status_bar(stub: StubApiClient) -> None:
    """ApiError from dismiss_thread appears in the status bar."""
    thread = make_thread(1, dismissed=False)
    stub.set_threads([thread])
    stub._dismiss_error = ApiError("Dismiss failed", status_code=500)

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await app.query_one("#list-pane", ArticleList).load_threads()
            await pilot.pause(0.1)

            app._current_nav_kind = "threads"
            await pilot.press("j")
            await pilot.pause(0.1)
            await pilot.press("d")
            await pilot.pause(0.1)

            assert "Error" in _status(app)

    asyncio.run(inner())


# ---------------------------------------------------------------------------
# Read errors surface in the status bar (no crash)
# ---------------------------------------------------------------------------


def test_get_article_api_error_routes_to_status_bar(stub: StubApiClient) -> None:
    """ApiError from get_article is caught in the reader pane and shown in status bar."""
    stub.set_articles([make_article(1)])

    # Override get_article to raise
    original_get_article = stub.get_article

    async def _failing_get(article_id: int):  # type: ignore[override]
        stub.calls.append(("get_article", article_id))
        raise ApiError("Article unavailable", status_code=404)

    stub.get_article = _failing_get  # type: ignore[method-assign]

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await app.query_one("#list-pane", ArticleList).load(view="all")
            await pilot.pause(0.1)
            app.query_one("#article-listview").focus()  # Enter acts on the focused list

            await pilot.press("j")
            await pilot.pause(0.1)
            await pilot.press("enter")
            await pilot.pause(0.2)

            status = _status(app)
            assert "Article unavailable" in status

    asyncio.run(inner())


def test_app_remains_usable_after_any_api_error(stub: StubApiClient) -> None:
    """After any ApiError, the app does not crash and widgets remain queryable."""
    stub.set_articles([make_article(1)])
    stub._mark_read_error = ApiError("Transient error")

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await app.query_one("#list-pane", ArticleList).load(view="all")
            await pilot.pause(0.1)

            await pilot.press("j")
            await pilot.pause(0.1)
            await pilot.press("m")
            await pilot.pause(0.1)

            # App is still alive — can query widgets and press keys
            assert app.query_one("#list-pane", ArticleList) is not None
            assert app.query_one("#status-bar") is not None

    asyncio.run(inner())


# ---------------------------------------------------------------------------
# notify_status / clear_status
# ---------------------------------------------------------------------------


def test_notify_status_updates_status_bar(stub: StubApiClient) -> None:
    """notify_status() writes a message to the status bar."""

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)

            app.notify_status("Hello from notify")
            await pilot.pause(0.05)

            assert "Hello from notify" in _status(app)

    asyncio.run(inner())


def test_clear_status_empties_status_bar(stub: StubApiClient) -> None:
    """clear_status() removes any existing message from the status bar."""

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)

            app.notify_status("Some message")
            await pilot.pause(0.05)
            assert len(_status(app).strip()) > 0

            app.clear_status()
            await pilot.pause(0.05)
            assert _status(app) == ""

    asyncio.run(inner())
