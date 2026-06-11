from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Union

from sqlalchemy import func, or_, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from aggregator_common.models import Article

_DEFAULT_IMPORTANT_THRESHOLD = 70


class ArticleNotFoundError(Exception):
    pass


@dataclass
class FeedSpec:
    """Identifies a feed for bulk operations (e.g. mark-all-read)."""

    type: Literal["smart", "category", "source"]
    value: Union[str, int]  # smart view name or category name (str), source id (int)


def _get_article(session: Session, article_id: int) -> Article:
    article = session.get(Article, article_id)
    if article is None:
        raise ArticleNotFoundError(f"Article {article_id} not found")
    return article


def mark_read(session: Session, article_id: int) -> Article:
    article = _get_article(session, article_id)
    article.is_read = True
    article.read_at = datetime.now(timezone.utc)
    session.flush()
    return article


def mark_unread(session: Session, article_id: int) -> Article:
    article = _get_article(session, article_id)
    article.is_read = False
    article.read_at = None
    session.flush()
    return article


def save(session: Session, article_id: int) -> Article:
    article = _get_article(session, article_id)
    article.is_saved = True
    session.flush()
    return article


def unsave(session: Session, article_id: int) -> Article:
    article = _get_article(session, article_id)
    article.is_saved = False
    session.flush()
    return article


def _feed_membership_filter(feed_spec: FeedSpec, important_threshold: int):
    """Build a WHERE clause covering all articles in a feed, ignoring any unread display filter."""
    base = Article.status == "ready"

    if feed_spec.type == "smart":
        view = str(feed_spec.value)
        if view in ("all", "unread"):
            # "unread" feed membership = all ready articles; mark-all-read ignores the display filter
            return base
        elif view == "saved":
            return base & (Article.is_saved == True)
        elif view == "important":
            return base & (Article.importance_score >= important_threshold)
        elif view == "uncategorized":
            return base & or_(
                Article.categories.is_(None),
                func.jsonb_array_length(Article.categories.cast(JSONB)) == 0,
            )
        else:
            raise ValueError(f"Unknown smart view: {view!r}")
    elif feed_spec.type == "category":
        return base & Article.categories.cast(JSONB).contains([str(feed_spec.value)])
    elif feed_spec.type == "source":
        return base & (Article.source_id == int(feed_spec.value))
    else:
        raise ValueError(f"Unknown feed type: {feed_spec.type!r}")


def mark_all_read(
    session: Session,
    feed_spec: FeedSpec,
    important_threshold: int = _DEFAULT_IMPORTANT_THRESHOLD,
) -> int:
    """Mark every article in the feed read. Returns the count of updated rows."""
    now = datetime.now(timezone.utc)
    stmt = (
        update(Article)
        .where(_feed_membership_filter(feed_spec, important_threshold))
        .values(is_read=True, read_at=now)
    )
    result = session.execute(stmt)
    session.flush()
    return result.rowcount
