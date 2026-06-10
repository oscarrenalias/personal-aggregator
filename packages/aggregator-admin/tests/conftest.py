"""Shared fixtures for aggregator-admin CLI tests.

Sets a placeholder DATABASE_URL at module load time so aggregator_common.db can be
imported during pytest collection without a live database. The session-scoped
db_engine fixture starts a real Postgres container and patches
aggregator_common.db.SessionFactory so every subsequent get_session() call uses
the test database.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

# Must be set before any import of aggregator_common.db triggers Settings() at module level.
os.environ.setdefault("DATABASE_URL", "postgresql://placeholder:placeholder@localhost/placeholder")

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
        "No Docker socket found. Ensure Docker or OrbStack is running. "
        "OrbStack socket expected at ~/.orbstack/run/docker.sock."
    )


@pytest.fixture(scope="session")
def db_engine():
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

        # Patch the module-level SessionFactory. get_session() resolves SessionFactory
        # from the module globals at call time, so this patch is sufficient for all
        # code paths that imported get_session from aggregator_common.db.
        import aggregator_common.db as db_mod
        db_mod.SessionFactory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

        yield engine
        engine.dispose()


@pytest.fixture(autouse=True)
def clean_db(db_engine):
    """Truncate all data tables before each test for isolation."""
    with db_engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE articles, sources, interest_profile RESTART IDENTITY CASCADE"))
        conn.commit()
    yield


@pytest.fixture
def db_session(db_engine) -> Generator[Session, None, None]:
    """Per-test session for direct DB setup and inspection."""
    factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    s = factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


@pytest.fixture
def runner():
    from typer.testing import CliRunner

    return CliRunner()


# ---------------------------------------------------------------------------
# Helper constructors
# ---------------------------------------------------------------------------

from aggregator_common.models import Article, Source  # noqa: E402
from aggregator_common.state import ArticleStatus  # noqa: E402

_NOW = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_source(
    session: Session,
    *,
    name: str = "Test Feed",
    url: str = "https://example.com/feed.xml",
    enabled: bool = True,
) -> Source:
    src = Source(name=name, feed_url=url, enabled=enabled)
    session.add(src)
    session.flush()
    session.commit()
    session.refresh(src)
    return src


def make_article(
    session: Session,
    *,
    source_id: int,
    dedup_key: str = "key-1",
    status: ArticleStatus = ArticleStatus.pending_processing,
    feed_title: str = "Test Article",
    retrieved_at: datetime = _NOW,
    claimed_by: str | None = None,
    claimed_at: datetime | None = None,
    last_error: str | None = None,
    retry_count: int = 0,
) -> Article:
    article = Article(
        source_id=source_id,
        dedup_key=dedup_key,
        status=status,
        feed_title=feed_title,
        raw_payload={},
        retrieved_at=retrieved_at,
        claimed_by=claimed_by,
        claimed_at=claimed_at,
        last_error=last_error,
        retry_count=retry_count,
    )
    session.add(article)
    session.flush()
    session.commit()
    session.refresh(article)
    return article
