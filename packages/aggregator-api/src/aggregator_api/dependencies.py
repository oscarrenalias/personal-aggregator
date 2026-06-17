from collections.abc import Generator

from sqlalchemy.orm import Session

from aggregator_common.db import SessionFactory


def get_db() -> Generator[Session, None, None]:
    session: Session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
