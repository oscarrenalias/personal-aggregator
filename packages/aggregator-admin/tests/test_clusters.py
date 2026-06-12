"""Integration tests for the clusters admin CLI commands."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from aggregator_admin.main import app
from aggregator_common.models import Thread


def _make_thread(
    session,
    *,
    title: str = "Test Thread",
    tier: str | None = "must_know",
    status: str = "active",
) -> Thread:
    now = datetime.now(tz=timezone.utc)
    thread = Thread(
        representative_title=title,
        first_seen=now,
        last_updated=now,
        status=status,
        tier=tier,
        source_list=[],
        known_facts=[],
        deltas=[],
    )
    session.add(thread)
    session.flush()
    session.commit()
    session.refresh(thread)
    return thread


class TestClustersList:
    def test_list_empty_exits_zero(self, runner, clean_db):
        result = runner.invoke(app, ["clusters", "list"])
        assert result.exit_code == 0

    def test_list_shows_thread_title(self, runner, db_session):
        _make_thread(db_session, title="AI Regulation Update")
        result = runner.invoke(app, ["clusters", "list"])
        assert result.exit_code == 0
        assert "AI Regulation Update" in result.output

    def test_list_tier_filter_includes_matching(self, runner, db_session):
        _make_thread(db_session, title="Must Know Story", tier="must_know")
        _make_thread(db_session, title="Low Noise Story", tier="low_noise")
        result = runner.invoke(app, ["clusters", "list", "--tier", "must_know"])
        assert result.exit_code == 0
        assert "Must Know Story" in result.output
        assert "Low Noise Story" not in result.output

    def test_list_json_output_is_valid(self, runner, db_session):
        _make_thread(db_session, title="JSON Thread")
        result = runner.invoke(app, ["clusters", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert any(r["representative_title"] == "JSON Thread" for r in data)

    def test_list_json_output_has_expected_keys(self, runner, db_session):
        _make_thread(db_session, title="Key Check Thread")
        result = runner.invoke(app, ["clusters", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        row = next(r for r in data if r["representative_title"] == "Key Check Thread")
        assert "id" in row
        assert "tier" in row
        assert "member_count" in row
        assert "last_updated" in row


class TestClustersShow:
    def test_show_valid_id_exits_zero(self, runner, db_session):
        thread = _make_thread(db_session, title="Detailed Story")
        result = runner.invoke(app, ["clusters", "show", str(thread.id)])
        assert result.exit_code == 0

    def test_show_valid_id_outputs_title(self, runner, db_session):
        thread = _make_thread(db_session, title="Climate Policy Report")
        result = runner.invoke(app, ["clusters", "show", str(thread.id)])
        assert result.exit_code == 0
        assert "Climate Policy Report" in result.output

    def test_show_invalid_id_exits_nonzero(self, runner, db_session):
        result = runner.invoke(app, ["clusters", "show", "999999"])
        assert result.exit_code == 1

    def test_show_invalid_id_prints_error_message(self, runner, db_session):
        result = runner.invoke(app, ["clusters", "show", "999999"])
        assert result.exit_code == 1
        assert "999999" in result.output

    def test_show_json_output_is_valid(self, runner, db_session):
        thread = _make_thread(db_session, title="JSON Show Thread")
        result = runner.invoke(app, ["clusters", "show", str(thread.id), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["id"] == thread.id
        assert data["representative_title"] == "JSON Show Thread"

    def test_show_json_has_score_fields(self, runner, db_session):
        thread = _make_thread(db_session, title="Score Thread")
        result = runner.invoke(app, ["clusters", "show", str(thread.id), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "relevance_score" in data
        assert "novelty_score" in data
        assert "importance_score" in data


class TestClustersRecluster:
    def test_recluster_exits_zero(self, runner, clean_db):
        result = runner.invoke(app, ["clusters", "recluster"])
        assert result.exit_code == 0

    def test_recluster_prints_confirmation(self, runner, clean_db):
        result = runner.invoke(app, ["clusters", "recluster"])
        assert result.exit_code == 0
        assert "enqueued" in result.output.lower()

    def test_recluster_idempotent_second_call(self, runner, clean_db):
        result1 = runner.invoke(app, ["clusters", "recluster"])
        result2 = runner.invoke(app, ["clusters", "recluster"])
        assert result1.exit_code == 0
        assert result2.exit_code == 0
