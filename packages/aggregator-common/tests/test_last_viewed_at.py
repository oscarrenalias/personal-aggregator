"""Tests for last_viewed_at column: migration round-trip, has_updates logic, and mark_thread_viewed."""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator

import pytest
from sqlalchemy.orm import Session

from aggregator_common.management import mark_thread_viewed
from aggregator_common.errors import NotFoundError
from aggregator_common.models import Thread

_NOW = datetime.now(tz=timezone.utc)

_PACKAGE_ROOT = Path(__file__).parent.parent
_ALEMBIC_INI = _PACKAGE_ROOT / "alembic.ini"
_MIGRATIONS_DIR = _PACKAGE_ROOT / "src" / "aggregator_common" / "migrations"


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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session(db_session_factory) -> Generator[Session, None, None]:
    s = db_session_factory()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _make_thread(session: Session, *, last_viewed_at: datetime | None = None, last_updated: datetime | None = None) -> Thread:
    now = last_updated or _NOW
    thread = Thread(
        representative_title="LVA Test Thread",
        first_seen=now,
        last_updated=now,
        status="active",
        source_list=[],
        known_facts=[],
        deltas=[],
        last_viewed_at=last_viewed_at,
    )
    session.add(thread)
    session.flush()
    return thread


# ---------------------------------------------------------------------------
# Migration round-trip: last_viewed_at column (c4d5e6f7a8b9)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def lva_migration_engine():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from testcontainers.postgres import PostgresContainer

    _ensure_docker_host()

    with PostgresContainer("postgres:16") as postgres:
        raw_url = postgres.get_connection_url()
        db_url = raw_url.replace("postgresql+psycopg2://", "postgresql+psycopg://")

        prev = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = db_url
        try:
            cfg = Config(str(_ALEMBIC_INI))
            cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
            cfg.set_main_option("sqlalchemy.url", db_url)

            # Upgrade exactly to the revision that adds last_viewed_at
            command.upgrade(cfg, "c4d5e6f7a8b9")

            engine = create_engine(db_url, pool_pre_ping=True)
            try:
                yield engine, cfg
            finally:
                engine.dispose()
        finally:
            if prev is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev


def test_migration_last_viewed_at_column_present_after_upgrade(lva_migration_engine):
    """Upgrade to c4d5e6f7a8b9 must add last_viewed_at to threads."""
    from sqlalchemy import inspect as sa_inspect

    engine, _ = lva_migration_engine
    inspector = sa_inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("threads")}
    assert "last_viewed_at" in columns


def test_migration_last_viewed_at_column_absent_after_downgrade(lva_migration_engine):
    """Downgrade to b2c3d4e5f6a7 (parent) must remove last_viewed_at."""
    from alembic import command
    from sqlalchemy import inspect as sa_inspect

    engine, cfg = lva_migration_engine
    # Downgrade to the parent revision of c4d5e6f7a8b9
    command.downgrade(cfg, "b2c3d4e5f6a7")
    inspector = sa_inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("threads")}
    assert "last_viewed_at" not in columns

    # Restore to head so subsequent tests still have a valid schema
    command.upgrade(cfg, "head")


# ---------------------------------------------------------------------------
# has_updates logic via _to_thread_result (tested through queries.get_thread)
# ---------------------------------------------------------------------------


class TestHasUpdatesLogic:
    def test_has_updates_true_when_never_viewed(self, session: Session):
        """last_viewed_at IS NULL → has_updates=True."""
        from aggregator_common import queries

        thread = _make_thread(session, last_viewed_at=None)

        result = queries.get_thread(session, thread.id)

        assert result is not None
        assert result.has_updates is True

    def test_has_updates_true_when_updated_after_last_view(self, session: Session):
        """last_updated > last_viewed_at → has_updates=True."""
        from aggregator_common import queries

        viewed_at = _NOW - timedelta(hours=2)
        updated_at = _NOW - timedelta(hours=1)
        thread = _make_thread(session, last_viewed_at=viewed_at, last_updated=updated_at)

        result = queries.get_thread(session, thread.id)

        assert result is not None
        assert result.has_updates is True

    def test_has_updates_false_when_viewed_after_last_update(self, session: Session):
        """last_viewed_at >= last_updated → has_updates=False."""
        from aggregator_common import queries

        updated_at = _NOW - timedelta(hours=2)
        viewed_at = _NOW - timedelta(hours=1)  # viewed after update
        thread = _make_thread(session, last_viewed_at=viewed_at, last_updated=updated_at)

        result = queries.get_thread(session, thread.id)

        assert result is not None
        assert result.has_updates is False

    def test_has_updates_false_when_viewed_at_same_instant_as_update(self, session: Session):
        """last_viewed_at == last_updated → has_updates=False (boundary case)."""
        from aggregator_common import queries

        ts = _NOW - timedelta(hours=1)
        thread = _make_thread(session, last_viewed_at=ts, last_updated=ts)

        result = queries.get_thread(session, thread.id)

        assert result is not None
        assert result.has_updates is False

    def test_has_updates_also_reflected_in_list_threads(self, session: Session):
        """list_threads also populates has_updates correctly."""
        from aggregator_common import queries

        thread = _make_thread(session, last_viewed_at=None)
        thread.surfaced = True
        session.flush()

        results, _ = queries.list_threads(session)
        matching = [r for r in results if r.id == thread.id]
        assert len(matching) == 1
        assert matching[0].has_updates is True


# ---------------------------------------------------------------------------
# mark_thread_viewed
# ---------------------------------------------------------------------------


class TestMarkThreadViewed:
    def test_stamps_last_viewed_at_on_first_call(self, session: Session):
        before = datetime.now(tz=timezone.utc)
        thread = _make_thread(session, last_viewed_at=None)

        mark_thread_viewed(session, thread.id)
        session.flush()
        session.refresh(thread)

        assert thread.last_viewed_at is not None
        assert thread.last_viewed_at >= before

    def test_repeated_calls_overwrite_timestamp(self, session: Session):
        thread = _make_thread(session, last_viewed_at=None)

        mark_thread_viewed(session, thread.id)
        session.flush()
        session.refresh(thread)
        first_stamp = thread.last_viewed_at

        # Brief pause so the second call has a measurably later wall-clock time
        time.sleep(0.05)

        mark_thread_viewed(session, thread.id)
        session.flush()
        session.refresh(thread)

        assert thread.last_viewed_at is not None
        assert thread.last_viewed_at >= first_stamp

    def test_raises_not_found_for_unknown_thread_id(self, session: Session):
        with pytest.raises(NotFoundError):
            mark_thread_viewed(session, 999_999_901)

    def test_has_updates_becomes_false_after_mark_viewed(self, session: Session):
        """After mark_thread_viewed, has_updates should be False when last_updated has not advanced."""
        from aggregator_common import queries

        thread = _make_thread(session, last_viewed_at=None)

        mark_thread_viewed(session, thread.id)
        session.flush()

        result = queries.get_thread(session, thread.id)
        assert result is not None
        assert result.has_updates is False
