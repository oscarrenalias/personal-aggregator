"""Shared fixtures for aggregator-api tests.

Sets a placeholder DATABASE_URL at module load time so aggregator_common.db can be
imported during pytest collection without a live database. The session-scoped
db_engine fixture starts a real Postgres container, runs Alembic migrations, and
patches SessionFactory in aggregator_common.db so both the get_db dependency and the
healthz route use the test database.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

# Must be set before any import of aggregator_common.db triggers Settings() at module level.
os.environ.setdefault("DATABASE_URL", "postgresql://placeholder:placeholder@localhost/placeholder")

_COMMON_ROOT = Path(__file__).parent.parent.parent / "aggregator-common"
_ALEMBIC_INI = _COMMON_ROOT / "alembic.ini"
_MIGRATIONS_DIR = _COMMON_ROOT / "src" / "aggregator_common" / "migrations"
_NOW = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


import socket as _socket


def _ensure_docker_host() -> str | None:
    """Return the resolved DOCKER_HOST path, or None if no socket is found."""
    if "DOCKER_HOST" in os.environ:
        return os.environ["DOCKER_HOST"]
    if Path("/var/run/docker.sock").exists():
        os.environ["DOCKER_HOST"] = "unix:///var/run/docker.sock"
        return os.environ["DOCKER_HOST"]
    orbstack = Path.home() / ".orbstack" / "run" / "docker.sock"
    if orbstack.exists():
        os.environ["DOCKER_HOST"] = f"unix://{orbstack}"
        return os.environ["DOCKER_HOST"]
    return None


def _docker_is_responsive() -> bool:
    """Return True only when the Docker daemon is actually answering HTTP.

    The OrbStack socket may exist on disk even when OrbStack is paused; a
    low-level connect succeeds but the first read times out.  We probe with a
    2-second timeout so the fixture fails fast instead of waiting 60 s.
    """
    docker_host = os.environ.get("DOCKER_HOST", "")
    sock_path = docker_host.replace("unix://", "") if docker_host.startswith("unix://") else None
    if not sock_path:
        return False
    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        s.connect(sock_path)
        s.sendall(b"GET /version HTTP/1.0\r\nHost: localhost\r\n\r\n")
        data = s.recv(64)
        return bool(data)
    except (_socket.timeout, OSError):
        return False
    finally:
        s.close()


@pytest.fixture(scope="session")
def db_engine():
    from testcontainers.postgres import PostgresContainer

    docker_host = _ensure_docker_host()
    if docker_host is None:
        pytest.skip("No Docker socket found — Docker or OrbStack must be running")
    if not _docker_is_responsive():
        pytest.skip(
            "Docker socket exists but daemon is not responding "
            "(OrbStack may be paused — start it and retry)"
        )

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

        # Patch all modules that captured SessionFactory at import time.
        import aggregator_common.db as db_mod

        db_mod.SessionFactory = new_factory
        db_mod.engine = engine

        yield engine
        engine.dispose()


@pytest.fixture
def clean_db(db_engine):
    """Truncate all data tables before each test for full isolation."""
    with db_engine.connect() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE brief_topics, briefs, thread_memberships, threads,"
                " articles, sources, categories RESTART IDENTITY CASCADE"
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
    from aggregator_api.app import app
    from aggregator_api.dependencies import get_db

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


# ---------------------------------------------------------------------------
# Helper constructors
# ---------------------------------------------------------------------------

from aggregator_common.models import (  # noqa: E402
    Article,
    Brief,
    BriefTopic,
    Category,
    Source,
    Thread,
    ThreadMembership,
)
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
    feed_title: str = "Test Article",
    clean_title: str | None = None,
    feed_url: str = "https://example.com/article",
    summary: str | None = None,
    excerpt: str | None = None,
    feed_published_at: datetime | None = _NOW,
    raw_payload: dict | None = None,
    retrieved_at: datetime = _NOW,
    categories: list | None = None,
    topics: list | None = None,
    importance_score: int | None = None,
    is_read: bool = False,
    is_saved: bool = False,
    search_text: str | None = None,
    header_image_url: str | None = None,
) -> Article:
    article = Article(
        source_id=source_id,
        dedup_key=dedup_key,
        status=status,
        feed_title=feed_title,
        clean_title=clean_title,
        feed_url=feed_url,
        summary=summary,
        excerpt=excerpt,
        feed_published_at=feed_published_at,
        raw_payload=raw_payload or {"link": feed_url},
        retrieved_at=retrieved_at,
        categories=categories,
        header_image_url=header_image_url,
        # Default to a realistic list of topic strings (matches production data,
        # where Article.topics is a JSON array) so serialization tests exercise
        # the real shape — not a dict, which previously masked a 500 bug.
        topics=topics if topics is not None else ["technology", "ai"],
        importance_score=importance_score,
        is_read=is_read,
        is_saved=is_saved,
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


def make_category(
    session: Session,
    *,
    name: str = "Technology",
    enabled: bool = True,
    sort_order: int = 0,
    description: str | None = None,
) -> Category:
    cat = Category(name=name, enabled=enabled, sort_order=sort_order, description=description)
    session.add(cat)
    session.flush()
    session.commit()
    session.refresh(cat)
    return cat


def make_thread(
    session: Session,
    *,
    title: str = "Test Thread",
    surfaced: bool = True,
    top_grade: int | None = 75,
    dismissed: bool = False,
    last_updated: datetime | None = None,
) -> Thread:
    now = last_updated or datetime.now(tz=timezone.utc)
    thread = Thread(
        representative_title=title,
        first_seen=now,
        last_updated=now,
        status="active",
        surfaced=surfaced,
        top_grade=top_grade,
        dismissed=dismissed,
        source_list=[],
        known_facts=[],
        deltas=[],
    )
    session.add(thread)
    session.flush()
    session.commit()
    session.refresh(thread)
    return thread


def make_thread_membership(
    session: Session,
    *,
    thread_id: int,
    article_id: int,
    suppressed: bool = False,
) -> ThreadMembership:
    now = datetime.now(tz=timezone.utc)
    tm = ThreadMembership(
        thread_id=thread_id,
        article_id=article_id,
        suppressed=suppressed,
        assigned_at=now,
    )
    session.add(tm)
    session.flush()
    session.commit()
    session.refresh(tm)
    return tm


def make_brief(
    session: Session,
    *,
    status: str = "ready",
    headline: str = "Today's Headlines",
    intro: str = "A summary of today's top stories.",
    model: str = "gpt-4.1",
    period_start: datetime | None = None,
    period_end: datetime | None = None,
) -> Brief:
    now = datetime.now(tz=timezone.utc)
    brief = Brief(
        status=status,
        headline=headline,
        intro=intro,
        model=model,
        period_start=period_start or now.replace(hour=0, minute=0, second=0, microsecond=0),
        period_end=period_end or now.replace(hour=23, minute=59, second=59, microsecond=0),
        generated_at=now,
    )
    session.add(brief)
    session.flush()
    session.commit()
    session.refresh(brief)
    return brief


def make_brief_topic(
    session: Session,
    *,
    brief_id: int,
    position: int = 1,
    headline: str = "Topic Headline",
    what_happened: str = "This is what happened.",
    why_it_matters: str = "This is why it matters.",
    historical_context: str | None = None,
    refs: list | None = None,
) -> BriefTopic:
    topic = BriefTopic(
        brief_id=brief_id,
        position=position,
        headline=headline,
        what_happened=what_happened,
        why_it_matters=why_it_matters,
        historical_context=historical_context,
        topic_refs=refs or [],
    )
    session.add(topic)
    session.flush()
    session.commit()
    session.refresh(topic)
    return topic
