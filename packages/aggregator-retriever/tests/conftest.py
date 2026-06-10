import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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
    raise RuntimeError(
        "No Docker socket found. Ensure Docker or OrbStack is running."
    )


@pytest.fixture(scope="session")
def db_url():
    from testcontainers.postgres import PostgresContainer

    _ensure_docker_host()

    with PostgresContainer("postgres:16") as postgres:
        raw_url = postgres.get_connection_url()
        url = raw_url.replace("postgresql+psycopg2://", "postgresql+psycopg://")

        os.environ["DATABASE_URL"] = url

        alembic_cfg = Config(str(_ALEMBIC_INI))
        alembic_cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
        alembic_cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(alembic_cfg, "head")

        yield url


@pytest.fixture(scope="session")
def db_session_factory(db_url):
    engine = create_engine(db_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    yield factory
    engine.dispose()


@pytest.fixture
def session(db_session_factory):
    s = db_session_factory()
    try:
        yield s
    finally:
        s.rollback()
        s.close()
