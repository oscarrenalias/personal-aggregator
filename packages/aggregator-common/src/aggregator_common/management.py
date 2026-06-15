from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aggregator_common.errors import ConflictError, NotFoundError
from aggregator_common.models import Article, Category, ClusterState, InterestProfile, Source, Thread, ThreadMembership


def set_interest_profile(session: Session, text: str) -> dict:
    """Upsert the singleton InterestProfile row and return its fields as a dict."""
    stmt = (
        pg_insert(InterestProfile)
        .values(id=True, profile_text=text)
        .on_conflict_do_update(
            index_elements=["id"],
            set_={"profile_text": text, "updated_at": func.now()},
        )
        .returning(InterestProfile.id, InterestProfile.profile_text, InterestProfile.updated_at)
    )
    row = session.execute(stmt).one()
    return {"id": row.id, "profile_text": row.profile_text, "updated_at": row.updated_at}


def add_source(
    session: Session,
    name: str,
    feed_url: str,
    *,
    refresh_interval_seconds: int | None = None,
    priority: int | None = None,
    enabled: bool = True,
) -> dict:
    """Create a new Source row and return its serialised fields.

    Raises ConflictError when feed_url already exists.
    """
    kwargs: dict = {"name": name, "feed_url": feed_url, "enabled": enabled}
    if refresh_interval_seconds is not None:
        kwargs["refresh_interval_seconds"] = refresh_interval_seconds
    if priority is not None:
        kwargs["priority"] = priority

    source = Source(**kwargs)
    session.add(source)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ConflictError(f"A source with feed_url '{feed_url}' already exists.")

    return {
        "id": source.id,
        "name": source.name,
        "feed_url": source.feed_url,
        "enabled": source.enabled,
        "refresh_interval_seconds": source.refresh_interval_seconds,
        "priority": source.priority,
        "created_at": source.created_at,
        "updated_at": source.updated_at,
    }


def remove_source(session: Session, source_id: int) -> dict:
    """Cascade-delete a source's articles then the source itself.

    Returns {sources_deleted, articles_deleted}.
    Raises NotFoundError when source_id is absent.
    """
    source = session.get(Source, source_id)
    if source is None:
        raise NotFoundError(f"Source {source_id} not found.")

    articles_deleted: int = session.query(Article).filter(Article.source_id == source_id).count()
    session.execute(delete(Article).where(Article.source_id == source_id))

    session.delete(source)
    session.flush()

    return {"sources_deleted": 1, "articles_deleted": articles_deleted}


def enable_source(session: Session, source_id: int) -> dict:
    """Enable a source and reset its failure state so it is picked up immediately.

    Raises NotFoundError when source_id is absent.
    """
    source = session.get(Source, source_id)
    if source is None:
        raise NotFoundError(f"Source {source_id} not found.")

    now = datetime.now(timezone.utc)
    source.enabled = True
    source.consecutive_failures = 0
    source.next_check_at = now
    session.flush()

    return {"id": source.id, "enabled": source.enabled, "consecutive_failures": source.consecutive_failures, "next_check_at": source.next_check_at}


def disable_source(session: Session, source_id: int) -> dict:
    """Disable a source so the retriever skips it.

    Raises NotFoundError when source_id is absent.
    """
    source = session.get(Source, source_id)
    if source is None:
        raise NotFoundError(f"Source {source_id} not found.")

    source.enabled = False
    session.flush()

    return {"id": source.id, "enabled": source.enabled}


def set_source_interval(session: Session, source_id: int, seconds: int) -> dict:
    """Update the polling interval for a source.

    Raises NotFoundError when source_id is absent.
    """
    source = session.get(Source, source_id)
    if source is None:
        raise NotFoundError(f"Source {source_id} not found.")

    source.refresh_interval_seconds = seconds
    session.flush()

    return {"id": source.id, "refresh_interval_seconds": source.refresh_interval_seconds}


def refresh_source_now(session: Session, source_id: int) -> dict:
    """Force a source to be polled on the next retriever cycle by setting next_check_at=now().

    Raises NotFoundError when source_id is absent.
    """
    source = session.get(Source, source_id)
    if source is None:
        raise NotFoundError(f"Source {source_id} not found.")

    source.next_check_at = datetime.now(timezone.utc)
    session.flush()

    return {"id": source.id, "next_check_at": source.next_check_at}


def _resolve_category(session: Session, category_id: int | str) -> Category:
    """Resolve a Category by integer primary key or exact name string.

    Raises NotFoundError when no match is found.
    """
    if isinstance(category_id, int):
        category = session.get(Category, category_id)
    elif str(category_id).isdigit():
        category = session.get(Category, int(category_id))
    else:
        category = session.query(Category).filter(Category.name == category_id).first()
    if category is None:
        raise NotFoundError(f"Category '{category_id}' not found.")
    return category


def add_category(
    session: Session,
    name: str,
    *,
    description: str | None = None,
    sort_order: int | None = None,
    enabled: bool = True,
) -> dict:
    """Create a new Category row and return its serialised fields.

    Raises ConflictError when a category with the same name already exists.
    """
    kwargs: dict = {"name": name, "enabled": enabled}
    if description is not None:
        kwargs["description"] = description
    if sort_order is not None:
        kwargs["sort_order"] = sort_order

    category = Category(**kwargs)
    session.add(category)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ConflictError(f"A category named '{name}' already exists.")

    return {
        "id": category.id,
        "name": category.name,
        "description": category.description,
        "enabled": category.enabled,
        "sort_order": category.sort_order,
        "created_at": category.created_at,
        "updated_at": category.updated_at,
    }


def remove_category(session: Session, category_id: int | str) -> dict:
    """Permanently delete a category.

    category_id may be an integer primary key or an exact name string.
    Raises NotFoundError when the category is absent.
    """
    category = _resolve_category(session, category_id)
    session.delete(category)
    session.flush()
    return {"categories_deleted": 1}


def rename_category(session: Session, category_id: int | str, new_name: str) -> dict:
    """Rename a category.

    Raises NotFoundError for unknown category.
    Raises ConflictError when new_name is already taken by another category.
    """
    category = _resolve_category(session, category_id)
    category.name = new_name
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ConflictError(f"A category named '{new_name}' already exists.")
    return {"id": category.id, "name": category.name}


def set_category_description(session: Session, category_id: int | str, description: str | None) -> dict:
    """Set or clear the description of a category.

    Raises NotFoundError for unknown category.
    """
    category = _resolve_category(session, category_id)
    category.description = description
    session.flush()
    return {"id": category.id, "description": category.description}


def set_category_order(session: Session, category_id: int | str, sort_order: int) -> dict:
    """Update the sort_order of a category.

    Raises NotFoundError for unknown category.
    """
    category = _resolve_category(session, category_id)
    category.sort_order = sort_order
    session.flush()
    return {"id": category.id, "sort_order": category.sort_order}


def enable_category(session: Session, category_id: int | str) -> dict:
    """Enable a category so it appears in listings.

    Raises NotFoundError for unknown category.
    """
    category = _resolve_category(session, category_id)
    category.enabled = True
    session.flush()
    return {"id": category.id, "enabled": category.enabled}


def disable_category(session: Session, category_id: int | str) -> dict:
    """Disable a category so it is hidden from listings.

    Raises NotFoundError for unknown category.
    """
    category = _resolve_category(session, category_id)
    category.enabled = False
    session.flush()
    return {"id": category.id, "enabled": category.enabled}


# ---------------------------------------------------------------------------
# Thread mutation helpers
# ---------------------------------------------------------------------------

_THREAD_MUTABLE_FIELDS = frozenset(
    {
        "representative_title",
        "rolling_summary",
        "known_facts",
        "source_list",
        "source_diversity",
        "confidence",
        "status",
        "novelty_label",
        "tier",
        "tier_reason",
        "relevance_score",
        "novelty_score",
        "importance_score",
        "diversity_score",
        "time_sensitivity_score",
    }
)


def create_thread(
    session: Session,
    *,
    representative_title: str,
    rolling_summary: str | None = None,
    known_facts: list | None = None,
    source_list: list | None = None,
    confidence: float | None = None,
) -> Thread:
    """Create and persist a new Thread row.  Returns the ORM object."""
    now = datetime.now(timezone.utc)
    thread = Thread(
        representative_title=representative_title,
        rolling_summary=rolling_summary,
        known_facts=known_facts,
        source_list=source_list,
        confidence=confidence,
        first_seen=now,
        last_updated=now,
    )
    session.add(thread)
    session.flush()
    return thread


def update_thread(session: Session, thread_id: int, **fields) -> Thread:
    """Update mutable fields on an existing Thread.

    Only fields in ``_THREAD_MUTABLE_FIELDS`` are accepted.
    Raises NotFoundError for an unknown thread_id.
    Raises ValueError for unrecognised field names.
    """
    thread = session.get(Thread, thread_id)
    if thread is None:
        raise NotFoundError(f"Thread {thread_id} not found.")

    unknown = set(fields) - _THREAD_MUTABLE_FIELDS
    if unknown:
        raise ValueError(f"Non-updatable Thread field(s): {sorted(unknown)}")

    for key, value in fields.items():
        setattr(thread, key, value)

    thread.last_updated = datetime.now(timezone.utc)
    session.flush()
    return thread


def assign_article_to_thread(
    session: Session,
    *,
    article_id: int,
    thread_id: int,
    classification_label: str | None = None,
    new_facts: list | None = None,
    reason: str | None = None,
    confidence: float | None = None,
    suppressed: bool = False,
) -> ThreadMembership:
    """Assign an article to a thread (idempotent).

    If a ThreadMembership row already exists for *article_id* (enforced by
    the unique constraint on that column), the existing row is returned
    unchanged.  Otherwise a new row is created and flushed.

    Raises NotFoundError when thread_id is absent.
    """
    existing = (
        session.query(ThreadMembership)
        .filter(ThreadMembership.article_id == article_id)
        .first()
    )
    if existing is not None:
        return existing

    if session.get(Thread, thread_id) is None:
        raise NotFoundError(f"Thread {thread_id} not found.")

    membership = ThreadMembership(
        thread_id=thread_id,
        article_id=article_id,
        classification_label=classification_label,
        new_facts=new_facts,
        reason=reason,
        confidence=confidence,
        suppressed=suppressed,
        assigned_at=datetime.now(timezone.utc),
    )
    session.add(membership)
    session.flush()
    return membership


def update_thread_scores(
    session: Session,
    thread_id: int,
    *,
    relevance_score: float | None = None,
    novelty_score: float | None = None,
    importance_score: float | None = None,
    diversity_score: float | None = None,
    time_sensitivity_score: float | None = None,
    tier: str | None = None,
    tier_reason: str | None = None,
    confidence: float | None = None,
) -> Thread:
    """Update scoring fields on a Thread.  Only non-None arguments are written.

    Raises NotFoundError for an unknown thread_id.
    """
    thread = session.get(Thread, thread_id)
    if thread is None:
        raise NotFoundError(f"Thread {thread_id} not found.")

    if relevance_score is not None:
        thread.relevance_score = relevance_score
    if novelty_score is not None:
        thread.novelty_score = novelty_score
    if importance_score is not None:
        thread.importance_score = importance_score
    if diversity_score is not None:
        thread.diversity_score = diversity_score
    if time_sensitivity_score is not None:
        thread.time_sensitivity_score = time_sensitivity_score
    if tier is not None:
        thread.tier = tier
    if tier_reason is not None:
        thread.tier_reason = tier_reason
    if confidence is not None:
        thread.confidence = confidence

    thread.last_updated = datetime.now(timezone.utc)
    session.flush()
    return thread


def enqueue_recluster(session: Session) -> None:
    """Signal the clustering worker to perform a full recluster pass.

    Mechanism: upserts a flag into the ``cluster_state`` singleton table
    (boolean PK ``id=true``, same pattern as ``InterestProfile``).  The worker
    poll loop checks ``recluster_requested`` each cycle and atomically reads
    and clears it with::

        UPDATE cluster_state
           SET recluster_requested = false
         WHERE recluster_requested = true
         RETURNING *

    This single-statement atomic clear means concurrent callers can safely
    enqueue without losing a signal, and the worker never processes the same
    recluster request twice.
    """
    stmt = (
        pg_insert(ClusterState)
        .values(id=True, recluster_requested=True, requested_at=func.now())
        .on_conflict_do_update(
            index_elements=["id"],
            set_={"recluster_requested": True, "requested_at": func.now()},
        )
    )
    session.execute(stmt)
    session.flush()


def set_thread_dismissed(session: Session, thread_id: int, dismissed: bool) -> Thread | None:
    """Set the dismissed flag on a thread and return the updated Thread.

    Returns None when thread_id does not exist (not-found signal, no exception).
    Idempotent: calling with the same dismissed value is a no-op with no error.
    """
    thread = session.get(Thread, thread_id)
    if thread is None:
        return None
    thread.dismissed = dismissed
    session.flush()
    return thread


def mark_thread_viewed(session: Session, thread_id: int) -> Thread:
    """Stamp last_viewed_at = now(UTC) on a thread.

    Idempotent — calling it multiple times overwrites the timestamp.
    Raises NotFoundError when thread_id does not exist.
    """
    thread = session.get(Thread, thread_id)
    if thread is None:
        raise NotFoundError(f"Thread {thread_id} not found.")
    thread.last_viewed_at = datetime.now(timezone.utc)
    session.flush()
    return thread


def merge_threads(session: Session, keep_id: int, absorb_id: int) -> Thread:
    """Merge absorb_id into keep_id and return the kept thread.

    Idempotent: if absorb_id no longer exists, returns the kept thread unchanged.
    No internal commit — caller owns the transaction.

    Steps:
    1. Reassign ThreadMembership rows from absorb_id to keep_id (skip duplicates).
    2. Union source_list and known_facts onto the kept thread.
    3. Retain the representative_title and rolling_summary from whichever thread
       has the higher composite score.
    4. Append a merge entry to the kept thread's deltas list.
    5. Delete the absorbed thread row.
    """
    keep = session.get(Thread, keep_id)
    if keep is None:
        raise NotFoundError(f"Thread {keep_id} not found.")

    absorb = session.get(Thread, absorb_id)
    if absorb is None:
        return keep

    # Composite proxy from stored dimension scores using default config weights.
    # Relevance is intentionally excluded (matches scoring.py behaviour).
    def _composite(t: Thread) -> float:
        return (
            0.15 * (t.novelty_score or 0.0)
            + 0.30 * (t.importance_score or 0.0)
            + 0.05 * (t.diversity_score or 0.0)
            + 0.10 * (t.confidence or 0.0)
            + 0.15 * (t.time_sensitivity_score or 0.0)
        )

    if _composite(absorb) > _composite(keep):
        keep.representative_title = absorb.representative_title
        keep.rolling_summary = absorb.rolling_summary

    # Union source_list (order-preserving dedup)
    merged_sources: list = list(keep.source_list or [])
    seen_sources: set = set(merged_sources)
    for s in absorb.source_list or []:
        if s not in seen_sources:
            merged_sources.append(s)
            seen_sources.add(s)
    keep.source_list = merged_sources

    # Union known_facts (dedup by JSON fingerprint to handle dict/list items)
    merged_facts: list = list(keep.known_facts or [])
    seen_facts: set = {json.dumps(f, sort_keys=True) for f in merged_facts}
    for fact in absorb.known_facts or []:
        key = json.dumps(fact, sort_keys=True)
        if key not in seen_facts:
            merged_facts.append(fact)
            seen_facts.add(key)
    keep.known_facts = merged_facts

    # Reassign memberships; delete exact duplicates (same article_id already in keep)
    keep_article_ids = {
        m.article_id
        for m in session.query(ThreadMembership)
        .filter(ThreadMembership.thread_id == keep_id)
        .all()
    }
    for membership in list(
        session.query(ThreadMembership)
        .filter(ThreadMembership.thread_id == absorb_id)
        .all()
    ):
        if membership.article_id not in keep_article_ids:
            membership.thread_id = keep_id
        else:
            session.delete(membership)

    # Append merge entry to deltas
    now = datetime.now(timezone.utc)
    deltas: list = list(keep.deltas or [])
    deltas.append({"type": "merge", "absorbed_id": absorb_id, "timestamp": now.isoformat()})
    keep.deltas = deltas
    keep.last_updated = now

    # Flush membership changes before deleting the absorbed thread so that the
    # DB-level FK cascade finds no remaining rows to remove.
    session.flush()
    session.delete(absorb)
    session.flush()

    return keep
