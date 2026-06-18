from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ArticleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: Optional[str]
    url: Optional[str]
    source_id: int
    source_name: Optional[str]
    feed_published_at: Optional[str]
    summary: Optional[str]
    excerpt: Optional[str]
    clean_text: Optional[str]
    importance_score: Optional[int]
    importance_reason: Optional[str]
    categories: Optional[List[Any]]
    topics: Optional[List[Any]]
    is_read: bool
    is_saved: bool
    author: Optional[str]
    word_count: Optional[int]
    language: Optional[str]


class SourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    feed_url: str


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str]
    sort_order: int


class BriefTopicResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    headline: str
    what_happened: str
    why_it_matters: str
    historical_context: Optional[str]
    refs: List[Any]


class ThreadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    representative_title: str
    rolling_summary: Optional[str]
    known_facts: Optional[List[Any]]
    first_seen: str
    last_updated: str
    status: str
    tier: Optional[str]
    tier_reason: Optional[str]
    relevance_score: Optional[float]
    novelty_score: Optional[float]
    importance_score: Optional[float]
    diversity_score: Optional[float]
    time_sensitivity_score: Optional[float]
    source_diversity: Optional[float]
    confidence: Optional[float]
    novelty_label: Optional[str]
    deltas: Optional[List[Any]]
    source_list: Optional[List[Any]]
    top_grade: Optional[int] = None
    surfaced: bool = False
    dismissed: bool = False
    source_count: int = 0
    member_count: int = 0
    image_url: Optional[str] = None
    has_updates: bool = True


class ThreadMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    thread_id: int
    article_id: int
    classification_label: Optional[str]
    new_facts: Optional[List[Any]]
    reason: Optional[str]
    confidence: Optional[float]
    suppressed: bool
    assigned_at: str
    clean_title: Optional[str]
    url: Optional[str]
    source_name: Optional[str] = None
    published_at: Optional[str] = None


class BriefResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    headline: Optional[str]
    intro: Optional[str]
    generated_at: Optional[str]
    period_start: str
    period_end: str
    model: Optional[str]
    topics: List[BriefTopicResponse]


class InterestProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    profile_text: str
    updated_at: Optional[datetime] = None


class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    next_cursor: Optional[str] = None
