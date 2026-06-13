"""Tests for thread management functions in aggregator_common.management."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from aggregator_common.management import (
    assign_article_to_thread,
    create_thread,
    enqueue_recluster,
    update_thread,
)
from aggregator_common.models import Article, ClusterState, Source, Thread, ThreadMembership

_NOW = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def session(db_session_factory) -> Generator[Session, None, None]:
    s = db_session_factory()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _make_source(session: Session, url: str = "https://mgmt.test/feed.xml") -> Source:
    src = Source(name="Test Feed", feed_url=url, enabled=True)
    session.add(src)
    session.flush()
    return src


def _make_article(session: Session, source_id: int, dedup_key: str = "k1") -> Article:
    article = Article(
        source_id=source_id,
        dedup_key=dedup_key,
        status="ready",
        raw_payload={},
        retrieved_at=_NOW,
    )
    session.add(article)
    session.flush()
    return article


class TestCreateThread:
    def test_returns_thread_with_pk(self, session):
        thread = create_thread(session, representative_title="Test Thread")
        assert thread.id is not None
        assert thread.id > 0

    def test_sets_title_and_timestamps(self, session):
        thread = create_thread(session, representative_title="My Thread")
        assert thread.representative_title == "My Thread"
        assert thread.first_seen is not None
        assert thread.last_updated is not None


class TestUpdateThread:
    def test_updates_mutable_field(self, session):
        thread = create_thread(session, representative_title="Original Title")
        updated = update_thread(session, thread.id, representative_title="Updated Title")
        assert updated.representative_title == "Updated Title"

    def test_unknown_field_raises_value_error(self, session):
        thread = create_thread(session, representative_title="Test")
        with pytest.raises(ValueError, match="Non-updatable"):
            update_thread(session, thread.id, nonexistent_field="value")


class TestAssignArticleToThread:
    def test_happy_path_creates_membership(self, session):
        src = _make_source(session, url="https://mgmt2.test/feed.xml")
        article = _make_article(session, src.id)
        thread = create_thread(session, representative_title="Test Thread")

        membership = assign_article_to_thread(
            session,
            article_id=article.id,
            thread_id=thread.id,
            classification_label="new_thread",
        )
        assert membership.id is not None
        assert membership.thread_id == thread.id
        assert membership.article_id == article.id

    def test_idempotent_returns_existing_membership(self, session):
        src = _make_source(session, url="https://mgmt3.test/feed.xml")
        article = _make_article(session, src.id)
        thread = create_thread(session, representative_title="Test Thread")

        first = assign_article_to_thread(
            session, article_id=article.id, thread_id=thread.id
        )
        session.flush()
        second = assign_article_to_thread(
            session, article_id=article.id, thread_id=thread.id
        )

        assert first.id == second.id

        count = session.query(ThreadMembership).filter(
            ThreadMembership.article_id == article.id
        ).count()
        assert count == 1


class TestEnqueueRecluster:
    def test_creates_singleton_row(self, session):
        enqueue_recluster(session)
        session.flush()
        row = session.get(ClusterState, True)
        assert row is not None
        assert row.recluster_requested is True

    def test_idempotent_second_call_does_not_fail(self, session):
        enqueue_recluster(session)
        session.flush()
        enqueue_recluster(session)
        session.flush()
        row = session.get(ClusterState, True)
        assert row.recluster_requested is True


# ---------------------------------------------------------------------------
# Migration tests: verify new tables are created on upgrade and removed on
# downgrade. These use a separate ephemeral container so migration state
# does not affect the session-scoped container.
# ---------------------------------------------------------------------------


def _ensure_docker_host() -> None:
    if "DOCKER_HOST" in os.environ:
        return
    if Path("/var/run/docker.sock").exists():
        os.environ["DOCKER_HOST"] = "unix:///var/run/docker.sock"
        return
    orbstack = Path.home() / ".orbstack" / "run" / "docker.sock"
    if orbstack.exists():
        os.environ["DOCKER_HOST"] = f"unix://{orbstack}"
        return
    raise RuntimeError("No Docker socket found.")


_PACKAGE_ROOT = Path(__file__).parent.parent
_ALEMBIC_INI = _PACKAGE_ROOT / "alembic.ini"
_MIGRATIONS_DIR = _PACKAGE_ROOT / "src" / "aggregator_common" / "migrations"


@pytest.fixture(scope="module")
def migration_engine():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from testcontainers.postgres import PostgresContainer

    _ensure_docker_host()

    with PostgresContainer("postgres:16") as postgres:
        raw_url = postgres.get_connection_url()
        db_url = raw_url.replace("postgresql+psycopg2://", "postgresql+psycopg://")

        # Alembic's env.py injects DATABASE_URL from the environment and ignores
        # alembic_cfg's sqlalchemy.url, so point DATABASE_URL at this testcontainer.
        # Without this the migration ran against the dev DB (:5432) — failing when
        # it's down and mutating it when it's up.
        prev_db_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = db_url
        try:
            alembic_cfg = Config(str(_ALEMBIC_INI))
            alembic_cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
            alembic_cfg.set_main_option("sqlalchemy.url", db_url)
            command.upgrade(alembic_cfg, "head")

            engine = create_engine(db_url, pool_pre_ping=True)
            try:
                yield engine, alembic_cfg
            finally:
                engine.dispose()
        finally:
            if prev_db_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev_db_url


def test_migration_threads_table_created(migration_engine):
    engine, _ = migration_engine
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert "threads" in tables
    assert "thread_memberships" in tables
    assert "cluster_state" in tables


def test_migration_thread_indexes_created(migration_engine):
    engine, _ = migration_engine
    inspector = inspect(engine)
    thread_indexes = {i["name"] for i in inspector.get_indexes("threads")}
    assert "ix_threads_status" in thread_indexes
    assert "ix_threads_tier" in thread_indexes
    assert "ix_threads_last_updated" in thread_indexes

    tm_indexes = {i["name"] for i in inspector.get_indexes("thread_memberships")}
    assert "ix_thread_memberships_thread_id" in tm_indexes


def test_migration_unique_constraint_on_article_id(migration_engine):
    engine, _ = migration_engine
    inspector = inspect(engine)
    constraints = {c["name"] for c in inspector.get_unique_constraints("thread_memberships")}
    assert "uq_thread_memberships_article_id" in constraints


def test_migration_downgrade_removes_cluster_state(migration_engine):
    from alembic import command

    engine, alembic_cfg = migration_engine
    # Downgrade -1: removes f7a8b9c0d1e2 (cluster_state)
    command.downgrade(alembic_cfg, "-1")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert "cluster_state" not in tables
    assert "threads" in tables


def test_migration_downgrade_removes_threads(migration_engine):
    from alembic import command

    engine, alembic_cfg = migration_engine
    # Downgrade another -1: removes e6f7a8b9c0d1 (threads + thread_memberships)
    command.downgrade(alembic_cfg, "-1")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert "threads" not in tables
    assert "thread_memberships" not in tables
