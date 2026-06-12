"""Tests for aggregator_admin brief CLI commands."""

import json

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from aggregator_admin.brief import brief_app
from aggregator_common.models import Brief

from conftest import make_brief


class TestBriefGenerateCommand:
    def test_exits_zero(self, runner, db_session):
        result = runner.invoke(brief_app, ["generate"])
        assert result.exit_code == 0, result.output

    def test_inserts_pending_brief(self, runner, db_engine, clean_db):
        runner.invoke(brief_app, ["generate"])

        factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
        s = factory()
        try:
            brief = s.execute(
                select(Brief).where(Brief.status == "pending")
            ).scalar_one_or_none()
            assert brief is not None
            assert brief.origin == "manual"
        finally:
            s.close()

    def test_output_contains_brief_id(self, runner, db_session):
        result = runner.invoke(brief_app, ["generate"])
        assert "created" in result.output.lower() or "Brief" in result.output


class TestBriefShowCommand:
    def test_shows_latest_ready_brief(self, runner, db_session):
        make_brief(db_session, status="ready", headline="Show Me This", intro="Intro text.")

        result = runner.invoke(brief_app, ["show"])
        assert result.exit_code == 0, result.output
        assert "Show Me This" in result.output

    def test_no_ready_brief_exits_1(self, runner, db_session):
        result = runner.invoke(brief_app, ["show"])
        assert result.exit_code == 1

    def test_unknown_id_exits_1(self, runner, db_session):
        result = runner.invoke(brief_app, ["show", "999999"])
        assert result.exit_code == 1

    def test_json_flag_emits_valid_json(self, runner, db_session):
        make_brief(db_session, status="ready", headline="JSON Brief", intro="JSON intro.")

        result = runner.invoke(brief_app, ["show", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["headline"] == "JSON Brief"
        assert data["status"] == "ready"

    def test_show_by_id(self, runner, db_session):
        brief = make_brief(db_session, status="ready", headline="Specific Brief")

        result = runner.invoke(brief_app, ["show", str(brief.id)])
        assert result.exit_code == 0, result.output
        assert "Specific Brief" in result.output


class TestBriefListCommand:
    def test_exits_zero_empty(self, runner, db_session):
        result = runner.invoke(brief_app, ["list"])
        assert result.exit_code == 0

    def test_returns_at_most_limit_rows(self, runner, db_session):
        for i in range(15):
            make_brief(db_session, status="ready", headline=f"Brief {i}")

        result = runner.invoke(brief_app, ["list", "--limit", "10"])
        assert result.exit_code == 0
        # The table output should not exceed 10 rows (plus header/separator).
        # Count lines containing "ready" to approximate row count.
        ready_lines = [line for line in result.output.splitlines() if "ready" in line]
        assert len(ready_lines) <= 10

    def test_json_flag_emits_list(self, runner, db_session):
        make_brief(db_session, status="ready", headline="Listed Brief")

        result = runner.invoke(brief_app, ["list", "--json"])
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        assert isinstance(rows, list)
        assert len(rows) >= 1
        assert rows[0]["headline"] == "Listed Brief"
