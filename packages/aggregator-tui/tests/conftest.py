"""Shared test fixtures and helpers for aggregator-tui tests.

The StubApiClient replaces real HTTP calls; configure responses via
set_articles(), set_threads(), set_search_results(), and error triggers
(_mark_read_error, _search_error, etc.). All API methods track calls in
the .calls list as (method_name, kwarg_dict) tuples.
"""
from __future__ import annotations

from typing import Optional

import pytest

from aggregator_tui.api_client import (
    ApiError,
    ArticleResponse,
    PaginatedResponse,
    ThreadResponse,
)


# ---------------------------------------------------------------------------
# Factory helpers (also exposed as fixtures below)
# ---------------------------------------------------------------------------


def make_article(
    id: int = 1,
    is_read: bool = False,
    is_saved: bool = False,
    title: str = "Test Article",
    source_name: str = "Test Source",
    url: Optional[str] = None,
) -> ArticleResponse:
    return ArticleResponse(
        id=id,
        source_id=1,
        is_read=is_read,
        is_saved=is_saved,
        title=title,
        url=url or f"http://example.com/article/{id}",
        source_name=source_name,
        feed_published_at="2024-01-15T00:00:00Z",
    )


def make_thread(
    id: int = 1,
    dismissed: bool = False,
    has_updates: bool = False,
    title: str = "Test Thread",
) -> ThreadResponse:
    return ThreadResponse(
        id=id,
        representative_title=title,
        first_seen="2024-01-15T00:00:00Z",
        last_updated="2024-01-15T00:00:00Z",
        status="ready",
        surfaced=True,
        dismissed=dismissed,
        source_count=2,
        member_count=3,
        has_updates=has_updates,
    )


# ---------------------------------------------------------------------------
# Stub ApiClient
# ---------------------------------------------------------------------------


class StubApiClient:
    """Drop-in ApiClient with no real HTTP calls.

    Tracks all calls in .calls as (method_name, kwargs_dict) tuples.
    Configure paginated responses via set_articles_pages() / set_threads_pages();
    the single-page helpers set_articles() / set_threads() are wrappers.
    Set error triggers (_mark_read_error etc.) to raise ApiError on specific calls.
    """

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        # articles: list of PaginatedResponse, returned in order (last is repeated)
        self._articles_pages: list[PaginatedResponse] = [
            PaginatedResponse(items=[], next_cursor=None)
        ]
        self._articles_idx: int = 0
        # threads
        self._threads_pages: list[PaginatedResponse] = [
            PaginatedResponse(items=[], next_cursor=None)
        ]
        self._threads_idx: int = 0
        # search
        self._search_pages: list[PaginatedResponse] = [
            PaginatedResponse(items=[], next_cursor=None)
        ]
        self._search_idx: int = 0
        # per-call error triggers
        self._mark_read_error: Optional[ApiError] = None
        self._mark_unread_error: Optional[ApiError] = None
        self._save_error: Optional[ApiError] = None
        self._unsave_error: Optional[ApiError] = None
        self._dismiss_error: Optional[ApiError] = None
        self._restore_error: Optional[ApiError] = None
        self._search_error: Optional[ApiError] = None

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    def set_articles(
        self,
        articles: list[ArticleResponse],
        next_cursor: Optional[str] = None,
    ) -> None:
        self._articles_pages = [PaginatedResponse(items=articles, next_cursor=next_cursor)]
        self._articles_idx = 0

    def set_articles_pages(
        self, pages: list[tuple[list[ArticleResponse], Optional[str]]]
    ) -> None:
        self._articles_pages = [
            PaginatedResponse(items=items, next_cursor=cursor) for items, cursor in pages
        ]
        self._articles_idx = 0

    def set_threads(
        self,
        threads: list[ThreadResponse],
        next_cursor: Optional[str] = None,
    ) -> None:
        self._threads_pages = [PaginatedResponse(items=threads, next_cursor=next_cursor)]
        self._threads_idx = 0

    def set_threads_pages(
        self, pages: list[tuple[list[ThreadResponse], Optional[str]]]
    ) -> None:
        self._threads_pages = [
            PaginatedResponse(items=items, next_cursor=cursor) for items, cursor in pages
        ]
        self._threads_idx = 0

    def set_search_results(
        self,
        articles: list[ArticleResponse],
        next_cursor: Optional[str] = None,
    ) -> None:
        self._search_pages = [PaginatedResponse(items=articles, next_cursor=next_cursor)]
        self._search_idx = 0

    # ------------------------------------------------------------------
    # API methods
    # ------------------------------------------------------------------

    async def list_articles(
        self,
        view: str = "all",
        category: Optional[str] = None,
        source_id: Optional[int] = None,
        unread_only: bool = False,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> PaginatedResponse:
        self.calls.append(("list_articles", {"view": view, "cursor": cursor}))
        page = self._articles_pages[min(self._articles_idx, len(self._articles_pages) - 1)]
        self._articles_idx += 1
        return page

    async def list_threads(
        self,
        sort: str = "importance",
        show_dismissed: bool = False,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> PaginatedResponse:
        self.calls.append(("list_threads", {"sort": sort, "cursor": cursor}))
        page = self._threads_pages[min(self._threads_idx, len(self._threads_pages) - 1)]
        self._threads_idx += 1
        return page

    async def search_articles(
        self,
        q: str,
        category: Optional[str] = None,
        source_id: Optional[int] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> PaginatedResponse:
        self.calls.append(("search_articles", {"q": q, "cursor": cursor}))
        if self._search_error:
            raise self._search_error
        page = self._search_pages[min(self._search_idx, len(self._search_pages) - 1)]
        self._search_idx += 1
        return page

    async def list_sources(self) -> list:
        return []

    async def list_categories(self) -> list:
        return []

    async def mark_read(self, article_id: int) -> bool:
        self.calls.append(("mark_read", article_id))
        if self._mark_read_error:
            raise self._mark_read_error
        return True

    async def mark_unread(self, article_id: int) -> bool:
        self.calls.append(("mark_unread", article_id))
        if self._mark_unread_error:
            raise self._mark_unread_error
        return True

    async def save_article(self, article_id: int) -> bool:
        self.calls.append(("save_article", article_id))
        if self._save_error:
            raise self._save_error
        return True

    async def unsave_article(self, article_id: int) -> bool:
        self.calls.append(("unsave_article", article_id))
        if self._unsave_error:
            raise self._unsave_error
        return True

    async def dismiss_thread(self, thread_id: int) -> bool:
        self.calls.append(("dismiss_thread", thread_id))
        if self._dismiss_error:
            raise self._dismiss_error
        return True

    async def restore_thread(self, thread_id: int) -> bool:
        self.calls.append(("restore_thread", thread_id))
        if self._restore_error:
            raise self._restore_error
        return True

    async def get_article(self, article_id: int) -> ArticleResponse:
        self.calls.append(("get_article", article_id))
        return ArticleResponse(
            id=article_id,
            source_id=1,
            is_read=False,
            is_saved=False,
            title=f"Article {article_id}",
            summary="Test summary.",
        )

    async def get_thread(self, thread_id: int) -> ThreadResponse:
        self.calls.append(("get_thread", thread_id))
        return ThreadResponse(
            id=thread_id,
            representative_title=f"Thread {thread_id}",
            first_seen="2024-01-15T00:00:00Z",
            last_updated="2024-01-15T00:00:00Z",
            status="ready",
            surfaced=True,
            dismissed=False,
            source_count=1,
            member_count=1,
            has_updates=False,
        )

    async def get_thread_members(self, thread_id: int) -> PaginatedResponse:
        self.calls.append(("get_thread_members", thread_id))
        return PaginatedResponse(items=[], next_cursor=None)

    async def aclose(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stub() -> StubApiClient:
    return StubApiClient()


@pytest.fixture
def make_article_fn():
    """Return the make_article factory so tests can create ArticleResponse objects."""
    return make_article


@pytest.fixture
def make_thread_fn():
    """Return the make_thread factory so tests can create ThreadResponse objects."""
    return make_thread
