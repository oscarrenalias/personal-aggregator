from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from aggregator_common.models import InterestProfile


def set_interest_profile(session: Session, text: str) -> dict:
    """Upsert the singleton InterestProfile row and return its fields as a dict."""
    stmt = (
        pg_insert(InterestProfile)
        .values(id=True, profile_text=text)
        .on_conflict_do_update(
            index_elements=["id"],
            set_={"profile_text": text, "updated_at": func.now()},
        )
        .returning(InterestProfile.id, InterestProfile.profile_text, InterestProfile.updated_at)
    )
    row = session.execute(stmt).one()
    return {"id": row.id, "profile_text": row.profile_text, "updated_at": row.updated_at}
