"""Shared fixtures for aggregator-clusterer tests."""
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

# Set placeholder before any aggregator_common.db import triggers Settings() at module level.
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

        import aggregator_common.db as db_mod
        db_mod.SessionFactory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        db_mod.engine = engine

        yield engine
        engine.dispose()


@pytest.fixture
def clean_db(db_engine):
    with db_engine.connect() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE thread_memberships, threads, articles, sources, categories"
                " RESTART IDENTITY CASCADE"
            )
        )
        conn.execute(text("DELETE FROM interest_profile"))
        conn.execute(text("DELETE FROM cluster_state"))
        conn.commit()
    yield


@pytest.fixture
def db_session(db_engine, clean_db) -> Generator[Session, None, None]:
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


from aggregator_common.models import Article, Source, Thread  # noqa: E402
from aggregator_common.state import ArticleStatus  # noqa: E402


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
    status: ArticleStatus = ArticleStatus.ready,
    feed_title: str | None = "Test Article",
    clean_title: str | None = None,
    summary: str | None = None,
    topics: list | dict | None = None,
    entities: list | dict | None = None,
    feed_published_at: datetime | None = None,
    raw_payload: dict | None = None,
    retrieved_at: datetime = _NOW,
    importance_score: int | None = None,
) -> Article:
    article = Article(
        source_id=source_id,
        dedup_key=dedup_key,
        status=status,
        feed_title=feed_title,
        clean_title=clean_title,
        summary=summary,
        topics=topics,
        entities=entities,
        feed_published_at=feed_published_at or _NOW,
        raw_payload=raw_payload or {},
        retrieved_at=retrieved_at,
        importance_score=importance_score,
    )
    session.add(article)
    session.flush()
    session.commit()
    session.refresh(article)
    return article


def make_thread(
    session: Session,
    *,
    title: str = "Test Thread",
    last_updated: datetime | None = None,
    status: str = "active",
    tier: str | None = None,
    source_diversity: float | None = None,
    confidence: float | None = None,
    source_list: list | None = None,
    known_facts: list | None = None,
) -> Thread:
    now = last_updated or datetime.now(tz=timezone.utc)
    thread = Thread(
        representative_title=title,
        first_seen=now,
        last_updated=now,
        status=status,
        tier=tier,
        source_diversity=source_diversity,
        confidence=confidence,
        source_list=source_list or [],
        known_facts=known_facts or [],
        deltas=[],
    )
    session.add(thread)
    session.flush()
    session.commit()
    session.refresh(thread)
    return thread
