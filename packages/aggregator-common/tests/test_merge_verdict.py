"""Tests for the thread_merge_verdicts migration and FK cascade behaviour.

Covers:
  8. Alembic upgrade creates the table; downgrade drops it cleanly.
  9. ON DELETE CASCADE removes verdict rows when a referenced thread is deleted.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

_PACKAGE_ROOT = Path(__file__).parent.parent
_ALEMBIC_INI = _PACKAGE_ROOT / "alembic.ini"
_MIGRATIONS_DIR = _PACKAGE_ROOT / "src" / "aggregator_common" / "migrations"

_PREV_REVISION = "e5f6a7b8c9d0"   # head before thread_merge_verdicts was added
_TMV_REVISION = "b4c5d6e7f8a9"    # the thread_merge_verdicts migration


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
    raise RuntimeError(
        "No Docker socket found. Ensure Docker or OrbStack is running. "
        "OrbStack socket expected at ~/.orbstack/run/docker.sock."
    )


def _make_cfg(db_url: str) -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture(scope="module")
def tmv_migration_db():
    """Isolated DB for the thread_merge_verdicts migration round-trip test."""
    from testcontainers.postgres import PostgresContainer

    _ensure_docker_host()
    original = os.environ.get("DATABASE_URL")
    with PostgresContainer("postgres:16") as postgres:
        raw_url = postgres.get_connection_url()
        db_url = raw_url.replace("postgresql+psycopg2://", "postgresql+psycopg://")
        os.environ["DATABASE_URL"] = db_url
        yield db_url
    if original is not None:
        os.environ["DATABASE_URL"] = original
    else:
        os.environ.pop("DATABASE_URL", None)


@pytest.fixture(scope="module")
def tmv_cascade_db():
    """Isolated DB pre-migrated to head for FK cascade tests."""
    from testcontainers.postgres import PostgresContainer

    _ensure_docker_host()
    original = os.environ.get("DATABASE_URL")
    with PostgresContainer("postgres:16") as postgres:
        raw_url = postgres.get_connection_url()
        db_url = raw_url.replace("postgresql+psycopg2://", "postgresql+psycopg://")
        os.environ["DATABASE_URL"] = db_url
        command.upgrade(_make_cfg(db_url), "head")
        yield db_url
    if original is not None:
        os.environ["DATABASE_URL"] = original
    else:
        os.environ.pop("DATABASE_URL", None)


def test_migration_round_trip(tmv_migration_db):
    """Scenario 8: upgrade creates thread_merge_verdicts; downgrade removes it."""
    cfg = _make_cfg(tmv_migration_db)

    # Upgrade to the revision just before the new migration
    command.upgrade(cfg, _PREV_REVISION)

    engine = create_engine(tmv_migration_db)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "thread_merge_verdicts" not in tables, \
            "table must not exist before the migration is applied"
    finally:
        engine.dispose()

    # Apply the thread_merge_verdicts migration
    command.upgrade(cfg, _TMV_REVISION)

    engine = create_engine(tmv_migration_db)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert "thread_merge_verdicts" in tables, "upgrade must create the table"
        columns = {c["name"] for c in inspector.get_columns("thread_merge_verdicts")}
        for col in ("keep_id", "absorb_id", "keep_last_updated", "absorb_last_updated", "decided_at"):
            assert col in columns, f"column {col!r} must exist after upgrade"
    finally:
        engine.dispose()

    # Downgrade removes the table cleanly
    command.downgrade(cfg, _PREV_REVISION)

    engine = create_engine(tmv_migration_db)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "thread_merge_verdicts" not in tables, \
            "downgrade must drop the table"
    finally:
        engine.dispose()

    # Leave the DB at head for any subsequent tests
    command.upgrade(cfg, "head")


def test_on_delete_cascade(tmv_cascade_db):
    """Scenario 9: deleting a thread cascade-deletes its verdict rows."""
    engine = create_engine(tmv_cascade_db)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()
    try:
        now = datetime.now(tz=timezone.utc)

        # Insert two threads
        result = session.execute(
            text(
                "INSERT INTO threads (representative_title, first_seen, last_updated, status) "
                "VALUES (:t1, :now, :now, 'active'), (:t2, :now, :now, 'active') "
                "RETURNING id"
            ),
            {"t1": "Cascade Thread Keep", "t2": "Cascade Thread Absorb", "now": now},
        )
        ids = sorted(row[0] for row in result.fetchall())
        keep_id, absorb_id = ids[0], ids[1]

        # Insert a verdict for (keep_id, absorb_id)
        session.execute(
            text(
                "INSERT INTO thread_merge_verdicts "
                "(keep_id, absorb_id, keep_last_updated, absorb_last_updated, decided_at) "
                "VALUES (:keep, :absorb, :now, :now, :now)"
            ),
            {"keep": keep_id, "absorb": absorb_id, "now": now},
        )
        session.commit()

        count_before = session.execute(
            text(
                "SELECT COUNT(*) FROM thread_merge_verdicts "
                "WHERE keep_id = :keep AND absorb_id = :absorb"
            ),
            {"keep": keep_id, "absorb": absorb_id},
        ).scalar()
        assert count_before == 1, "verdict must exist before the delete"

        # Delete the keep thread — CASCADE should remove the verdict
        session.execute(text("DELETE FROM threads WHERE id = :id"), {"id": keep_id})
        session.commit()

        count_after = session.execute(
            text(
                "SELECT COUNT(*) FROM thread_merge_verdicts "
                "WHERE keep_id = :keep AND absorb_id = :absorb"
            ),
            {"keep": keep_id, "absorb": absorb_id},
        ).scalar()
        assert count_after == 0, \
            "verdict must be cascade-deleted when the referenced thread is deleted"
    finally:
        session.close()
        engine.dispose()
