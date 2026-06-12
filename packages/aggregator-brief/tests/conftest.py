"""Shared fixtures for aggregator-brief tests.

Sets a placeholder DATABASE_URL at module load time so aggregator_common.db can be
imported during pytest collection without a live database. The session-scoped
db_engine fixture starts a real Postgres container, runs Alembic migrations, and
patches SessionFactory in aggregator_common.db and aggregator_web.app.
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
_NOW = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


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

        import aggregator_common.db as db_mod

        db_mod.SessionFactory = new_factory
        db_mod.engine = engine

        import aggregator_web.app as app_mod

        app_mod.SessionFactory = new_factory

        yield engine
        engine.dispose()


@pytest.fixture
def clean_db(db_engine):
    """Truncate all data tables before each test for full isolation."""
    with db_engine.connect() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE brief_topics, briefs, articles, sources, categories"
                " RESTART IDENTITY CASCADE"
            )
        )
        conn.execute(text("DELETE FROM interest_profile"))
        conn.commit()
    yield


@pytest.fixture
def db_session(db_engine, clean_db) -> Generator[Session, None, None]:
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
def client(db_engine, clean_db):
    """FastAPI TestClient with get_db overridden to use the test DB."""
    from fastapi.testclient import TestClient

    from aggregator_web.app import app, get_db

    factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def runner():
    from typer.testing import CliRunner

    return CliRunner()


# ---------------------------------------------------------------------------
# Helper constructors
# ---------------------------------------------------------------------------

from aggregator_common.models import Article, Brief, Source  # noqa: E402
from aggregator_common.state import ArticleStatus  # noqa: E402


def make_source(
    session: Session,
    *,
    name: str = "Test Feed",
    url: str = "https://example.com/feed.xml",
) -> Source:
    src = Source(name=name, feed_url=url)
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
    status: ArticleStatus = ArticleStatus.ready,
    feed_title: str = "Test Article",
    clean_title: str | None = None,
    summary: str | None = None,
    raw_payload: dict | None = None,
    retrieved_at: datetime = _NOW,
    published_at: datetime | None = None,
    categories: list | None = None,
    importance_score: int | None = None,
    search_text: str | None = None,
) -> Article:
    article = Article(
        source_id=source_id,
        dedup_key=dedup_key,
        status=status,
        feed_title=feed_title,
        clean_title=clean_title,
        summary=summary,
        raw_payload=raw_payload or {},
        retrieved_at=retrieved_at,
        published_at=published_at,
        categories=categories,
        importance_score=importance_score,
    )
    session.add(article)
    session.flush()
    session.commit()
    session.refresh(article)

    if search_text:
        session.execute(
            text(
                "UPDATE articles SET search_vector = to_tsvector('english', :txt)"
                " WHERE id = :id"
            ),
            {"txt": search_text, "id": article.id},
        )
        session.commit()
        session.refresh(article)

    return article


def make_brief(
    session: Session,
    *,
    status: str = "pending",
    origin: str = "manual",
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    headline: str | None = None,
    intro: str | None = None,
    model: str | None = None,
    claimed_by: str | None = None,
    claimed_at: datetime | None = None,
    error: str | None = None,
) -> Brief:
    if period_start is None:
        period_start = _NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    if period_end is None:
        period_end = period_start.replace(hour=23, minute=59, second=59)
    brief = Brief(
        status=status,
        origin=origin,
        period_start=period_start,
        period_end=period_end,
        headline=headline,
        intro=intro,
        model=model,
        claimed_by=claimed_by,
        claimed_at=claimed_at,
        error=error,
    )
    session.add(brief)
    session.flush()
    session.commit()
    session.refresh(brief)
    return brief
