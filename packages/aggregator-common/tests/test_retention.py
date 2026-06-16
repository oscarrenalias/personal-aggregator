"""Tests for aggregator_common.retention purge helpers (real Postgres via testcontainers)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Generator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from aggregator_common.models import Article, Brief, LlmCall, Source, Thread, ThreadMembership
from aggregator_common.retention import (
    purge_expired_articles,
    purge_expired_briefs,
    purge_expired_llm_calls,
    purge_expired_threads,
)
from aggregator_common.state import ArticleStatus

_NOW = datetime.now(timezone.utc)
_OLD = _NOW - timedelta(days=60)
_RECENT = _NOW - timedelta(days=1)
_RETENTION = 30  # days


@pytest.fixture
def session(db_session_factory) -> Generator[Session, None, None]:
    s = db_session_factory()
    s.execute(
        text(
            "TRUNCATE TABLE thread_memberships, threads, brief_topics, briefs,"
            " articles, sources RESTART IDENTITY CASCADE"
        )
    )
    s.commit()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _make_source(session: Session, suffix: str = "") -> Source:
    src = Source(name=f"ret-src{suffix}", feed_url=f"https://ret{suffix}.example.com/feed.xml")
    session.add(src)
    session.flush()
    return src


def _make_article(
    session: Session,
    source_id: int,
    dedup_key: str,
    *,
    retrieved_at: datetime = _OLD,
    is_saved: bool = False,
    is_read: bool = False,
) -> Article:
    art = Article(
        source_id=source_id,
        dedup_key=dedup_key,
        status=ArticleStatus.ready,
        raw_payload={},
        retrieved_at=retrieved_at,
        is_saved=is_saved,
        is_read=is_read,
    )
    session.add(art)
    session.flush()
    return art


def _make_thread(
    session: Session, *, status: str = "active", last_updated: datetime = _OLD
) -> Thread:
    t = Thread(
        representative_title="Thread",
        first_seen=last_updated,
        last_updated=last_updated,
        status=status,
        source_list=[],
        deltas=[],
    )
    session.add(t)
    session.flush()
    return t


def _add_membership(session: Session, thread_id: int, article_id: int) -> ThreadMembership:
    m = ThreadMembership(
        thread_id=thread_id,
        article_id=article_id,
        suppressed=False,
        assigned_at=_NOW,
    )
    session.add(m)
    session.flush()
    return m


def _make_brief(session: Session, *, age: datetime = _OLD) -> Brief:
    b = Brief(
        status="completed",
        origin="manual",
        period_start=age.replace(hour=0, minute=0, second=0, microsecond=0),
        period_end=age.replace(hour=23, minute=59, second=59),
    )
    session.add(b)
    session.flush()
    session.execute(
        text("UPDATE briefs SET created_at = :ts WHERE id = :id"), {"ts": age, "id": b.id}
    )
    return b


def _row_exists(session: Session, model, row_id: int) -> bool:
    """Check row existence without touching the identity map (avoids ObjectDeletedError)."""
    session.expunge_all()
    return session.get(model, row_id) is not None


# ---------------------------------------------------------------------------
# purge_expired_articles
# ---------------------------------------------------------------------------


class TestPurgeExpiredArticles:
    def test_empty_returns_zero(self, session):
        assert purge_expired_articles(session, _RETENTION) == 0

    def test_old_unsaved_no_membership_deleted(self, session):
        src = _make_source(session, "-a1")
        art = _make_article(session, src.id, "a1", retrieved_at=_OLD)
        session.commit()
        art_id = art.id

        assert purge_expired_articles(session, _RETENTION) == 1
        session.commit()
        assert not _row_exists(session, Article, art_id)

    def test_saved_article_not_deleted(self, session):
        src = _make_source(session, "-a2")
        art = _make_article(session, src.id, "a2", retrieved_at=_OLD, is_saved=True)
        session.commit()

        assert purge_expired_articles(session, _RETENTION) == 0
        assert _row_exists(session, Article, art.id)

    def test_article_in_active_thread_not_deleted(self, session):
        src = _make_source(session, "-a3")
        art = _make_article(session, src.id, "a3", retrieved_at=_OLD)
        t = _make_thread(session, status="active")
        _add_membership(session, t.id, art.id)
        session.commit()

        assert purge_expired_articles(session, _RETENTION) == 0
        assert _row_exists(session, Article, art.id)

    def test_article_in_dormant_thread_not_deleted(self, session):
        src = _make_source(session, "-a4")
        art = _make_article(session, src.id, "a4", retrieved_at=_OLD)
        t = _make_thread(session, status="dormant")
        _add_membership(session, t.id, art.id)
        session.commit()

        assert purge_expired_articles(session, _RETENTION) == 0
        assert _row_exists(session, Article, art.id)

    def test_article_in_archived_thread_deleted(self, session):
        src = _make_source(session, "-a5")
        art = _make_article(session, src.id, "a5", retrieved_at=_OLD)
        t = _make_thread(session, status="archived")
        _add_membership(session, t.id, art.id)
        session.commit()
        art_id = art.id

        assert purge_expired_articles(session, _RETENTION) == 1
        session.commit()
        assert not _row_exists(session, Article, art_id)

    def test_membership_removed_before_article_no_fk_error(self, session):
        """FK-safe deletion: ThreadMembership rows are removed before Article rows (no IntegrityError)."""
        src = _make_source(session, "-a6")
        art = _make_article(session, src.id, "a6", retrieved_at=_OLD)
        t = _make_thread(session, status="archived")
        m = _add_membership(session, t.id, art.id)
        session.commit()
        art_id, mem_id = art.id, m.id

        purge_expired_articles(session, _RETENTION)
        session.commit()  # must not raise IntegrityError

        assert not _row_exists(session, ThreadMembership, mem_id)
        assert not _row_exists(session, Article, art_id)

    def test_recent_article_not_deleted(self, session):
        src = _make_source(session, "-a7")
        art = _make_article(session, src.id, "a7", retrieved_at=_RECENT)
        session.commit()

        assert purge_expired_articles(session, _RETENTION) == 0
        assert _row_exists(session, Article, art.id)

    def test_read_status_does_not_protect(self, session):
        """is_read=True has no protective effect; eligible articles are still deleted."""
        src = _make_source(session, "-a8")
        art = _make_article(session, src.id, "a8", retrieved_at=_OLD, is_read=True)
        session.commit()
        art_id = art.id

        assert purge_expired_articles(session, _RETENTION) == 1
        session.commit()
        assert not _row_exists(session, Article, art_id)

    def test_no_membership_article_deleted(self, session):
        """Article with no thread membership at all is eligible for deletion."""
        src = _make_source(session, "-a9")
        art = _make_article(session, src.id, "a9", retrieved_at=_OLD)
        session.commit()
        art_id = art.id

        assert purge_expired_articles(session, _RETENTION) == 1
        session.commit()
        assert not _row_exists(session, Article, art_id)


# ---------------------------------------------------------------------------
# purge_expired_threads
# ---------------------------------------------------------------------------


class TestPurgeExpiredThreads:
    def test_empty_returns_zero(self, session):
        assert purge_expired_threads(session, _RETENTION) == 0

    def test_old_thread_deleted(self, session):
        t = _make_thread(session, last_updated=_OLD)
        session.commit()
        tid = t.id

        assert purge_expired_threads(session, _RETENTION) == 1
        session.commit()
        assert not _row_exists(session, Thread, tid)

    def test_recent_thread_survives(self, session):
        t = _make_thread(session, last_updated=_RECENT)
        session.commit()

        assert purge_expired_threads(session, _RETENTION) == 0
        assert _row_exists(session, Thread, t.id)

    def test_only_old_deleted_when_mixed(self, session):
        old = _make_thread(session, last_updated=_OLD)
        new = _make_thread(session, last_updated=_RECENT)
        session.commit()
        old_id, new_id = old.id, new.id

        assert purge_expired_threads(session, _RETENTION) == 1
        session.commit()
        assert not _row_exists(session, Thread, old_id)
        assert _row_exists(session, Thread, new_id)


# ---------------------------------------------------------------------------
# purge_expired_briefs
# ---------------------------------------------------------------------------


class TestPurgeExpiredBriefs:
    def test_empty_returns_zero(self, session):
        assert purge_expired_briefs(session, _RETENTION) == 0

    def test_old_brief_deleted(self, session):
        b = _make_brief(session, age=_OLD)
        session.commit()
        bid = b.id

        assert purge_expired_briefs(session, _RETENTION) == 1
        session.commit()
        assert not _row_exists(session, Brief, bid)

    def test_recent_brief_survives(self, session):
        b = _make_brief(session, age=_RECENT)
        session.commit()

        assert purge_expired_briefs(session, _RETENTION) == 0
        assert _row_exists(session, Brief, b.id)

    def test_only_old_deleted_when_mixed(self, session):
        old_b = _make_brief(session, age=_OLD)
        new_b = _make_brief(session, age=_RECENT)
        session.commit()
        old_id, new_id = old_b.id, new_b.id

        assert purge_expired_briefs(session, _RETENTION) == 1
        session.commit()
        assert not _row_exists(session, Brief, old_id)
        assert _row_exists(session, Brief, new_id)


# ---------------------------------------------------------------------------
# purge_expired_llm_calls
# ---------------------------------------------------------------------------


def _make_llm_call(session: Session, *, age: datetime = _OLD) -> LlmCall:
    row = LlmCall(
        id=uuid.uuid4(),
        service="test-svc",
        operation="test-op",
        model="gpt-4.1-mini",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        cached_tokens=0,
        cost_usd=0.001,
        latency_ms=100,
        status="success",
        num_tool_calls=0,
    )
    session.add(row)
    session.flush()
    session.execute(
        text("UPDATE llm_calls SET created_at = :ts WHERE id = :id"),
        {"ts": age, "id": row.id},
    )
    return row


class TestPurgeExpiredLlmCalls:
    def test_empty_returns_zero(self, session):
        session.execute(text("TRUNCATE TABLE llm_calls"))
        session.commit()
        assert purge_expired_llm_calls(session, _RETENTION) == 0

    def test_old_row_deleted(self, session):
        session.execute(text("TRUNCATE TABLE llm_calls"))
        session.commit()
        row = _make_llm_call(session, age=_OLD)
        session.commit()
        row_id = row.id

        assert purge_expired_llm_calls(session, _RETENTION) == 1
        session.commit()
        assert not _row_exists(session, LlmCall, row_id)

    def test_recent_row_retained(self, session):
        session.execute(text("TRUNCATE TABLE llm_calls"))
        session.commit()
        row = _make_llm_call(session, age=_RECENT)
        session.commit()

        assert purge_expired_llm_calls(session, _RETENTION) == 0
        assert _row_exists(session, LlmCall, row.id)

    def test_only_old_deleted_when_mixed(self, session):
        session.execute(text("TRUNCATE TABLE llm_calls"))
        session.commit()
        old_row = _make_llm_call(session, age=_OLD)
        new_row = _make_llm_call(session, age=_RECENT)
        session.commit()
        old_id, new_id = old_row.id, new_row.id

        assert purge_expired_llm_calls(session, _RETENTION) == 1
        session.commit()
        assert not _row_exists(session, LlmCall, old_id)
        assert _row_exists(session, LlmCall, new_id)

    def test_importability(self):
        from aggregator_common.models import LlmCall as _LlmCall  # noqa: F401
        assert _LlmCall.__tablename__ == "llm_calls"
