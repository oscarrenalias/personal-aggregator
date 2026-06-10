import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect


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
