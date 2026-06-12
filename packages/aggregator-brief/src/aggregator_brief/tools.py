from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from aggregator_common.models import Article
from aggregator_brief.schema import BriefSubmitSchema


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _isoformat(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def search_articles(
    session: Session,
    query: str,
    since: str | None = None,
    until: str | None = None,
    categories: list[str] | None = None,
    limit: int = 20,
) -> list[dict]:
    effective_date = func.coalesce(Article.published_at, Article.feed_published_at)

    stmt = (
        select(Article)
        .where(Article.status == "ready")
        .where(Article.search_vector.op("@@")(func.websearch_to_tsquery("english", query)))
    )

    if since is not None:
        stmt = stmt.where(effective_date >= _parse_dt(since))
    if until is not None:
        stmt = stmt.where(effective_date <= _parse_dt(until))
    if categories:
        stmt = stmt.where(
            or_(
                *(
                    Article.categories.op("@>")(literal([cat], JSONB))
                    for cat in categories
                )
            )
        )

    stmt = stmt.order_by(
        Article.importance_score.desc().nulls_last(),
        effective_date.desc().nulls_last(),
        Article.id.desc(),
    ).limit(limit)

    rows = session.execute(stmt).scalars().all()
    return [
        {
            "id": a.id,
            "title": a.clean_title or a.feed_title,
            "summary": a.summary,
            "published_at": _isoformat(a.published_at or a.feed_published_at),
            "url": (a.raw_payload or {}).get("link"),
            "categories": a.categories or [],
        }
        for a in rows
    ]


def get_article(session: Session, article_id: int) -> dict:
    article = session.execute(
        select(Article)
        .where(Article.id == article_id)
        .where(Article.status == "ready")
    ).scalar_one_or_none()

    if article is None:
        return {"error": f"Article {article_id} not found or not ready"}

    raw = article.raw_payload or {}
    return {
        "id": article.id,
        "title": article.clean_title or article.feed_title,
        "summary": article.summary,
        "excerpt": article.excerpt,
        "published_at": _isoformat(article.published_at or article.feed_published_at),
        "url": raw.get("link"),
        "categories": article.categories or [],
        "topics": article.topics or [],
        "importance_score": article.importance_score,
        "importance_reason": article.importance_reason,
        "author": article.author,
        "source": article.feed_title,
    }


TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_articles",
            "description": (
                "Full-text search over all ready articles in the historical database. "
                "Use this to look up background context and continuity for topics. "
                "Supports websearch query syntax (quoted phrases, +/- operators, OR, etc.)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Websearch-style query string (e.g. 'AI regulation' or '\"interest rates\" fed').",
                    },
                    "since": {
                        "type": "string",
                        "format": "date-time",
                        "description": "ISO-8601 datetime lower bound (inclusive) on article published date.",
                    },
                    "until": {
                        "type": "string",
                        "format": "date-time",
                        "description": "ISO-8601 datetime upper bound (inclusive) on article published date.",
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Restrict results to articles matching any of these category names.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results to return (default 20).",
                        "default": 20,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_article",
            "description": (
                "Retrieve full metadata and content excerpt for a single ready article by id. "
                "Use after search_articles when you need more detail about a specific article."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "article_id": {
                        "type": "integer",
                        "description": "The id of the article to retrieve.",
                    },
                },
                "required": ["article_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_brief",
            "description": (
                "Terminal tool — emit the completed daily brief. "
                "Call this exactly once when you have gathered enough context to produce the full brief. "
                "Calling this ends the generation loop."
            ),
            "parameters": BriefSubmitSchema.model_json_schema(),
        },
    },
]


def dispatch_tool(session: Session, tool_name: str, tool_args: dict[str, Any]) -> list[dict] | dict | str:
    if tool_name == "search_articles":
        return search_articles(session, **tool_args)
    if tool_name == "get_article":
        return get_article(session, **tool_args)
    if tool_name == "submit_brief":
        return tool_args
    raise ValueError(f"Unknown tool: {tool_name!r}")
