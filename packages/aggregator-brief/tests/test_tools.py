"""Tests for aggregator_brief.tools — search_articles and dispatch_tool."""

from datetime import timedelta

import pytest

from aggregator_brief.tools import dispatch_tool, search_articles
from aggregator_common.state import ArticleStatus

from conftest import _NOW, make_article, make_source


class TestSearchArticles:
    def test_returns_only_ready_articles(self, db_session):
        src = make_source(db_session)
        ready = make_article(
            db_session,
            source_id=src.id,
            dedup_key="ready-1",
            status=ArticleStatus.ready,
            search_text="python programming language",
        )
        make_article(
            db_session,
            source_id=src.id,
            dedup_key="pending-1",
            status=ArticleStatus.pending_processing,
            search_text="python programming language",
        )

        results = search_articles(db_session, "python")
        result_ids = [r["id"] for r in results]

        assert ready.id in result_ids
        assert all(
            r["id"] != a_id
            for r in results
            for a_id in [r["id"]]
            if r["id"] != ready.id
        )

    def test_ready_article_not_returned_when_no_search_vector(self, db_session):
        src = make_source(db_session)
        # Article inserted without search_text, so search_vector is NULL — won't match.
        make_article(db_session, source_id=src.id, dedup_key="no-vec", status=ArticleStatus.ready)

        results = search_articles(db_session, "python")
        assert results == []

    def test_fulltext_match_returns_article(self, db_session):
        src = make_source(db_session)
        art = make_article(
            db_session,
            source_id=src.id,
            dedup_key="fts-1",
            search_text="climate change renewable energy",
        )

        results = search_articles(db_session, "climate")
        assert any(r["id"] == art.id for r in results)

    def test_since_filter_excludes_old_articles(self, db_session):
        src = make_source(db_session)
        old = make_article(
            db_session,
            source_id=src.id,
            dedup_key="old-1",
            published_at=_NOW - timedelta(days=7),
            search_text="economy policy update",
        )
        recent = make_article(
            db_session,
            source_id=src.id,
            dedup_key="recent-1",
            published_at=_NOW,
            search_text="economy policy update",
        )

        since = (_NOW - timedelta(days=1)).isoformat()
        results = search_articles(db_session, "economy", since=since)
        result_ids = [r["id"] for r in results]

        assert recent.id in result_ids
        assert old.id not in result_ids

    def test_until_filter_excludes_future_articles(self, db_session):
        src = make_source(db_session)
        past = make_article(
            db_session,
            source_id=src.id,
            dedup_key="past-1",
            published_at=_NOW - timedelta(days=1),
            search_text="technology innovation startup",
        )
        future = make_article(
            db_session,
            source_id=src.id,
            dedup_key="future-1",
            published_at=_NOW + timedelta(days=1),
            search_text="technology innovation startup",
        )

        until = _NOW.isoformat()
        results = search_articles(db_session, "technology", until=until)
        result_ids = [r["id"] for r in results]

        assert past.id in result_ids
        assert future.id not in result_ids

    def test_categories_filter(self, db_session):
        src = make_source(db_session)
        tech = make_article(
            db_session,
            source_id=src.id,
            dedup_key="tech-1",
            categories=["technology"],
            search_text="robotics computing innovation",
        )
        make_article(
            db_session,
            source_id=src.id,
            dedup_key="finance-1",
            categories=["finance"],
            search_text="banking credit interest rates",
        )

        # Verify tech article is found without categories filter first.
        all_results = search_articles(db_session, "robotics")
        all_ids = [r["id"] for r in all_results]
        assert tech.id in all_ids, f"tech article not found without categories filter; DB session state issue? got={all_ids}"

        # Now verify the categories filter correctly restricts to technology only.
        filtered = search_articles(db_session, "robotics", categories=["technology"])
        filtered_ids = [r["id"] for r in filtered]
        assert tech.id in filtered_ids

    def test_categories_filter_wrong_category_excluded(self, db_session):
        """Searching with a category that doesn't match returns nothing."""
        src = make_source(db_session)
        make_article(
            db_session,
            source_id=src.id,
            dedup_key="tech-2",
            categories=["technology"],
            search_text="robotics computing innovation",
        )

        results = search_articles(db_session, "robotics", categories=["finance"])
        assert results == []

    def test_limit_parameter_respected(self, db_session):
        src = make_source(db_session)
        for i in range(5):
            make_article(
                db_session,
                source_id=src.id,
                dedup_key=f"limit-{i}",
                search_text="machine learning artificial intelligence",
            )

        results = search_articles(db_session, "machine", limit=3)
        assert len(results) <= 3

    def test_result_dict_has_expected_keys(self, db_session):
        src = make_source(db_session)
        make_article(
            db_session,
            source_id=src.id,
            dedup_key="keys-1",
            feed_title="Key Test Article",
            raw_payload={"link": "https://example.com/article/1"},
            search_text="database storage system",
        )

        results = search_articles(db_session, "database")
        assert len(results) == 1
        r = results[0]
        assert "id" in r
        assert "title" in r
        assert "published_at" in r
        assert "url" in r
        assert "categories" in r


class TestDispatchTool:
    def test_search_articles_dispatched(self, db_session):
        result = dispatch_tool(db_session, "search_articles", {"query": "test query"})
        assert isinstance(result, list)

    def test_get_article_dispatched_missing_returns_error(self, db_session):
        result = dispatch_tool(db_session, "get_article", {"article_id": 999999})
        assert isinstance(result, dict)
        assert "error" in result

    def test_submit_brief_returns_passthrough(self, db_session):
        payload = {"headline": "h", "intro": "i", "topics": []}
        result = dispatch_tool(db_session, "submit_brief", payload)
        assert result == payload

    def test_unknown_tool_raises_value_error(self, db_session):
        with pytest.raises(ValueError, match="Unknown tool"):
            dispatch_tool(db_session, "nonexistent_tool", {})
