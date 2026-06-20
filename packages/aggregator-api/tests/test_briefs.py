"""Integration tests for GET /briefs and GET /briefs/{id}."""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

from conftest import make_brief, make_brief_topic

_now = datetime.now(tz=timezone.utc)
_base = _now.replace(hour=0, minute=0, second=0, microsecond=0)

_day_counter = itertools.count(100)


def _period(offset_days: int):
    start = _base - timedelta(days=offset_days)
    end = start + timedelta(hours=23)
    return start, end


def _next_period():
    n = next(_day_counter)
    return _period(n)


class TestListBriefs:
    def test_returns_200(self, client, db_session):
        response = client.get("/briefs")
        assert response.status_code == 200

    def test_returns_paginated_envelope(self, client, db_session):
        data = client.get("/briefs").json()
        assert "items" in data
        assert "next_cursor" in data

    def test_empty_when_no_ready_briefs(self, client, db_session):
        data = client.get("/briefs").json()
        assert data["items"] == []
        assert data["next_cursor"] is None

    def test_non_ready_briefs_excluded(self, client, db_session):
        ps1, pe1 = _next_period()
        ps2, pe2 = _next_period()
        make_brief(db_session, status="pending", period_start=ps1, period_end=pe1)
        make_brief(db_session, status="failed", period_start=ps2, period_end=pe2)
        data = client.get("/briefs").json()
        assert data["items"] == []

    def test_returns_ready_brief(self, client, db_session):
        ps, pe = _next_period()
        brief = make_brief(db_session, status="ready", headline="Ready Brief", period_start=ps, period_end=pe)
        make_brief_topic(db_session, brief_id=brief.id)
        data = client.get("/briefs").json()
        assert len(data["items"]) == 1
        assert data["items"][0]["headline"] == "Ready Brief"

    def test_newest_first_ordering(self, client, db_session):
        ps1, pe1 = _next_period()
        ps2, pe2 = _next_period()
        older = make_brief(db_session, status="ready", headline="Older Brief", period_start=ps1, period_end=pe1)
        newer = make_brief(db_session, status="ready", headline="Newer Brief", period_start=ps2, period_end=pe2)
        data = client.get("/briefs").json()
        ids = [item["id"] for item in data["items"]]
        assert newer.id in ids
        assert older.id in ids
        # newer was inserted last so has a higher id; id DESC in ORDER BY puts it first
        assert ids.index(newer.id) < ids.index(older.id)

    def test_next_cursor_none_when_fewer_than_limit(self, client, db_session):
        ps, pe = _next_period()
        make_brief(db_session, status="ready", period_start=ps, period_end=pe)
        data = client.get("/briefs?limit=50").json()
        assert data["next_cursor"] is None

    def test_next_cursor_set_when_page_full(self, client, db_session):
        for _ in range(3):
            ps, pe = _next_period()
            make_brief(db_session, status="ready", period_start=ps, period_end=pe)
        data = client.get("/briefs?limit=2").json()
        assert data["next_cursor"] is not None

    def test_cursor_round_trip_no_overlap(self, client, db_session):
        for i in range(4):
            ps, pe = _next_period()
            make_brief(db_session, status="ready", headline=f"Brief {i}", period_start=ps, period_end=pe)
        page1 = client.get("/briefs?limit=2").json()
        cursor = page1["next_cursor"]
        assert cursor is not None
        page2 = client.get(f"/briefs?limit=2&cursor={cursor}").json()
        ids1 = {item["id"] for item in page1["items"]}
        ids2 = {item["id"] for item in page2["items"]}
        assert ids1.isdisjoint(ids2), "Pages overlap"

    def test_item_has_brief_fields(self, client, db_session):
        from test_contract import _BRIEF_FIELDS

        ps, pe = _next_period()
        brief = make_brief(db_session, status="ready", period_start=ps, period_end=pe)
        make_brief_topic(db_session, brief_id=brief.id)
        data = client.get("/briefs").json()
        assert len(data["items"]) >= 1
        assert set(data["items"][0].keys()) == _BRIEF_FIELDS


class TestGetBrief:
    def test_returns_200_for_ready_brief(self, client, db_session):
        ps, pe = _next_period()
        brief = make_brief(db_session, status="ready", period_start=ps, period_end=pe)
        make_brief_topic(db_session, brief_id=brief.id)
        response = client.get(f"/briefs/{brief.id}")
        assert response.status_code == 200

    def test_returns_full_brief_with_topics(self, client, db_session):
        ps, pe = _next_period()
        brief = make_brief(db_session, status="ready", headline="Full Brief", period_start=ps, period_end=pe)
        make_brief_topic(db_session, brief_id=brief.id, position=1, headline="Topic Alpha")
        make_brief_topic(db_session, brief_id=brief.id, position=2, headline="Topic Beta")
        data = client.get(f"/briefs/{brief.id}").json()
        assert data["headline"] == "Full Brief"
        assert len(data["topics"]) == 2
        assert data["topics"][0]["headline"] == "Topic Alpha"
        assert data["topics"][1]["headline"] == "Topic Beta"

    def test_topics_ordered_by_position(self, client, db_session):
        ps, pe = _next_period()
        brief = make_brief(db_session, status="ready", period_start=ps, period_end=pe)
        make_brief_topic(db_session, brief_id=brief.id, position=3, headline="Third")
        make_brief_topic(db_session, brief_id=brief.id, position=1, headline="First")
        make_brief_topic(db_session, brief_id=brief.id, position=2, headline="Second")
        data = client.get(f"/briefs/{brief.id}").json()
        assert [t["position"] for t in data["topics"]] == [1, 2, 3]

    def test_returns_404_for_unknown_id(self, client, db_session):
        response = client.get("/briefs/999999777")
        assert response.status_code == 404

    def test_returns_404_for_pending_brief(self, client, db_session):
        ps, pe = _next_period()
        brief = make_brief(db_session, status="pending", period_start=ps, period_end=pe)
        response = client.get(f"/briefs/{brief.id}")
        assert response.status_code == 404

    def test_returns_404_for_failed_brief(self, client, db_session):
        ps, pe = _next_period()
        brief = make_brief(db_session, status="failed", period_start=ps, period_end=pe)
        response = client.get(f"/briefs/{brief.id}")
        assert response.status_code == 404

    def test_response_shape_matches_brief_fields(self, client, db_session):
        from test_contract import _BRIEF_FIELDS

        ps, pe = _next_period()
        brief = make_brief(db_session, status="ready", period_start=ps, period_end=pe)
        make_brief_topic(db_session, brief_id=brief.id)
        data = client.get(f"/briefs/{brief.id}").json()
        assert set(data.keys()) == _BRIEF_FIELDS
