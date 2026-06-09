import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_PACKAGE_ROOT = Path(__file__).parent.parent
_ALEMBIC_INI = _PACKAGE_ROOT / "alembic.ini"
_MIGRATIONS_DIR = _PACKAGE_ROOT / "src" / "aggregator_common" / "migrations"


def _ensure_docker_host() -> None:
    """Set DOCKER_HOST if not already set, probing known socket locations."""
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


@pytest.fixture(scope="session")
def db_session_factory():
    from testcontainers.postgres import PostgresContainer

    _ensure_docker_host()

    with PostgresContainer("postgres:16") as postgres:
        raw_url = postgres.get_connection_url()
        db_url = raw_url.replace("postgresql+psycopg2://", "postgresql+psycopg://")

        os.environ["DATABASE_URL"] = db_url

        alembic_cfg = Config(str(_ALEMBIC_INI))
        alembic_cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
        alembic_cfg.set_main_option("sqlalchemy.url", db_url)
        command.upgrade(alembic_cfg, "head")

        engine = create_engine(db_url, pool_pre_ping=True)
        factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        yield factory
        engine.dispose()
