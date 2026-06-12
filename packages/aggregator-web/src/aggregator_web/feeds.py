from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Literal, Optional

from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.orm import Session

from aggregator_common.models import Article, Category, Source
from aggregator_common.state import ArticleStatus

SmartViewName = Literal["all", "unread", "saved", "important", "uncategorized"]


@dataclass
class Cursor:
    """Opaque keyset pagination token.

    Serialised as base64url(JSON) with keys:
      "s" = importance_score (int | null)
      "p" = feed_published_at as ISO-8601 string (str | null)
      "i" = article id (int)
    """

    importance_score: Optional[int]
    feed_published_at: Optional[datetime]
    id: int

    def encode(self) -> str:
        payload = {
            "s": self.importance_score,
            "p": self.feed_published_at.isoformat() if self.feed_published_at else None,
            "i": self.id,
        }
        return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()

    @classmethod
    def decode(cls, token: str) -> Cursor:
        payload = json.loads(base64.urlsafe_b64decode(token.encode()).decode())
        pub = datetime.fromisoformat(payload["p"]) if payload["p"] else None
        return cls(importance_score=payload["s"], feed_published_at=pub, id=payload["i"])


@dataclass
class FeedPage:
    articles: List[Article]
    next_cursor: Optional[str]


@dataclass
class SidebarCounts:
    smart: Dict[str, int]
    categories: Dict[str, int]
    sources: Dict[int, int]


def _ready_base():
    return and_(
        Article.status == ArticleStatus.ready,
        Article.is_hidden == False,
    )


def _smart_extra_filter(view: SmartViewName, important_threshold: int):
    if view == "all":
        return None
    elif view == "unread":
        return Article.is_read == False
    elif view == "saved":
        return Article.is_saved == True
    elif view == "important":
        return Article.importance_score >= important_threshold
    elif view == "uncategorized":
        # jsonb_array_length raises on scalar JSONB (including JSONB null, which psycopg3
        # stores when Python None is passed to a JSONB column). Guard with jsonb_typeof.
        return or_(
            Article.categories.is_(None),
            func.jsonb_typeof(Article.categories) == "null",
            and_(
                func.jsonb_typeof(Article.categories) == "array",
                func.jsonb_array_length(Article.categories) == 0,
            ),
        )
    else:
        raise ValueError(f"Unknown smart view: {view!r}")


def _cursor_condition(cursor: Cursor):
    """Keyset WHERE clause matching sort order: importance_score DESC NULLS LAST, feed_published_at DESC NULLS LAST, id DESC."""
    s = Article.importance_score
    p = Article.feed_published_at

    # pub_before: feed_published_at sorts before cursor's value in DESC NULLS LAST order
    if cursor.feed_published_at is not None:
        pub_before = or_(p < cursor.feed_published_at, p.is_(None))
        pub_same = p == cursor.feed_published_at
    else:
        # cursor was at NULL pub; nothing comes after NULL in DESC NULLS LAST
        pub_before = false()
        pub_same = p.is_(None)

    id_before = Article.id < cursor.id
    after_pub = or_(pub_before, and_(pub_same, id_before))

    if cursor.importance_score is not None:
        return or_(
            s < cursor.importance_score,
            and_(s == cursor.importance_score, after_pub),
            s.is_(None),
        )
    # cursor was at NULL score level; NULL sorts after all non-null scores
    return and_(s.is_(None), after_pub)


def _paginate(session: Session, q, page_size: int, cursor_token: Optional[str]) -> FeedPage:
    if cursor_token:
        q = q.where(_cursor_condition(Cursor.decode(cursor_token)))

    q = q.order_by(
        Article.importance_score.desc().nulls_last(),
        Article.feed_published_at.desc().nulls_last(),
        Article.id.desc(),
    ).limit(page_size + 1)

    rows = list(session.execute(q).scalars().all())
    next_cursor = None
    if len(rows) > page_size:
        rows = rows[:page_size]
        last = rows[-1]
        next_cursor = Cursor(
            importance_score=last.importance_score,
            feed_published_at=last.feed_published_at,
            id=last.id,
        ).encode()

    return FeedPage(articles=rows, next_cursor=next_cursor)


def smart_feed(
    view: SmartViewName,
    session: Session,
    page_size: int,
    important_threshold: int,
    cursor: Optional[str] = None,
    unread_only: bool = False,
) -> FeedPage:
    filters = [_ready_base()]
    extra = _smart_extra_filter(view, important_threshold)
    if extra is not None:
        filters.append(extra)
    if unread_only and view != "unread":
        filters.append(Article.is_read == False)
    return _paginate(session, select(Article).where(*filters), page_size, cursor)


def category_feed(
    name: str,
    session: Session,
    page_size: int,
    cursor: Optional[str] = None,
    unread_only: bool = False,
) -> FeedPage:
    # JSONB @> operator via .contains(); uses GIN index on categories
    filters = [_ready_base(), Article.categories.contains([name])]
    if unread_only:
        filters.append(Article.is_read == False)
    return _paginate(session, select(Article).where(*filters), page_size, cursor)


def source_feed(
    source_id: int,
    session: Session,
    page_size: int,
    cursor: Optional[str] = None,
    unread_only: bool = False,
) -> FeedPage:
    filters = [_ready_base(), Article.source_id == source_id]
    if unread_only:
        filters.append(Article.is_read == False)
    return _paginate(session, select(Article).where(*filters), page_size, cursor)


def smart_feed_count(
    view: SmartViewName,
    session: Session,
    since: int,
    important_threshold: int,
    unread_only: bool = False,
) -> int:
    """Count ready articles matching the smart-view filter with id > since."""
    filters = [_ready_base(), Article.id > since]
    extra = _smart_extra_filter(view, important_threshold)
    if extra is not None:
        filters.append(extra)
    if unread_only and view != "unread":
        filters.append(Article.is_read == False)
    return session.execute(select(func.count(Article.id)).where(*filters)).scalar_one()


def category_feed_count(
    name: str,
    session: Session,
    since: int,
    unread_only: bool = False,
) -> int:
    """Count ready articles in the given category with id > since."""
    filters = [_ready_base(), Article.categories.contains([name]), Article.id > since]
    if unread_only:
        filters.append(Article.is_read == False)
    return session.execute(select(func.count(Article.id)).where(*filters)).scalar_one()


def source_feed_count(
    source_id: int,
    session: Session,
    since: int,
    unread_only: bool = False,
) -> int:
    """Count ready articles from the given source with id > since."""
    filters = [_ready_base(), Article.source_id == source_id, Article.id > since]
    if unread_only:
        filters.append(Article.is_read == False)
    return session.execute(select(func.count(Article.id)).where(*filters)).scalar_one()


def get_sidebar_counts(
    session: Session,
    important_threshold: int,
) -> SidebarCounts:
    base = _ready_base()
    unread = and_(base, Article.is_read == False)

    def _count(*extra) -> int:
        return session.execute(
            select(func.count(Article.id)).where(unread, *extra)
        ).scalar_one()

    smart: Dict[str, int] = {
        "all": _count(),
        "unread": _count(),
        "saved": _count(Article.is_saved == True),
        "important": _count(Article.importance_score >= important_threshold),
        "uncategorized": _count(
            or_(
                Article.categories.is_(None),
                func.jsonb_typeof(Article.categories) == "null",
                and_(
                    func.jsonb_typeof(Article.categories) == "array",
                    func.jsonb_array_length(Article.categories) == 0,
                ),
            )
        ),
    }

    # Source counts: single GROUP BY query, then join to enabled sources
    source_count_rows = session.execute(
        select(Article.source_id, func.count(Article.id).label("cnt"))
        .where(unread)
        .group_by(Article.source_id)
    ).all()
    all_source_counts: Dict[int, int] = {row.source_id: row.cnt for row in source_count_rows}

    enabled_sources = session.execute(
        select(Source).where(Source.enabled == True).order_by(Source.name)
    ).scalars().all()
    sources: Dict[int, int] = {src.id: all_source_counts.get(src.id, 0) for src in enabled_sources}

    # Category counts: per-category JSONB containment
    enabled_categories = session.execute(
        select(Category).where(Category.enabled == True).order_by(Category.sort_order, Category.name)
    ).scalars().all()
    categories: Dict[str, int] = {
        cat.name: _count(Article.categories.contains([cat.name]))
        for cat in enabled_categories
    }

    return SidebarCounts(smart=smart, categories=categories, sources=sources)
