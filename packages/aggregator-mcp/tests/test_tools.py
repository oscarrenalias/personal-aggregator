"""Integration tests: invoke each MCP tool callable against a testcontainers Postgres DB."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Generator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from aggregator_common.models import Article, Source
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
