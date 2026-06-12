from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aggregator_common.errors import ConflictError, NotFoundError
from aggregator_common.models import Article, Category, InterestProfile, Source


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
