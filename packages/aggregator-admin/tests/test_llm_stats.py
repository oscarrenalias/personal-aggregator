"""Tests for the aggregator-admin llm-stats CLI command."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import text

from aggregator_admin.main import app
from aggregator_common.models import LlmCall

_NOW = datetime.now(tz=timezone.utc)
_RECENT = _NOW - timedelta(days=1)


def _insert_llm_call(session, *, service="svc", model="gpt-4.1-mini", created_at=_RECENT):
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
        {"ts": created_at, "id": row.id},
    )
    session.commit()
    return row


class TestLlmStatsCommand:
    def test_empty_db_prints_message_and_no_error(self, runner, db_session, db_engine):
        with db_engine.connect() as conn:
            conn.execute(text("TRUNCATE TABLE llm_calls"))
            conn.commit()
        result = runner.invoke(app, ["llm-stats"])
        assert result.exit_code == 0
        assert "No LLM calls" in result.output

    def test_appears_in_help(self, runner):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "llm-stats" in result.output

    def test_days_option_accepted(self, runner, db_session, db_engine):
        with db_engine.connect() as conn:
            conn.execute(text("TRUNCATE TABLE llm_calls"))
            conn.commit()
        result = runner.invoke(app, ["llm-stats", "--days", "30"])
        assert result.exit_code == 0

    def test_renders_table_when_rows_present(self, runner, db_session, db_engine):
        with db_engine.connect() as conn:
            conn.execute(text("TRUNCATE TABLE llm_calls"))
            conn.commit()
        _insert_llm_call(db_session, service="clusterer", model="gpt-4.1-mini")
        result = runner.invoke(app, ["llm-stats"])
        assert result.exit_code == 0
        # Rich table renders; heading confirms stats were returned
        assert "LLM stats" in result.output
        # Table rows are rendered (at least one separator line beyond the header)
        assert "─" in result.output or "└" in result.output
