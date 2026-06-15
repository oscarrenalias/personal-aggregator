"""Tests for last_viewed_at stamping on web thread detail view and thread-update dot rendering."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aggregator_common.models import Source, Thread, ThreadMembership

_NOW = datetime.now(tz=timezone.utc)


def _make_source(session, name: str = "LVA Test Source") -> Source:
    src = Source(name=name, feed_url=f"https://{name.lower().replace(' ', '-')}.example.com/feed.xml")
    session.add(src)
    session.flush()
    session.commit()
    session.refresh(src)
    return src


def _make_thread(
    session,
    *,
    title: str = "LVA Thread",
    surfaced: bool = True,
    last_viewed_at: datetime | None = None,
    last_updated: datetime | None = None,
) -> Thread:
    now = last_updated or _NOW
    thread = Thread(
        representative_title=title,
        first_seen=now,
        last_updated=now,
        status="active",
        surfaced=surfaced,
        top_grade=75,
        source_list=[],
        known_facts=[],
        deltas=[],
        last_viewed_at=last_viewed_at,
    )
    session.add(thread)
    session.flush()
    session.commit()
    session.refresh(thread)
    return thread


# ---------------------------------------------------------------------------
# Web GET /threads/{id} stamps last_viewed_at
# ---------------------------------------------------------------------------


class TestThreadDetailStampsLastViewedAt:
    def test_get_thread_detail_sets_last_viewed_at(self, client, db_session):
        """Visiting GET /threads/{id} must set last_viewed_at on the Thread row."""
        thread = _make_thread(db_session, title="LVA Stamp Test Thread", last_viewed_at=None)

        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})

        assert response.status_code == 200
        db_session.expire(thread)
        db_session.refresh(thread)
        assert thread.last_viewed_at is not None

    def test_get_thread_detail_last_viewed_at_recent(self, client, db_session):
        """The stamped last_viewed_at must be at or after the time the request was made."""
        before = datetime.now(tz=timezone.utc)
        thread = _make_thread(db_session, title="LVA Recency Thread", last_viewed_at=None)

        client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})

        db_session.expire(thread)
        db_session.refresh(thread)
        assert thread.last_viewed_at is not None
        assert thread.last_viewed_at >= before

    def test_get_thread_detail_repeated_visits_overwrite_stamp(self, client, db_session):
        """A second visit must overwrite (not leave stale) the last_viewed_at stamp."""
        thread = _make_thread(db_session, title="LVA Overwrite Thread", last_viewed_at=None)

        client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})
        db_session.expire(thread)
        db_session.refresh(thread)
        first_stamp = thread.last_viewed_at

        client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})
        db_session.expire(thread)
        db_session.refresh(thread)

        assert thread.last_viewed_at is not None
        assert thread.last_viewed_at >= first_stamp

    def test_get_thread_detail_returns_correct_response(self, client, db_session):
        """The stamp must not break the normal 200 response with thread title."""
        thread = _make_thread(db_session, title="LVA Response Thread")

        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})

        assert response.status_code == 200
        assert "LVA Response Thread" in response.text


# ---------------------------------------------------------------------------
# Thread-update dot in thread list HTML
# ---------------------------------------------------------------------------


class TestThreadUpdateDotRendering:
    def test_dot_present_when_has_updates_true(self, client, db_session):
        """When has_updates=True (never viewed), the update dot must appear in thread list HTML."""
        _make_thread(db_session, title="Unviewed Thread", surfaced=True, last_viewed_at=None)

        response = client.get("/threads", headers={"HX-Request": "true"})

        assert response.status_code == 200
        assert "thread-update-dot" in response.text

    def test_dot_absent_when_has_updates_false(self, client, db_session):
        """When has_updates=False (viewed after last update), no update dot appears."""
        updated_at = _NOW - timedelta(hours=2)
        viewed_at = _NOW - timedelta(hours=1)
        _make_thread(
            db_session,
            title="Already Viewed Thread",
            surfaced=True,
            last_viewed_at=viewed_at,
            last_updated=updated_at,
        )

        response = client.get("/threads", headers={"HX-Request": "true"})

        assert response.status_code == 200
        assert "thread-update-dot" not in response.text

    def test_dot_carries_no_numeric_content(self, client, db_session):
        """The update dot must not contain a number (only a visual marker)."""
        import re

        _make_thread(db_session, title="Dot No Number Thread", surfaced=True, last_viewed_at=None)

        response = client.get("/threads", headers={"HX-Request": "true"})

        assert response.status_code == 200
        # Extract the dot span and verify it has no digits between its tags
        dot_matches = re.findall(r'<span[^>]*thread-update-dot[^>]*>(.*?)</span>', response.text, re.DOTALL)
        assert len(dot_matches) >= 1
        for dot_content in dot_matches:
            assert not re.search(r'\d', dot_content), f"Dot should not contain digits, got: {dot_content!r}"

    def test_dot_has_sidebar_dot_class(self, client, db_session):
        """The dot span must carry both sidebar-dot and thread-update-dot CSS classes."""
        _make_thread(db_session, title="Dot Classes Thread", surfaced=True, last_viewed_at=None)

        response = client.get("/threads", headers={"HX-Request": "true"})

        assert response.status_code == 200
        assert "sidebar-dot thread-update-dot" in response.text

    def test_mixed_threads_only_unviewed_has_dot(self, client, db_session):
        """Only threads with has_updates=True get the dot; viewed threads do not."""
        updated_at = _NOW - timedelta(hours=3)
        viewed_at = _NOW - timedelta(hours=1)
        _make_thread(db_session, title="Unviewed For Mix", surfaced=True, last_viewed_at=None)
        _make_thread(
            db_session,
            title="Viewed For Mix",
            surfaced=True,
            last_viewed_at=viewed_at,
            last_updated=updated_at,
        )

        response = client.get("/threads", headers={"HX-Request": "true"})

        assert response.status_code == 200
        assert "thread-update-dot" in response.text  # at least one dot (for the unviewed thread)
