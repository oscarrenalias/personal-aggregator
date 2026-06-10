"""Shared fixtures for aggregator-processor tests.

Sets a placeholder DATABASE_URL at module load time so aggregator_common.db can be
imported during pytest collection without a live database. The session-scoped
db_engine fixture starts a real Postgres container, runs Alembic migrations, and
patches the module-level SessionFactory in every module that imported it at import
time (aggregator_common.db, aggregator_processor.process, aggregator_processor.loop).
"""

from __future__ import annotations

import os
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
        new_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

        # Patch all modules that bound SessionFactory at import time via `from ... import`.
        # Python functions look up globals at call time, so replacing the module attribute
        # is sufficient for every subsequent call.
        import aggregator_common.db as db_mod
        db_mod.SessionFactory = new_factory

        import aggregator_processor.process as process_mod
        process_mod.SessionFactory = new_factory

        import aggregator_processor.loop as loop_mod
        loop_mod.SessionFactory = new_factory

        yield engine
        engine.dispose()


@pytest.fixture(scope="session")
def db_url(db_engine):
    """Connection URL string for the live test container — used by subprocess tests."""
    return os.environ["DATABASE_URL"]


@pytest.fixture(autouse=True)
def clean_db(db_engine):
    """Truncate all data tables before each test for full isolation."""
    with db_engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE articles, sources RESTART IDENTITY CASCADE"))
        conn.commit()
    yield


@pytest.fixture
def db_session(db_engine) -> Generator[Session, None, None]:
    """Per-test session for DB setup and direct inspection."""
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
def processor_settings(db_engine):
    """ProcessorSettings backed by the live test DB."""
    from aggregator_processor.config import ProcessorSettings

    return ProcessorSettings()


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
    default_image_url: str | None = None,
) -> Source:
    src = Source(name=name, feed_url=url, enabled=enabled, default_image_url=default_image_url)
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
    feed_url: str = "https://example.com/article/1",
    feed_summary: str | None = None,
    feed_published_at: datetime | None = None,
    raw_payload: dict | None = None,
    retrieved_at: datetime = _NOW,
    claimed_by: str | None = None,
    claimed_at: datetime | None = None,
    last_error: str | None = None,
    retry_count: int = 0,
    next_retry_at: datetime | None = None,
) -> Article:
    article = Article(
        source_id=source_id,
        dedup_key=dedup_key,
        status=status,
        feed_title=feed_title,
        feed_url=feed_url,
        feed_summary=feed_summary,
        feed_published_at=feed_published_at,
        raw_payload=raw_payload or {},
        retrieved_at=retrieved_at,
        claimed_by=claimed_by,
        claimed_at=claimed_at,
        last_error=last_error,
        retry_count=retry_count,
        next_retry_at=next_retry_at,
    )
    session.add(article)
    session.flush()
    session.commit()
    session.refresh(article)
    return article
