"""Tests for aggregator_brief.schema — Pydantic model validation."""

import pytest
from pydantic import ValidationError

from aggregator_brief.schema import BriefReferenceSchema, BriefSubmitSchema, BriefTopicSchema


class TestBriefReferenceSchema:
    def test_defaults(self):
        ref = BriefReferenceSchema(title="Some Article")
        assert ref.article_id is None
        assert ref.url is None
        assert ref.internal is False

    def test_explicit_internal_flag(self):
        ref = BriefReferenceSchema(title="Internal", article_id=42, internal=True)
        assert ref.internal is True
        assert ref.article_id == 42

    def test_missing_title_raises(self):
        with pytest.raises(ValidationError):
            BriefReferenceSchema()


class TestBriefTopicSchema:
    def test_valid_minimal(self):
        topic = BriefTopicSchema(
            headline="Topic headline",
            what_happened="Something occurred.",
            why_it_matters="It affects people.",
        )
        assert topic.historical_context is None
        assert topic.references == []

    def test_missing_headline_raises(self):
        with pytest.raises(ValidationError):
            BriefTopicSchema(what_happened="x", why_it_matters="y")

    def test_missing_what_happened_raises(self):
        with pytest.raises(ValidationError):
            BriefTopicSchema(headline="h", why_it_matters="y")

    def test_missing_why_it_matters_raises(self):
        with pytest.raises(ValidationError):
            BriefTopicSchema(headline="h", what_happened="x")

    def test_with_references(self):
        topic = BriefTopicSchema(
            headline="h",
            what_happened="x",
            why_it_matters="y",
            references=[BriefReferenceSchema(title="Ref 1", url="https://example.com")],
        )
        assert len(topic.references) == 1


class TestBriefSubmitSchema:
    def test_valid_schema(self):
        submit = BriefSubmitSchema(
            headline="Today's Brief",
            intro="A short intro.",
            topics=[
                BriefTopicSchema(
                    headline="Topic",
                    what_happened="Something.",
                    why_it_matters="Matters.",
                )
            ],
        )
        assert submit.headline == "Today's Brief"
        assert len(submit.topics) == 1

    def test_missing_headline_raises(self):
        with pytest.raises(ValidationError):
            BriefSubmitSchema(intro="i", topics=[])

    def test_missing_intro_raises(self):
        with pytest.raises(ValidationError):
            BriefSubmitSchema(headline="h", topics=[])

    def test_missing_topics_raises(self):
        with pytest.raises(ValidationError):
            BriefSubmitSchema(headline="h", intro="i")

    def test_model_json_schema_suitable_for_llm(self):
        schema = BriefSubmitSchema.model_json_schema()
        assert "properties" in schema
        assert "headline" in schema["properties"]
        assert "intro" in schema["properties"]
        assert "topics" in schema["properties"]

    def test_model_validate_from_dict(self):
        data = {
            "headline": "H",
            "intro": "I",
            "topics": [
                {
                    "headline": "T",
                    "what_happened": "W",
                    "why_it_matters": "M",
                    "references": [],
                }
            ],
        }
        submit = BriefSubmitSchema.model_validate(data)
        assert submit.headline == "H"
        assert len(submit.topics) == 1
