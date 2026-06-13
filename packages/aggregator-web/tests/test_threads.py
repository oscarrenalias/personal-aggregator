"""Web route tests for /threads endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

from aggregator_common.models import Thread


def _make_thread(
    session,
    *,
    title: str = "Test Thread",
    surfaced: bool = True,
    top_grade: int | None = 75,
) -> Thread:
    now = datetime.now(tz=timezone.utc)
    thread = Thread(
        representative_title=title,
        first_seen=now,
        last_updated=now,
        status="active",
        surfaced=surfaced,
        top_grade=top_grade,
        source_list=[],
        known_facts=[],
        deltas=[],
    )
    session.add(thread)
    session.flush()
    session.commit()
    session.refresh(thread)
    return thread


class TestThreadsIndex:
    def test_get_threads_returns_200(self, client, db_session):
        response = client.get("/threads")
        assert response.status_code == 200

    def test_get_threads_htmx_returns_partial(self, client, db_session):
        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        # HTMX partial returns _thread_list.html content (not a full HTML page)
        assert "<!DOCTYPE html>" not in response.text

    def test_get_threads_full_page_has_doctype(self, client, db_session):
        response = client.get("/threads")
        assert response.status_code == 200
        assert "<!DOCTYPE html>" in response.text

    def test_get_threads_shows_surfaced_thread_titles(self, client, db_session):
        _make_thread(db_session, title="Breaking AI News", surfaced=True)
        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "Breaking AI News" in response.text

    def test_get_threads_unsurfaced_threads_hidden(self, client, db_session):
        _make_thread(db_session, title="Hidden Thread", surfaced=False)
        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "Hidden Thread" not in response.text

    def test_get_threads_no_tier_section_headings(self, client, db_session):
        _make_thread(db_session, title="Some Thread")
        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        # Flat list: no tier section labels in the HTML
        assert "must_know" not in response.text
        assert "worth_tracking" not in response.text
        assert "low_noise" not in response.text

    def test_get_threads_grade_descending_order(self, client, db_session):
        _make_thread(db_session, title="Low Grade Thread", top_grade=30)
        _make_thread(db_session, title="High Grade Thread", top_grade=90)
        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        high_pos = response.text.index("High Grade Thread")
        low_pos = response.text.index("Low Grade Thread")
        assert high_pos < low_pos


class TestThreadDetail:
    def test_get_thread_detail_returns_200(self, client, db_session):
        thread = _make_thread(db_session, title="Detailed Thread")
        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})
        assert response.status_code == 200

    def test_get_thread_detail_contains_thread_title(self, client, db_session):
        thread = _make_thread(db_session, title="Climate Change Summit 2025")
        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "Climate Change Summit 2025" in response.text

    def test_get_thread_detail_unknown_id_returns_404(self, client, db_session):
        response = client.get("/threads/999999", headers={"HX-Request": "true"})
        assert response.status_code == 404

    def test_get_thread_detail_full_page_returns_shell(self, client, db_session):
        thread = _make_thread(db_session, title="Full Page Thread")
        response = client.get(f"/threads/{thread.id}")
        assert response.status_code == 200
        assert "<!DOCTYPE html>" in response.text


class TestThreadsRecluster:
    def test_post_recluster_returns_202(self, client, db_session):
        response = client.post("/threads/recluster")
        assert response.status_code == 202

    def test_post_recluster_has_hx_trigger_header(self, client, db_session):
        response = client.post("/threads/recluster")
        assert response.headers.get("HX-Trigger") == "reclustered"

    def test_post_recluster_enqueues_recluster(self, client, db_session):
        from aggregator_common.models import ClusterState
        response = client.post("/threads/recluster")
        assert response.status_code == 202
        # Verify the ClusterState row was created
        row = db_session.get(ClusterState, True)
        assert row is not None
        assert row.recluster_requested is True
