"""Integration tests: invoke each MCP tool callable against a testcontainers Postgres DB."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Generator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from aggregator_common.models import Article, Brief, BriefTopic, Source
from aggregator_common.state import ArticleStatus

_NOW = datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_get_session(session: Session, monkeypatch) -> None:
    """Redirect server's get_session to the test session so tools hit the test DB."""

    @contextmanager
    def _mock_get_session() -> Generator[Session, None, None]:
        yield session

    import aggregator_mcp.server as srv  # noqa: PLC0415

    monkeypatch.setattr(srv, "get_session", _mock_get_session)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source(session: Session, suffix: str = "") -> Source:
    src = Source(
        name=f"Tool Source{suffix}",
        feed_url=f"https://tooltest{suffix}.example.com/feed.xml",
    )
    session.add(src)
    session.flush()
    return src


def _make_ready_article(
    session: Session,
    source_id: int,
    dedup_key: str,
    *,
    title: str = "Tool Test Article",
    is_read: bool = False,
    is_saved: bool = False,
    categories: list | None = None,
    importance_score: int | None = None,
    feed_published_at: datetime | None = None,
) -> Article:
    article = Article(
        source_id=source_id,
        dedup_key=dedup_key,
        status=ArticleStatus.ready,
        clean_title=title,
        is_read=is_read,
        is_saved=is_saved,
        is_hidden=False,
        categories=categories,
        importance_score=importance_score,
        feed_published_at=feed_published_at or _NOW,
        raw_payload={"link": f"https://example.com/{dedup_key}"},
        retrieved_at=_NOW,
    )
    session.add(article)
    session.flush()
    return article


def _index(session: Session, article_id: int, content: str) -> None:
    session.execute(
        text(
            "UPDATE articles SET search_vector = to_tsvector('english', :txt)"
            " WHERE id = :id"
        ),
        {"txt": content, "id": article_id},
    )
    session.flush()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSearchArticlesTool:
    def test_returns_list_of_dicts(self, session: Session):
        import aggregator_mcp.server as srv

        src = _make_source(session, "-sa")
        art = _make_ready_article(session, src.id, "sa-1")
        _index(session, art.id, "artificial intelligence transformer models")

        results = srv.search_articles(query="artificial intelligence")

        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, dict)
            assert "id" in r and "title" in r

    def test_since_parameter_parsed_and_filters(self, session: Session):
        import aggregator_mcp.server as srv

        src = _make_source(session, "-sasince")
        recent = _make_ready_article(session, src.id, "sa-recent", feed_published_at=_NOW)
        old = _make_ready_article(
            session, src.id, "sa-old", feed_published_at=_NOW - timedelta(days=10)
        )
        for art in [recent, old]:
            _index(session, art.id, "deep learning neural network")

        since_str = (_NOW - timedelta(days=1)).isoformat()
        results = srv.search_articles(query="deep learning", since=since_str)
        ids = [r["id"] for r in results]

        assert recent.id in ids
        assert old.id not in ids

    def test_unknown_query_returns_empty(self, session: Session):
        import aggregator_mcp.server as srv

        results = srv.search_articles(query="zzznomatchxyzabc")
        assert results == []


class TestListArticlesTool:
    def test_all_view_returns_list_of_dicts(self, session: Session):
        import aggregator_mcp.server as srv

        src = _make_source(session, "-la")
        art = _make_ready_article(session, src.id, "la-1")

        results = srv.list_articles(view="all")

        assert isinstance(results, list)
        ids = [r["id"] for r in results]
        assert art.id in ids

    def test_unread_view_filters_correctly(self, session: Session):
        import aggregator_mcp.server as srv

        src = _make_source(session, "-launr")
        unread = _make_ready_article(session, src.id, "la-unread", is_read=False)
        read = _make_ready_article(session, src.id, "la-read", is_read=True)

        results = srv.list_articles(view="unread")
        ids = [r["id"] for r in results]

        assert unread.id in ids
        assert read.id not in ids


class TestGetArticleTool:
    def test_returns_dict_for_known_article(self, session: Session):
        import aggregator_mcp.server as srv

        src = _make_source(session, "-ga")
        art = _make_ready_article(session, src.id, "ga-1", title="Fetchable Article")

        result = srv.get_article(article_id=art.id)

        assert isinstance(result, dict)
        assert result["id"] == art.id
        assert result["title"] == "Fetchable Article"

    def test_raises_for_unknown_id(self, session: Session):
        import aggregator_mcp.server as srv

        with pytest.raises(ValueError, match="not found"):
            srv.get_article(article_id=999_999_901)


class TestGetInterestProfileTool:
    def test_returns_string(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.get_interest_profile()

        assert isinstance(result, str)


class TestListCategoriesTool:
    def test_returns_list(self, session: Session):
        import aggregator_mcp.server as srv

        results = srv.list_categories()

        assert isinstance(results, list)


class TestListSourcesTool:
    def test_returns_list_of_dicts(self, session: Session):
        import aggregator_mcp.server as srv

        src = _make_source(session, "-ls")

        results = srv.list_sources()

        assert isinstance(results, list)
        ids = [r["id"] for r in results if r.get("name", "").startswith("Tool Source-ls")]
        assert src.id in ids


class TestMarkReadTool:
    def test_sets_is_read_true(self, session: Session):
        import aggregator_mcp.server as srv

        src = _make_source(session, "-mr")
        art = _make_ready_article(session, src.id, "mr-1", is_read=False)

        result = srv.mark_read(article_id=art.id)

        assert isinstance(result, dict)
        assert result["is_read"] is True

    def test_raises_for_unknown_id(self, session: Session):
        import aggregator_mcp.server as srv

        with pytest.raises(ValueError, match="not found"):
            srv.mark_read(article_id=999_999_902)


class TestMarkUnreadTool:
    def test_sets_is_read_false(self, session: Session):
        import aggregator_mcp.server as srv

        src = _make_source(session, "-mu")
        art = _make_ready_article(session, src.id, "mu-1", is_read=True)

        result = srv.mark_unread(article_id=art.id)

        assert isinstance(result, dict)
        assert result["is_read"] is False

    def test_raises_for_unknown_id(self, session: Session):
        import aggregator_mcp.server as srv

        with pytest.raises(ValueError, match="not found"):
            srv.mark_unread(article_id=999_999_903)


class TestSaveArticleTool:
    def test_sets_is_saved_true(self, session: Session):
        import aggregator_mcp.server as srv

        src = _make_source(session, "-sv")
        art = _make_ready_article(session, src.id, "sv-1", is_saved=False)

        result = srv.save_article(article_id=art.id)

        assert isinstance(result, dict)
        assert result["is_saved"] is True

    def test_raises_for_unknown_id(self, session: Session):
        import aggregator_mcp.server as srv

        with pytest.raises(ValueError, match="not found"):
            srv.save_article(article_id=999_999_904)


class TestUnsaveArticleTool:
    def test_sets_is_saved_false(self, session: Session):
        import aggregator_mcp.server as srv

        src = _make_source(session, "-us")
        art = _make_ready_article(session, src.id, "us-1", is_saved=True)

        result = srv.unsave_article(article_id=art.id)

        assert isinstance(result, dict)
        assert result["is_saved"] is False

    def test_raises_for_unknown_id(self, session: Session):
        import aggregator_mcp.server as srv

        with pytest.raises(ValueError, match="not found"):
            srv.unsave_article(article_id=999_999_905)


def _make_brief(
    session: Session,
    *,
    status: str = "ready",
    headline: str = "Tool Test Brief",
    intro: str = "Tool intro.",
    model: str = "gpt-4.1",
) -> Brief:
    now = _NOW
    period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    brief = Brief(
        status=status,
        origin="manual",
        period_start=period_start,
        period_end=period_start.replace(hour=23, minute=59, second=59),
        headline=headline,
        intro=intro,
        model=model,
        generated_at=now,
    )
    session.add(brief)
    session.flush()
    return brief


def _make_brief_topic(
    session: Session,
    brief_id: int,
    position: int,
    *,
    headline: str = "Tool Topic",
    what_happened: str = "It happened.",
    why_it_matters: str = "It matters.",
) -> BriefTopic:
    topic = BriefTopic(
        brief_id=brief_id,
        position=position,
        headline=headline,
        what_happened=what_happened,
        why_it_matters=why_it_matters,
        topic_refs=[],
    )
    session.add(topic)
    session.flush()
    return topic


class TestGetDailyBriefTool:
    def test_returns_no_brief_when_none_ready(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.get_daily_brief()

        assert isinstance(result, dict)
        assert result == {"status": "no_brief"}

    def test_returns_brief_dict_with_topics(self, session: Session):
        import aggregator_mcp.server as srv

        brief = _make_brief(session, headline="Today's Headlines")
        _make_brief_topic(session, brief.id, 1, headline="Topic One")

        result = srv.get_daily_brief()

        assert isinstance(result, dict)
        assert result["headline"] == "Today's Headlines"
        assert result["id"] == brief.id
        assert isinstance(result["topics"], list)
        assert len(result["topics"]) == 1
        assert result["topics"][0]["headline"] == "Topic One"

    def test_returns_no_brief_for_pending_brief(self, session: Session):
        import aggregator_mcp.server as srv

        _make_brief(session, status="pending")

        result = srv.get_daily_brief()

        assert result == {"status": "no_brief"}


class TestRefreshBriefTool:
    def test_returns_queued_when_no_pending_brief(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.refresh_brief()

        assert isinstance(result, dict)
        assert result == {"status": "queued"}

    def test_returns_already_pending_when_brief_in_flight(self, session: Session):
        import aggregator_mcp.server as srv

        _make_brief(session, status="pending")

        result = srv.refresh_brief()

        assert result == {"status": "already_pending"}


# ---------------------------------------------------------------------------
# Profile management tools
# ---------------------------------------------------------------------------


class TestSetInterestProfileTool:
    def test_success_returns_profile_fields(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.set_interest_profile("Python, ML, cloud")

        assert isinstance(result, dict)
        assert result["profile_text"] == "Python, ML, cloud"
        assert "id" in result
        assert "updated_at" in result
        assert "error" not in result

    def test_upsert_updates_on_second_call(self, session: Session):
        import aggregator_mcp.server as srv

        srv.set_interest_profile("first")
        result = srv.set_interest_profile("second")

        assert result["profile_text"] == "second"


# ---------------------------------------------------------------------------
# Source management tools
# ---------------------------------------------------------------------------


class TestAddSourceTool:
    def test_success_returns_source_dict(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.add_source("New Feed", "https://mcp-addsrc.example.com/feed.xml")

        assert isinstance(result, dict)
        assert "id" in result
        assert result["name"] == "New Feed"
        assert "error" not in result

    def test_duplicate_feed_url_returns_conflict_dict(self, session: Session):
        import aggregator_mcp.server as srv

        src = _make_source(session, "-mcpdup")
        result = srv.add_source("Dup", src.feed_url)

        assert isinstance(result, dict)
        assert result.get("error") == "conflict"
        assert "detail" in result


class TestEnableSourceTool:
    def test_success_returns_enabled_true(self, session: Session):
        import aggregator_mcp.server as srv

        src = _make_source(session, "-mcpen")
        result = srv.enable_source(source_id=src.id)

        assert isinstance(result, dict)
        assert result.get("enabled") is True

    def test_not_found_returns_error_dict(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.enable_source(source_id=999_999_801)

        assert result.get("error") == "not_found"
        assert "detail" in result


class TestDisableSourceTool:
    def test_success_returns_enabled_false(self, session: Session):
        import aggregator_mcp.server as srv

        src = _make_source(session, "-mcpdis")
        result = srv.disable_source(source_id=src.id)

        assert isinstance(result, dict)
        assert result.get("enabled") is False

    def test_not_found_returns_error_dict(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.disable_source(source_id=999_999_802)

        assert result.get("error") == "not_found"


class TestSetSourceIntervalTool:
    def test_success_returns_updated_interval(self, session: Session):
        import aggregator_mcp.server as srv

        src = _make_source(session, "-mcpsi")
        result = srv.set_source_interval(source_id=src.id, seconds=7200)

        assert isinstance(result, dict)
        assert result.get("refresh_interval_seconds") == 7200

    def test_not_found_returns_error_dict(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.set_source_interval(source_id=999_999_803, seconds=3600)

        assert result.get("error") == "not_found"


class TestRefreshSourceNowTool:
    def test_success_returns_next_check_at(self, session: Session):
        import aggregator_mcp.server as srv

        src = _make_source(session, "-mcprsn")
        result = srv.refresh_source_now(source_id=src.id)

        assert isinstance(result, dict)
        assert "next_check_at" in result

    def test_not_found_returns_error_dict(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.refresh_source_now(source_id=999_999_804)

        assert result.get("error") == "not_found"


class TestRemoveSourceTool:
    def test_success_returns_deleted_counts(self, session: Session):
        import aggregator_mcp.server as srv

        src = _make_source(session, "-mcprmsrc")
        result = srv.remove_source(source_id=src.id)

        assert isinstance(result, dict)
        assert result.get("sources_deleted") == 1
        assert "articles_deleted" in result

    def test_not_found_returns_error_dict(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.remove_source(source_id=999_999_805)

        assert result.get("error") == "not_found"


# ---------------------------------------------------------------------------
# Category management tools
# ---------------------------------------------------------------------------


class TestAddCategoryTool:
    def test_success_returns_category_dict(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.add_category("MCP Tech", description="Tech news")

        assert isinstance(result, dict)
        assert result.get("name") == "MCP Tech"
        assert "id" in result
        assert "error" not in result

    def test_duplicate_name_returns_conflict_dict(self, session: Session):
        import aggregator_mcp.server as srv
        from aggregator_common.models import Category

        cat = Category(name="MCP-DupCat")
        session.add(cat)
        session.flush()
        result = srv.add_category("MCP-DupCat")

        assert result.get("error") == "conflict"
        assert "detail" in result


class TestRenameCategoryTool:
    def test_success_returns_updated_name(self, session: Session):
        import aggregator_mcp.server as srv
        from aggregator_common.models import Category

        cat = Category(name="MCP-OldName")
        session.add(cat)
        session.flush()
        result = srv.rename_category(category_id=cat.id, new_name="MCP-NewName")

        assert isinstance(result, dict)
        assert result.get("name") == "MCP-NewName"

    def test_conflict_returns_error_dict(self, session: Session):
        import aggregator_mcp.server as srv
        from aggregator_common.models import Category

        cat1 = Category(name="MCP-Existing")
        cat2 = Category(name="MCP-Other")
        session.add_all([cat1, cat2])
        session.flush()
        result = srv.rename_category(category_id=cat2.id, new_name="MCP-Existing")

        assert result.get("error") == "conflict"

    def test_not_found_returns_error_dict(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.rename_category(category_id=999_999_806, new_name="NewName")

        assert result.get("error") == "not_found"


class TestSetCategoryDescriptionTool:
    def test_success_returns_updated_description(self, session: Session):
        import aggregator_mcp.server as srv
        from aggregator_common.models import Category

        cat = Category(name="MCP-DescCat")
        session.add(cat)
        session.flush()
        result = srv.set_category_description(category_id=cat.id, description="New desc")

        assert isinstance(result, dict)
        assert result.get("description") == "New desc"

    def test_not_found_returns_error_dict(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.set_category_description(category_id=999_999_807, description="desc")

        assert result.get("error") == "not_found"


class TestSetCategoryOrderTool:
    def test_success_returns_updated_sort_order(self, session: Session):
        import aggregator_mcp.server as srv
        from aggregator_common.models import Category

        cat = Category(name="MCP-OrderCat")
        session.add(cat)
        session.flush()
        result = srv.set_category_order(category_id=cat.id, sort_order=42)

        assert isinstance(result, dict)
        assert result.get("sort_order") == 42

    def test_not_found_returns_error_dict(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.set_category_order(category_id=999_999_808, sort_order=1)

        assert result.get("error") == "not_found"


class TestEnableCategoryTool:
    def test_success_sets_enabled_true(self, session: Session):
        import aggregator_mcp.server as srv
        from aggregator_common.models import Category

        cat = Category(name="MCP-EnableCat", enabled=False)
        session.add(cat)
        session.flush()
        result = srv.enable_category(category_id=cat.id)

        assert isinstance(result, dict)
        assert result.get("enabled") is True

    def test_not_found_returns_error_dict(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.enable_category(category_id=999_999_809)

        assert result.get("error") == "not_found"


class TestDisableCategoryTool:
    def test_success_sets_enabled_false(self, session: Session):
        import aggregator_mcp.server as srv
        from aggregator_common.models import Category

        cat = Category(name="MCP-DisableCat")
        session.add(cat)
        session.flush()
        result = srv.disable_category(category_id=cat.id)

        assert isinstance(result, dict)
        assert result.get("enabled") is False

    def test_not_found_returns_error_dict(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.disable_category(category_id=999_999_810)

        assert result.get("error") == "not_found"


class TestRemoveCategoryTool:
    def test_success_returns_deleted_count(self, session: Session):
        import aggregator_mcp.server as srv
        from aggregator_common.models import Category

        cat = Category(name="MCP-DelCat")
        session.add(cat)
        session.flush()
        result = srv.remove_category(category_id=cat.id)

        assert isinstance(result, dict)
        assert result.get("categories_deleted") == 1

    def test_not_found_returns_error_dict(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.remove_category(category_id=999_999_811)

        assert result.get("error") == "not_found"


# ---------------------------------------------------------------------------
# Ops diagnostic tools
# ---------------------------------------------------------------------------


class TestPipelineStatusTool:
    def test_returns_expected_structure(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.pipeline_status()

        assert isinstance(result, dict)
        assert "article_counts" in result
        assert "in_flight" in result
        assert "sources" in result


class TestListStuckTool:
    def test_returns_list(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.list_stuck(lease_seconds=600)

        assert isinstance(result, list)

    def test_stale_claim_returned_with_iso_claimed_at(self, session: Session):
        import aggregator_mcp.server as srv

        src = _make_source(session, "-mcplstiso")
        stale_time = _NOW - timedelta(hours=2)
        art = Article(
            source_id=src.id,
            dedup_key="mcplstiso-1",
            status=ArticleStatus.pending_processing,
            claimed_by="old-worker",
            claimed_at=stale_time,
            raw_payload={},
            retrieved_at=_NOW,
        )
        session.add(art)
        session.flush()

        result = srv.list_stuck(lease_seconds=600)

        matching = [r for r in result if r.get("id") == art.id]
        assert len(matching) == 1
        assert isinstance(matching[0]["claimed_at"], str)


class TestListFailuresTool:
    def test_returns_list(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.list_failures()

        assert isinstance(result, list)

    def test_stage_processor_filter(self, session: Session):
        import aggregator_mcp.server as srv

        src = _make_source(session, "-mcplff")
        art_proc = Article(
            source_id=src.id, dedup_key="mcplff-proc",
            status=ArticleStatus.failed_processing,
            raw_payload={}, retrieved_at=_NOW,
        )
        art_rank = Article(
            source_id=src.id, dedup_key="mcplff-rank",
            status=ArticleStatus.failed_ranking,
            raw_payload={}, retrieved_at=_NOW,
        )
        session.add_all([art_proc, art_rank])
        session.flush()

        result = srv.list_failures(stage="processor")

        assert all(r["status"] == "failed_processing" for r in result)

    def test_invalid_stage_returns_error_dict(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.list_failures(stage="bad_stage")

        assert isinstance(result, dict)
        assert result["error"] == "invalid_stage"
        assert "bad_stage" in result["detail"]


# ---------------------------------------------------------------------------
# Ops remediation tools
# ---------------------------------------------------------------------------


class TestReapStaleClaimsTool:
    def test_returns_per_kind_released_counts(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.reap_stale_claims(lease_seconds=600)

        assert isinstance(result, dict)
        assert "articles_released" in result
        assert "briefs_released" in result

    def test_releases_stale_article_claim(self, session: Session):
        import aggregator_mcp.server as srv

        src = _make_source(session, "-mcprsc")
        stale_time = _NOW - timedelta(hours=2)
        art = Article(
            source_id=src.id, dedup_key="mcprsc-1",
            status=ArticleStatus.pending_processing,
            claimed_by="old-worker", claimed_at=stale_time,
            raw_payload={}, retrieved_at=_NOW,
        )
        session.add(art)
        session.flush()

        result = srv.reap_stale_claims(lease_seconds=600)

        assert result["articles_released"] >= 1


class TestRetryFailedTool:
    def test_success_returns_retried_count(self, session: Session):
        import aggregator_mcp.server as srv

        src = _make_source(session, "-mcprtf")
        art = Article(
            source_id=src.id, dedup_key="mcprtf-1",
            status=ArticleStatus.failed_processing,
            raw_payload={}, retrieved_at=_NOW,
        )
        session.add(art)
        session.flush()

        result = srv.retry_failed(stage="processor")

        assert isinstance(result, dict)
        assert result.get("retried", 0) >= 1

    def test_invalid_stage_returns_error_dict(self, session: Session):
        import aggregator_mcp.server as srv

        result = srv.retry_failed(stage="invalid_stage")

        assert isinstance(result, dict)
        assert result.get("error") == "invalid_transition"
        assert "detail" in result


class TestRerankTool:
    def test_all_ready_returns_reranked_count(self, session: Session):
        import aggregator_mcp.server as srv

        src = _make_source(session, "-mcprr")
        art = _make_ready_article(session, src.id, "mcprr-1")
        result = srv.rerank(all_ready=True)

        assert isinstance(result, dict)
        assert result.get("reranked", 0) >= 1

    def test_disallowed_transition_returns_error_dict(self, session: Session):
        """failed_processing → pending_ranking is not an allowed transition."""
        import aggregator_mcp.server as srv

        src = _make_source(session, "-mcprrbad")
        art = Article(
            source_id=src.id, dedup_key="mcprrbad-1",
            status=ArticleStatus.failed_processing,
            raw_payload={}, retrieved_at=_NOW,
        )
        session.add(art)
        session.flush()

        result = srv.rerank(article_id=art.id)

        assert isinstance(result, dict)
        assert result.get("error") == "invalid_transition"
