from __future__ import annotations

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aggregator_common.errors import ConflictError, NotFoundError
from aggregator_common.models import Article, InterestProfile, Source


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
