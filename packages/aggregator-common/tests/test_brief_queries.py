"""Tests for list_briefs and get_brief query helpers."""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone
from typing import Generator

import pytest
from sqlalchemy.orm import Session

import aggregator_common.queries as queries
from aggregator_common.models import Brief, BriefTopic

_NOW = datetime.now(tz=timezone.utc)
_TODAY_START = _NOW.replace(hour=0, minute=0, second=0, microsecond=0)

_counter = itertools.count(1)


@pytest.fixture
def session(db_session_factory) -> Generator[Session, None, None]:
    s = db_session_factory()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _make_brief(
    session: Session,
    *,
    status: str = "ready",
    headline: str = "Brief Headline",
    created_at: datetime | None = None,
) -> Brief:
    n = next(_counter)
    ts = created_at or _NOW
    brief = Brief(
        status=status,
        origin="manual",
        period_start=_TODAY_START - timedelta(days=n * 10),
        period_end=_TODAY_START - timedelta(days=n * 10 - 1),
        headline=headline,
        intro="Some intro.",
        model="gpt-4.1",
        generated_at=ts,
        created_at=ts,
    )
    session.add(brief)
    session.flush()
    return brief


def _make_topic(session: Session, brief_id: int, position: int, headline: str = "Topic") -> BriefTopic:
    topic = BriefTopic(
        brief_id=brief_id,
        position=position,
        headline=headline,
        what_happened="Things happened.",
        why_it_matters="It matters.",
        topic_refs=[],
    )
    session.add(topic)
    session.flush()
    return topic


class TestListBriefs:
    def test_returns_ready_briefs(self, session: Session):
        b = _make_brief(session, status="ready", headline="Ready One")
        results, _ = queries.list_briefs(session)
        ids = {r.id for r in results}
        assert b.id in ids

    def test_excludes_non_ready_briefs(self, session: Session):
        pending = _make_brief(session, status="pending", headline="Pending Brief")
        failed = _make_brief(session, status="failed", headline="Failed Brief")
        results, _ = queries.list_briefs(session)
        ids = {r.id for r in results}
        assert pending.id not in ids
        assert failed.id not in ids

    def test_newest_first_ordering(self, session: Session):
        older = _make_brief(
            session, headline="Older", created_at=_TODAY_START - timedelta(days=2)
        )
        newer = _make_brief(
            session, headline="Newer", created_at=_TODAY_START
        )
        results, _ = queries.list_briefs(session)
        ids = [r.id for r in results]
        assert newer.id in ids
        assert older.id in ids
        assert ids.index(newer.id) < ids.index(older.id)

    def test_topics_ordered_by_position(self, session: Session):
        b = _make_brief(session)
        _make_topic(session, b.id, 3, "Third")
        _make_topic(session, b.id, 1, "First")
        _make_topic(session, b.id, 2, "Second")
        results, _ = queries.list_briefs(session)
        match = next(r for r in results if r.id == b.id)
        assert [t.position for t in match.topics] == [1, 2, 3]

    def test_next_cursor_none_when_fewer_than_limit(self, session: Session):
        _make_brief(session, headline="Only One")
        _, next_cursor = queries.list_briefs(session, limit=200)
        assert next_cursor is None

    def test_next_cursor_set_when_page_full(self, session: Session):
        for i in range(3):
            _make_brief(session, headline=f"Brief {i}")
        # Use limit=1 to guarantee a full page among briefs this test created
        _, next_cursor = queries.list_briefs(session, limit=1)
        assert next_cursor is not None

    def test_pagination_no_gaps_or_duplicates(self, session: Session):
        """All my briefs appear exactly once across pages; no duplicates."""
        my_briefs = []
        for i in range(5):
            b = _make_brief(session, headline=f"Brief P{i}")
            my_briefs.append(b)
        my_ids = {b.id for b in my_briefs}

        limit = 2
        seen_ids: list[int] = []
        cursor = None
        while True:
            results, next_cursor = queries.list_briefs(session, limit=limit, cursor=cursor)
            seen_ids.extend(r.id for r in results)
            if next_cursor is None:
                break
            cursor = next_cursor

        collected = [i for i in seen_ids if i in my_ids]
        assert len(collected) == len(set(collected)), "Duplicate brief ids across pages"
        assert set(collected) == my_ids, "Gaps detected: not all seeded briefs returned"

    def test_pagination_second_page_has_no_overlap(self, session: Session):
        for i in range(4):
            _make_brief(session, headline=f"Brief O{i}")
        page1, cursor = queries.list_briefs(session, limit=1)
        assert cursor is not None
        page2, _ = queries.list_briefs(session, limit=1, cursor=cursor)
        ids1 = {r.id for r in page1}
        ids2 = {r.id for r in page2}
        assert ids1.isdisjoint(ids2), "Pages overlap"

    def test_non_ready_briefs_not_included(self, session: Session):
        pending = _make_brief(session, status="pending")
        failed = _make_brief(session, status="failed")
        results, _ = queries.list_briefs(session)
        ids = {r.id for r in results}
        assert pending.id not in ids
        assert failed.id not in ids


class TestGetBrief:
    def test_returns_ready_brief_with_topics(self, session: Session):
        b = _make_brief(session, headline="Get Me")
        _make_topic(session, b.id, 1, "Only Topic")
        result = queries.get_brief(session, b.id)
        assert result is not None
        assert result.id == b.id
        assert result.headline == "Get Me"
        assert len(result.topics) == 1
        assert result.topics[0].headline == "Only Topic"

    def test_topics_ordered_by_position(self, session: Session):
        b = _make_brief(session, headline="Ordered Topics")
        _make_topic(session, b.id, 2, "B")
        _make_topic(session, b.id, 1, "A")
        result = queries.get_brief(session, b.id)
        assert result is not None
        assert [t.position for t in result.topics] == [1, 2]

    def test_returns_none_for_unknown_id(self, session: Session):
        result = queries.get_brief(session, 999_999_888)
        assert result is None

    def test_returns_none_for_non_ready_brief(self, session: Session):
        b = _make_brief(session, status="pending")
        result = queries.get_brief(session, b.id)
        assert result is None

    def test_returns_none_for_failed_brief(self, session: Session):
        b = _make_brief(session, status="failed")
        result = queries.get_brief(session, b.id)
        assert result is None
