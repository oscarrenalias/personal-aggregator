"""Regression: brief loop must not delete any brief rows (pruning moved to janitor)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from aggregator_common.models import Brief

_NOW = datetime.now(timezone.utc)
_OLD = _NOW - timedelta(days=60)


def test_brief_loop_does_not_delete_briefs(db_engine, clean_db):
    """Brief loop iteration leaves existing briefs intact (regression: pruning was removed from loop.py)."""
    from aggregator_brief.config import BriefSettings
    from aggregator_brief.loop import _run_one_iteration

    factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)

    # Insert an old completed brief that would have been pruned under the old code.
    setup = factory()
    try:
        old_brief = Brief(
            status="completed",
            origin="manual",
            period_start=_OLD.replace(hour=0, minute=0, second=0, microsecond=0),
            period_end=_OLD.replace(hour=23, minute=59, second=59),
        )
        setup.add(old_brief)
        setup.flush()
        setup.execute(
            text("UPDATE briefs SET created_at = :ts WHERE id = :id"),
            {"ts": _OLD, "id": old_brief.id},
        )
        setup.commit()
        saved_id = old_brief.id
    finally:
        setup.close()

    # brief_generation_hour=25 → _maybe_enqueue_auto_brief always returns False (no auto trigger).
    settings = BriefSettings(brief_generation_hour=25)

    # Run a loop iteration — no pending briefs, so it returns immediately without doing work.
    _run_one_iteration(settings, factory, "test-worker-regression")

    check = factory()
    try:
        remaining = check.get(Brief, saved_id)
        assert remaining is not None, (
            "Brief loop must not delete briefs; pruning responsibility belongs to the janitor service"
        )
    finally:
        check.close()
