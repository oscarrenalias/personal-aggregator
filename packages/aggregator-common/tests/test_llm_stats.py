"""Tests for aggregator_common.queries.llm_stats aggregation function."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Generator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from aggregator_common.models import LlmCall
from aggregator_common.queries import llm_stats

_NOW = datetime.now(timezone.utc)
_RECENT = _NOW - timedelta(days=1)
_OLD = _NOW - timedelta(days=60)
_DAYS = 30


@pytest.fixture
def session(db_session_factory) -> Generator[Session, None, None]:
    s = db_session_factory()
    s.execute(text("TRUNCATE TABLE llm_calls"))
    s.commit()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _make_llm_call(
    session: Session,
    *,
    service: str,
    model: str,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    total_tokens: int = 150,
    cached_tokens: int = 0,
    cost_usd: float | None = 0.001,
    latency_ms: int = 200,
    status: str = "success",
    error_type: str | None = None,
    finish_reason: str | None = "stop",
    num_tool_calls: int = 0,
    created_at: datetime | None = None,
) -> LlmCall:
    row = LlmCall(
        id=uuid.uuid4(),
        service=service,
        operation="test-op",
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cached_tokens=cached_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        status=status,
        error_type=error_type,
        finish_reason=finish_reason,
        num_tool_calls=num_tool_calls,
    )
    session.add(row)
    session.flush()
    if created_at is not None:
        session.execute(
            text("UPDATE llm_calls SET created_at = :ts WHERE id = :id"),
            {"ts": created_at, "id": row.id},
        )
    return row


class TestLlmStats:
    def test_empty_table_returns_empty_list(self, session):
        results = llm_stats(session, days=_DAYS)
        assert results == []

    def test_aggregates_two_service_model_pairs(self, session):
        # Pair A: clusterer / gpt-4.1-mini — 2 rows, one with finish_reason='length'
        _make_llm_call(
            session,
            service="clusterer",
            model="gpt-4.1-mini",
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.001,
            finish_reason="stop",
            num_tool_calls=2,
            created_at=_RECENT,
        )
        _make_llm_call(
            session,
            service="clusterer",
            model="gpt-4.1-mini",
            prompt_tokens=200,
            completion_tokens=80,
            cost_usd=0.002,
            finish_reason="length",
            num_tool_calls=0,
            created_at=_RECENT,
        )
        # Pair B: summarize-rank / gpt-4.1-mini — 1 error row
        _make_llm_call(
            session,
            service="summarize-rank",
            model="gpt-4.1-mini",
            prompt_tokens=50,
            completion_tokens=0,
            cost_usd=None,
            status="error",
            error_type="timeout",
            finish_reason=None,
            created_at=_RECENT,
        )
        session.commit()

        results = llm_stats(session, days=_DAYS)
        assert len(results) == 2

        by_service = {r.service: r for r in results}
        assert "clusterer" in by_service
        assert "summarize-rank" in by_service

        c = by_service["clusterer"]
        assert c.request_count == 2
        assert c.total_cost_usd == pytest.approx(0.003, rel=1e-4)
        assert c.avg_prompt_tokens == pytest.approx(150.0, rel=1e-4)
        assert c.truncated_count == 1
        assert c.error_count == 0
        assert c.max_tool_calls == 2

        sr = by_service["summarize-rank"]
        assert sr.request_count == 1
        assert sr.error_count == 1
        assert sr.error_pct == pytest.approx(100.0, rel=1e-4)

    def test_rows_outside_window_excluded(self, session):
        _make_llm_call(
            session,
            service="svc",
            model="gpt-4.1-mini",
            created_at=_OLD,
        )
        session.commit()
        results = llm_stats(session, days=_DAYS)
        assert results == []

    def test_rows_inside_window_included(self, session):
        _make_llm_call(
            session,
            service="svc",
            model="gpt-4.1-mini",
            created_at=_RECENT,
        )
        session.commit()
        results = llm_stats(session, days=_DAYS)
        assert len(results) == 1
        assert results[0].request_count == 1
