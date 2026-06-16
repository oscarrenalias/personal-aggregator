"""Tests for the MCP status://llm resource and llm_stats tool."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

_NOW = datetime.now(tz=timezone.utc)
_RECENT = _NOW - timedelta(days=1)


def _insert_llm_call(session, *, service="svc", model="gpt-4.1-mini"):
    from aggregator_common.models import LlmCall

    row = LlmCall(
        id=uuid.uuid4(),
        service=service,
        operation="test-op",
        model=model,
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cached_tokens=0,
        cost_usd=0.001,
        latency_ms=200,
        status="success",
        num_tool_calls=0,
    )
    session.add(row)
    session.flush()
    session.execute(
        text("UPDATE llm_calls SET created_at = :ts WHERE id = :id"),
        {"ts": _RECENT, "id": row.id},
    )
    session.commit()


class TestLlmStatusResource:
    def test_returns_days_and_stats_keys(self, session):
        """status://llm resource returns a dict with 'days' and 'stats' keys."""
        import aggregator_common.db as db_mod
        from aggregator_mcp.server import llm_status_resource

        original = db_mod.SessionFactory
        try:
            db_mod.SessionFactory = lambda: session
            result = llm_status_resource()
        finally:
            db_mod.SessionFactory = original

        assert isinstance(result, dict)
        assert "days" in result
        assert "stats" in result
        assert result["days"] == 7
        assert isinstance(result["stats"], list)

    def test_stats_entries_have_expected_fields(self, session):
        """Each stats entry has the required aggregate fields."""
        import aggregator_common.db as db_mod
        from aggregator_mcp.server import llm_status_resource

        session.execute(text("TRUNCATE TABLE llm_calls"))
        session.commit()
        _insert_llm_call(session, service="clusterer", model="gpt-4.1-mini")

        original = db_mod.SessionFactory
        try:
            db_mod.SessionFactory = lambda: session
            result = llm_status_resource()
        finally:
            db_mod.SessionFactory = original

        assert len(result["stats"]) >= 1
        entry = result["stats"][0]
        for field in (
            "service",
            "model",
            "request_count",
            "total_cost_usd",
            "avg_cost_usd",
            "avg_prompt_tokens",
            "p95_prompt_tokens",
            "avg_completion_tokens",
            "truncated_count",
            "error_count",
            "error_pct",
            "avg_tool_calls",
            "max_tool_calls",
        ):
            assert field in entry, f"Missing field {field!r} in stats entry"
        assert "prompt_text" not in entry

    def test_empty_db_returns_empty_stats(self, session):
        import aggregator_common.db as db_mod
        from aggregator_mcp.server import llm_status_resource

        session.execute(text("TRUNCATE TABLE llm_calls"))
        session.commit()

        original = db_mod.SessionFactory
        try:
            db_mod.SessionFactory = lambda: session
            result = llm_status_resource()
        finally:
            db_mod.SessionFactory = original

        assert result["days"] == 7
        assert result["stats"] == []

    def test_status_pipeline_resource_unaffected(self, mcp_server):
        """status://pipeline resource still works after status://llm was added."""
        resources = asyncio.run(mcp_server.list_resources())
        uris = {str(r.uri) for r in resources}
        assert "status://pipeline" in uris
        assert "status://llm" in uris
