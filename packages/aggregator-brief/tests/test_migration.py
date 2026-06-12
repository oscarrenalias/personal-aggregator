"""Test the c3d4e5f6a1b2 migration round-trip: upgrade creates tables, downgrade drops them."""

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

_COMMON_ROOT = Path(__file__).parent.parent.parent / "aggregator-common"
_ALEMBIC_INI = _COMMON_ROOT / "alembic.ini"
_MIGRATIONS_DIR = _COMMON_ROOT / "src" / "aggregator_common" / "migrations"


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


def _make_cfg(db_url: str):
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture(scope="module")
def migration_db():
    import os as _os

    from alembic import command
    from testcontainers.postgres import PostgresContainer

    _ensure_docker_host()

    original_url = _os.environ.get("DATABASE_URL")
    with PostgresContainer("postgres:16") as postgres:
        raw_url = postgres.get_connection_url()
        db_url = raw_url.replace("postgresql+psycopg2://", "postgresql+psycopg://")
        # env.py reads DATABASE_URL via Settings(); set it before running migrations.
        _os.environ["DATABASE_URL"] = db_url
        # Run up to the revision before our target so we can test upgrade explicitly.
        command.upgrade(_make_cfg(db_url), "b1c2d3e4f5a6")
        yield db_url

    # Restore previous DATABASE_URL so other fixtures are unaffected.
    if original_url is not None:
        _os.environ["DATABASE_URL"] = original_url
    else:
        _os.environ.pop("DATABASE_URL", None)


def test_upgrade_creates_briefs_table(migration_db):
    from alembic import command

    command.upgrade(_make_cfg(migration_db), "c3d4e5f6a1b2")

    engine = create_engine(migration_db)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "briefs" in tables
        assert "brief_topics" in tables
    finally:
        engine.dispose()


def test_upgrade_creates_partial_unique_index(migration_db):
    engine = create_engine(migration_db)
    try:
        indexes = {idx["name"] for idx in inspect(engine).get_indexes("briefs")}
        assert "uq_briefs_period_start_auto" in indexes
    finally:
        engine.dispose()


def test_downgrade_drops_tables(migration_db):
    from alembic import command

    command.downgrade(_make_cfg(migration_db), "b1c2d3e4f5a6")

    engine = create_engine(migration_db)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "briefs" not in tables
        assert "brief_topics" not in tables
    finally:
        engine.dispose()


def test_re_upgrade_after_downgrade(migration_db):
    """Applying upgrade again after downgrade must succeed."""
    from alembic import command

    command.upgrade(_make_cfg(migration_db), "c3d4e5f6a1b2")

    engine = create_engine(migration_db)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "briefs" in tables
        assert "brief_topics" in tables
    finally:
        engine.dispose()
