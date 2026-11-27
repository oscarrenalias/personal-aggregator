"""Integration tests for ops sub-commands."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

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


# ---------------------------------------------------------------------------
# ops llm-cutover-cleanup
# ---------------------------------------------------------------------------


def _seed_shadow_and_verdicts(db_engine, n_shadow: int = 2, n_verdicts: int = 1) -> None:
    with db_engine.connect() as conn:
        for i in range(n_shadow):
            conn.execute(text(
                "INSERT INTO llm_shadow_results"
                "(experiment,service,operation,ref_id,arm,model,prompt_hash,status,attempts,params)"
                f" VALUES('exp','svc','op','{i}','control','m','h','ok',1,'{{}}')"
            ))
        # thread_merge_verdicts has FK to threads.id; insert threads first
        thread_ids: list[int] = []
        for i in range(n_verdicts * 2):
            row = conn.execute(text(
                "INSERT INTO threads(representative_title,first_seen,last_updated)"
                " VALUES('t',now(),now()) RETURNING id"
            ))
            thread_ids.append(row.scalar_one())
        for i in range(n_verdicts):
            keep_id = thread_ids[i * 2]
            absorb_id = thread_ids[i * 2 + 1]
            if keep_id > absorb_id:
                keep_id, absorb_id = absorb_id, keep_id
            conn.execute(text(
                "INSERT INTO thread_merge_verdicts"
                "(keep_id,absorb_id,keep_last_updated,absorb_last_updated,decided_at)"
                f" VALUES({keep_id},{absorb_id},now(),now(),now())"
                " ON CONFLICT DO NOTHING"
            ))
        conn.commit()


def test_llm_cutover_cleanup_dry_run_writes_nothing(runner, db_engine, db_session, monkeypatch):
    """Without --yes: prints counts to stderr, writes nothing."""
    monkeypatch.delenv("LLM_EXPERIMENT", raising=False)
    with db_engine.connect() as conn:
        conn.execute(text("TRUNCATE llm_shadow_results CASCADE"))
        conn.execute(text("TRUNCATE thread_merge_verdicts CASCADE"))
        conn.commit()

    _seed_shadow_and_verdicts(db_engine, n_shadow=3, n_verdicts=1)

    result = runner.invoke(app, ["ops", "llm-cutover-cleanup"])
    assert result.exit_code == 0

    # Nothing deleted
    with db_engine.connect() as conn:
        shadow_count = conn.execute(text("SELECT COUNT(*) FROM llm_shadow_results")).scalar_one()
        verdict_count = conn.execute(text("SELECT COUNT(*) FROM thread_merge_verdicts")).scalar_one()
    assert shadow_count == 3
    assert verdict_count == 1


def test_llm_cutover_cleanup_yes_empties_both_tables(runner, db_engine, db_session, monkeypatch):
    """With --yes: both tables are emptied; llm_shadow_results still exists and accepts an INSERT."""
    monkeypatch.delenv("LLM_EXPERIMENT", raising=False)
    with db_engine.connect() as conn:
        conn.execute(text("TRUNCATE llm_shadow_results CASCADE"))
        conn.execute(text("TRUNCATE thread_merge_verdicts CASCADE"))
        conn.commit()

    _seed_shadow_and_verdicts(db_engine, n_shadow=2, n_verdicts=1)

    result = runner.invoke(app, ["ops", "llm-cutover-cleanup", "--yes"])
    assert result.exit_code == 0

    with db_engine.connect() as conn:
        shadow_count = conn.execute(text("SELECT COUNT(*) FROM llm_shadow_results")).scalar_one()
        verdict_count = conn.execute(text("SELECT COUNT(*) FROM thread_merge_verdicts")).scalar_one()
    assert shadow_count == 0
    assert verdict_count == 0

    # Table still exists and accepts inserts
    with db_engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO llm_shadow_results"
            "(experiment,service,operation,ref_id,arm,model,prompt_hash,status,attempts,params)"
            " VALUES('post-cleanup','svc','op','99','control','m','h','ok',1,'{}')"
        ))
        conn.commit()
        count_after = conn.execute(text("SELECT COUNT(*) FROM llm_shadow_results")).scalar_one()
    assert count_after == 1


def test_llm_cutover_cleanup_refuses_when_experiment_enabled(runner, db_session, monkeypatch):
    """Refuses (exit 1) when LLM_EXPERIMENT has enabled=true."""
    monkeypatch.setenv("LLM_EXPERIMENT", '{"name":"test","enabled":true}')
    result = runner.invoke(app, ["ops", "llm-cutover-cleanup"])
    assert result.exit_code != 0


def test_llm_cutover_cleanup_refuses_on_invalid_json(runner, db_session, monkeypatch):
    """Refuses (exit 1) when LLM_EXPERIMENT contains invalid JSON."""
    monkeypatch.setenv("LLM_EXPERIMENT", "{not valid json")
    result = runner.invoke(app, ["ops", "llm-cutover-cleanup"])
    assert result.exit_code != 0


def test_llm_cutover_cleanup_allows_unset_experiment(runner, db_engine, db_session, monkeypatch):
    """Runs successfully when LLM_EXPERIMENT is unset."""
    monkeypatch.delenv("LLM_EXPERIMENT", raising=False)
    with db_engine.connect() as conn:
        conn.execute(text("TRUNCATE llm_shadow_results CASCADE"))
        conn.execute(text("TRUNCATE thread_merge_verdicts CASCADE"))
        conn.commit()
    result = runner.invoke(app, ["ops", "llm-cutover-cleanup", "--yes"])
    assert result.exit_code == 0


def test_llm_cutover_cleanup_allows_empty_string_experiment(runner, db_engine, db_session, monkeypatch):
    """Runs successfully when LLM_EXPERIMENT is empty string."""
    monkeypatch.setenv("LLM_EXPERIMENT", "")
    with db_engine.connect() as conn:
        conn.execute(text("TRUNCATE llm_shadow_results CASCADE"))
        conn.execute(text("TRUNCATE thread_merge_verdicts CASCADE"))
        conn.commit()
    result = runner.invoke(app, ["ops", "llm-cutover-cleanup", "--yes"])
    assert result.exit_code == 0


def test_llm_cutover_cleanup_allows_disabled_experiment(runner, db_engine, db_session, monkeypatch):
    """Runs successfully when LLM_EXPERIMENT has enabled=false."""
    monkeypatch.setenv("LLM_EXPERIMENT", '{"name":"test","enabled":false}')
    with db_engine.connect() as conn:
        conn.execute(text("TRUNCATE llm_shadow_results CASCADE"))
        conn.execute(text("TRUNCATE thread_merge_verdicts CASCADE"))
        conn.commit()
    result = runner.invoke(app, ["ops", "llm-cutover-cleanup", "--yes"])
    assert result.exit_code == 0


def test_llm_cutover_cleanup_refuses_when_no_enabled_key(runner, db_engine, db_session, monkeypatch):
    """Refuses (exit 1) when LLM_EXPERIMENT is a valid dict but has no 'enabled' key.

    aggregator_common.experiment defaults enabled=True when the key is absent, so the
    experiment is live and cleanup must be blocked.
    """
    monkeypatch.setenv("LLM_EXPERIMENT", '{"name":"x","operations":{}}')
    with db_engine.connect() as conn:
        conn.execute(text("TRUNCATE llm_shadow_results CASCADE"))
        conn.commit()
    _seed_shadow_and_verdicts(db_engine, n_shadow=1, n_verdicts=0)
    result = runner.invoke(app, ["ops", "llm-cutover-cleanup"])
    assert result.exit_code != 0
    # Nothing deleted
    with db_engine.connect() as conn:
        shadow_count = conn.execute(text("SELECT COUNT(*) FROM llm_shadow_results")).scalar_one()
    assert shadow_count == 1


def test_llm_cutover_cleanup_refuses_on_non_object_json(runner, db_engine, db_session, monkeypatch):
    """Refuses (exit 1) when LLM_EXPERIMENT is valid JSON but not a dict (e.g. a list)."""
    monkeypatch.setenv("LLM_EXPERIMENT", "[]")
    with db_engine.connect() as conn:
        conn.execute(text("TRUNCATE llm_shadow_results CASCADE"))
        conn.commit()
    _seed_shadow_and_verdicts(db_engine, n_shadow=1, n_verdicts=0)
    result = runner.invoke(app, ["ops", "llm-cutover-cleanup"])
    assert result.exit_code != 0
    # Nothing deleted
    with db_engine.connect() as conn:
        shadow_count = conn.execute(text("SELECT COUNT(*) FROM llm_shadow_results")).scalar_one()
    assert shadow_count == 1
