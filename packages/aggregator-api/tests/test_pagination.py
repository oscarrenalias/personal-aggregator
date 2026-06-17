"""Pagination cursor tests for /articles and /threads endpoints.

Seeds more rows than the page limit, paginates via next_cursor, and
asserts no gaps or duplicates across the boundary.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import make_article, make_source, make_thread


class TestArticlePaginationCursor:
    def test_first_page_has_next_cursor_when_more_results(self, client, db_session):
        src = make_source(db_session)
        for i in range(5):
            make_article(
                db_session,
                source_id=src.id,
                dedup_key=f"page-art-{i}",
                feed_published_at=datetime(2025, 1, i + 1, tzinfo=timezone.utc),
            )
        response = client.get("/articles?view=all&limit=3")
        data = response.json()
        assert response.status_code == 200
        assert len(data["items"]) == 3
        assert data["next_cursor"] is not None

    def test_second_page_via_cursor_has_remaining_items(self, client, db_session):
        src = make_source(db_session)
        for i in range(5):
            make_article(
                db_session,
                source_id=src.id,
                dedup_key=f"cursor-art-{i}",
                feed_published_at=datetime(2025, 1, i + 1, tzinfo=timezone.utc),
            )
        page1 = client.get("/articles?view=all&limit=3").json()
        cursor = page1["next_cursor"]
        assert cursor is not None

        page2 = client.get(f"/articles?view=all&limit=3&cursor={cursor}").json()
        assert len(page2["items"]) == 2

    def test_no_gaps_or_duplicates_across_pages(self, client, db_session):
        src = make_source(db_session)
        for i in range(5):
            make_article(
                db_session,
                source_id=src.id,
                dedup_key=f"nodup-art-{i}",
                feed_published_at=datetime(2025, 1, i + 1, tzinfo=timezone.utc),
            )

        page1 = client.get("/articles?view=all&limit=3").json()
        page1_ids = [item["id"] for item in page1["items"]]
        assert len(page1_ids) == 3

        cursor = page1["next_cursor"]
        page2 = client.get(f"/articles?view=all&limit=3&cursor={cursor}").json()
        page2_ids = [item["id"] for item in page2["items"]]
        assert len(page2_ids) == 2

        all_ids = page1_ids + page2_ids
        assert len(all_ids) == len(set(all_ids)), "Duplicate article ids across pages"

        expected_total = 5
        assert len(all_ids) == expected_total, "Gaps detected: total ids != seeded count"

    def test_last_page_has_no_next_cursor(self, client, db_session):
        src = make_source(db_session)
        for i in range(4):
            make_article(
                db_session,
                source_id=src.id,
                dedup_key=f"last-page-art-{i}",
                feed_published_at=datetime(2025, 1, i + 1, tzinfo=timezone.utc),
            )
        page1 = client.get("/articles?view=all&limit=3").json()
        cursor = page1["next_cursor"]
        page2 = client.get(f"/articles?view=all&limit=3&cursor={cursor}").json()
        assert page2["next_cursor"] is None

    def test_no_next_cursor_when_results_less_than_limit(self, client, db_session):
        src = make_source(db_session)
        make_article(db_session, source_id=src.id, dedup_key="single-art")
        data = client.get("/articles?view=all&limit=10").json()
        assert data["next_cursor"] is None


class TestThreadPaginationCursor:
    def test_thread_first_page_has_next_cursor(self, client, db_session):
        now = datetime.now(tz=timezone.utc)
        for i in range(5):
            make_thread(
                db_session,
                title=f"Thread Page {i}",
                top_grade=50 + i,
                last_updated=now - timedelta(hours=i),
            )
        data = client.get("/threads?limit=3").json()
        assert data["next_cursor"] is not None
        assert len(data["items"]) == 3

    def test_thread_no_gaps_or_duplicates_across_pages(self, client, db_session):
        now = datetime.now(tz=timezone.utc)
        for i in range(5):
            make_thread(
                db_session,
                title=f"Thread Cursor {i}",
                top_grade=50 + i,
                last_updated=now - timedelta(hours=i),
            )
        page1 = client.get("/threads?limit=3").json()
        page1_ids = [t["id"] for t in page1["items"]]
        assert len(page1_ids) == 3

        cursor = page1["next_cursor"]
        page2 = client.get(f"/threads?limit=3&cursor={cursor}").json()
        page2_ids = [t["id"] for t in page2["items"]]
        assert len(page2_ids) == 2

        all_ids = page1_ids + page2_ids
        assert len(all_ids) == len(set(all_ids)), "Duplicate thread ids across pages"
        assert len(all_ids) == 5, "Gaps detected: total ids != seeded count"

    def test_thread_last_page_no_next_cursor(self, client, db_session):
        now = datetime.now(tz=timezone.utc)
        for i in range(4):
            make_thread(
                db_session,
                title=f"Thread Last {i}",
                last_updated=now - timedelta(hours=i),
            )
        page1 = client.get("/threads?limit=3").json()
        cursor = page1["next_cursor"]
        page2 = client.get(f"/threads?limit=3&cursor={cursor}").json()
        assert page2["next_cursor"] is None
