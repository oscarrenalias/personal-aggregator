"""Tests for aggregator_web /today and /today/refresh routes."""

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from aggregator_common.models import Brief

from conftest import make_brief


class TestGetToday:
    def test_no_brief_returns_200_with_empty_state(self, client, db_session):
        resp = client.get("/today")
        assert resp.status_code == 200
        assert "No brief yet" in resp.text

    def test_pending_brief_returns_generating_indicator(self, client, db_session):
        make_brief(db_session, status="pending")

        resp = client.get("/today")
        assert resp.status_code == 200
        assert "Generating" in resp.text

    def test_generating_brief_returns_generating_indicator(self, client, db_session):
        make_brief(db_session, status="generating")

        resp = client.get("/today")
        assert resp.status_code == 200
        assert "Generating" in resp.text

    def test_ready_brief_headline_in_response(self, client, db_session):
        make_brief(db_session, status="ready", headline="Big News Today", intro="A lot happened.")

        resp = client.get("/today")
        assert resp.status_code == 200
        assert "Big News Today" in resp.text

    def test_ready_brief_intro_in_response(self, client, db_session):
        make_brief(db_session, status="ready", headline="Headline", intro="The intro text here.")

        resp = client.get("/today")
        assert resp.status_code == 200
        assert "The intro text here." in resp.text

    def test_failed_brief_treated_as_no_brief(self, client, db_session):
        """A failed brief is not displayed; the empty state is shown instead."""
        make_brief(db_session, status="failed", error="generation failed")

        resp = client.get("/today")
        assert resp.status_code == 200
        assert "No brief yet" in resp.text


class TestPostTodayRefresh:
    def test_creates_pending_brief(self, client, db_engine, clean_db):
        resp = client.post("/today/refresh")
        assert resp.status_code == 200

        factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
        s = factory()
        try:
            count = s.execute(
                select(func.count()).select_from(Brief).where(
                    Brief.status.in_(["pending", "generating"])
                )
            ).scalar()
            assert count == 1

            pending = s.execute(
                select(Brief).where(Brief.status.in_(["pending", "generating"]))
            ).scalar_one()
            assert pending.origin == "manual"
        finally:
            s.close()

    def test_creates_brief_with_generating_response(self, client, db_session):
        resp = client.post("/today/refresh")
        assert resp.status_code == 200
        assert "Generating" in resp.text

    def test_noop_when_pending_already_exists(self, client, db_session, db_engine):
        make_brief(db_session, status="pending")

        resp = client.post("/today/refresh")
        assert resp.status_code == 200

        factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
        s = factory()
        try:
            count = s.execute(
                select(func.count()).select_from(Brief).where(
                    Brief.status.in_(["pending", "generating"])
                )
            ).scalar()
            assert count == 1  # still only the original one
        finally:
            s.close()

    def test_noop_when_generating_already_exists(self, client, db_session, db_engine):
        make_brief(db_session, status="generating")

        client.post("/today/refresh")

        factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
        s = factory()
        try:
            count = s.execute(
                select(func.count()).select_from(Brief).where(
                    Brief.status.in_(["pending", "generating"])
                )
            ).scalar()
            assert count == 1
        finally:
            s.close()
