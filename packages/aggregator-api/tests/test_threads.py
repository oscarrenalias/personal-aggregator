"""Integration tests for /threads endpoints."""

from __future__ import annotations

from conftest import make_article, make_source, make_thread, make_thread_membership
from test_contract import _THREAD_FIELDS, _THREAD_MEMBER_FIELDS


class TestListThreads:
    def test_list_threads_returns_200(self, client, db_session):
        response = client.get("/threads")
        assert response.status_code == 200

    def test_list_threads_returns_paginated_shape(self, client, db_session):
        data = client.get("/threads").json()
        assert "items" in data
        assert "next_cursor" in data

    def test_list_threads_includes_surfaced_thread(self, client, db_session):
        make_thread(db_session, title="Surfaced Thread", surfaced=True)
        data = client.get("/threads").json()
        titles = [t["representative_title"] for t in data["items"]]
        assert "Surfaced Thread" in titles

    def test_list_threads_excludes_unsurfaced_thread(self, client, db_session):
        make_thread(db_session, title="Hidden Thread", surfaced=False)
        data = client.get("/threads").json()
        titles = [t["representative_title"] for t in data["items"]]
        assert "Hidden Thread" not in titles

    def test_list_threads_excludes_dismissed_by_default(self, client, db_session):
        make_thread(db_session, title="Dismissed Thread", dismissed=True)
        data = client.get("/threads").json()
        titles = [t["representative_title"] for t in data["items"]]
        assert "Dismissed Thread" not in titles

    def test_list_threads_show_dismissed_includes_dismissed(self, client, db_session):
        make_thread(db_session, title="Dismissed Visible", dismissed=True)
        data = client.get("/threads?show_dismissed=true").json()
        titles = [t["representative_title"] for t in data["items"]]
        assert "Dismissed Visible" in titles

    def test_list_threads_item_has_expected_fields(self, client, db_session):
        make_thread(db_session, title="Field Check Thread")
        data = client.get("/threads").json()
        assert len(data["items"]) >= 1
        assert set(data["items"][0].keys()) == _THREAD_FIELDS

    def test_list_threads_invalid_sort_returns_400(self, client, db_session):
        response = client.get("/threads?sort=invalid_sort_value")
        assert response.status_code == 400

    def test_list_threads_invalid_limit_returns_422(self, client, db_session):
        response = client.get("/threads?limit=notanint")
        assert response.status_code == 422


class TestGetThread:
    def test_get_thread_returns_200(self, client, db_session):
        thread = make_thread(db_session, title="Detail Thread")
        response = client.get(f"/threads/{thread.id}")
        assert response.status_code == 200

    def test_get_thread_returns_correct_title(self, client, db_session):
        thread = make_thread(db_session, title="Exact Title Thread")
        data = client.get(f"/threads/{thread.id}").json()
        assert data["representative_title"] == "Exact Title Thread"

    def test_get_thread_returns_expected_fields(self, client, db_session):
        thread = make_thread(db_session)
        data = client.get(f"/threads/{thread.id}").json()
        assert set(data.keys()) == _THREAD_FIELDS

    def test_get_unknown_thread_returns_404(self, client, db_session):
        response = client.get("/threads/999999")
        assert response.status_code == 404

    def test_get_thread_does_not_update_last_viewed_at(self, client, db_session):
        """Spec requirement: GET /threads/{id} must not stamp last_viewed_at."""
        thread = make_thread(db_session, title="No Stamp Thread")
        assert thread.last_viewed_at is None

        client.get(f"/threads/{thread.id}")

        db_session.expire(thread)
        db_session.refresh(thread)
        assert thread.last_viewed_at is None

    def test_get_thread_returns_dismissed_flag(self, client, db_session):
        thread = make_thread(db_session, title="Dismissed Flag Thread", dismissed=True)
        data = client.get(f"/threads/{thread.id}").json()
        assert data["dismissed"] is True


class TestGetThreadMembers:
    def test_get_members_returns_200(self, client, db_session):
        src = make_source(db_session)
        thread = make_thread(db_session, title="Members Thread")
        article = make_article(db_session, source_id=src.id, dedup_key="mem-art-1")
        make_thread_membership(db_session, thread_id=thread.id, article_id=article.id)

        response = client.get(f"/threads/{thread.id}/members")
        assert response.status_code == 200

    def test_get_members_returns_paginated_shape(self, client, db_session):
        thread = make_thread(db_session)
        data = client.get(f"/threads/{thread.id}/members").json()
        assert "items" in data
        assert "next_cursor" in data
        assert data["next_cursor"] is None

    def test_get_members_returns_member_items(self, client, db_session):
        src = make_source(db_session)
        thread = make_thread(db_session, title="Members List Thread")
        article = make_article(db_session, source_id=src.id, dedup_key="ml-art-1")
        make_thread_membership(db_session, thread_id=thread.id, article_id=article.id)

        data = client.get(f"/threads/{thread.id}/members").json()
        assert len(data["items"]) == 1
        assert set(data["items"][0].keys()) == _THREAD_MEMBER_FIELDS

    def test_get_members_unknown_thread_returns_404(self, client, db_session):
        response = client.get("/threads/999999/members")
        assert response.status_code == 404


class TestDismissThread:
    def test_dismiss_returns_200(self, client, db_session):
        thread = make_thread(db_session, title="Dismiss Me")
        response = client.post(f"/threads/{thread.id}/dismiss")
        assert response.status_code == 200

    def test_dismiss_sets_dismissed_true(self, client, db_session):
        thread = make_thread(db_session, title="Dismiss Flag")
        data = client.post(f"/threads/{thread.id}/dismiss").json()
        assert data["dismissed"] is True

    def test_dismiss_persists_to_db(self, client, db_session):
        thread = make_thread(db_session, title="Dismiss Persist")
        client.post(f"/threads/{thread.id}/dismiss")
        db_session.expire(thread)
        db_session.refresh(thread)
        assert thread.dismissed is True

    def test_dismiss_unknown_thread_returns_404(self, client, db_session):
        response = client.post("/threads/999999/dismiss")
        assert response.status_code == 404

    def test_dismiss_returns_thread_shape(self, client, db_session):
        thread = make_thread(db_session)
        data = client.post(f"/threads/{thread.id}/dismiss").json()
        assert set(data.keys()) == _THREAD_FIELDS


class TestRestoreThread:
    def test_restore_returns_200(self, client, db_session):
        thread = make_thread(db_session, title="Restore Me", dismissed=True)
        response = client.post(f"/threads/{thread.id}/restore")
        assert response.status_code == 200

    def test_restore_sets_dismissed_false(self, client, db_session):
        thread = make_thread(db_session, title="Restore Flag", dismissed=True)
        data = client.post(f"/threads/{thread.id}/restore").json()
        assert data["dismissed"] is False

    def test_restore_persists_to_db(self, client, db_session):
        thread = make_thread(db_session, title="Restore Persist", dismissed=True)
        client.post(f"/threads/{thread.id}/restore")
        db_session.expire(thread)
        db_session.refresh(thread)
        assert thread.dismissed is False

    def test_restore_unknown_thread_returns_404(self, client, db_session):
        response = client.post("/threads/999999/restore")
        assert response.status_code == 404

    def test_restore_returns_thread_shape(self, client, db_session):
        thread = make_thread(db_session, dismissed=True)
        data = client.post(f"/threads/{thread.id}/restore").json()
        assert set(data.keys()) == _THREAD_FIELDS
