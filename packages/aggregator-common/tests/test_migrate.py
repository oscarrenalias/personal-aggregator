import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

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
    raise RuntimeError(
        "No Docker socket found. Ensure Docker or OrbStack is running."
    )


@pytest.fixture(scope="module")
def migrated_db():
    from testcontainers.postgres import PostgresContainer

    from aggregator_common.migrate import main

    _ensure_docker_host()

    original = os.environ.get("DATABASE_URL")
    with PostgresContainer("postgres:16") as postgres:
        raw_url = postgres.get_connection_url()
        db_url = raw_url.replace("postgresql+psycopg2://", "postgresql+psycopg://")
        os.environ["DATABASE_URL"] = db_url

        main()  # first migration run

        yield db_url

    if original is not None:
        os.environ["DATABASE_URL"] = original
    else:
        os.environ.pop("DATABASE_URL", None)


def test_creates_expected_tables(migrated_db):
    engine = create_engine(migrated_db)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
    finally:
        engine.dispose()

    assert "articles" in tables
    assert "sources" in tables
    assert "interest_profile" in tables


def test_idempotent(migrated_db):
    from aggregator_common.migrate import main

    main()  # second call — should not raise


@pytest.fixture(scope="module")
def roundtrip_db():
    """Isolated DB for migration round-trip test (does not share with migrated_db)."""
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


def _make_alembic_cfg(db_url: str) -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_surfaced_top_grade_migration_round_trip(roundtrip_db):
    """Migration a2b3c4d5e6f7 adds surfaced/top_grade; downgrade removes them cleanly."""
    cfg = _make_alembic_cfg(roundtrip_db)

    command.upgrade(cfg, "head")

    engine = create_engine(roundtrip_db)
    try:
        inspector = inspect(engine)
        columns = {c["name"] for c in inspector.get_columns("threads")}
        assert "surfaced" in columns, "upgrade should add 'surfaced' column"
        assert "top_grade" in columns, "upgrade should add 'top_grade' column"
    finally:
        engine.dispose()

    # Downgrade one step (removes surfaced and top_grade)
    command.downgrade(cfg, "-1")

    engine = create_engine(roundtrip_db)
    try:
        inspector = inspect(engine)
        columns = {c["name"] for c in inspector.get_columns("threads")}
        assert "surfaced" not in columns, "downgrade should remove 'surfaced' column"
        assert "top_grade" not in columns, "downgrade should remove 'top_grade' column"
    finally:
        engine.dispose()

    # Upgrade back to head to leave DB in a clean state
    command.upgrade(cfg, "head")
