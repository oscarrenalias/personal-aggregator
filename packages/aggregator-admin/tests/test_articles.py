"""Integration tests for articles sub-commands."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from aggregator_admin.main import app
from aggregator_common.models import Article
from aggregator_common.state import ArticleStatus

from .conftest import make_article, make_source

_OLD_TIME = datetime(2024, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# articles list
# ---------------------------------------------------------------------------

def test_articles_list_no_filter(runner, db_session):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, feed_title="First Article")
    result = runner.invoke(app, ["articles", "list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert any(a["feed_title"] == "First Article" for a in data)


def test_articles_list_status_filter(runner, db_session):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, feed_title="Failed Art", status=ArticleStatus.failed_processing, dedup_key="k1")
    make_article(db_session, source_id=src.id, feed_title="Ready Art", status=ArticleStatus.ready, dedup_key="k2")
    result = runner.invoke(app, ["articles", "list", "--status", "failed_processing"])
    assert result.exit_code == 0
    assert "Failed Art" in result.output
    assert "Ready Art" not in result.output


def test_articles_list_source_filter(runner, db_session):
    src1 = make_source(db_session, url="http://src1.com/feed.xml")
    src2 = make_source(db_session, url="http://src2.com/feed.xml", name="Feed 2")
    art1 = make_article(db_session, source_id=src1.id, feed_title="Art A", dedup_key="k1")
    make_article(db_session, source_id=src2.id, feed_title="Art B", dedup_key="k2")
    result = runner.invoke(app, ["articles", "list", "--source", str(src1.id), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    ids = [a["id"] for a in data]
    assert art1.id in ids
    assert all(a["source_id"] == src1.id for a in data)


def test_articles_list_json(runner, db_session):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    result = runner.invoke(app, ["articles", "list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 1


def test_articles_list_limit(runner, db_session):
    src = make_source(db_session)
    for i in range(5):
        make_article(db_session, source_id=src.id, dedup_key=f"key-{i}")
    result = runner.invoke(app, ["articles", "list", "--limit", "2", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 2


# ---------------------------------------------------------------------------
# articles show
# ---------------------------------------------------------------------------

def test_articles_show_success(runner, db_session):
    src = make_source(db_session)
    art = make_article(db_session, source_id=src.id, feed_title="Detail Article")
    result = runner.invoke(app, ["articles", "show", str(art.id), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data[0]["feed_title"] == "Detail Article"
    assert data[0]["id"] == art.id


def test_articles_show_not_found(runner, db_session):
    result = runner.invoke(app, ["articles", "show", "9999"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_articles_show_json(runner, db_session):
    src = make_source(db_session)
    art = make_article(db_session, source_id=src.id, feed_title="JSON Show")
    result = runner.invoke(app, ["articles", "show", str(art.id), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data[0]["feed_title"] == "JSON Show"


# ---------------------------------------------------------------------------
# articles search
# ---------------------------------------------------------------------------

def test_articles_search_matching(runner, db_session, db_engine):
    src = make_source(db_session)
    art = make_article(db_session, source_id=src.id, feed_title="Python Programming")
    with db_engine.connect() as conn:
        conn.execute(
            text(
                "UPDATE articles SET search_vector = to_tsvector('english', 'python programming language') "
                "WHERE id = :id"
            ),
            {"id": art.id},
        )
        conn.commit()
    result = runner.invoke(app, ["articles", "search", "python", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["id"] == art.id


def test_articles_search_no_match(runner, db_session):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    result = runner.invoke(app, ["articles", "search", "zxqnothing"])
    assert result.exit_code == 0
    # No results — just confirm it doesn't crash


# ---------------------------------------------------------------------------
# articles retry (single article)
# ---------------------------------------------------------------------------

def test_articles_retry_failed_processing(runner, db_session):
    src = make_source(db_session)
    art = make_article(db_session, source_id=src.id, status=ArticleStatus.failed_processing)
    result = runner.invoke(app, ["articles", "retry", str(art.id)])
    assert result.exit_code == 0
    assert "pending_processing" in result.output
    db_session.refresh(art)
    assert art.status == ArticleStatus.pending_processing.value
    assert art.claimed_by is None
    assert art.last_error is None
    assert art.retry_count == 0


def test_articles_retry_failed_ranking(runner, db_session):
    src = make_source(db_session)
    art = make_article(db_session, source_id=src.id, status=ArticleStatus.failed_ranking)
    result = runner.invoke(app, ["articles", "retry", str(art.id)])
    assert result.exit_code == 0
    assert "pending_ranking" in result.output
    db_session.refresh(art)
    assert art.status == ArticleStatus.pending_ranking.value


def test_articles_retry_non_failed_exits_nonzero(runner, db_session):
    src = make_source(db_session)
    art = make_article(db_session, source_id=src.id, status=ArticleStatus.ready)
    result = runner.invoke(app, ["articles", "retry", str(art.id)])
    assert result.exit_code == 1
    assert "not in a failed status" in result.output


def test_articles_retry_not_found(runner, db_session):
    result = runner.invoke(app, ["articles", "retry", "9999"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_articles_retry_no_args_exits_nonzero(runner, db_session):
    result = runner.invoke(app, ["articles", "retry"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# articles retry --status (batch)
# ---------------------------------------------------------------------------

def test_articles_retry_batch_status_targets_only_matching_rows(runner, db_session):
    src = make_source(db_session)
    art1 = make_article(db_session, source_id=src.id, status=ArticleStatus.failed_processing, dedup_key="k1")
    art2 = make_article(db_session, source_id=src.id, status=ArticleStatus.failed_processing, dedup_key="k2")
    art3 = make_article(db_session, source_id=src.id, status=ArticleStatus.ready, dedup_key="k3")
    result = runner.invoke(app, ["articles", "retry", "--status", "failed_processing"])
    assert result.exit_code == 0
    assert "Retried 2" in result.output
    db_session.refresh(art1)
    db_session.refresh(art2)
    db_session.refresh(art3)
    assert art1.status == ArticleStatus.pending_processing.value
    assert art2.status == ArticleStatus.pending_processing.value
    assert art3.status == ArticleStatus.ready.value  # untouched


def test_articles_retry_batch_invalid_status_exits_nonzero(runner, db_session):
    result = runner.invoke(app, ["articles", "retry", "--status", "ready"])
    assert result.exit_code == 1


def test_articles_retry_batch_and_id_together_exits_nonzero(runner, db_session):
    src = make_source(db_session)
    art = make_article(db_session, source_id=src.id, status=ArticleStatus.failed_processing)
    result = runner.invoke(app, ["articles", "retry", str(art.id), "--status", "failed_processing"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# articles rerank
# ---------------------------------------------------------------------------

def test_articles_rerank_ready(runner, db_session):
    src = make_source(db_session)
    art = make_article(db_session, source_id=src.id, status=ArticleStatus.ready)
    result = runner.invoke(app, ["articles", "rerank", str(art.id)])
    assert result.exit_code == 0
    assert "pending_ranking" in result.output
    db_session.refresh(art)
    assert art.status == ArticleStatus.pending_ranking.value
    assert art.claimed_by is None
    assert art.claimed_at is None


def test_articles_rerank_non_ready_exits_nonzero(runner, db_session):
    src = make_source(db_session)
    art = make_article(db_session, source_id=src.id, status=ArticleStatus.failed_processing)
    result = runner.invoke(app, ["articles", "rerank", str(art.id)])
    assert result.exit_code == 1
    assert "cannot be reranked" in result.output


def test_articles_rerank_not_found(runner, db_session):
    result = runner.invoke(app, ["articles", "rerank", "9999"])
    assert result.exit_code == 1
    assert "not found" in result.output


# ---------------------------------------------------------------------------
# articles interaction commands
# ---------------------------------------------------------------------------

def test_articles_mark_read_sets_read_at(runner, db_session):
    src = make_source(db_session)
    art = make_article(db_session, source_id=src.id)
    result = runner.invoke(app, ["articles", "mark-read", str(art.id)])
    assert result.exit_code == 0
    db_session.refresh(art)
    assert art.is_read is True
    assert art.read_at is not None


def test_articles_mark_unread_clears_read_at(runner, db_session):
    src = make_source(db_session)
    art = make_article(db_session, source_id=src.id)
    runner.invoke(app, ["articles", "mark-read", str(art.id)])
    result = runner.invoke(app, ["articles", "mark-unread", str(art.id)])
    assert result.exit_code == 0
    db_session.refresh(art)
    assert art.is_read is False
    assert art.read_at is None


def test_articles_save_and_unsave(runner, db_session):
    src = make_source(db_session)
    art = make_article(db_session, source_id=src.id)
    result = runner.invoke(app, ["articles", "save", str(art.id)])
    assert result.exit_code == 0
    db_session.refresh(art)
    assert art.is_saved is True
    result = runner.invoke(app, ["articles", "unsave", str(art.id)])
    assert result.exit_code == 0
    db_session.refresh(art)
    assert art.is_saved is False


def test_articles_hide_and_unhide(runner, db_session):
    src = make_source(db_session)
    art = make_article(db_session, source_id=src.id)
    result = runner.invoke(app, ["articles", "hide", str(art.id)])
    assert result.exit_code == 0
    db_session.refresh(art)
    assert art.is_hidden is True
    result = runner.invoke(app, ["articles", "unhide", str(art.id)])
    assert result.exit_code == 0
    db_session.refresh(art)
    assert art.is_hidden is False


@pytest.mark.parametrize("command", ["mark-read", "mark-unread", "save", "unsave", "hide", "unhide"])
def test_articles_interaction_not_found(runner, db_session, command):
    result = runner.invoke(app, ["articles", command, "9999"])
    assert result.exit_code == 1
    assert "not found" in result.output


# ---------------------------------------------------------------------------
# articles purge
# ---------------------------------------------------------------------------

def test_articles_purge_no_filter_exits_nonzero(runner, db_session):
    result = runner.invoke(app, ["articles", "purge", "--yes"])
    assert result.exit_code == 1
    assert "at least one filter" in result.output


def test_articles_purge_yes_gate_noninteractive(runner, db_session):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, status=ArticleStatus.failed_processing)
    result = runner.invoke(app, ["articles", "purge", "--status", "failed_processing"])
    assert result.exit_code == 1
    assert "non-interactive" in result.output


def test_articles_purge_by_status(runner, db_session):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id, status=ArticleStatus.failed_processing, dedup_key="k1")
    make_article(db_session, source_id=src.id, status=ArticleStatus.ready, dedup_key="k2")
    result = runner.invoke(app, ["articles", "purge", "--status", "failed_processing", "--yes"])
    assert result.exit_code == 0
    assert "Deleted 1" in result.output
    remaining = db_session.query(Article).all()
    assert len(remaining) == 1
    assert remaining[0].status == ArticleStatus.ready.value


def test_articles_purge_by_before(runner, db_session):
    src = make_source(db_session)
    # Old article: will be purged
    make_article(
        db_session,
        source_id=src.id,
        dedup_key="old",
        retrieved_at=_OLD_TIME,  # 2024-01-01 — before the --before threshold
    )
    # New article: default retrieved_at = 2025-01-01T12:00 — after threshold
    make_article(db_session, source_id=src.id, dedup_key="new")
    result = runner.invoke(app, ["articles", "purge", "--before", "2025-01-01", "--yes"])
    assert result.exit_code == 0
    assert "Deleted 1" in result.output
    remaining = db_session.query(Article).all()
    assert len(remaining) == 1
    assert remaining[0].dedup_key == "new"


def test_articles_purge_by_source(runner, db_session):
    src1 = make_source(db_session, url="http://p1.com/feed.xml")
    src2 = make_source(db_session, url="http://p2.com/feed.xml", name="Feed 2")
    make_article(db_session, source_id=src1.id, dedup_key="k1")
    make_article(db_session, source_id=src2.id, dedup_key="k2")
    result = runner.invoke(app, ["articles", "purge", "--source", str(src1.id), "--yes"])
    assert result.exit_code == 0
    assert "Deleted 1" in result.output
    remaining = db_session.query(Article).all()
    assert len(remaining) == 1
    assert remaining[0].source_id == src2.id


def test_articles_purge_invalid_before_date(runner, db_session):
    result = runner.invoke(app, ["articles", "purge", "--before", "not-a-date", "--yes"])
    assert result.exit_code == 1
    assert "not a valid ISO date" in result.output
