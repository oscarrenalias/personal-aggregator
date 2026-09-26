"""Regression tests for podcast API routes.

Covers:
- GET /podcasts/{id}          — get by integer id; 404 on unknown
- GET /podcasts/by-date/{date} — get by date; 400 on bad format; 404 when absent
- GET /podcasts/latest         — still resolves; not shadowed by /{episode_id}
- HEAD /podcasts/{id}/audio    — returns 200 with content-length, no body
- Ranged GET /podcasts/{id}/audio — returns 206 (not tested here; needs real file on disk)
"""

from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from aggregator_common.models import PodcastEpisode


def make_episode(
    session: Session,
    *,
    episode_date: date | None = None,
    status: str = "ready",
    audio_path: str | None = None,
    origin: str = "auto",
    artwork_url: str | None = None,
    script_json: dict | None = None,
) -> PodcastEpisode:
    now = datetime.now(tz=timezone.utc)
    ep = PodcastEpisode(
        date=episode_date or date(2025, 1, 1),
        status=status,
        origin=origin,
        script_json=script_json if script_json is not None else {"segments": [{"text": "Hello world"}]},
        audio_path=audio_path,
        generated_at=now,
        artwork_url=artwork_url,
    )
    session.add(ep)
    session.flush()
    session.commit()
    session.refresh(ep)
    return ep


class TestGetEpisodeById:
    def test_returns_episode_for_valid_id(self, client, db_session):
        ep = make_episode(db_session, episode_date=date(2025, 3, 1))
        resp = client.get(f"/podcasts/{ep.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == ep.id
        assert data["date"] == "2025-03-01"

    def test_returns_404_for_unknown_id(self, client):
        resp = client.get("/podcasts/999999")
        assert resp.status_code == 404

    def test_audio_url_uses_id(self, client, db_session):
        ep = make_episode(db_session, episode_date=date(2025, 3, 2))
        data = client.get(f"/podcasts/{ep.id}").json()
        assert data["audio_url"] == f"/api/v1/podcasts/{ep.id}/audio"

    def test_numeric_id_no_longer_returns_400(self, client, db_session):
        """Regression: before the fix a numeric segment caused 400 date-parse error."""
        ep = make_episode(db_session, episode_date=date(2025, 3, 3))
        resp = client.get(f"/podcasts/{ep.id}")
        assert resp.status_code != 400


class TestGetEpisodeByDate:
    def test_returns_episode_for_valid_date(self, client, db_session):
        ep = make_episode(db_session, episode_date=date(2025, 4, 10))
        resp = client.get("/podcasts/by-date/2025-04-10")
        assert resp.status_code == 200
        assert resp.json()["id"] == ep.id

    def test_returns_400_for_invalid_date_format(self, client):
        resp = client.get("/podcasts/by-date/not-a-date")
        assert resp.status_code == 400
        assert "YYYY-MM-DD" in resp.json()["detail"]

    def test_returns_404_when_no_episode_for_date(self, client):
        resp = client.get("/podcasts/by-date/2000-01-01")
        assert resp.status_code == 404

    def test_old_date_path_no_longer_exists(self, client):
        """Regression: the old /{date} route captured any path segment; now /{episode_id:int}
        rejects a date string with 422 instead of the old 400 date-parse error."""
        resp = client.get("/podcasts/2025-05-01")
        assert resp.status_code == 422


class TestGetLatestEpisode:
    def test_latest_resolves_and_is_not_shadowed_by_id_route(self, client, db_session):
        """'/latest' must be matched as a literal segment, not coerced to int."""
        ep = make_episode(db_session, episode_date=date(2025, 6, 1))
        resp = client.get("/podcasts/latest")
        assert resp.status_code == 200
        assert resp.json()["id"] == ep.id

    def test_latest_returns_404_when_no_ready_episode(self, client, db_session):
        make_episode(db_session, episode_date=date(2025, 6, 2), status="pending")
        resp = client.get("/podcasts/latest")
        assert resp.status_code == 404

    def test_latest_returns_most_recent_ready_episode(self, client, db_session):
        make_episode(db_session, episode_date=date(2025, 7, 1))
        newer = make_episode(db_session, episode_date=date(2025, 7, 2))
        data = client.get("/podcasts/latest").json()
        assert data["id"] == newer.id


class TestArtworkUrl:
    """artwork_url field appears in all four podcast read endpoints."""

    def test_artwork_url_is_null_when_not_set_in_list(self, client, db_session):
        """GET /podcasts list returns artwork_url=null when episode has no artwork."""
        make_episode(db_session, episode_date=date(2025, 9, 1))
        resp = client.get("/podcasts")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert "artwork_url" in items[0]
        assert items[0]["artwork_url"] is None

    def test_artwork_url_present_in_list(self, client, db_session):
        """GET /podcasts list returns artwork_url as a string when set."""
        make_episode(
            db_session,
            episode_date=date(2025, 9, 2),
            artwork_url="https://cdn.example.com/cover.jpg",
        )
        resp = client.get("/podcasts")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert items[0]["artwork_url"] == "https://cdn.example.com/cover.jpg"

    def test_artwork_url_null_in_get_by_id(self, client, db_session):
        """GET /podcasts/{id} returns artwork_url=null when episode has no artwork."""
        ep = make_episode(db_session, episode_date=date(2025, 9, 3))
        resp = client.get(f"/podcasts/{ep.id}")
        assert resp.status_code == 200
        assert resp.json()["artwork_url"] is None

    def test_artwork_url_string_in_get_by_id(self, client, db_session):
        """GET /podcasts/{id} returns artwork_url as string when set."""
        ep = make_episode(
            db_session,
            episode_date=date(2025, 9, 4),
            artwork_url="https://cdn.example.com/art.jpg",
        )
        resp = client.get(f"/podcasts/{ep.id}")
        assert resp.status_code == 200
        assert resp.json()["artwork_url"] == "https://cdn.example.com/art.jpg"

    def test_artwork_url_in_latest(self, client, db_session):
        """GET /podcasts/latest includes artwork_url field."""
        make_episode(
            db_session,
            episode_date=date(2025, 9, 5),
            artwork_url="https://cdn.example.com/latest.jpg",
        )
        resp = client.get("/podcasts/latest")
        assert resp.status_code == 200
        assert resp.json()["artwork_url"] == "https://cdn.example.com/latest.jpg"

    def test_artwork_url_null_in_latest(self, client, db_session):
        """GET /podcasts/latest returns artwork_url=null when not set."""
        make_episode(db_session, episode_date=date(2025, 9, 6))
        resp = client.get("/podcasts/latest")
        assert resp.status_code == 200
        assert resp.json()["artwork_url"] is None

    def test_artwork_url_in_by_date(self, client, db_session):
        """GET /podcasts/by-date/{date} includes artwork_url field."""
        make_episode(
            db_session,
            episode_date=date(2025, 9, 7),
            artwork_url="https://cdn.example.com/bydate.jpg",
        )
        resp = client.get("/podcasts/by-date/2025-09-07")
        assert resp.status_code == 200
        assert resp.json()["artwork_url"] == "https://cdn.example.com/bydate.jpg"

    def test_artwork_url_null_in_by_date(self, client, db_session):
        """GET /podcasts/by-date/{date} returns artwork_url=null when not set."""
        make_episode(db_session, episode_date=date(2025, 9, 8))
        resp = client.get("/podcasts/by-date/2025-09-08")
        assert resp.status_code == 200
        assert resp.json()["artwork_url"] is None

    def test_legacy_script_json_without_artwork_url_does_not_raise(self, client, db_session):
        """Episodes with script_json that has no 'artwork_url' key are served without error."""
        ep = make_episode(
            db_session,
            episode_date=date(2025, 9, 9),
            # script_json without artwork_url key (simulates pre-feature episodes)
            script_json={"segments": [{"text": "Legacy episode content"}]},
        )
        # Episode has no artwork_url column value either
        assert ep.artwork_url is None

        # All four read endpoints must return 200 without error
        assert client.get(f"/podcasts/{ep.id}").status_code == 200
        assert client.get("/podcasts/latest").status_code == 200
        assert client.get("/podcasts/by-date/2025-09-09").status_code == 200
        list_resp = client.get("/podcasts")
        assert list_resp.status_code == 200

        # artwork_url must be null, not missing from the response
        data = client.get(f"/podcasts/{ep.id}").json()
        assert "artwork_url" in data
        assert data["artwork_url"] is None


class TestHeadAudioEndpoint:
    def test_head_audio_returns_200_with_content_length(self, client, db_session):
        """HEAD must return 200 + content-length header (no body)."""
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(b"\xff\xfb" * 512)  # fake MP3 bytes
            path = f.name
        try:
            ep = make_episode(db_session, episode_date=date(2025, 8, 1), audio_path=path)
            resp = client.head(f"/podcasts/{ep.id}/audio")
            assert resp.status_code == 200
            assert "content-length" in {k.lower() for k in resp.headers}
            assert resp.content == b""
        finally:
            os.unlink(path)

    def test_head_audio_returns_404_when_no_file(self, client, db_session):
        ep = make_episode(db_session, episode_date=date(2025, 8, 2), audio_path=None)
        resp = client.head(f"/podcasts/{ep.id}/audio")
        assert resp.status_code == 404
