"""Tests for thread query functions in aggregator_common.queries, focusing on
ThreadResult fields top_grade and surfaced."""
from __future__ import annotations

from datetime import datetime, timezone
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
    src = Source(name=f"Query Test Source{suffix}", feed_url=f"https://tq-test{suffix}.example.com/feed.xml")
    session.add(src)
    session.flush()
    return src


def _make_thread(
    session: Session,
    suffix: str = "",
    *,
    surfaced: bool = True,
    top_grade: int | None = 85,
    status: str = "active",
) -> Thread:
    thread = Thread(
        representative_title=f"Query Test Thread {suffix}",
        first_seen=_NOW,
        last_updated=_NOW,
        status=status,
        surfaced=surfaced,
        top_grade=top_grade,
        source_list=[],
        known_facts=[],
        deltas=[],
    )
    session.add(thread)
    session.flush()
    return thread


def _make_article(session: Session, source_id: int, dedup_key: str) -> Article:
    article = Article(
        source_id=source_id,
        dedup_key=dedup_key,
        status="ready",
        raw_payload={"link": f"https://tq-example.com/{dedup_key}"},
        retrieved_at=_NOW,
        clean_title=f"Article {dedup_key}",
        feed_published_at=_NOW,
    )
    session.add(article)
    session.flush()
    return article


def _make_membership(session: Session, thread_id: int, article_id: int) -> ThreadMembership:
    membership = ThreadMembership(
        thread_id=thread_id,
        article_id=article_id,
        suppressed=False,
        assigned_at=_NOW,
    )
    session.add(membership)
    session.flush()
    return membership


class TestListThreadsTopGradeAndSurfaced:
    def test_top_grade_present_and_populated(self, session: Session):
        _make_thread(session, "-tg-present", surfaced=True, top_grade=90)

        results = queries.list_threads(session)

        assert len(results) >= 1
        matching = [r for r in results if r.representative_title == "Query Test Thread -tg-present"]
        assert len(matching) == 1
        result = matching[0]
        assert hasattr(result, "top_grade")
        assert result.top_grade == 90

    def test_surfaced_field_present_and_true(self, session: Session):
        _make_thread(session, "-sf-present", surfaced=True, top_grade=75)

        results = queries.list_threads(session)

        matching = [r for r in results if r.representative_title == "Query Test Thread -sf-present"]
        assert len(matching) == 1
        result = matching[0]
        assert hasattr(result, "surfaced")
        assert result.surfaced is True

    def test_unsurfaced_thread_excluded(self, session: Session):
        _make_thread(session, "-unsurfaced", surfaced=False, top_grade=95)

        results = queries.list_threads(session)

        titles = [r.representative_title for r in results]
        assert "Query Test Thread -unsurfaced" not in titles

    def test_top_grade_none_when_unset(self, session: Session):
        _make_thread(session, "-tg-none", surfaced=True, top_grade=None)

        results = queries.list_threads(session)

        matching = [r for r in results if r.representative_title == "Query Test Thread -tg-none"]
        assert len(matching) == 1
        assert matching[0].top_grade is None

    def test_returns_list_type(self, session: Session):
        results = queries.list_threads(session)
        assert isinstance(results, list)

    def test_limit_applied(self, session: Session):
        for i in range(5):
            _make_thread(session, f"-lim-{i}", surfaced=True, top_grade=50 + i)

        results = queries.list_threads(session, limit=2)

        assert len(results) <= 2


class TestGetThreadTopGradeAndSurfaced:
    def test_top_grade_present_and_populated(self, session: Session):
        thread = _make_thread(session, "-gt-tg", surfaced=True, top_grade=88)

        result = queries.get_thread(session, thread.id)

        assert result is not None
        assert hasattr(result, "top_grade")
        assert result.top_grade == 88

    def test_surfaced_field_present(self, session: Session):
        thread = _make_thread(session, "-gt-sf", surfaced=True, top_grade=70)

        result = queries.get_thread(session, thread.id)

        assert result is not None
        assert hasattr(result, "surfaced")
        assert result.surfaced is True

    def test_surfaced_false_when_not_surfaced(self, session: Session):
        thread = _make_thread(session, "-gt-sffalse", surfaced=False, top_grade=60)

        result = queries.get_thread(session, thread.id)

        assert result is not None
        assert result.surfaced is False

    def test_unknown_id_returns_none(self, session: Session):
        result = queries.get_thread(session, 999_999_888)
        assert result is None

    def test_member_count_reflects_non_suppressed(self, session: Session):
        src = _make_source(session, "-gt-mc")
        art1 = _make_article(session, src.id, "tq-mc-art1")
        art2 = _make_article(session, src.id, "tq-mc-art2")
        thread = _make_thread(session, "-gt-mc", surfaced=True)

        _make_membership(session, thread.id, art1.id)
        suppressed = ThreadMembership(
            thread_id=thread.id,
            article_id=art2.id,
            suppressed=True,
            assigned_at=_NOW,
        )
        session.add(suppressed)
        session.flush()

        result = queries.get_thread(session, thread.id)

        assert result is not None
        assert result.member_count == 1
