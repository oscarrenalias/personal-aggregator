from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, Engine
from sqlalchemy.orm import sessionmaker, Session

from .config import Settings


def _make_engine(settings: Settings) -> Engine:
    url = settings.database_url
    # Ensure psycopg (v3) driver is used, not psycopg2
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif not url.startswith("postgresql+psycopg://"):
        raise ValueError(f"DATABASE_URL must use a postgresql scheme, got: {url!r}")
    return create_engine(url, pool_pre_ping=True)


_settings = Settings()
engine = _make_engine(_settings)
SessionFactory = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    session: Session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
