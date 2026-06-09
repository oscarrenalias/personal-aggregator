import threading
from datetime import datetime, timedelta, timezone
from typing import Generator

import pytest
from sqlalchemy import delete as sql_delete
from sqlalchemy.orm import Session

from aggregator_common.claim import claim_batch, fail, reap_stale_claims
from aggregator_common.models import Article, Source
from aggregator_common.state import ArticleStatus

_NOW = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


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
        name=f"test-source{suffix}",
        feed_url=f"https://example.com/feed{suffix}.xml",
    )
    session.add(src)
    session.flush()
    return src


def _make_article(
    session: Session,
    source_id: int,
    dedup_key: str,
    status: ArticleStatus,
    *,
    claimed_by: str | None = None,
    claimed_at: datetime | None = None,
    next_retry_at: datetime | None = None,
    retry_count: int = 0,
) -> Article:
    article = Article(
        source_id=source_id,
        dedup_key=dedup_key,
        status=status,
        claimed_by=claimed_by,
        claimed_at=claimed_at,
        next_retry_at=next_retry_at,
        retry_count=retry_count,
        raw_payload={},
        retrieved_at=_NOW,
    )
    session.add(article)
    session.flush()
    return article


class TestClaimBatchConcurrency:
    def test_skip_locked_returns_disjoint_sets(self, db_session_factory):
        """Two concurrent sessions on the same status never claim overlapping rows."""
        setup = db_session_factory()
        src = Source(name="conc-src", feed_url="https://example.com/conc.xml")
        setup.add(src)
        setup.flush()
        for i in range(10):
            setup.add(Article(
                source_id=src.id,
                dedup_key=f"conc-{i}",
                status=ArticleStatus.pending_processing,
                raw_payload={},
                retrieved_at=_NOW,
            ))
        setup.commit()
        source_id = src.id
        setup.close()

        now = datetime.now(tz=timezone.utc)
        # T1 signals it holds row locks; T2 signals it has committed.
        t1_locked = threading.Event()
        t2_done = threading.Event()
        results: list[set[int]] = [set(), set()]
        errors: list[Exception | None] = [None, None]

        def worker1() -> None:
            s = db_session_factory()
            try:
                claimed = claim_batch(s, ArticleStatus.pending_processing, "worker-0", 5, now)
                results[0] = {a.id for a in claimed}
                t1_locked.set()           # row locks acquired; let T2 proceed
                t2_done.wait(timeout=10)  # keep tx open until T2 commits
                s.commit()
            except Exception as e:
                errors[0] = e
                s.rollback()
            finally:
                s.close()

        def worker2() -> None:
            t1_locked.wait(timeout=10)  # ensure T1 holds locks before we SELECT
            s = db_session_factory()
            try:
                claimed = claim_batch(s, ArticleStatus.pending_processing, "worker-1", 10, now)
                results[1] = {a.id for a in claimed}
                s.commit()
            except Exception as e:
                errors[1] = e
                s.rollback()
            finally:
                s.close()
                t2_done.set()

        t1 = threading.Thread(target=worker1)
        t2 = threading.Thread(target=worker2)
        try:
            t1.start()
            t2.start()
            t1.join(timeout=15)
            t2.join(timeout=15)

            assert errors[0] is None, f"worker-0 error: {errors[0]}"
            assert errors[1] is None, f"worker-1 error: {errors[1]}"
            assert results[0].isdisjoint(results[1]), "Workers claimed overlapping rows"
            assert len(results[0] | results[1]) == 10, "Together workers must claim all 10 rows"
        finally:
            cleanup = db_session_factory()
            cleanup.execute(sql_delete(Article).where(Article.source_id == source_id))
            cleanup.execute(sql_delete(Source).where(Source.id == source_id))
            cleanup.commit()
            cleanup.close()


class TestClaimBatchRetryGate:
    def test_next_retry_at_in_future_skips_row(self, session: Session):
        """Row with next_retry_at in the future is excluded from claim_batch."""
        src = _make_source(session, "-retry-gate")
        blocked = _make_article(
            session, src.id, "gate-blocked",
            ArticleStatus.pending_processing,
            next_retry_at=_NOW + timedelta(hours=1),
        )
        ready = _make_article(
            session, src.id, "gate-ready",
            ArticleStatus.pending_processing,
        )

        claimed = claim_batch(session, ArticleStatus.pending_processing, "worker", 10, _NOW)
        claimed_ids = {a.id for a in claimed}

        assert ready.id in claimed_ids
        assert blocked.id not in claimed_ids


class TestFail:
    def test_backoff_and_exhaustion_processor_stage(self, session: Session):
        """fail() re-queues with exponential backoff; at max_retries sets failed_processing."""
        src = _make_source(session, "-fail-proc")
        article = _make_article(
            session, src.id, "fail-proc-1",
            ArticleStatus.pending_processing,
        )

        max_retries = 3
        backoff = 10.0

        fail(session, article, "err1", max_retries, backoff, _NOW)
        assert article.retry_count == 1
        assert article.status == ArticleStatus.pending_processing
        assert article.next_retry_at == _NOW + timedelta(seconds=10)  # 10 * 2^0

        fail(session, article, "err2", max_retries, backoff, _NOW)
        assert article.retry_count == 2
        assert article.status == ArticleStatus.pending_processing
        assert article.next_retry_at == _NOW + timedelta(seconds=20)  # 10 * 2^1

        fail(session, article, "err3", max_retries, backoff, _NOW)
        assert article.retry_count == max_retries
        assert article.status == ArticleStatus.failed_processing
        assert article.next_retry_at is None

    def test_backoff_and_exhaustion_ranking_stage(self, session: Session):
        """fail() on pending_ranking exhausts to failed_ranking."""
        src = _make_source(session, "-fail-rank")
        article = _make_article(
            session, src.id, "fail-rank-1",
            ArticleStatus.pending_ranking,
        )

        max_retries = 2
        backoff = 5.0

        fail(session, article, "err1", max_retries, backoff, _NOW)
        assert article.retry_count == 1
        assert article.status == ArticleStatus.pending_ranking
        assert article.next_retry_at == _NOW + timedelta(seconds=5)  # 5 * 2^0

        fail(session, article, "err2", max_retries, backoff, _NOW)
        assert article.retry_count == max_retries
        assert article.status == ArticleStatus.failed_ranking
        assert article.next_retry_at is None


class TestReapStaleClaims:
    def test_stale_released_fresh_claim_untouched(self, session: Session):
        """reap_stale_claims releases claims older than the lease; leaves fresh claims alone."""
        src = _make_source(session, "-reap")
        lease_seconds = 300.0

        stale_time = _NOW - timedelta(seconds=lease_seconds + 1)
        fresh_time = _NOW - timedelta(seconds=lease_seconds - 1)

        stale = _make_article(
            session, src.id, "stale-1",
            ArticleStatus.pending_processing,
            claimed_by="old-worker",
            claimed_at=stale_time,
        )
        fresh = _make_article(
            session, src.id, "fresh-1",
            ArticleStatus.pending_processing,
            claimed_by="new-worker",
            claimed_at=fresh_time,
        )

        reaped = reap_stale_claims(session, lease_seconds, _NOW)

        assert reaped == 1
        assert stale.claimed_by is None
        assert stale.claimed_at is None
        assert fresh.claimed_by == "new-worker"
        assert fresh.claimed_at == fresh_time
