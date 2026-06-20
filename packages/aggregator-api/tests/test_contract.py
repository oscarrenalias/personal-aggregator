"""Contract tests: assert exact JSON key sets for every response model.

A field rename in models.py must cause one of these tests to fail.
Tests here are pure unit tests — no database or HTTP client needed.
"""

from __future__ import annotations

from aggregator_api.models import (
    ArticleResponse,
    BriefResponse,
    BriefTopicResponse,
    CategoryResponse,
    InterestProfileResponse,
    PaginatedResponse,
    SourceResponse,
    ThreadMemberResponse,
    ThreadResponse,
)


_ARTICLE_FIELDS = {
    "id",
    "title",
    "url",
    "source_id",
    "source_name",
    "feed_published_at",
    "summary",
    "excerpt",
    "clean_text",
    "importance_score",
    "importance_reason",
    "categories",
    "topics",
    "is_read",
    "is_saved",
    "author",
    "word_count",
    "language",
    "image_url",
    "comments_url",
}

_THREAD_FIELDS = {
    "id",
    "representative_title",
    "rolling_summary",
    "known_facts",
    "first_seen",
    "last_updated",
    "status",
    "tier",
    "tier_reason",
    "relevance_score",
    "novelty_score",
    "importance_score",
    "diversity_score",
    "time_sensitivity_score",
    "source_diversity",
    "confidence",
    "novelty_label",
    "deltas",
    "source_list",
    "top_grade",
    "surfaced",
    "dismissed",
    "source_count",
    "member_count",
    "image_url",
    "has_updates",
}

_THREAD_MEMBER_FIELDS = {
    "id",
    "thread_id",
    "article_id",
    "classification_label",
    "new_facts",
    "reason",
    "confidence",
    "suppressed",
    "assigned_at",
    "clean_title",
    "url",
    "source_name",
    "published_at",
}

_SOURCE_FIELDS = {"id", "name", "feed_url"}

_CATEGORY_FIELDS = {"id", "name", "description", "sort_order"}

_BRIEF_TOPIC_FIELDS = {
    "position",
    "headline",
    "what_happened",
    "why_it_matters",
    "historical_context",
    "refs",
}

_BRIEF_FIELDS = {
    "id",
    "headline",
    "intro",
    "generated_at",
    "period_start",
    "period_end",
    "model",
    "topics",
}

_INTEREST_PROFILE_FIELDS = {"profile_text", "updated_at"}

_PAGINATED_FIELDS = {"items", "next_cursor"}


class TestArticleResponseContract:
    def test_exact_field_names(self):
        assert set(ArticleResponse.model_fields) == _ARTICLE_FIELDS

    def test_validates_from_result_dataclass(self):
        from aggregator_common.queries import ArticleResult

        result = ArticleResult(
            id=1,
            title="Test Article",
            url="https://example.com/article",
            source_id=1,
            source_name="Test Feed",
            feed_published_at="2025-01-01T12:00:00+00:00",
            summary="Summary text",
            excerpt="Excerpt text",
            clean_text=None,
            importance_score=80,
            importance_reason="High relevance",
            categories=["Tech"],
            topics=["tech", "ai"],
            is_read=False,
            is_saved=False,
            author="Author Name",
            word_count=500,
            language="en",
        )
        response = ArticleResponse(**vars(result))
        assert set(response.model_dump().keys()) == _ARTICLE_FIELDS

    def test_json_output_keys_match(self):
        """Ensure the serialised JSON uses the declared field names, not aliases."""
        from aggregator_common.queries import ArticleResult

        result = ArticleResult(
            id=42,
            title="T",
            url=None,
            source_id=1,
            source_name=None,
            feed_published_at=None,
            summary=None,
            excerpt=None,
            clean_text=None,
            importance_score=None,
            importance_reason=None,
            categories=None,
            topics=None,
            is_read=False,
            is_saved=False,
            author=None,
            word_count=None,
            language=None,
        )
        response = ArticleResponse(**vars(result))
        json_keys = set(response.model_dump().keys())
        assert json_keys == _ARTICLE_FIELDS


class TestThreadResponseContract:
    def test_exact_field_names(self):
        assert set(ThreadResponse.model_fields) == _THREAD_FIELDS


class TestThreadMemberResponseContract:
    def test_exact_field_names(self):
        assert set(ThreadMemberResponse.model_fields) == _THREAD_MEMBER_FIELDS

    def test_validates_from_result_dataclass(self):
        from aggregator_common.queries import ThreadMemberResult

        result = ThreadMemberResult(
            id=1,
            thread_id=10,
            article_id=5,
            classification_label="same_thread",
            new_facts=["fact one"],
            reason="Good match",
            confidence=0.9,
            suppressed=False,
            assigned_at="2025-01-01T12:00:00+00:00",
            clean_title="Article Title",
            url="https://example.com/article",
            source_name="Source",
            published_at="2025-01-01T11:00:00+00:00",
        )
        response = ThreadMemberResponse(**vars(result))
        assert set(response.model_dump().keys()) == _THREAD_MEMBER_FIELDS


class TestSourceResponseContract:
    def test_exact_field_names(self):
        assert set(SourceResponse.model_fields) == _SOURCE_FIELDS

    def test_validates_from_result_dataclass(self):
        from aggregator_common.queries import SourceResult

        result = SourceResult(id=1, name="Feed Name", feed_url="https://example.com/feed.xml")
        response = SourceResponse.model_validate(result)
        assert set(response.model_dump().keys()) == _SOURCE_FIELDS


class TestCategoryResponseContract:
    def test_exact_field_names(self):
        assert set(CategoryResponse.model_fields) == _CATEGORY_FIELDS

    def test_validates_from_result_dataclass(self):
        from aggregator_common.queries import CategoryResult

        result = CategoryResult(id=1, name="Tech", description="Technology news", sort_order=0)
        response = CategoryResponse.model_validate(result)
        assert set(response.model_dump().keys()) == _CATEGORY_FIELDS


class TestBriefTopicResponseContract:
    def test_exact_field_names(self):
        assert set(BriefTopicResponse.model_fields) == _BRIEF_TOPIC_FIELDS

    def test_refs_not_topic_refs(self):
        """Field must be 'refs' in the JSON output, not 'topic_refs' (the ORM column name)."""
        assert "refs" in BriefTopicResponse.model_fields
        assert "topic_refs" not in BriefTopicResponse.model_fields

    def test_validates_from_result_dataclass(self):
        from aggregator_common.queries import BriefTopicResult

        result = BriefTopicResult(
            position=1,
            headline="Topic",
            what_happened="Things happened",
            why_it_matters="Important",
            historical_context=None,
            refs=[],
        )
        response = BriefTopicResponse.model_validate(result)
        assert set(response.model_dump().keys()) == _BRIEF_TOPIC_FIELDS


class TestBriefResponseContract:
    def test_exact_field_names(self):
        assert set(BriefResponse.model_fields) == _BRIEF_FIELDS


class TestInterestProfileResponseContract:
    def test_exact_field_names(self):
        assert set(InterestProfileResponse.model_fields) == _INTEREST_PROFILE_FIELDS


class TestPaginatedResponseContract:
    def test_exact_field_names(self):
        assert set(PaginatedResponse.model_fields) == _PAGINATED_FIELDS

    def test_items_and_next_cursor_in_json(self):
        from aggregator_api.models import ArticleResponse

        resp: PaginatedResponse[ArticleResponse] = PaginatedResponse(items=[], next_cursor=None)
        dumped = resp.model_dump()
        assert "items" in dumped
        assert "next_cursor" in dumped
        assert len(dumped) == 2
