"""Integration tests for /articles endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from conftest import make_article, make_source


class TestListArticles:
    def test_list_all_view_returns_200(self, client, db_session):
        response = client.get("/articles?view=all")
        assert response.status_code == 200

    def test_list_all_view_returns_paginated_shape(self, client, db_session):
        response = client.get("/articles?view=all")
        data = response.json()
        assert "items" in data
        assert "next_cursor" in data

    def test_list_all_view_returns_article(self, client, db_session):
        src = make_source(db_session)
        make_article(db_session, source_id=src.id, feed_title="My Article")
        response = client.get("/articles?view=all")
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["id"] is not None

    def test_list_unread_view_returns_unread_articles(self, client, db_session):
        src = make_source(db_session)
        make_article(db_session, source_id=src.id, dedup_key="unread-1", is_read=False)
        make_article(db_session, source_id=src.id, dedup_key="read-1", is_read=True)
        response = client.get("/articles?view=unread")
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["is_read"] is False

    def test_list_important_view_filters_by_score(self, client, db_session):
        src = make_source(db_session)
        make_article(db_session, source_id=src.id, dedup_key="imp-1", importance_score=80)
        make_article(db_session, source_id=src.id, dedup_key="low-1", importance_score=50)
        response = client.get("/articles?view=important")
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["importance_score"] == 80

    def test_list_saved_view_returns_saved_articles(self, client, db_session):
        src = make_source(db_session)
        make_article(db_session, source_id=src.id, dedup_key="saved-1", is_saved=True)
        make_article(db_session, source_id=src.id, dedup_key="unsaved-1", is_saved=False)
        response = client.get("/articles?view=saved")
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["is_saved"] is True

    def test_list_today_view_filters_by_today(self, client, db_session):
        src = make_source(db_session)
        now = datetime.now(tz=timezone.utc)
        make_article(db_session, source_id=src.id, dedup_key="today-1", feed_published_at=now)
        make_article(
            db_session,
            source_id=src.id,
            dedup_key="old-1",
            feed_published_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        response = client.get("/articles?view=today")
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1

    def test_list_uncategorized_view_includes_articles_without_categories(self, client, db_session):
        src = make_source(db_session)
        make_article(db_session, source_id=src.id, dedup_key="no-cat", categories=None)
        response = client.get("/articles?view=uncategorized")
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1

    def test_list_invalid_view_returns_400(self, client, db_session):
        response = client.get("/articles?view=nonexistent")
        assert response.status_code == 400

    def test_list_invalid_source_id_returns_422(self, client, db_session):
        response = client.get("/articles?source_id=notanint")
        assert response.status_code == 422

    def test_list_invalid_limit_returns_422(self, client, db_session):
        response = client.get("/articles?limit=notanint")
        assert response.status_code == 422


class TestListArticlesSort:
    def test_sort_recent_returns_most_recent_first(self, client, db_session):
        src = make_source(db_session)
        from datetime import timedelta
        now = datetime.now(tz=timezone.utc)
        old = make_article(db_session, source_id=src.id, dedup_key="sort-old",
                           feed_published_at=now - timedelta(days=3))
        new = make_article(db_session, source_id=src.id, dedup_key="sort-new",
                           feed_published_at=now)
        response = client.get("/articles?sort=recent")
        assert response.status_code == 200
        ids = [item["id"] for item in response.json()["items"]]
        assert ids.index(new.id) < ids.index(old.id)

    def test_sort_importance_is_default(self, client, db_session):
        src = make_source(db_session)
        from datetime import timedelta
        now = datetime.now(tz=timezone.utc)
        low = make_article(db_session, source_id=src.id, dedup_key="simp-low",
                           importance_score=10, feed_published_at=now)
        high = make_article(db_session, source_id=src.id, dedup_key="simp-high",
                            importance_score=90, feed_published_at=now - timedelta(days=1))
        # Omit sort param — should default to importance ordering
        response = client.get("/articles")
        assert response.status_code == 200
        ids = [item["id"] for item in response.json()["items"]]
        assert ids.index(high.id) < ids.index(low.id)

    def test_invalid_sort_returns_400(self, client, db_session):
        response = client.get("/articles?sort=bogus")
        assert response.status_code == 400
        assert "Invalid sort" in response.json()["detail"]

    def test_sort_recent_cursor_round_trip(self, client, db_session):
        src = make_source(db_session)
        from datetime import timedelta
        now = datetime.now(tz=timezone.utc)
        seeded_ids = set()
        for i in range(5):
            a = make_article(db_session, source_id=src.id, dedup_key=f"rcursor-{i}",
                             feed_published_at=now - timedelta(hours=i))
            seeded_ids.add(a.id)

        all_ids = []
        cursor = None
        while True:
            url = "/articles?sort=recent&limit=3"
            if cursor:
                url += f"&cursor={cursor}"
            page = client.get(url).json()
            all_ids.extend(item["id"] for item in page["items"])
            cursor = page.get("next_cursor")
            if cursor is None:
                break

        assert len(all_ids) == len(set(all_ids)), "Duplicate ids across pages"
        assert seeded_ids.issubset(set(all_ids)), "Not all seeded articles returned"


class TestSearchArticles:
    def test_search_with_no_results_returns_empty(self, client, db_session):
        response = client.get("/articles/search?q=xyznonexistentterm9999")
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["next_cursor"] is None

    def test_search_returns_matching_article(self, client, db_session):
        src = make_source(db_session)
        make_article(
            db_session,
            source_id=src.id,
            dedup_key="py-art",
            feed_title="Python Programming Guide",
            search_text="python programming guide",
        )
        response = client.get("/articles/search?q=python")
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1

    def test_search_missing_q_param_returns_422(self, client, db_session):
        response = client.get("/articles/search")
        assert response.status_code == 422

    def test_search_invalid_source_id_returns_422(self, client, db_session):
        response = client.get("/articles/search?q=test&source_id=notanint")
        assert response.status_code == 422


class TestGetArticle:
    def test_get_existing_article_returns_200(self, client, db_session):
        src = make_source(db_session)
        article = make_article(db_session, source_id=src.id, feed_title="Detail Article")
        response = client.get(f"/articles/{article.id}")
        assert response.status_code == 200

    def test_get_article_returns_correct_fields(self, client, db_session):
        src = make_source(db_session, name="Detail Source")
        article = make_article(
            db_session,
            source_id=src.id,
            feed_title="Full Detail Article",
            importance_score=75,
        )
        response = client.get(f"/articles/{article.id}")
        data = response.json()
        assert data["id"] == article.id
        assert data["title"] == "Full Detail Article"
        assert data["importance_score"] == 75
        assert data["is_read"] is False
        assert data["is_saved"] is False

    def test_get_article_returns_image_url_when_set(self, client, db_session):
        src = make_source(db_session)
        article = make_article(
            db_session,
            source_id=src.id,
            header_image_url="https://img.example.com/hero.jpg",
        )
        data = client.get(f"/articles/{article.id}").json()
        assert data["image_url"] == "https://img.example.com/hero.jpg"

    def test_get_article_image_url_is_null_when_absent(self, client, db_session):
        src = make_source(db_session)
        article = make_article(db_session, source_id=src.id)  # no header image
        data = client.get(f"/articles/{article.id}").json()
        assert "image_url" in data
        assert data["image_url"] is None

    def test_mark_read_response_carries_image_url(self, client, db_session):
        # Mutation responses also serialize the article, so they must carry image_url too.
        src = make_source(db_session)
        article = make_article(
            db_session,
            source_id=src.id,
            header_image_url="https://img.example.com/hero.jpg",
        )
        data = client.post(f"/articles/{article.id}/read").json()
        assert data["image_url"] == "https://img.example.com/hero.jpg"

    def test_get_unknown_article_returns_404(self, client, db_session):
        response = client.get("/articles/999999")
        assert response.status_code == 404

    def test_get_article_response_has_all_expected_fields(self, client, db_session):
        from test_contract import _ARTICLE_FIELDS

        src = make_source(db_session)
        article = make_article(db_session, source_id=src.id)
        response = client.get(f"/articles/{article.id}")
        assert response.status_code == 200
        assert set(response.json().keys()) == _ARTICLE_FIELDS


class TestMarkArticleRead:
    def test_mark_read_returns_200(self, client, db_session):
        src = make_source(db_session)
        article = make_article(db_session, source_id=src.id, is_read=False)
        response = client.post(f"/articles/{article.id}/read")
        assert response.status_code == 200

    def test_mark_read_sets_is_read_true(self, client, db_session):
        src = make_source(db_session)
        article = make_article(db_session, source_id=src.id, is_read=False)
        response = client.post(f"/articles/{article.id}/read")
        assert response.json()["is_read"] is True

    def test_mark_read_unknown_article_returns_404(self, client, db_session):
        response = client.post("/articles/999999/read")
        assert response.status_code == 404


class TestMarkArticleUnread:
    def test_mark_unread_returns_200(self, client, db_session):
        src = make_source(db_session)
        article = make_article(db_session, source_id=src.id, is_read=True)
        response = client.post(f"/articles/{article.id}/unread")
        assert response.status_code == 200

    def test_mark_unread_sets_is_read_false(self, client, db_session):
        src = make_source(db_session)
        article = make_article(db_session, source_id=src.id, is_read=True)
        response = client.post(f"/articles/{article.id}/unread")
        assert response.json()["is_read"] is False

    def test_mark_unread_unknown_article_returns_404(self, client, db_session):
        response = client.post("/articles/999999/unread")
        assert response.status_code == 404


class TestSaveArticle:
    def test_save_article_returns_200(self, client, db_session):
        src = make_source(db_session)
        article = make_article(db_session, source_id=src.id, is_saved=False)
        response = client.post(f"/articles/{article.id}/save")
        assert response.status_code == 200

    def test_save_article_sets_is_saved_true(self, client, db_session):
        src = make_source(db_session)
        article = make_article(db_session, source_id=src.id, is_saved=False)
        response = client.post(f"/articles/{article.id}/save")
        assert response.json()["is_saved"] is True

    def test_save_unknown_article_returns_404(self, client, db_session):
        response = client.post("/articles/999999/save")
        assert response.status_code == 404


class TestUnsaveArticle:
    def test_unsave_article_returns_200(self, client, db_session):
        src = make_source(db_session)
        article = make_article(db_session, source_id=src.id, is_saved=True)
        response = client.post(f"/articles/{article.id}/unsave")
        assert response.status_code == 200

    def test_unsave_article_sets_is_saved_false(self, client, db_session):
        src = make_source(db_session)
        article = make_article(db_session, source_id=src.id, is_saved=True)
        response = client.post(f"/articles/{article.id}/unsave")
        assert response.json()["is_saved"] is False

    def test_unsave_unknown_article_returns_404(self, client, db_session):
        response = client.post("/articles/999999/unsave")
        assert response.status_code == 404
