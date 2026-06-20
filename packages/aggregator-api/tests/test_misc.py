"""Integration tests for miscellaneous endpoints: brief, sources, categories, interest-profile, healthz, CORS."""

from __future__ import annotations

from datetime import datetime

from conftest import make_article, make_brief, make_brief_topic, make_category, make_source


class TestBriefToday:
    def test_brief_today_absent_returns_404(self, client, db_session):
        response = client.get("/brief/today")
        assert response.status_code == 404

    def test_brief_today_present_returns_200(self, client, db_session):
        brief = make_brief(db_session, status="ready", headline="Today's Brief")
        make_brief_topic(db_session, brief_id=brief.id, headline="Topic One")
        response = client.get("/brief/today")
        assert response.status_code == 200

    def test_brief_today_response_shape(self, client, db_session):
        from test_contract import _BRIEF_FIELDS

        brief = make_brief(db_session, status="ready")
        make_brief_topic(db_session, brief_id=brief.id)
        data = client.get("/brief/today").json()
        assert set(data.keys()) == _BRIEF_FIELDS

    def test_brief_today_topic_uses_refs_not_topic_refs(self, client, db_session):
        """The JSON field must be 'refs', not the ORM column name 'topic_refs'."""
        from test_contract import _BRIEF_TOPIC_FIELDS

        brief = make_brief(db_session, status="ready")
        make_brief_topic(db_session, brief_id=brief.id, refs=["https://example.com"])
        data = client.get("/brief/today").json()
        topic = data["topics"][0]
        assert set(topic.keys()) == _BRIEF_TOPIC_FIELDS
        assert "refs" in topic
        assert "topic_refs" not in topic

    def test_brief_today_non_ready_brief_not_returned(self, client, db_session):
        make_brief(db_session, status="pending")
        response = client.get("/brief/today")
        assert response.status_code == 404

    def test_brief_today_headline_matches(self, client, db_session):
        brief = make_brief(db_session, status="ready", headline="Special Headline")
        make_brief_topic(db_session, brief_id=brief.id)
        data = client.get("/brief/today").json()
        assert data["headline"] == "Special Headline"


class TestSources:
    def test_sources_returns_200(self, client, db_session):
        response = client.get("/sources")
        assert response.status_code == 200

    def test_sources_returns_list(self, client, db_session):
        data = client.get("/sources").json()
        assert isinstance(data, list)

    def test_sources_returns_enabled_source(self, client, db_session):
        make_source(db_session, name="Active Feed", enabled=True)
        data = client.get("/sources").json()
        names = [s["name"] for s in data]
        assert "Active Feed" in names

    def test_sources_excludes_disabled_source(self, client, db_session):
        make_source(db_session, name="Disabled Feed", enabled=False)
        data = client.get("/sources").json()
        names = [s["name"] for s in data]
        assert "Disabled Feed" not in names

    def test_sources_item_shape(self, client, db_session):
        from test_contract import _SOURCE_FIELDS

        make_source(db_session)
        data = client.get("/sources").json()
        assert len(data) >= 1
        assert set(data[0].keys()) == _SOURCE_FIELDS

    def test_sources_has_new_false_when_no_unread(self, client, db_session):
        src = make_source(db_session, name="Read Source")
        make_article(db_session, source_id=src.id, dedup_key="src-read-1", is_read=True, importance_score=50)
        data = client.get("/sources").json()
        src_data = next(s for s in data if s["name"] == "Read Source")
        assert src_data["has_new"] is False
        assert src_data["has_priority"] is False

    def test_sources_has_new_true_when_unread_ready(self, client, db_session):
        src = make_source(db_session, name="Unread Source")
        make_article(db_session, source_id=src.id, dedup_key="src-unread-1", is_read=False, importance_score=40)
        data = client.get("/sources").json()
        src_data = next(s for s in data if s["name"] == "Unread Source")
        assert src_data["has_new"] is True
        assert src_data["has_priority"] is False

    def test_sources_has_priority_true_when_unread_above_threshold(self, client, db_session):
        src = make_source(db_session, name="Priority Source")
        make_article(db_session, source_id=src.id, dedup_key="src-pri-1", is_read=False, importance_score=80)
        data = client.get("/sources").json()
        src_data = next(s for s in data if s["name"] == "Priority Source")
        assert src_data["has_new"] is True
        assert src_data["has_priority"] is True

    def test_sources_hidden_article_excluded_from_flags(self, client, db_session):
        src = make_source(db_session, name="Hidden Only Source")
        art = make_article(db_session, source_id=src.id, dedup_key="src-hid-1", is_read=False, importance_score=90)
        # Mark the article hidden directly
        art.is_hidden = True
        db_session.commit()
        data = client.get("/sources").json()
        src_data = next(s for s in data if s["name"] == "Hidden Only Source")
        assert src_data["has_new"] is False
        assert src_data["has_priority"] is False


class TestCategories:
    def test_categories_returns_200(self, client, db_session):
        response = client.get("/categories")
        assert response.status_code == 200

    def test_categories_returns_list(self, client, db_session):
        data = client.get("/categories").json()
        assert isinstance(data, list)

    def test_categories_returns_enabled_category(self, client, db_session):
        make_category(db_session, name="Science", enabled=True)
        data = client.get("/categories").json()
        names = [c["name"] for c in data]
        assert "Science" in names

    def test_categories_excludes_disabled_category(self, client, db_session):
        make_category(db_session, name="Hidden Cat", enabled=False)
        data = client.get("/categories").json()
        names = [c["name"] for c in data]
        assert "Hidden Cat" not in names

    def test_categories_item_shape(self, client, db_session):
        from test_contract import _CATEGORY_FIELDS

        make_category(db_session, name="Shape Test Cat")
        data = client.get("/categories").json()
        assert len(data) >= 1
        assert set(data[0].keys()) == _CATEGORY_FIELDS

    def test_categories_last_activity_null_when_no_articles(self, client, db_session):
        make_category(db_session, name="Empty Cat")
        data = client.get("/categories").json()
        cat_data = next(c for c in data if c["name"] == "Empty Cat")
        assert cat_data["last_activity"] is None
        assert cat_data["has_priority"] is False

    def test_categories_last_activity_iso_when_articles_present(self, client, db_session):
        src = make_source(db_session, name="Cat Test Source")
        make_category(db_session, name="Activity Cat")
        make_article(db_session, source_id=src.id, dedup_key="cat-act-1", categories=["Activity Cat"], is_read=True, importance_score=30)
        data = client.get("/categories").json()
        cat_data = next(c for c in data if c["name"] == "Activity Cat")
        assert cat_data["last_activity"] is not None
        # Verify it's a valid ISO string
        datetime.fromisoformat(cat_data["last_activity"])
        assert cat_data["has_priority"] is False

    def test_categories_has_priority_true_when_unread_important(self, client, db_session):
        src = make_source(db_session, name="Cat Priority Source")
        make_category(db_session, name="Priority Cat")
        make_article(db_session, source_id=src.id, dedup_key="cat-pri-1", categories=["Priority Cat"], is_read=False, importance_score=80)
        data = client.get("/categories").json()
        cat_data = next(c for c in data if c["name"] == "Priority Cat")
        assert cat_data["has_priority"] is True
        assert cat_data["last_activity"] is not None

    def test_categories_has_priority_false_when_below_threshold(self, client, db_session):
        src = make_source(db_session, name="Cat Low Source")
        make_category(db_session, name="LowScore Cat")
        make_article(db_session, source_id=src.id, dedup_key="cat-low-1", categories=["LowScore Cat"], is_read=False, importance_score=50)
        data = client.get("/categories").json()
        cat_data = next(c for c in data if c["name"] == "LowScore Cat")
        assert cat_data["has_priority"] is False
        assert cat_data["last_activity"] is not None


class TestInterestProfile:
    def test_interest_profile_returns_200(self, client, db_session):
        response = client.get("/interest-profile")
        assert response.status_code == 200

    def test_interest_profile_returns_empty_text_when_none(self, client, db_session):
        data = client.get("/interest-profile").json()
        assert data["profile_text"] == ""

    def test_interest_profile_returns_stored_text(self, client, db_session):
        from sqlalchemy import text

        db_session.execute(
            text(
                "INSERT INTO interest_profile (id, profile_text) VALUES (true, :txt)"
                " ON CONFLICT (id) DO UPDATE SET profile_text = EXCLUDED.profile_text"
            ),
            {"txt": "Interested in AI and software engineering"},
        )
        db_session.commit()
        data = client.get("/interest-profile").json()
        assert data["profile_text"] == "Interested in AI and software engineering"

    def test_interest_profile_shape(self, client, db_session):
        from test_contract import _INTEREST_PROFILE_FIELDS

        data = client.get("/interest-profile").json()
        assert set(data.keys()) == _INTEREST_PROFILE_FIELDS


class TestHealthz:
    def test_healthz_returns_200(self, client, db_session):
        response = client.get("/healthz")
        assert response.status_code == 200

    def test_healthz_returns_db_ok(self, client, db_session):
        data = client.get("/healthz").json()
        assert data["db"] == "ok"

    def test_healthz_returns_version_key(self, client, db_session):
        data = client.get("/healthz").json()
        assert "version" in data


class TestCORS:
    def test_cors_preflight_returns_allow_origin_header(self, client, db_session):
        response = client.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code in (200, 204)
        lower_headers = {k.lower(): v for k, v in response.headers.items()}
        assert "access-control-allow-origin" in lower_headers

    def test_cors_get_request_includes_allow_origin(self, client, db_session):
        response = client.get("/healthz", headers={"Origin": "http://example.com"})
        lower_headers = {k.lower(): v for k, v in response.headers.items()}
        assert "access-control-allow-origin" in lower_headers

    def test_cors_wildcard_by_default(self, client, db_session):
        response = client.get("/healthz", headers={"Origin": "http://example.com"})
        lower_headers = {k.lower(): v for k, v in response.headers.items()}
        assert lower_headers.get("access-control-allow-origin") == "*"

    def test_api_cors_allow_origins_setting_restricts_origins(self):
        """ApiSettings parses the comma-separated env var and restricts origins when set."""
        import os

        os.environ["API_CORS_ALLOW_ORIGINS"] = "http://localhost:3000,http://app.example.com"
        try:
            from aggregator_api.settings import ApiSettings

            settings = ApiSettings()
            origins = [o.strip() for o in settings.api_cors_allow_origins.split(",")]
            assert "http://localhost:3000" in origins
            assert "http://app.example.com" in origins
            assert "*" not in origins
        finally:
            del os.environ["API_CORS_ALLOW_ORIGINS"]
