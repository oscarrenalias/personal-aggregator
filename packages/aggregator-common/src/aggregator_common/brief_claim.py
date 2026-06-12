from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Brief


def claim_brief(session: Session, worker_id: str, now: datetime) -> Brief | None:
    stmt = (
        select(Brief)
        .where(Brief.status == "pending")
        .where(Brief.claimed_at.is_(None))
        .order_by(Brief.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    brief = session.scalars(stmt).first()
    if brief is None:
        return None
    brief.status = "generating"
    brief.claimed_by = worker_id
    brief.claimed_at = now
    session.flush()
    return brief


def complete_brief(
    session: Session,
    brief_id: int,
    model: str,
    headline: str,
    intro: str,
    generated_at: datetime,
) -> None:
    brief = session.get(Brief, brief_id)
    if brief is None:
        raise ValueError(f"Brief {brief_id} not found")
    brief.status = "ready"
    brief.model = model
    brief.headline = headline
    brief.intro = intro
    brief.generated_at = generated_at
    brief.claimed_by = None
    brief.claimed_at = None
    session.flush()


def fail_brief(session: Session, brief_id: int, error: str) -> None:
    brief = session.get(Brief, brief_id)
    if brief is None:
        raise ValueError(f"Brief {brief_id} not found")
    brief.status = "failed"
    brief.error = error
    brief.claimed_by = None
    brief.claimed_at = None
    session.flush()


def reap_stale_brief_claims(
    session: Session,
    lease_seconds: float,
    now: datetime,
) -> int:
    cutoff = now - timedelta(seconds=lease_seconds)
    stmt = (
        select(Brief)
        .where(Brief.status == "generating")
        .where(Brief.claimed_at.is_not(None))
        .where(Brief.claimed_at < cutoff)
        .with_for_update(skip_locked=True)
    )
    briefs = list(session.scalars(stmt).all())
    for brief in briefs:
        brief.status = "pending"
        brief.claimed_by = None
        brief.claimed_at = None
    session.flush()
    return len(briefs)
