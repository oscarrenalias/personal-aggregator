"""Test that the partial unique index on briefs prevents duplicate auto-briefs per period."""

from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from aggregator_common.models import Brief

_PERIOD_START = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
_PERIOD_END = datetime(2025, 1, 1, 23, 59, 59, tzinfo=timezone.utc)


def test_auto_brief_unique_per_period_start(db_engine, clean_db):
    """Inserting two auto-origin briefs for the same period_start raises IntegrityError."""
    factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    s = factory()
    try:
        b1 = Brief(status="pending", origin="auto", period_start=_PERIOD_START, period_end=_PERIOD_END)
        s.add(b1)
        s.commit()

        b2 = Brief(status="pending", origin="auto", period_start=_PERIOD_START, period_end=_PERIOD_END)
        s.add(b2)
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()
    finally:
        s.close()


def test_auto_brief_different_periods_allowed(db_engine, clean_db):
    """Two auto briefs with different period_start values are both accepted."""
    from datetime import timedelta

    factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    s = factory()
    try:
        b1 = Brief(
            status="pending",
            origin="auto",
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
        )
        b2 = Brief(
            status="pending",
            origin="auto",
            period_start=_PERIOD_START + timedelta(days=1),
            period_end=_PERIOD_END + timedelta(days=1),
        )
        s.add(b1)
        s.add(b2)
        s.commit()

        count = s.query(Brief).filter_by(origin="auto").count()
        assert count == 2
    finally:
        s.close()


def test_manual_brief_allows_same_period_start(db_engine, clean_db):
    """Manual briefs have no uniqueness constraint; two for the same period_start is allowed."""
    factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    s = factory()
    try:
        b1 = Brief(status="pending", origin="manual", period_start=_PERIOD_START, period_end=_PERIOD_END)
        b2 = Brief(status="pending", origin="manual", period_start=_PERIOD_START, period_end=_PERIOD_END)
        s.add(b1)
        s.add(b2)
        s.commit()  # must not raise

        count = s.query(Brief).filter_by(origin="manual").count()
        assert count == 2
    finally:
        s.close()


def test_auto_and_manual_same_period_coexist(db_engine, clean_db):
    """An auto brief and a manual brief for the same period_start can coexist."""
    factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    s = factory()
    try:
        auto = Brief(status="pending", origin="auto", period_start=_PERIOD_START, period_end=_PERIOD_END)
        manual = Brief(status="pending", origin="manual", period_start=_PERIOD_START, period_end=_PERIOD_END)
        s.add(auto)
        s.add(manual)
        s.commit()

        count = s.query(Brief).count()
        assert count == 2
    finally:
        s.close()
