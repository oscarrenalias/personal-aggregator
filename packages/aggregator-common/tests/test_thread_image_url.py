"""Tests for ThreadResult.image_url selection in list_threads and get_thread.

Covers:
- image_url=None when no member has header_image_url
- image_url picks the highest-importance article's header_image_url
- recency tie-break when importance scores are equal
- suppressed members are excluded from image selection
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Generator

import pytest
from sqlalchemy.orm import Session

from aggregator_common import queries
from aggregator_common.models import Article, Source, Thread, ThreadMembership

_NOW = datetime.now(tz=timezone.utc)


@pytest.fixture
def session(db_session_factory) -> Generator[Session, None, None]:
    s = db_session_factory()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _make_source(session: Session, suffix: str = "") -> Source:
    src = Source(
        name=f"ImgTest Source{suffix}",
        feed_url=f"https://imgtest{suffix}.example.com/feed.xml",
    )
    session.add(src)
    session.flush()
    return src


def _make_thread(session: Session, suffix: str = "", *, surfaced: bool = True) -> Thread:
    thread = Thread(
        representative_title=f"ImgTest Thread {suffix}",
        first_seen=_NOW,
        last_updated=_NOW,
        status="active",
        surfaced=surfaced,
        top_grade=80,
        source_list=[],
        known_facts=[],
        deltas=[],
    )
    session.add(thread)
    session.flush()
    return thread


def _make_article(
    session: Session,
    source_id: int,
    dedup_key: str,
    *,
    importance_score: int | None = None,
    header_image_url: str | None = None,
    published_at: datetime | None = None,
) -> Article:
    article = Article(
        source_id=source_id,
        dedup_key=dedup_key,
        status="ready",
        raw_payload={"link": f"https://imgtest.example.com/{dedup_key}"},
        retrieved_at=_NOW,
        clean_title=f"Article {dedup_key}",
        feed_published_at=published_at or _NOW,
        importance_score=importance_score,
        header_image_url=header_image_url,
    )
    session.add(article)
    session.flush()
    return article


def _make_membership(
    session: Session,
    thread_id: int,
    article_id: int,
    *,
    suppressed: bool = False,
) -> ThreadMembership:
    membership = ThreadMembership(
        thread_id=thread_id,
        article_id=article_id,
        suppressed=suppressed,
        assigned_at=_NOW,
    )
    session.add(membership)
    session.flush()
    return membership


# ---------------------------------------------------------------------------
# list_threads image_url selection
# ---------------------------------------------------------------------------


class TestListThreadsImageUrl:
    def test_image_url_none_when_no_member_has_header_image(self, session: Session):
        src = _make_source(session, "-lt-no-img")
        thread = _make_thread(session, "-lt-no-img")
        art = _make_article(session, src.id, "lt-no-img-1", header_image_url=None)
        _make_membership(session, thread.id, art.id)

        results = queries.list_threads(session)
        match = next(
            (r for r in results if r.representative_title == "ImgTest Thread -lt-no-img"),
            None,
        )
        assert match is not None
        assert match.image_url is None

    def test_image_url_picks_highest_importance_member(self, session: Session):
        src = _make_source(session, "-lt-hi-imp")
        thread = _make_thread(session, "-lt-hi-imp")
        low_art = _make_article(
            session, src.id, "lt-hi-imp-low",
            importance_score=30,
            header_image_url="https://example.com/lt-low.jpg",
        )
        high_art = _make_article(
            session, src.id, "lt-hi-imp-high",
            importance_score=90,
            header_image_url="https://example.com/lt-high.jpg",
        )
        _make_membership(session, thread.id, low_art.id)
        _make_membership(session, thread.id, high_art.id)

        results = queries.list_threads(session)
        match = next(
            (r for r in results if r.representative_title == "ImgTest Thread -lt-hi-imp"),
            None,
        )
        assert match is not None
        assert match.image_url == "https://example.com/lt-high.jpg"

    def test_image_url_recency_tiebreak_when_importance_equal(self, session: Session):
        src = _make_source(session, "-lt-recency")
        thread = _make_thread(session, "-lt-recency")
        older = _make_article(
            session, src.id, "lt-recency-old",
            importance_score=50,
            header_image_url="https://example.com/lt-older.jpg",
            published_at=_NOW - timedelta(hours=5),
        )
        newer = _make_article(
            session, src.id, "lt-recency-new",
            importance_score=50,
            header_image_url="https://example.com/lt-newer.jpg",
            published_at=_NOW - timedelta(hours=1),
        )
        _make_membership(session, thread.id, older.id)
        _make_membership(session, thread.id, newer.id)

        results = queries.list_threads(session)
        match = next(
            (r for r in results if r.representative_title == "ImgTest Thread -lt-recency"),
            None,
        )
        assert match is not None
        assert match.image_url == "https://example.com/lt-newer.jpg"

    def test_suppressed_members_excluded_from_image_selection(self, session: Session):
        src = _make_source(session, "-lt-supp")
        thread = _make_thread(session, "-lt-supp")
        suppressed_art = _make_article(
            session, src.id, "lt-supp-1",
            importance_score=99,
            header_image_url="https://example.com/lt-supp.jpg",
        )
        active_art = _make_article(
            session, src.id, "lt-supp-2",
            importance_score=10,
            header_image_url="https://example.com/lt-active.jpg",
        )
        _make_membership(session, thread.id, suppressed_art.id, suppressed=True)
        _make_membership(session, thread.id, active_art.id, suppressed=False)

        results = queries.list_threads(session)
        match = next(
            (r for r in results if r.representative_title == "ImgTest Thread -lt-supp"),
            None,
        )
        assert match is not None
        # suppressed article's image must not win even though its importance is higher
        assert match.image_url == "https://example.com/lt-active.jpg"

    def test_image_url_none_when_all_members_suppressed(self, session: Session):
        src = _make_source(session, "-lt-all-supp")
        thread = _make_thread(session, "-lt-all-supp")
        art = _make_article(
            session, src.id, "lt-all-supp-1",
            importance_score=99,
            header_image_url="https://example.com/lt-supp-only.jpg",
        )
        _make_membership(session, thread.id, art.id, suppressed=True)

        results = queries.list_threads(session)
        match = next(
            (r for r in results if r.representative_title == "ImgTest Thread -lt-all-supp"),
            None,
        )
        assert match is not None
        assert match.image_url is None


# ---------------------------------------------------------------------------
# get_thread image_url selection
# ---------------------------------------------------------------------------


class TestGetThreadImageUrl:
    def test_image_url_none_when_no_member_has_header_image(self, session: Session):
        src = _make_source(session, "-gt-no-img")
        thread = _make_thread(session, "-gt-no-img")
        art = _make_article(session, src.id, "gt-no-img-1", header_image_url=None)
        _make_membership(session, thread.id, art.id)

        result = queries.get_thread(session, thread.id)
        assert result is not None
        assert result.image_url is None

    def test_image_url_picks_highest_importance_member(self, session: Session):
        src = _make_source(session, "-gt-hi-imp")
        thread = _make_thread(session, "-gt-hi-imp")
        low_art = _make_article(
            session, src.id, "gt-hi-imp-low",
            importance_score=20,
            header_image_url="https://example.com/gt-low.jpg",
        )
        high_art = _make_article(
            session, src.id, "gt-hi-imp-high",
            importance_score=85,
            header_image_url="https://example.com/gt-high.jpg",
        )
        _make_membership(session, thread.id, low_art.id)
        _make_membership(session, thread.id, high_art.id)

        result = queries.get_thread(session, thread.id)
        assert result is not None
        assert result.image_url == "https://example.com/gt-high.jpg"

    def test_image_url_recency_tiebreak(self, session: Session):
        src = _make_source(session, "-gt-recency")
        thread = _make_thread(session, "-gt-recency")
        older = _make_article(
            session, src.id, "gt-recency-old",
            importance_score=50,
            header_image_url="https://example.com/gt-older.jpg",
            published_at=_NOW - timedelta(hours=5),
        )
        newer = _make_article(
            session, src.id, "gt-recency-new",
            importance_score=50,
            header_image_url="https://example.com/gt-newer.jpg",
            published_at=_NOW - timedelta(hours=1),
        )
        _make_membership(session, thread.id, older.id)
        _make_membership(session, thread.id, newer.id)

        result = queries.get_thread(session, thread.id)
        assert result is not None
        assert result.image_url == "https://example.com/gt-newer.jpg"

    def test_suppressed_members_excluded_from_image_selection(self, session: Session):
        src = _make_source(session, "-gt-supp")
        thread = _make_thread(session, "-gt-supp")
        suppressed_art = _make_article(
            session, src.id, "gt-supp-1",
            importance_score=99,
            header_image_url="https://example.com/gt-supp.jpg",
        )
        active_art = _make_article(
            session, src.id, "gt-supp-2",
            importance_score=10,
            header_image_url="https://example.com/gt-active.jpg",
        )
        _make_membership(session, thread.id, suppressed_art.id, suppressed=True)
        _make_membership(session, thread.id, active_art.id, suppressed=False)

        result = queries.get_thread(session, thread.id)
        assert result is not None
        assert result.image_url == "https://example.com/gt-active.jpg"

    def test_image_url_field_present_on_result(self, session: Session):
        src = _make_source(session, "-gt-field")
        thread = _make_thread(session, "-gt-field")
        art = _make_article(
            session, src.id, "gt-field-1",
            importance_score=70,
            header_image_url="https://example.com/gt-field.jpg",
        )
        _make_membership(session, thread.id, art.id)

        result = queries.get_thread(session, thread.id)
        assert result is not None
        assert hasattr(result, "image_url")
        assert result.image_url == "https://example.com/gt-field.jpg"
