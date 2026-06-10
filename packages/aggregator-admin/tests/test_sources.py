"""Integration tests for sources sub-commands."""
from __future__ import annotations

import json

import pytest

from aggregator_admin.main import app
from aggregator_common.models import Article, Source

from .conftest import make_article, make_source


# ---------------------------------------------------------------------------
# sources add
# ---------------------------------------------------------------------------

def test_sources_add_prints_new_id(runner, db_session):
    result = runner.invoke(app, ["sources", "add", "--name", "My Feed", "--url", "http://example.com/feed.xml"])
    assert result.exit_code == 0
    new_id = int(result.output.strip())
    assert new_id >= 1


def test_sources_add_duplicate_url_exits_nonzero(runner, db_session):
    make_source(db_session, url="http://dupe.com/feed.xml")
    result = runner.invoke(app, ["sources", "add", "--name", "Dupe", "--url", "http://dupe.com/feed.xml"])
    assert result.exit_code == 1
    assert "already exists" in result.output


# ---------------------------------------------------------------------------
# sources list
# ---------------------------------------------------------------------------

def test_sources_list_all(runner, db_session):
    make_source(db_session, name="Feed A", url="http://a.com/feed.xml", enabled=True)
    make_source(db_session, name="Feed B", url="http://b.com/feed.xml", enabled=False)
    result = runner.invoke(app, ["sources", "list"])
    assert result.exit_code == 0
    assert "Feed A" in result.output
    assert "Feed B" in result.output


def test_sources_list_enabled_filter(runner, db_session):
    src_en = make_source(db_session, name="E-Feed", url="http://en.com/feed.xml", enabled=True)
    make_source(db_session, name="D-Feed", url="http://dis.com/feed.xml", enabled=False)
    result = runner.invoke(app, ["sources", "list", "--enabled", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    ids = [s["id"] for s in data]
    assert src_en.id in ids
    assert all(s["enabled"] is True for s in data)


def test_sources_list_disabled_filter(runner, db_session):
    make_source(db_session, name="E-Feed", url="http://en2.com/feed.xml", enabled=True)
    src_dis = make_source(db_session, name="D-Feed", url="http://dis2.com/feed.xml", enabled=False)
    result = runner.invoke(app, ["sources", "list", "--disabled", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    ids = [s["id"] for s in data]
    assert src_dis.id in ids
    assert all(s["enabled"] is False for s in data)


def test_sources_list_json_output(runner, db_session):
    make_source(db_session, name="JSON Feed", url="http://json.com/feed.xml")
    result = runner.invoke(app, ["sources", "list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert any(s["name"] == "JSON Feed" for s in data)


# ---------------------------------------------------------------------------
# sources show
# ---------------------------------------------------------------------------

def test_sources_show_success(runner, db_session):
    src = make_source(db_session, name="Show Me", url="http://show.com/feed.xml")
    result = runner.invoke(app, ["sources", "show", str(src.id), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data[0]["name"] == "Show Me"
    assert data[0]["id"] == src.id


def test_sources_show_not_found(runner, db_session):
    result = runner.invoke(app, ["sources", "show", "9999"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_sources_show_json(runner, db_session):
    src = make_source(db_session, name="JSON Show", url="http://jsonshow.com/feed.xml")
    result = runner.invoke(app, ["sources", "show", str(src.id), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["name"] == "JSON Show"


# ---------------------------------------------------------------------------
# sources enable / disable
# ---------------------------------------------------------------------------

def test_sources_enable(runner, db_session):
    src = make_source(db_session, enabled=False)
    result = runner.invoke(app, ["sources", "enable", str(src.id)])
    assert result.exit_code == 0
    db_session.refresh(src)
    assert src.enabled is True
    assert src.consecutive_failures == 0
    assert src.next_check_at is not None


def test_sources_enable_not_found(runner, db_session):
    result = runner.invoke(app, ["sources", "enable", "9999"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_sources_disable(runner, db_session):
    src = make_source(db_session, enabled=True)
    result = runner.invoke(app, ["sources", "disable", str(src.id)])
    assert result.exit_code == 0
    db_session.refresh(src)
    assert src.enabled is False


def test_sources_disable_not_found(runner, db_session):
    result = runner.invoke(app, ["sources", "disable", "9999"])
    assert result.exit_code == 1
    assert "not found" in result.output


# ---------------------------------------------------------------------------
# sources set-interval
# ---------------------------------------------------------------------------

def test_sources_set_interval(runner, db_session):
    src = make_source(db_session)
    result = runner.invoke(app, ["sources", "set-interval", str(src.id), "1800"])
    assert result.exit_code == 0
    db_session.refresh(src)
    assert src.refresh_interval_seconds == 1800


def test_sources_set_interval_not_found(runner, db_session):
    result = runner.invoke(app, ["sources", "set-interval", "9999", "300"])
    assert result.exit_code == 1
    assert "not found" in result.output


# ---------------------------------------------------------------------------
# sources remove
# ---------------------------------------------------------------------------

def test_sources_remove_with_articles_no_force_exits_1(runner, db_session):
    src = make_source(db_session)
    make_article(db_session, source_id=src.id)
    result = runner.invoke(app, ["sources", "remove", str(src.id), "--yes"])
    assert result.exit_code == 1
    assert "--force" in result.output


def test_sources_remove_force_yes_deletes_source_and_articles(runner, db_session):
    src = make_source(db_session)
    src_id = src.id  # capture before potential deletion
    make_article(db_session, source_id=src_id)
    result = runner.invoke(app, ["sources", "remove", str(src_id), "--force", "--yes"])
    assert result.exit_code == 0
    # Use query (not session.get) to bypass the identity-map cache
    assert db_session.query(Source).filter(Source.id == src_id).count() == 0
    assert db_session.query(Article).filter(Article.source_id == src_id).count() == 0


def test_sources_remove_yes_no_articles(runner, db_session):
    src = make_source(db_session)
    src_id = src.id  # capture before potential deletion
    result = runner.invoke(app, ["sources", "remove", str(src_id), "--yes"])
    assert result.exit_code == 0
    # Use query to bypass the identity-map cache
    assert db_session.query(Source).filter(Source.id == src_id).count() == 0


def test_sources_remove_no_yes_noninteractive_exits_1(runner, db_session):
    src = make_source(db_session)
    result = runner.invoke(app, ["sources", "remove", str(src.id)])
    assert result.exit_code == 1
    assert "non-interactive" in result.output


def test_sources_remove_not_found(runner, db_session):
    result = runner.invoke(app, ["sources", "remove", "9999", "--yes"])
    assert result.exit_code == 1
    assert "not found" in result.output


# ---------------------------------------------------------------------------
# sources refresh-now
# ---------------------------------------------------------------------------

def test_sources_refresh_now(runner, db_session):
    src = make_source(db_session)
    result = runner.invoke(app, ["sources", "refresh-now", str(src.id)])
    assert result.exit_code == 0
    db_session.refresh(src)
    assert src.next_check_at is not None


def test_sources_refresh_now_not_found(runner, db_session):
    result = runner.invoke(app, ["sources", "refresh-now", "9999"])
    assert result.exit_code == 1
    assert "not found" in result.output
