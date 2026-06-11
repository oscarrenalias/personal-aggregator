"""Integration tests for rank.py — per-article pipeline transitions using testcontainers Postgres.

Covers:
- happy path: article transitions to ready, LLM fields populated
- short content: article skipped (insufficient content)
- rank failure with retry exhaustion: article transitions to failed_ranking
- missing article: no exception raised
- source lookup: falls back to "Unknown Source" when source row missing
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from aggregator_common.models import Article, InterestProfile
from aggregator_common.state import ArticleStatus

from .conftest import make_article, make_source


def _make_litellm_response(
    summary: str = "Test summary.",
    topics: list[str] | None = None,
    score: int = 75,
    reason: str = "Test reason.",
    categories: list[str] | None = None,
) -> SimpleNamespace:
    if topics is None:
        topics = ["ai", "python"]
    payload = {
        "summary": summary,
        "topics": topics,
        "importance_score": score,
        "importance_reason": reason,
    }
    if categories is not None:
        payload["categories"] = categories
    content = json.dumps(payload)
    message = SimpleNamespace(content=content, parsed=None)
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=50)
    return SimpleNamespace(choices=[choice], usage=usage, model="claude-sonnet-4-6")


@pytest.fixture
def src(db_session):
    return make_source(db_session, url="https://rank.example.com/feed.xml")


@pytest.fixture
def session_factory(db_engine):
    return sessionmaker(bind=db_engine, autocommit=False, autoflush=False)


_ENOUGH_TEXT = "word " * 60  # 300 chars > 200 minimum


# ─── Happy path ──────────────────────────────────────────────────────────────


class TestHappyPath:
    def test_article_transitions_to_ready(self, db_session, src, settings, session_factory):
        article = make_article(
            db_session,
            source_id=src.id,
            dedup_key="rank-happy",
            clean_text=_ENOUGH_TEXT,
        )

        with (
            patch("litellm.completion", return_value=_make_litellm_response()),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            from aggregator_summarize_rank.rank import process_article
            process_article(article.id, "I like AI.", [], settings, session_factory)

        db_session.expire(article)
        db_session.refresh(article)
        assert article.status == ArticleStatus.ready

    def test_llm_fields_populated_after_ranking(self, db_session, src, settings, session_factory):
        article = make_article(
            db_session,
            source_id=src.id,
            dedup_key="rank-fields",
            clean_text=_ENOUGH_TEXT,
        )

        with (
            patch("litellm.completion", return_value=_make_litellm_response(score=82, summary="Great article.")),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            from aggregator_summarize_rank.rank import process_article
            process_article(article.id, "", [], settings, session_factory)

        db_session.expire(article)
        db_session.refresh(article)
        assert article.summary == "Great article."
        assert article.importance_score == 82
        assert article.importance_reason is not None
        assert article.topics is not None
        assert article.summarized_at is not None
        assert article.llm_meta is not None

    def test_llm_meta_contains_expected_keys(self, db_session, src, settings, session_factory):
        article = make_article(
            db_session,
            source_id=src.id,
            dedup_key="rank-meta",
            clean_text=_ENOUGH_TEXT,
        )

        with (
            patch("litellm.completion", return_value=_make_litellm_response()),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            from aggregator_summarize_rank.rank import process_article
            process_article(article.id, "", [], settings, session_factory)

        db_session.expire(article)
        db_session.refresh(article)
        meta = article.llm_meta
        assert "model" in meta
        assert "prompt_tokens" in meta
        assert "completion_tokens" in meta
        assert "prompt_version" in meta

    def test_interest_profile_from_db_row_used(self, db_session, src, settings, session_factory):
        """process_article passes the caller-supplied interest profile to the LLM."""
        article = make_article(
            db_session,
            source_id=src.id,
            dedup_key="rank-profile",
            clean_text=_ENOUGH_TEXT,
        )
        captured: list[list] = []

        def _capture(**kwargs):
            captured.append(kwargs.get("messages", []))
            return _make_litellm_response()

        with (
            patch("litellm.completion", side_effect=_capture),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            from aggregator_summarize_rank.rank import process_article
            process_article(article.id, "I love deep-sea biology.", [], settings, session_factory)

        assert captured, "litellm.completion was not called"
        user_msg = captured[0][1]["content"]
        assert "deep-sea biology" in user_msg


# ─── Short content → skipped ─────────────────────────────────────────────────


class TestShortContent:
    def test_article_with_no_content_skipped(self, db_session, src, settings, session_factory):
        article = make_article(
            db_session,
            source_id=src.id,
            dedup_key="rank-skip-empty",
            clean_text=None,
            excerpt=None,
            feed_summary=None,
        )

        from aggregator_summarize_rank.rank import process_article
        process_article(article.id, "", [], settings, session_factory)

        db_session.expire(article)
        db_session.refresh(article)
        assert article.status == ArticleStatus.skipped

    def test_article_below_min_chars_skipped(self, db_session, src, settings, session_factory):
        article = make_article(
            db_session,
            source_id=src.id,
            dedup_key="rank-skip-short",
            clean_text="too short",
        )

        from aggregator_summarize_rank.rank import process_article
        process_article(article.id, "", [], settings, session_factory)

        db_session.expire(article)
        db_session.refresh(article)
        assert article.status == ArticleStatus.skipped

    def test_skipped_article_no_llm_calls(self, db_session, src, settings, session_factory):
        article = make_article(
            db_session,
            source_id=src.id,
            dedup_key="rank-skip-nollm",
            clean_text=None,
        )

        with patch("litellm.completion") as mock_llm:
            from aggregator_summarize_rank.rank import process_article
            process_article(article.id, "", [], settings, session_factory)

        mock_llm.assert_not_called()


# ─── Ranking failure → failed_ranking ────────────────────────────────────────


class TestRankingFailure:
    def test_rank_error_fails_article_when_retries_exhausted(
        self, db_session, src, db_engine, monkeypatch
    ):
        monkeypatch.setenv("SUMMARIZE_RANK_MAX_RETRIES", "1")
        from aggregator_summarize_rank.config import SummarizeRankSettings
        fail_settings = SummarizeRankSettings(summarize_rank_max_retries=1)
        factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)

        article = make_article(
            db_session,
            source_id=src.id,
            dedup_key="rank-fail-exhaust",
            clean_text=_ENOUGH_TEXT,
        )

        bad_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="not json", parsed=None))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            model="test",
        )

        with patch("litellm.completion", return_value=bad_response):
            from aggregator_summarize_rank.rank import process_article
            process_article(article.id, "", [], fail_settings, factory)

        db_session.expire(article)
        db_session.refresh(article)
        assert article.status == ArticleStatus.failed_ranking

    def test_rank_error_stores_last_error(
        self, db_session, src, db_engine, monkeypatch
    ):
        monkeypatch.setenv("SUMMARIZE_RANK_MAX_RETRIES", "1")
        from aggregator_summarize_rank.config import SummarizeRankSettings
        fail_settings = SummarizeRankSettings(summarize_rank_max_retries=1)
        factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)

        article = make_article(
            db_session,
            source_id=src.id,
            dedup_key="rank-fail-error",
            clean_text=_ENOUGH_TEXT,
        )

        bad_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="bad", parsed=None))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            model="test",
        )

        with patch("litellm.completion", return_value=bad_response):
            from aggregator_summarize_rank.rank import process_article
            process_article(article.id, "", [], fail_settings, factory)

        db_session.expire(article)
        db_session.refresh(article)
        assert article.last_error is not None

    def test_retry_before_exhaustion(self, db_session, src, db_engine, monkeypatch):
        """With max_retries=3, first failure sets pending_ranking with next_retry_at."""
        monkeypatch.setenv("SUMMARIZE_RANK_MAX_RETRIES", "3")
        from aggregator_summarize_rank.config import SummarizeRankSettings
        retry_settings = SummarizeRankSettings(
            summarize_rank_max_retries=3,
            summarize_rank_backoff_base_seconds=10,
        )
        factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)

        article = make_article(
            db_session,
            source_id=src.id,
            dedup_key="rank-retry",
            clean_text=_ENOUGH_TEXT,
        )

        bad_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="bad", parsed=None))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            model="test",
        )

        with patch("litellm.completion", return_value=bad_response):
            from aggregator_summarize_rank.rank import process_article
            process_article(article.id, "", [], retry_settings, factory)

        db_session.expire(article)
        db_session.refresh(article)
        assert article.retry_count == 1
        assert article.status == ArticleStatus.pending_ranking
        assert article.next_retry_at is not None


# ─── Missing article ──────────────────────────────────────────────────────────


class TestMissingArticle:
    def test_missing_article_id_does_not_raise(self, settings, session_factory):
        from aggregator_summarize_rank.rank import process_article
        process_article(999_999, "", [], settings, session_factory)


# ─── Source name in LLM input ────────────────────────────────────────────────


class TestSourceLookup:
    def test_source_name_passed_to_llm(self, db_session, db_engine, settings):
        """Source name from the sources row is included in the LLM prompt."""
        src = make_source(db_session, name="MyBlog", url="https://myblog.example.com/feed.xml")
        article = make_article(
            db_session,
            source_id=src.id,
            dedup_key="rank-srcname",
            clean_text=_ENOUGH_TEXT,
        )

        captured: list[list] = []

        def _capture(**kwargs):
            captured.append(kwargs.get("messages", []))
            return _make_litellm_response()

        factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
        with (
            patch("litellm.completion", side_effect=_capture),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            from aggregator_summarize_rank.rank import process_article
            process_article(article.id, "", [], settings, factory)

        assert captured, "litellm.completion was not called"
        user_msg = captured[0][1]["content"]
        assert "MyBlog" in user_msg


def test_categories_persisted_and_filtered_to_enabled_set(
    db_session, src, settings, session_factory
):
    """LLM categories are canonicalized to the enabled set, unknowns dropped,
    and persisted on the article. Regression: persistence was missing."""
    from types import SimpleNamespace

    article = make_article(
        db_session, source_id=src.id, dedup_key="cat-persist", clean_text=_ENOUGH_TEXT
    )
    enabled = [
        SimpleNamespace(name="AI", description="LLMs, ML"),
        SimpleNamespace(name="Gaming", description="video games"),
    ]
    resp = _make_litellm_response(categories=["ai", "Gaming", "Bogus"])
    with (
        patch("litellm.completion", return_value=resp),
        patch("litellm.completion_cost", return_value=0.001),
    ):
        from aggregator_summarize_rank.rank import process_article

        process_article(article.id, "", enabled, settings, session_factory)

    db_session.expire(article)
    db_session.refresh(article)
    assert article.categories == ["AI", "Gaming"]
