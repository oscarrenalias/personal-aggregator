from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Optional

from mcp.server.fastmcp import FastMCP

import aggregator_common.management as management
import aggregator_common.queries as queries
from aggregator_common.db import get_session
from aggregator_common.errors import ConflictError, NotFoundError
from aggregator_mcp.config import McpSettings

# Evaluated at import time, which is after load_env() per __main__.py import ordering.
_settings = McpSettings()

mcp = FastMCP("aggregator-mcp")


@mcp.tool()
def search_articles(
    query: str,
    limit: int = _settings.mcp_default_limit,
    since: Optional[str] = None,
    category: Optional[str] = None,
    source_id: Optional[int] = None,
) -> list:
    limit = min(limit, _settings.mcp_max_limit)
    since_dt = datetime.fromisoformat(since) if since is not None else None
    with get_session() as session:
        results = queries.search_articles(
            session, query, limit=limit, since=since_dt, category=category, source_id=source_id
        )
    return [asdict(r) for r in results]


@mcp.tool()
def list_articles(
    view: str = "unread",
    category: Optional[str] = None,
    source_id: Optional[int] = None,
    unread_only: bool = False,
    limit: int = _settings.mcp_default_limit,
) -> list:
    limit = min(limit, _settings.mcp_max_limit)
    with get_session() as session:
        results = queries.list_articles(
            session, view, category=category, source_id=source_id, unread_only=unread_only, limit=limit
        )
    return [asdict(r) for r in results]


@mcp.tool()
def get_article(article_id: int) -> dict:
    with get_session() as session:
        result = queries.get_article(session, article_id)
    return asdict(result)


@mcp.tool()
def get_interest_profile() -> str:
    with get_session() as session:
        return queries.get_interest_profile(session)


@mcp.tool()
def set_interest_profile(text: str) -> dict:
    """Update the user's interest profile text used by the ranker.

    Replaces the singleton interest profile with the provided free-text description
    of the user's topics, sources, and priorities. The new profile takes effect on
    the next summarize-rank cycle. Returns the saved profile fields on success, or
    an error dict with 'error' and 'detail' keys on failure.
    """
    try:
        with get_session() as session:
            return management.set_interest_profile(session, text)
    except NotFoundError as exc:
        return {"error": "not_found", "detail": str(exc)}
    except ConflictError as exc:
        return {"error": "conflict", "detail": str(exc)}


@mcp.tool()
def list_categories() -> list:
    with get_session() as session:
        results = queries.list_categories(session)
    return [asdict(r) for r in results]


@mcp.tool()
def list_sources() -> list:
    with get_session() as session:
        results = queries.list_sources(session)
    return [asdict(r) for r in results]


@mcp.tool()
def mark_read(article_id: int) -> dict:
    with get_session() as session:
        return queries.mark_read(session, article_id)


@mcp.tool()
def mark_unread(article_id: int) -> dict:
    with get_session() as session:
        return queries.mark_unread(session, article_id)


@mcp.tool()
def save_article(article_id: int) -> dict:
    with get_session() as session:
        return queries.save_article(session, article_id)


@mcp.tool()
def unsave_article(article_id: int) -> dict:
    with get_session() as session:
        return queries.unsave_article(session, article_id)


@mcp.resource("article://{id}")
def article_resource(id: int) -> dict:
    with get_session() as session:
        result = queries.get_article(session, id)
    return asdict(result)


@mcp.resource("feed://{view}")
def feed_resource(view: str) -> list:
    with get_session() as session:
        results = queries.list_articles(session, view)
    return [asdict(r) for r in results]


@mcp.resource("profile://interests")
def profile_resource() -> str:
    with get_session() as session:
        return queries.get_interest_profile(session)


@mcp.prompt()
def whats_latest(topic: str) -> str:
    return (
        f"Search for recent articles about '{topic}' using the search_articles tool. "
        f"Summarize the top results with their titles, key points, and links."
    )


@mcp.tool()
def get_daily_brief() -> dict:
    with get_session() as session:
        result = queries.get_latest_brief(session)
    if result is None:
        return {"status": "no_brief"}
    return asdict(result)


@mcp.tool()
def refresh_brief() -> dict:
    with get_session() as session:
        return queries.enqueue_brief(session)


@mcp.resource("brief://today")
def brief_today_resource() -> dict:
    with get_session() as session:
        result = queries.get_latest_brief(session)
    if result is None:
        return {"status": "no_brief"}
    return asdict(result)


@mcp.prompt()
def daily_brief() -> str:
    return (
        "Fetch the daily brief using the brief://today resource or the get_daily_brief tool. "
        "Present the headline and intro paragraph, then for each topic present: "
        "what happened, why it matters, and any historical context. "
        "Include article links from refs where available."
    )


@mcp.tool()
def add_source(
    name: str,
    feed_url: str,
    refresh_interval_seconds: Optional[int] = None,
    priority: Optional[int] = None,
    enabled: bool = True,
) -> dict:
    """Add a new RSS/Atom source to the aggregator.

    Returns the created source fields on success, or an error dict with 'error'
    and 'detail' keys when feed_url already exists (conflict).
    """
    try:
        with get_session() as session:
            return management.add_source(
                session,
                name,
                feed_url,
                refresh_interval_seconds=refresh_interval_seconds,
                priority=priority,
                enabled=enabled,
            )
    except NotFoundError as exc:
        return {"error": "not_found", "detail": str(exc)}
    except ConflictError as exc:
        return {"error": "conflict", "detail": str(exc)}


@mcp.tool()
def enable_source(source_id: int) -> dict:
    """Enable a source and schedule it for immediate retrieval on the next poll cycle.

    Returns the updated source fields on success, or an error dict with 'error'
    and 'detail' keys when source_id is not found.
    """
    try:
        with get_session() as session:
            return management.enable_source(session, source_id)
    except NotFoundError as exc:
        return {"error": "not_found", "detail": str(exc)}
    except ConflictError as exc:
        return {"error": "conflict", "detail": str(exc)}


@mcp.tool()
def disable_source(source_id: int) -> dict:
    """Disable a source so the retriever skips it on future poll cycles.

    Returns the updated source fields on success, or an error dict with 'error'
    and 'detail' keys when source_id is not found.
    """
    try:
        with get_session() as session:
            return management.disable_source(session, source_id)
    except NotFoundError as exc:
        return {"error": "not_found", "detail": str(exc)}
    except ConflictError as exc:
        return {"error": "conflict", "detail": str(exc)}


@mcp.tool()
def set_source_interval(source_id: int, seconds: int) -> dict:
    """Update the polling interval for a source.

    Returns the updated source fields on success, or an error dict with 'error'
    and 'detail' keys when source_id is not found.
    """
    try:
        with get_session() as session:
            return management.set_source_interval(session, source_id, seconds)
    except NotFoundError as exc:
        return {"error": "not_found", "detail": str(exc)}
    except ConflictError as exc:
        return {"error": "conflict", "detail": str(exc)}


@mcp.tool()
def refresh_source_now(source_id: int) -> dict:
    """Force a source to be polled on the next retriever cycle by resetting its schedule.

    Returns the updated source fields on success, or an error dict with 'error'
    and 'detail' keys when source_id is not found.
    """
    try:
        with get_session() as session:
            return management.refresh_source_now(session, source_id)
    except NotFoundError as exc:
        return {"error": "not_found", "detail": str(exc)}
    except ConflictError as exc:
        return {"error": "conflict", "detail": str(exc)}


@mcp.tool()
def remove_source(source_id: int) -> dict:
    """Permanently delete a source and all of its articles.

    WARNING: This operation cascade-deletes every article belonging to this source
    and is irreversible. All associated article data will be lost permanently.

    Returns {sources_deleted, articles_deleted} on success, or an error dict with
    'error' and 'detail' keys when source_id is not found.
    """
    try:
        with get_session() as session:
            return management.remove_source(session, source_id)
    except NotFoundError as exc:
        return {"error": "not_found", "detail": str(exc)}
    except ConflictError as exc:
        return {"error": "conflict", "detail": str(exc)}


@mcp.tool()
def add_category(
    name: str,
    description: Optional[str] = None,
    sort_order: Optional[int] = None,
    enabled: bool = True,
) -> dict:
    """Add a new category to the aggregator.

    Returns the created category fields on success, or an error dict with 'error'
    and 'detail' keys when a category with the same name already exists (conflict).
    """
    try:
        with get_session() as session:
            return management.add_category(
                session,
                name,
                description=description,
                sort_order=sort_order,
                enabled=enabled,
            )
    except NotFoundError as exc:
        return {"error": "not_found", "detail": str(exc)}
    except ConflictError as exc:
        return {"error": "conflict", "detail": str(exc)}


@mcp.tool()
def rename_category(category_id: int, new_name: str) -> dict:
    """Rename an existing category.

    category_id may be an integer primary key or an exact name string.
    Returns the updated category id and name on success, or an error dict with
    'error' and 'detail' keys when the category is not found or the new name
    is already taken by another category.
    """
    try:
        with get_session() as session:
            return management.rename_category(session, category_id, new_name)
    except NotFoundError as exc:
        return {"error": "not_found", "detail": str(exc)}
    except ConflictError as exc:
        return {"error": "conflict", "detail": str(exc)}


@mcp.tool()
def set_category_description(category_id: int, description: Optional[str]) -> dict:
    """Set or clear the description of a category.

    Pass None to clear the description. Returns the updated category id and
    description on success, or an error dict with 'error' and 'detail' keys
    when the category is not found.
    """
    try:
        with get_session() as session:
            return management.set_category_description(session, category_id, description)
    except NotFoundError as exc:
        return {"error": "not_found", "detail": str(exc)}
    except ConflictError as exc:
        return {"error": "conflict", "detail": str(exc)}


@mcp.tool()
def set_category_order(category_id: int, sort_order: int) -> dict:
    """Update the display sort order of a category.

    Returns the updated category id and sort_order on success, or an error dict
    with 'error' and 'detail' keys when the category is not found.
    """
    try:
        with get_session() as session:
            return management.set_category_order(session, category_id, sort_order)
    except NotFoundError as exc:
        return {"error": "not_found", "detail": str(exc)}
    except ConflictError as exc:
        return {"error": "conflict", "detail": str(exc)}


@mcp.tool()
def enable_category(category_id: int) -> dict:
    """Enable a category so it appears in listings.

    Returns the updated category id and enabled state on success, or an error
    dict with 'error' and 'detail' keys when the category is not found.
    """
    try:
        with get_session() as session:
            return management.enable_category(session, category_id)
    except NotFoundError as exc:
        return {"error": "not_found", "detail": str(exc)}
    except ConflictError as exc:
        return {"error": "conflict", "detail": str(exc)}


@mcp.tool()
def disable_category(category_id: int) -> dict:
    """Disable a category so it is hidden from listings.

    Returns the updated category id and enabled state on success, or an error
    dict with 'error' and 'detail' keys when the category is not found.
    """
    try:
        with get_session() as session:
            return management.disable_category(session, category_id)
    except NotFoundError as exc:
        return {"error": "not_found", "detail": str(exc)}
    except ConflictError as exc:
        return {"error": "conflict", "detail": str(exc)}


@mcp.tool()
def remove_category(category_id: int) -> dict:
    """Permanently delete a category.

    WARNING: This operation is irreversible. The category record is permanently
    deleted and cannot be recovered. Any articles previously assigned to this
    category will lose their category association.

    Returns {categories_deleted: 1} on success, or an error dict with 'error'
    and 'detail' keys when the category is not found.
    """
    try:
        with get_session() as session:
            return management.remove_category(session, category_id)
    except NotFoundError as exc:
        return {"error": "not_found", "detail": str(exc)}
    except ConflictError as exc:
        return {"error": "conflict", "detail": str(exc)}
