from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Optional

from mcp.server.fastmcp import FastMCP

import aggregator_common.management as management
import aggregator_common.ops as ops
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
def list_threads(
    sort: str = "importance",
    status: Optional[str] = None,
    limit: int = _settings.mcp_default_limit,
) -> list:
    limit = min(limit, _settings.mcp_max_limit)
    with get_session() as session:
        results = queries.list_threads(session, sort=sort, status=status, limit=limit)  # type: ignore[arg-type]
    return [asdict(r) for r in results]


@mcp.tool()
def get_thread(thread_id: int) -> dict:
    with get_session() as session:
        result = queries.get_thread(session, thread_id)
        if result is None:
            return {"error": "not_found", "detail": f"Thread {thread_id} not found"}
        members = queries.get_thread_members(session, thread_id)
    return {"thread": asdict(result), "members": [asdict(m) for m in members]}


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


@mcp.resource("thread://{id}")
def thread_resource(id: str) -> dict:
    thread_id = int(id)
    with get_session() as session:
        result = queries.get_thread(session, thread_id)
        if result is None:
            return {"error": "not_found"}
        members = queries.get_thread_members(session, thread_id)
    return {"thread": asdict(result), "members": [asdict(m) for m in members]}


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

    category_id must be the integer primary key of the category (not a name string).
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


@mcp.tool()
def pipeline_status() -> dict:
    """Return a snapshot of the article processing pipeline.

    Returns a dict with three keys:
    - article_counts: mapping of ArticleStatus value → count of articles in that status.
    - in_flight: number of articles currently claimed by a worker.
    - sources: dict with 'enabled' and 'disabled' source counts.

    Use this as a first-pass health check before diving into list_stuck or list_failures.
    """
    with get_session() as session:
        return ops.pipeline_status(session)


@mcp.tool()
def list_stuck(lease_seconds: int = 600) -> list:
    """List articles with a stale claim older than lease_seconds seconds.

    An article is considered stuck when its claimed_at timestamp is older than
    lease_seconds ago, meaning the worker that claimed it has likely crashed or
    stalled. Returns a list of dicts, each with:
    - id: article id
    - status: current article status
    - claimed_by: worker identifier that holds the claim
    - claimed_at: ISO-format timestamp when the claim was taken
    - source_name: name of the source the article belongs to

    To release stuck articles so they can be reprocessed, call reap_stale_claims
    with the same lease_seconds value.
    """
    with get_session() as session:
        rows = ops.list_stuck(session, lease_seconds)
    for row in rows:
        if row.get("claimed_at") is not None:
            row["claimed_at"] = row["claimed_at"].isoformat()
    return rows


@mcp.tool()
def list_failures(stage: Optional[str] = None, limit: int = 50) -> list | dict:
    """List articles that have failed processing or ranking.

    stage filters by pipeline stage: pass 'processor' for failed_processing,
    'summarize_rank' for failed_ranking, or omit (None) for both.

    Returns up to limit results ordered by most-recently-updated first. Each
    result dict contains:
    - id: article id
    - status: failed_processing or failed_ranking
    - retry_count: number of attempts made
    - last_error: error message from the last failure attempt
    - source_name: name of the source the article belongs to

    To requeue failed articles for retry, call retry_failed with the same stage.
    """
    try:
        with get_session() as session:
            return ops.list_failures(session, stage=stage, limit=limit)
    except ValueError as exc:
        return {"error": "invalid_stage", "detail": str(exc)}


@mcp.tool()
def reap_stale_claims(lease_seconds: int = 600) -> dict:
    """Release stale article and brief claims older than lease_seconds seconds.

    Returns per-kind released counts:
    - articles_released: number of stale article claims cleared
    - briefs_released: number of stale brief claims cleared

    Cleared rows return to their pending status and become re-claimable on the
    next worker poll. Safe to call at any time; has no effect when there are no
    stale claims. Use list_stuck first to see what will be released.
    """
    with get_session() as session:
        return ops.reap_stale_claims(session, lease_seconds)


@mcp.tool()
def retry_failed(
    stage: Optional[str] = None,
    article_id: Optional[int] = None,
) -> dict:
    """Reset failed articles to their pending state so workers retry them.

    stage filters which failures to retry: pass 'processor' for failed_processing,
    'summarize_rank' for failed_ranking, or omit (None) for both. article_id
    further narrows the reset to a single article.

    Returns {retried: N} on success, or an error dict with 'error' and 'detail'
    keys when stage is invalid or the status transition is not allowed.
    """
    try:
        with get_session() as session:
            return ops.retry_failed(session, stage=stage, article_id=article_id)
    except ValueError as exc:
        return {"error": "invalid_transition", "detail": str(exc)}


@mcp.tool()
def rerank(
    article_id: Optional[int] = None,
    all_ready: bool = False,
    failed_only: bool = False,
) -> dict:
    """Transition articles to pending_ranking so the summarize-rank service re-scores them.

    Pass one targeting flag:
    - article_id: re-rank a single article by id.
    - all_ready=True: re-rank every article currently in 'ready' status.
    - failed_only=True: re-rank only articles that failed ranking.

    Returns {reranked: N} on success, or an error dict with 'error' and 'detail'
    keys when the status transition is not allowed.
    """
    try:
        with get_session() as session:
            return ops.rerank(session, article_id=article_id, all_ready=all_ready, failed_only=failed_only)
    except ValueError as exc:
        return {"error": "invalid_transition", "detail": str(exc)}


@mcp.resource("status://pipeline")
def pipeline_status_resource() -> dict:
    """Quick pipeline health snapshot: article counts by status, in-flight claims, source counts."""
    with get_session() as session:
        return ops.pipeline_status(session)


@mcp.prompt()
def troubleshoot() -> str:
    return (
        "Follow these steps to diagnose and fix a stalled aggregator pipeline:\n\n"
        "1. Call pipeline_status (or read the status://pipeline resource) to get a "
        "high-level view: article counts by status, in-flight claims, and source counts.\n\n"
        "2. If in_flight is non-zero and the pipeline looks frozen, call list_stuck "
        "(default lease_seconds=600) to identify articles whose worker claim has expired. "
        "Then call reap_stale_claims with the same lease_seconds to release them so "
        "workers can re-claim and retry.\n\n"
        "3. If article_counts shows failed_processing or failed_ranking entries, call "
        "list_failures (optionally filtered by stage='processor' or 'summarize_rank') "
        "to inspect the errors. Then call retry_failed (with the matching stage or "
        "article_id) to reset those articles to pending so workers retry them.\n\n"
        "4. If you want to force the summarize-rank service to re-score ready articles "
        "(e.g. after updating the interest profile), call rerank with all_ready=True. "
        "To re-score only previously failed articles, use rerank with failed_only=True.\n\n"
        "5. After any remediation step, re-check pipeline_status to confirm article "
        "counts are moving in the expected direction."
    )
