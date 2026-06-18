from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Generic, List, Optional, TypeVar

import httpx


class ApiError(Exception):
    """Raised on network failure or non-2xx HTTP response from the aggregator API."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


T = TypeVar("T")


@dataclass
class PaginatedResponse(Generic[T]):
    items: List[T]
    next_cursor: Optional[str]


@dataclass
class ArticleResponse:
    id: int
    source_id: int
    is_read: bool
    is_saved: bool
    title: Optional[str] = None
    url: Optional[str] = None
    source_name: Optional[str] = None
    feed_published_at: Optional[str] = None
    summary: Optional[str] = None
    excerpt: Optional[str] = None
    clean_text: Optional[str] = None
    importance_score: Optional[int] = None
    importance_reason: Optional[str] = None
    categories: Optional[List[Any]] = None
    topics: Optional[Dict[str, Any]] = None
    author: Optional[str] = None
    word_count: Optional[int] = None
    language: Optional[str] = None


@dataclass
class SourceResponse:
    id: int
    name: str
    feed_url: str


@dataclass
class CategoryResponse:
    id: int
    name: str
    sort_order: int
    description: Optional[str] = None


@dataclass
class BriefTopicResponse:
    position: int
    headline: str
    what_happened: str
    why_it_matters: str
    refs: List[Any]
    historical_context: Optional[str] = None


@dataclass
class BriefResponse:
    id: int
    period_start: str
    period_end: str
    topics: List[BriefTopicResponse]
    headline: Optional[str] = None
    intro: Optional[str] = None
    generated_at: Optional[str] = None
    model: Optional[str] = None


@dataclass
class ThreadResponse:
    id: int
    representative_title: str
    first_seen: str
    last_updated: str
    status: str
    surfaced: bool
    dismissed: bool
    source_count: int
    member_count: int
    has_updates: bool
    rolling_summary: Optional[str] = None
    known_facts: Optional[List[Any]] = None
    tier: Optional[str] = None
    tier_reason: Optional[str] = None
    relevance_score: Optional[float] = None
    novelty_score: Optional[float] = None
    importance_score: Optional[float] = None
    diversity_score: Optional[float] = None
    time_sensitivity_score: Optional[float] = None
    source_diversity: Optional[float] = None
    confidence: Optional[float] = None
    novelty_label: Optional[str] = None
    deltas: Optional[List[Any]] = None
    source_list: Optional[List[Any]] = None
    top_grade: Optional[int] = None
    image_url: Optional[str] = None


@dataclass
class ThreadMemberResponse:
    id: int
    thread_id: int
    article_id: int
    suppressed: bool
    assigned_at: str
    classification_label: Optional[str] = None
    new_facts: Optional[List[Any]] = None
    reason: Optional[str] = None
    confidence: Optional[float] = None
    clean_title: Optional[str] = None
    url: Optional[str] = None
    source_name: Optional[str] = None
    published_at: Optional[str] = None


class ApiClient:
    """Async HTTP client for the aggregator JSON API."""

    def __init__(self, base_url: str, headers: Optional[Dict[str, str]] = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers or None,
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0),
        )

    async def __aenter__(self) -> ApiClient:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        await self._client.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str) -> bool:
        try:
            response = await self._client.post(path)
        except httpx.HTTPError as exc:
            raise ApiError(f"Network error: {exc}") from exc
        if not response.is_success:
            raise ApiError(
                f"API error {response.status_code}: {response.text}",
                status_code=response.status_code,
            )
        return True

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        filtered = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            response = await self._client.get(path, params=filtered)
        except httpx.HTTPError as exc:
            raise ApiError(f"Network error: {exc}") from exc
        if not response.is_success:
            raise ApiError(
                f"API error {response.status_code}: {response.text}",
                status_code=response.status_code,
            )
        return response.json()

    async def list_articles(
        self,
        view: str = "all",
        category: Optional[str] = None,
        source_id: Optional[int] = None,
        unread_only: bool = False,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> PaginatedResponse[ArticleResponse]:
        data = await self._get(
            "/articles",
            params={
                "view": view,
                "category": category,
                "source_id": source_id,
                "unread_only": unread_only or None,
                "limit": limit,
                "cursor": cursor,
            },
        )
        return PaginatedResponse(
            items=[ArticleResponse(**item) for item in data["items"]],
            next_cursor=data.get("next_cursor"),
        )

    async def search_articles(
        self,
        q: str,
        category: Optional[str] = None,
        source_id: Optional[int] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> PaginatedResponse[ArticleResponse]:
        data = await self._get(
            "/articles/search",
            params={
                "q": q,
                "category": category,
                "source_id": source_id,
                "limit": limit,
                "cursor": cursor,
            },
        )
        return PaginatedResponse(
            items=[ArticleResponse(**item) for item in data["items"]],
            next_cursor=data.get("next_cursor"),
        )

    async def get_article(self, article_id: int) -> ArticleResponse:
        data = await self._get(f"/articles/{article_id}")
        return ArticleResponse(**data)

    async def list_threads(
        self,
        sort: str = "importance",
        show_dismissed: bool = False,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> PaginatedResponse[ThreadResponse]:
        data = await self._get(
            "/threads",
            params={
                "sort": sort,
                "show_dismissed": show_dismissed or None,
                "limit": limit,
                "cursor": cursor,
            },
        )
        return PaginatedResponse(
            items=[ThreadResponse(**item) for item in data["items"]],
            next_cursor=data.get("next_cursor"),
        )

    async def get_thread(self, thread_id: int) -> ThreadResponse:
        data = await self._get(f"/threads/{thread_id}")
        return ThreadResponse(**data)

    async def get_thread_members(self, thread_id: int) -> PaginatedResponse[ThreadMemberResponse]:
        data = await self._get(f"/threads/{thread_id}/members")
        return PaginatedResponse(
            items=[ThreadMemberResponse(**item) for item in data["items"]],
            next_cursor=data.get("next_cursor"),
        )

    async def get_brief_today(self) -> BriefResponse:
        data = await self._get("/brief/today")
        topics = [BriefTopicResponse(**t) for t in data.get("topics", [])]
        return BriefResponse(**{**data, "topics": topics})

    async def list_sources(self) -> List[SourceResponse]:
        data = await self._get("/sources")
        return [SourceResponse(**item) for item in data]

    async def list_categories(self) -> List[CategoryResponse]:
        data = await self._get("/categories")
        return [CategoryResponse(**item) for item in data]

    async def mark_read(self, article_id: int) -> bool:
        return await self._post(f"/articles/{article_id}/read")

    async def mark_unread(self, article_id: int) -> bool:
        return await self._post(f"/articles/{article_id}/unread")

    async def save_article(self, article_id: int) -> bool:
        return await self._post(f"/articles/{article_id}/save")

    async def unsave_article(self, article_id: int) -> bool:
        return await self._post(f"/articles/{article_id}/unsave")

    async def dismiss_thread(self, thread_id: int) -> bool:
        return await self._post(f"/threads/{thread_id}/dismiss")

    async def restore_thread(self, thread_id: int) -> bool:
        return await self._post(f"/threads/{thread_id}/restore")
