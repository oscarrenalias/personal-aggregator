"""Tests for article and thread action keys: m, n, s, d.

Covers optimistic UI update, ApiError revert, and d no-op outside threads view.
"""
from __future__ import annotations

import asyncio

from aggregator_tui.api_client import ApiError
from aggregator_tui.app import AggregatorApp
from aggregator_tui.widgets.article_list import ArticleList

from .conftest import StubApiClient, make_article, make_thread


# ---------------------------------------------------------------------------
# m — toggle read / unread
# ---------------------------------------------------------------------------


def test_m_marks_unread_article_as_read(stub: StubApiClient) -> None:
    """m calls mark_read and flips is_read True when article was unread."""
    article = make_article(1, is_read=False)
    stub.set_articles([article])

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

            await pilot.press("m")
            await pilot.pause(0.1)

            assert article.is_read is True
            assert ("mark_read", 1) in stub.calls

    asyncio.run(inner())


def test_m_marks_read_article_as_unread(stub: StubApiClient) -> None:
    """m calls mark_unread and flips is_read False when article was already read."""
    article = make_article(1, is_read=True)
    stub.set_articles([article])

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

            assert article.is_read is False
            assert ("mark_unread", 1) in stub.calls

    asyncio.run(inner())


def test_m_reverts_is_read_on_api_error(stub: StubApiClient) -> None:
    """When mark_read raises ApiError, is_read is reverted to False."""
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

            assert article.is_read is False  # reverted

    asyncio.run(inner())


def test_m_shows_error_in_status_bar_on_failure(stub: StubApiClient) -> None:
    """When mark_read raises ApiError, a message appears in the status bar."""
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

            status = app.query_one("#status-bar")._Static__content
            assert len(str(status).strip()) > 0
            assert "Error" in str(status)

    asyncio.run(inner())


# ---------------------------------------------------------------------------
# n — mark read and advance to next
# ---------------------------------------------------------------------------


def test_n_marks_read_and_moves_cursor_down(stub: StubApiClient) -> None:
    """n calls mark_read on the current article then advances the list cursor."""
    articles = [make_article(1, is_read=False), make_article(2, is_read=False)]
    stub.set_articles(articles)

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await app.query_one("#list-pane", ArticleList).load(view="all")
            await pilot.pause(0.1)

            await pilot.press("j")
            await pilot.pause(0.1)
            assert app._selected_article.id == 1

            await pilot.press("n")
            await pilot.pause(0.1)

            assert ("mark_read", 1) in stub.calls
            assert app._selected_article.id == 2

    asyncio.run(inner())


def test_n_on_already_read_article_still_advances(stub: StubApiClient) -> None:
    """n advances the cursor even when the article is already read (no extra API call)."""
    articles = [make_article(1, is_read=True), make_article(2, is_read=False)]
    stub.set_articles(articles)

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await app.query_one("#list-pane", ArticleList).load(view="all")
            await pilot.pause(0.1)

            await pilot.press("j")
            await pilot.pause(0.1)
            assert app._selected_article.id == 1

            await pilot.press("n")
            await pilot.pause(0.1)

            mark_read_calls = [c for c in stub.calls if c[0] == "mark_read"]
            assert len(mark_read_calls) == 0  # already read — no API call
            assert app._selected_article.id == 2

    asyncio.run(inner())


def test_n_loads_next_article_into_reader(stub: StubApiClient) -> None:
    """n advances AND loads the next article into the reader pane (reading flow)."""
    articles = [make_article(1, is_read=False), make_article(2, is_read=False)]
    stub.set_articles(articles)

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await app.query_one("#list-pane", ArticleList).load(view="all")
            await pilot.pause(0.1)

            await pilot.press("j")
            await pilot.pause(0.1)
            assert app._selected_article.id == 1

            await pilot.press("n")
            await pilot.pause(0.1)

            # The next article (2) was fetched into the reader pane.
            assert ("get_article", 2) in stub.calls
            assert app._selected_article.id == 2

    asyncio.run(inner())


# ---------------------------------------------------------------------------
# s — toggle save / unsave
# ---------------------------------------------------------------------------


def test_s_saves_unsaved_article(stub: StubApiClient) -> None:
    """s calls save_article and flips is_saved True."""
    article = make_article(1, is_saved=False)
    stub.set_articles([article])

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

            assert article.is_saved is True
            assert ("save_article", 1) in stub.calls

    asyncio.run(inner())


def test_s_unsaves_saved_article(stub: StubApiClient) -> None:
    """s calls unsave_article and flips is_saved False."""
    article = make_article(1, is_saved=True)
    stub.set_articles([article])

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

            assert article.is_saved is False
            assert ("unsave_article", 1) in stub.calls

    asyncio.run(inner())


def test_s_reverts_is_saved_on_api_error(stub: StubApiClient) -> None:
    """When save_article raises ApiError, is_saved is reverted."""
    article = make_article(1, is_saved=False)
    stub.set_articles([article])
    stub._save_error = ApiError("Save failed", status_code=500)

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

            assert article.is_saved is False  # reverted

    asyncio.run(inner())


# ---------------------------------------------------------------------------
# d — dismiss / restore thread (threads view only)
# ---------------------------------------------------------------------------


def test_d_dismisses_thread_in_threads_view(stub: StubApiClient) -> None:
    """d calls dismiss_thread and flips dismissed True when in threads view."""
    thread = make_thread(1, dismissed=False)
    stub.set_threads([thread])

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
            assert app._selected_thread is not None

            await pilot.press("d")
            await pilot.pause(0.1)

            assert thread.dismissed is True
            assert ("dismiss_thread", 1) in stub.calls

    asyncio.run(inner())


def test_d_restores_dismissed_thread(stub: StubApiClient) -> None:
    """d calls restore_thread and flips dismissed False when thread was dismissed."""
    thread = make_thread(1, dismissed=True)
    stub.set_threads([thread])

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

            assert thread.dismissed is False
            assert ("restore_thread", 1) in stub.calls

    asyncio.run(inner())


def test_d_is_no_op_outside_threads_view(stub: StubApiClient) -> None:
    """d does nothing when the current nav view is not 'threads'."""
    article = make_article(1)
    stub.set_articles([article])

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await app.query_one("#list-pane", ArticleList).load(view="all")
            await pilot.pause(0.1)

            app._current_nav_kind = "smart"

            await pilot.press("j")
            await pilot.pause(0.1)
            await pilot.press("d")
            await pilot.pause(0.1)

            assert ("dismiss_thread",) not in [
                (c[0],) for c in stub.calls if c[0] in ("dismiss_thread", "restore_thread")
            ]

    asyncio.run(inner())


def test_d_reverts_dismissed_on_api_error(stub: StubApiClient) -> None:
    """When dismiss_thread raises ApiError, dismissed is reverted and status bar shows error."""
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

            assert thread.dismissed is False  # reverted
            status = str(app.query_one("#status-bar")._Static__content)
            assert len(status.strip()) > 0

    asyncio.run(inner())


# ---------------------------------------------------------------------------
# Thread member articles are openable from the reader
# ---------------------------------------------------------------------------


def test_open_member_article_loads_it_into_reader(stub: StubApiClient) -> None:
    """Clicking a thread member title (action_open_member_article) opens that
    article in the reader pane — enabling a jump from a thread to one of its sources."""

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            app.action_open_member_article(42)
            await pilot.pause(0.1)
            assert ("get_article", 42) in stub.calls

    asyncio.run(inner())
