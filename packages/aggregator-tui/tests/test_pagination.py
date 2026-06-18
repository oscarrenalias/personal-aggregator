"""Tests for cursor-based pagination in article and thread lists."""
from __future__ import annotations

import asyncio

from aggregator_tui.app import AggregatorApp
from aggregator_tui.widgets.article_list import ArticleList, ArticleRow, ThreadRow
from textual.widgets import ListView

from .conftest import StubApiClient, make_article, make_thread


# ---------------------------------------------------------------------------
# Articles — next page triggered at last item
# ---------------------------------------------------------------------------


def test_reaching_last_article_triggers_next_page_load(stub: StubApiClient) -> None:
    """Navigating to the last article item requests the next page via cursor."""
    page1 = [make_article(i) for i in range(1, 4)]
    page2 = [make_article(i) for i in range(4, 7)]
    stub.set_articles_pages([
        (page1, "cursor_page2"),
        (page2, None),
    ])

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await app.query_one("#list-pane", ArticleList).load(view="all")
            await pilot.pause(0.1)

            # G jumps to last item, triggering pagination
            await pilot.press("G")
            await pilot.pause(0.3)

            calls = [c for c in stub.calls if c[0] == "list_articles"]
            assert len(calls) == 2
            assert calls[1][1]["cursor"] == "cursor_page2"

    asyncio.run(inner())


def test_next_page_items_are_appended_not_replaced(stub: StubApiClient) -> None:
    """Items from the second page are appended; first-page items remain."""
    page1 = [make_article(i) for i in range(1, 4)]
    page2 = [make_article(i) for i in range(4, 7)]
    stub.set_articles_pages([
        (page1, "cursor_page2"),
        (page2, None),
    ])

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await app.query_one("#list-pane", ArticleList).load(view="all")
            await pilot.pause(0.1)

            lv = app.query_one("#article-listview", ListView)
            initial_count = len([c for c in lv.children if isinstance(c, ArticleRow)])
            assert initial_count == 3

            await pilot.press("G")
            await pilot.pause(0.3)

            final_count = len([c for c in lv.children if isinstance(c, ArticleRow)])
            assert final_count == 6

    asyncio.run(inner())


def test_no_pagination_call_when_next_cursor_is_null(stub: StubApiClient) -> None:
    """No extra list_articles call is made when next_cursor is None."""
    stub.set_articles([make_article(1), make_article(2)], next_cursor=None)

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await app.query_one("#list-pane", ArticleList).load(view="all")
            await pilot.pause(0.1)

            calls_before = len([c for c in stub.calls if c[0] == "list_articles"])
            assert calls_before == 1

            await pilot.press("G")
            await pilot.pause(0.3)

            calls_after = len([c for c in stub.calls if c[0] == "list_articles"])
            assert calls_after == 1  # no new call

    asyncio.run(inner())


# ---------------------------------------------------------------------------
# Threads — next page triggered at last item
# ---------------------------------------------------------------------------


def test_reaching_last_thread_triggers_next_page(stub: StubApiClient) -> None:
    """Navigating to the last thread row requests the next page via cursor."""
    page1 = [make_thread(i) for i in range(1, 4)]
    page2 = [make_thread(i) for i in range(4, 7)]
    stub.set_threads_pages([
        (page1, "thread_cursor2"),
        (page2, None),
    ])

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await app.query_one("#list-pane", ArticleList).load_threads()
            await pilot.pause(0.1)

            await pilot.press("G")
            await pilot.pause(0.3)

            calls = [c for c in stub.calls if c[0] == "list_threads"]
            assert len(calls) == 2
            assert calls[1][1]["cursor"] == "thread_cursor2"

    asyncio.run(inner())


def test_thread_next_page_items_are_appended(stub: StubApiClient) -> None:
    """Thread items from the second page are appended to the existing list."""
    page1 = [make_thread(i) for i in range(1, 4)]
    page2 = [make_thread(i) for i in range(4, 7)]
    stub.set_threads_pages([
        (page1, "thread_cursor2"),
        (page2, None),
    ])

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await app.query_one("#list-pane", ArticleList).load_threads()
            await pilot.pause(0.1)

            lv = app.query_one("#article-listview", ListView)
            assert len([c for c in lv.children if isinstance(c, ThreadRow)]) == 3

            await pilot.press("G")
            await pilot.pause(0.3)

            assert len([c for c in lv.children if isinstance(c, ThreadRow)]) == 6

    asyncio.run(inner())


def test_no_thread_pagination_when_next_cursor_null(stub: StubApiClient) -> None:
    """No extra list_threads call when next_cursor is None."""
    stub.set_threads([make_thread(1), make_thread(2)], next_cursor=None)

    async def inner() -> None:
        app = AggregatorApp(api_url="http://test")
        app.api_client = stub
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            await app.query_one("#list-pane", ArticleList).load_threads()
            await pilot.pause(0.1)

            calls_before = len([c for c in stub.calls if c[0] == "list_threads"])
            assert calls_before == 1

            await pilot.press("G")
            await pilot.pause(0.3)

            calls_after = len([c for c in stub.calls if c[0] == "list_threads"])
            assert calls_after == 1

    asyncio.run(inner())
