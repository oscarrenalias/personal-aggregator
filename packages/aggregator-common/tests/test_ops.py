"""Integration tests for aggregator_common.ops functions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Generator

import pytest
from sqlalchemy.orm import Session

from aggregator_common.models import Article, Brief, Source
from aggregator_common.ops import (
    list_failures,
    list_stuck,
    pipeline_status,
    reap_stale_claims,
    rerank,
    retry_failed,
)
from aggregator_common.state import ArticleStatus

_FIXED = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


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
        name=f"OpsSource{suffix}",
        feed_url=f"https://ops{suffix}.example.com/feed.xml",
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
    last_error: str | None = None,
    retry_count: int = 0,
    next_retry_at: datetime | None = None,
) -> Article:
    article = Article(
        source_id=source_id,
        dedup_key=dedup_key,
        status=status,
        claimed_by=claimed_by,
        claimed_at=claimed_at,
        last_error=last_error,
        retry_count=retry_count,
        next_retry_at=next_retry_at,
        raw_payload={},
        retrieved_at=_FIXED,
    )
    session.add(article)
    session.flush()
    return article


# ---------------------------------------------------------------------------
# pipeline_status
# ---------------------------------------------------------------------------


class TestPipelineStatus:
    def test_returns_expected_keys(self, session: Session):
        result = pipeline_status(session)
        assert "article_counts" in result
        assert "in_flight" in result
        assert "sources" in result
        assert "enabled" in result["sources"]
        assert "disabled" in result["sources"]

    def test_article_counts_by_status(self, session: Session):
        src = _make_source(session, "-psac")
        _make_article(session, src.id, "psac-1", ArticleStatus.pending_processing)
        _make_article(session, src.id, "psac-2", ArticleStatus.ready)
        result = pipeline_status(session)
        counts = result["article_counts"]
        assert counts.get("pending_processing", 0) >= 1
        assert counts.get("ready", 0) >= 1
        assert counts.get("failed_processing", 0) == 0

    def test_in_flight_counts_claimed_articles(self, session: Session):
        src = _make_source(session, "-psif")
        _make_article(
            session, src.id, "psif-1",
            ArticleStatus.pending_processing,
            claimed_by="worker-1",
            claimed_at=datetime.now(tz=timezone.utc),
        )
        _make_article(session, src.id, "psif-2", ArticleStatus.ready)
        result = pipeline_status(session)
        assert result["in_flight"] >= 1

    def test_source_counts_enabled_and_disabled(self, session: Session):
        session.add(Source(name="PS-En1", feed_url="https://psen1.example.com/feed.xml", enabled=True))
        session.add(Source(name="PS-Dis1", feed_url="https://psdis1.example.com/feed.xml", enabled=False))
        session.flush()
        result = pipeline_status(session)
        assert result["sources"]["enabled"] >= 1
        assert result["sources"]["disabled"] >= 1


# ---------------------------------------------------------------------------
# list_stuck
# ---------------------------------------------------------------------------


class TestListStuck:
    def test_empty_when_no_claims(self, session: Session):
        src = _make_source(session, "-lsnc")
        art = _make_article(session, src.id, "lsnc-1", ArticleStatus.pending_processing)
        result = list_stuck(session, 600)
        ids = [r["id"] for r in result]
        assert art.id not in ids

    def test_returns_stale_articles(self, session: Session):
        src = _make_source(session, "-lsst")
        stale_time = datetime.now(tz=timezone.utc) - timedelta(hours=2)
        art = _make_article(
            session, src.id, "lsst-1",
            ArticleStatus.pending_processing,
            claimed_by="old-worker",
            claimed_at=stale_time,
        )
        result = list_stuck(session, 600)
        ids = [r["id"] for r in result]
        assert art.id in ids

    def test_excludes_fresh_claims(self, session: Session):
        src = _make_source(session, "-lsfr")
        fresh_time = datetime.now(tz=timezone.utc) - timedelta(seconds=60)
        art = _make_article(
            session, src.id, "lsfr-1",
            ArticleStatus.pending_processing,
            claimed_by="new-worker",
            claimed_at=fresh_time,
        )
        result = list_stuck(session, 600)
        ids = [r["id"] for r in result]
        assert art.id not in ids

    def test_includes_source_name_key(self, session: Session):
        src = _make_source(session, "-lssn")
        stale_time = datetime.now(tz=timezone.utc) - timedelta(hours=2)
        _make_article(
            session, src.id, "lssn-1",
            ArticleStatus.pending_processing,
            claimed_by="worker",
            claimed_at=stale_time,
        )
        result = list_stuck(session, 600)
        matching = [r for r in result if r.get("claimed_by") == "worker"]
        assert len(matching) >= 1
        assert "source_name" in matching[0]


# ---------------------------------------------------------------------------
# list_failures
# ---------------------------------------------------------------------------


class TestListFailures:
    def test_both_stages_when_stage_none(self, session: Session):
        src = _make_source(session, "-lfbs")
        _make_article(session, src.id, "lfbs-1", ArticleStatus.failed_processing)
        _make_article(session, src.id, "lfbs-2", ArticleStatus.failed_ranking)
        result = list_failures(session, stage=None)
        statuses = {r["status"] for r in result}
        assert "failed_processing" in statuses
        assert "failed_ranking" in statuses

    def test_stage_processor_filters_to_failed_processing(self, session: Session):
        src = _make_source(session, "-lfpr")
        _make_article(session, src.id, "lfpr-1", ArticleStatus.failed_processing)
        _make_article(session, src.id, "lfpr-2", ArticleStatus.failed_ranking)
        result = list_failures(session, stage="processor")
        assert len(result) >= 1
        assert all(r["status"] == "failed_processing" for r in result)

    def test_stage_summarize_rank_filters_to_failed_ranking(self, session: Session):
        src = _make_source(session, "-lfsr")
        _make_article(session, src.id, "lfsr-1", ArticleStatus.failed_processing)
        _make_article(session, src.id, "lfsr-2", ArticleStatus.failed_ranking)
        result = list_failures(session, stage="summarize_rank")
        assert len(result) >= 1
        assert all(r["status"] == "failed_ranking" for r in result)

    def test_invalid_stage_raises_value_error(self, session: Session):
        with pytest.raises(ValueError, match="stage"):
            list_failures(session, stage="invalid_stage")


# ---------------------------------------------------------------------------
# reap_stale_claims
# ---------------------------------------------------------------------------


class TestReapStaleClaims:
    def test_releases_stale_article_claims(self, session: Session):
        src = _make_source(session, "-rsca")
        stale_time = datetime.now(tz=timezone.utc) - timedelta(hours=2)
        art = _make_article(
            session, src.id, "rsca-1",
            ArticleStatus.pending_processing,
            claimed_by="old-worker",
            claimed_at=stale_time,
        )
        result = reap_stale_claims(session, 600)
        assert result["articles_released"] >= 1
        session.refresh(art)
        assert art.claimed_by is None
        assert art.claimed_at is None

    def test_releases_stale_brief_claims(self, session: Session):
        stale_time = datetime.now(tz=timezone.utc) - timedelta(hours=2)
        now = datetime.now(tz=timezone.utc)
        period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        brief = Brief(
            status="generating",
            origin="schedule",
            period_start=period_start,
            period_end=period_start.replace(hour=23, minute=59),
            claimed_by="old-brief-worker",
            claimed_at=stale_time,
        )
        session.add(brief)
        session.flush()

        result = reap_stale_claims(session, 600)

        assert result["briefs_released"] >= 1
        session.refresh(brief)
        assert brief.claimed_by is None
        assert brief.claimed_at is None
        assert brief.status == "pending"

    def test_returns_per_kind_counts(self, session: Session):
        result = reap_stale_claims(session, 600)
        assert "articles_released" in result
        assert "briefs_released" in result

    def test_leaves_fresh_article_claims_untouched(self, session: Session):
        src = _make_source(session, "-rscf")
        fresh_time = datetime.now(tz=timezone.utc) - timedelta(seconds=60)
        art = _make_article(
            session, src.id, "rscf-1",
            ArticleStatus.pending_processing,
            claimed_by="fresh-worker",
            claimed_at=fresh_time,
        )
        reap_stale_claims(session, 600)
        session.refresh(art)
        assert art.claimed_by == "fresh-worker"


# ---------------------------------------------------------------------------
# retry_failed
# ---------------------------------------------------------------------------


class TestRetryFailed:
    def test_invalid_stage_raises_value_error(self, session: Session):
        with pytest.raises(ValueError, match="stage"):
            retry_failed(session, stage="invalid")

    def test_clears_all_fields_for_processor_stage(self, session: Session):
        src = _make_source(session, "-rtfp")
        art = _make_article(
            session, src.id, "rtfp-1",
            ArticleStatus.failed_processing,
            claimed_by="w",
            claimed_at=_FIXED,
            last_error="oops",
            retry_count=2,
        )
        result = retry_failed(session, stage="processor")
        assert result["retried"] >= 1
        session.refresh(art)
        assert art.status == "pending_processing"
        assert art.claimed_by is None
        assert art.claimed_at is None
        assert art.last_error is None
        assert art.retry_count == 0
        assert art.next_retry_at is None

    def test_retry_ranking_stage_transitions_to_pending_ranking(self, session: Session):
        src = _make_source(session, "-rtfr")
        art = _make_article(
            session, src.id, "rtfr-1",
            ArticleStatus.failed_ranking,
            last_error="rank error",
            retry_count=1,
        )
        result = retry_failed(session, stage="summarize_rank")
        assert result["retried"] >= 1
        session.refresh(art)
        assert art.status == "pending_ranking"

    def test_retry_both_stages_when_stage_none(self, session: Session):
        src = _make_source(session, "-rtfa")
        _make_article(session, src.id, "rtfa-1", ArticleStatus.failed_processing)
        _make_article(session, src.id, "rtfa-2", ArticleStatus.failed_ranking)
        result = retry_failed(session, stage=None)
        assert result["retried"] >= 2

    def test_retry_single_article_by_id(self, session: Session):
        src = _make_source(session, "-rtfid")
        art = _make_article(session, src.id, "rtfid-1", ArticleStatus.failed_processing)
        result = retry_failed(session, article_id=art.id)
        assert result["retried"] == 1


# ---------------------------------------------------------------------------
# rerank
# ---------------------------------------------------------------------------


class TestRerank:
    def test_all_ready_transitions_to_pending_ranking(self, session: Session):
        src = _make_source(session, "-rrar")
        art = _make_article(session, src.id, "rrar-1", ArticleStatus.ready)
        result = rerank(session, all_ready=True)
        assert result["reranked"] >= 1
        session.refresh(art)
        assert art.status == "pending_ranking"

    def test_failed_only_reranks_failed_ranking(self, session: Session):
        src = _make_source(session, "-rrfo")
        art = _make_article(session, src.id, "rrfo-1", ArticleStatus.failed_ranking)
        result = rerank(session, failed_only=True)
        assert result["reranked"] >= 1
        session.refresh(art)
        assert art.status == "pending_ranking"

    def test_single_article_by_id(self, session: Session):
        src = _make_source(session, "-rrid")
        art = _make_article(session, src.id, "rrid-1", ArticleStatus.ready)
        result = rerank(session, article_id=art.id)
        assert result == {"reranked": 1}
        session.refresh(art)
        assert art.status == "pending_ranking"

    def test_failed_processing_raises_value_error(self, session: Session):
        """failed_processing → pending_ranking is not an allowed transition."""
        src = _make_source(session, "-rrbad")
        art = _make_article(session, src.id, "rrbad-1", ArticleStatus.failed_processing)
        with pytest.raises(ValueError, match="Invalid transition"):
            rerank(session, article_id=art.id)

    def test_no_flags_returns_zero_reranked(self, session: Session):
        result = rerank(session)
        assert result == {"reranked": 0}
