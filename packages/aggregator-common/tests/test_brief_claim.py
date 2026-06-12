"""Tests for aggregator_common.brief_claim — claim/complete/fail/reap helpers."""

from datetime import datetime, timedelta, timezone
from typing import Generator

import pytest
from sqlalchemy.orm import Session

from aggregator_common.brief_claim import (
    claim_brief,
    complete_brief,
    fail_brief,
    reap_stale_brief_claims,
)
from aggregator_common.models import Brief

_NOW = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
_PERIOD_START = _NOW.replace(hour=0, minute=0, second=0, microsecond=0)
_PERIOD_END = _PERIOD_START.replace(hour=23, minute=59, second=59)


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
    status: str = "pending",
    origin: str = "manual",
    claimed_by: str | None = None,
    claimed_at: datetime | None = None,
) -> Brief:
    brief = Brief(
        status=status,
        origin=origin,
        period_start=_PERIOD_START,
        period_end=_PERIOD_END,
        claimed_by=claimed_by,
        claimed_at=claimed_at,
    )
    session.add(brief)
    session.flush()
    return brief


class TestClaimBrief:
    def test_returns_none_when_no_pending(self, session: Session):
        result = claim_brief(session, "worker-1", _NOW)
        assert result is None

    def test_claims_pending_brief(self, session: Session):
        brief = _make_brief(session, status="pending")

        result = claim_brief(session, "worker-1", _NOW)

        assert result is not None
        assert result.id == brief.id
        assert result.status == "generating"
        assert result.claimed_by == "worker-1"
        assert result.claimed_at == _NOW

    def test_generating_brief_not_reclaimed(self, session: Session):
        _make_brief(
            session,
            status="generating",
            claimed_by="other-worker",
            claimed_at=_NOW,
        )

        result = claim_brief(session, "worker-1", _NOW)
        assert result is None

    def test_ready_brief_not_claimable(self, session: Session):
        _make_brief(session, status="ready")

        result = claim_brief(session, "worker-1", _NOW)
        assert result is None

    def test_failed_brief_not_claimable(self, session: Session):
        _make_brief(session, status="failed")

        result = claim_brief(session, "worker-1", _NOW)
        assert result is None


class TestCompleteBrief:
    def test_marks_ready_and_clears_claim(self, session: Session):
        brief = _make_brief(session, status="generating", claimed_by="w", claimed_at=_NOW)

        complete_brief(
            session,
            brief.id,
            model="gpt-4.1",
            headline="Test Headline",
            intro="Test intro.",
            generated_at=_NOW,
        )

        session.refresh(brief)
        assert brief.status == "ready"
        assert brief.model == "gpt-4.1"
        assert brief.headline == "Test Headline"
        assert brief.intro == "Test intro."
        assert brief.claimed_by is None
        assert brief.claimed_at is None

    def test_unknown_id_raises(self, session: Session):
        with pytest.raises(ValueError, match="not found"):
            complete_brief(session, 99999, "model", "headline", "intro", _NOW)


class TestFailBrief:
    def test_marks_failed_stores_error(self, session: Session):
        brief = _make_brief(session, status="generating", claimed_by="w", claimed_at=_NOW)

        fail_brief(session, brief.id, "something went wrong")

        session.refresh(brief)
        assert brief.status == "failed"
        assert brief.error == "something went wrong"
        assert brief.claimed_by is None
        assert brief.claimed_at is None

    def test_unknown_id_raises(self, session: Session):
        with pytest.raises(ValueError, match="not found"):
            fail_brief(session, 99999, "error")


class TestReapStaleBriefClaims:
    def test_stale_claim_released(self, session: Session):
        lease_seconds = 300.0
        stale_time = _NOW - timedelta(seconds=lease_seconds + 1)

        brief = _make_brief(
            session,
            status="generating",
            claimed_by="old-worker",
            claimed_at=stale_time,
        )

        count = reap_stale_brief_claims(session, lease_seconds, _NOW)

        assert count == 1
        session.refresh(brief)
        assert brief.status == "pending"
        assert brief.claimed_by is None
        assert brief.claimed_at is None

    def test_fresh_claim_untouched(self, session: Session):
        lease_seconds = 300.0
        fresh_time = _NOW - timedelta(seconds=lease_seconds - 1)

        brief = _make_brief(
            session,
            status="generating",
            claimed_by="active-worker",
            claimed_at=fresh_time,
        )

        count = reap_stale_brief_claims(session, lease_seconds, _NOW)

        assert count == 0
        session.refresh(brief)
        assert brief.claimed_by == "active-worker"

    def test_pending_brief_not_reaped(self, session: Session):
        _make_brief(session, status="pending")

        count = reap_stale_brief_claims(session, 300.0, _NOW)
        assert count == 0

    def test_multiple_stale_claims_all_released(self, session: Session):
        stale_time = _NOW - timedelta(seconds=400)
        _make_brief(session, status="generating", claimed_by="w1", claimed_at=stale_time)
        _make_brief(session, status="generating", claimed_by="w2", claimed_at=stale_time)

        count = reap_stale_brief_claims(session, 300.0, _NOW)
        assert count == 2
