"""Integration tests for ops sub-commands."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from aggregator_admin.main import app
from aggregator_common.state import ArticleStatus

from .conftest import make_article, make_source

_NOW = datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# ops status
# ---------------------------------------------------------------------------

def test_ops_status_article_counts(runner, db_session):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, status=ArticleStatus.pending_processing, dedup_key="k1")
    make_article(db_session, source_id=src.id, status=ArticleStatus.ready, dedup_key="k2")
    result = runner.invoke(app, ["ops", "status", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    counts = data["article_counts"]
    assert counts["pending_processing"] == 1
    assert counts["ready"] == 1
    assert counts.get("failed_processing", 0) == 0


def test_ops_status_in_flight_count(runner, db_session):
    src = make_source(db_session)
    make_article(
        db_session,
        source_id=src.id,
        status=ArticleStatus.pending_processing,
        claimed_by="worker-1",
        claimed_at=_NOW,
        dedup_key="k1",
    )
    make_article(db_session, source_id=src.id, status=ArticleStatus.ready, dedup_key="k2")
    result = runner.invoke(app, ["ops", "status", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["in_flight"] == 1


def test_ops_status_source_counts(runner, db_session):
    make_source(db_session, enabled=True, url="http://e1.com/feed.xml")
    make_source(db_session, enabled=True, url="http://e2.com/feed.xml", name="Feed 2")
    make_source(db_session, enabled=False, url="http://d1.com/feed.xml", name="Disabled")
    result = runner.invoke(app, ["ops", "status", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["sources"]["enabled"] == 2
    assert data["sources"]["disabled"] == 1


def test_ops_status_table_output(runner, db_session):
    result = runner.invoke(app, ["ops", "status"])
    assert result.exit_code == 0
    assert "pending_processing" in result.output


# ---------------------------------------------------------------------------
# ops stuck
# ---------------------------------------------------------------------------

def test_ops_stuck_no_stale_claims(runner, db_session):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    result = runner.invoke(app, ["ops", "stuck", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data == []


def test_ops_stuck_returns_stale_claims(runner, db_session):
    src = make_source(db_session)
    stale_time = _NOW - timedelta(seconds=7200)
    make_article(
        db_session,
        source_id=src.id,
        status=ArticleStatus.pending_processing,
        claimed_by="worker-stale",
        claimed_at=stale_time,
    )
    result = runner.invoke(app, ["ops", "stuck", "--lease-seconds", "600", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["claimed_by"] == "worker-stale"


def test_ops_stuck_respects_explicit_lease_seconds(runner, db_session):
    src = make_source(db_session)
    # Claimed 30 seconds ago
    claimed_30s_ago = _NOW - timedelta(seconds=30)
    make_article(
        db_session,
        source_id=src.id,
        status=ArticleStatus.pending_processing,
        claimed_by="worker-2",
        claimed_at=claimed_30s_ago,
    )
    # Under 10s lease → stale
    result_short = runner.invoke(app, ["ops", "stuck", "--lease-seconds", "10", "--json"])
    assert result_short.exit_code == 0
    data_short = json.loads(result_short.output)
    assert len(data_short) == 1

    # Under 600s lease → not stale
    result_long = runner.invoke(app, ["ops", "stuck", "--lease-seconds", "600", "--json"])
    assert result_long.exit_code == 0
    data_long = json.loads(result_long.output)
    assert len(data_long) == 0


# ---------------------------------------------------------------------------
# ops failures
# ---------------------------------------------------------------------------

def test_ops_failures_all_stages(runner, db_session):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, status=ArticleStatus.failed_processing, dedup_key="k1")
    make_article(db_session, source_id=src.id, status=ArticleStatus.failed_ranking, dedup_key="k2")
    make_article(db_session, source_id=src.id, status=ArticleStatus.ready, dedup_key="k3")
    result = runner.invoke(app, ["ops", "failures", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 2
    statuses = {row["status"] for row in data}
    assert "failed_processing" in statuses
    assert "failed_ranking" in statuses


def test_ops_failures_stage_ranking_excludes_processing(runner, db_session):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, status=ArticleStatus.failed_processing, dedup_key="k1")
    make_article(db_session, source_id=src.id, status=ArticleStatus.failed_ranking, dedup_key="k2")
    result = runner.invoke(app, ["ops", "failures", "--stage", "ranking", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["status"] == "failed_ranking"


def test_ops_failures_stage_processing(runner, db_session):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, status=ArticleStatus.failed_processing, dedup_key="k1")
    make_article(db_session, source_id=src.id, status=ArticleStatus.failed_ranking, dedup_key="k2")
    result = runner.invoke(app, ["ops", "failures", "--stage", "processing", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["status"] == "failed_processing"


def test_ops_failures_invalid_stage_exits_nonzero(runner, db_session):
    result = runner.invoke(app, ["ops", "failures", "--stage", "invalid"])
    assert result.exit_code == 1
    assert "processing" in result.output or "ranking" in result.output


# ---------------------------------------------------------------------------
# ops reap
# ---------------------------------------------------------------------------

def test_ops_reap_zero_released(runner, db_session):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)  # no claim
    result = runner.invoke(app, ["ops", "reap"])
    assert result.exit_code == 0
    assert "Released 0" in result.output


def test_ops_reap_with_stale_claims(runner, db_session):
    src = make_source(db_session)
    stale_time = _NOW - timedelta(seconds=7200)
    art = make_article(
        db_session,
        source_id=src.id,
        status=ArticleStatus.pending_processing,
        claimed_by="worker-stale",
        claimed_at=stale_time,
    )
    result = runner.invoke(app, ["ops", "reap", "--lease-seconds", "600"])
    assert result.exit_code == 0
    assert "Released 1" in result.output
    db_session.refresh(art)
    assert art.claimed_by is None
    assert art.claimed_at is None


def test_ops_reap_explicit_lease_seconds(runner, db_session):
    src = make_source(db_session)
    claimed_30s_ago = _NOW - timedelta(seconds=30)
    make_article(
        db_session,
        source_id=src.id,
        status=ArticleStatus.pending_processing,
        claimed_by="worker-r",
        claimed_at=claimed_30s_ago,
    )
    # Default 600s lease: 30s not stale → Released 0
    result_default = runner.invoke(app, ["ops", "reap"])
    assert result_default.exit_code == 0
    assert "Released 0" in result_default.output

    # Explicit 10s lease: 30s is stale → Released 1
    result_short = runner.invoke(app, ["ops", "reap", "--lease-seconds", "10"])
    assert result_short.exit_code == 0
    assert "Released 1" in result_short.output
