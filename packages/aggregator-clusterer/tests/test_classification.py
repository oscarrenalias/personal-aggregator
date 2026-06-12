"""Tests for aggregator_clusterer.classification module."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from aggregator_clusterer.classification import ClassificationResult, classify_article
from aggregator_clusterer.config import ClustererSettings
from aggregator_common.models import ClassificationLabel

from .conftest import make_article, make_source, make_thread

_SETTINGS = ClustererSettings()


def _mock_response(content: str) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.usage = None
    return response


class TestClassifyArticle:
    def test_happy_path_returns_populated_result(self, db_session):
        src = make_source(db_session, url="https://cls1.test/feed.xml")
        article = make_article(
            db_session, source_id=src.id, dedup_key="k1",
            clean_title="AI Breakthrough Announced",
            summary="A major AI development.",
        )
        payload = {
            "label": "new_thread",
            "thread_id": None,
            "confidence": 0.9,
            "new_facts": ["Fact one", "Fact two"],
            "reason": "Distinct new story",
        }
        with patch("aggregator_clusterer.classification.litellm.completion") as mock_llm:
            mock_llm.return_value = _mock_response(json.dumps(payload))
            result = classify_article(article, [], db_session, _SETTINGS)

        assert isinstance(result, ClassificationResult)
        assert result.label == ClassificationLabel.new_thread
        assert result.thread_id is None
        assert result.confidence == 0.9
        assert "Fact one" in result.new_facts
        assert result.reason == "Distinct new story"

    def test_llm_exception_returns_new_thread_fallback(self, db_session):
        src = make_source(db_session, url="https://cls2.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="k1")
        with patch(
            "aggregator_clusterer.classification.litellm.completion",
            side_effect=Exception("timeout"),
        ):
            result = classify_article(article, [], db_session, _SETTINGS)

        assert result.label == ClassificationLabel.new_thread
        assert result.confidence == 0.0
        assert result.reason == "classification_error"

    def test_json_parse_error_returns_new_thread_fallback(self, db_session):
        src = make_source(db_session, url="https://cls3.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="k1")
        with patch("aggregator_clusterer.classification.litellm.completion") as mock_llm:
            mock_llm.return_value = _mock_response("not valid json {{{")
            result = classify_article(article, [], db_session, _SETTINGS)

        assert result.label == ClassificationLabel.new_thread
        assert result.confidence == 0.0

    def test_new_thread_label_forces_thread_id_none(self, db_session):
        src = make_source(db_session, url="https://cls4.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="k1")
        thread = make_thread(db_session)
        payload = {
            "label": "new_thread",
            "thread_id": thread.id,  # LLM erroneously provides a thread_id
            "confidence": 0.8,
            "new_facts": [],
            "reason": "New story",
        }
        with patch("aggregator_clusterer.classification.litellm.completion") as mock_llm:
            mock_llm.return_value = _mock_response(json.dumps(payload))
            result = classify_article(article, [], db_session, _SETTINGS)

        assert result.label == ClassificationLabel.new_thread
        assert result.thread_id is None

    def test_related_new_thread_forces_thread_id_none(self, db_session):
        src = make_source(db_session, url="https://cls5.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="k1")
        thread = make_thread(db_session)
        payload = {
            "label": "related_new_thread",
            "thread_id": thread.id,
            "confidence": 0.7,
            "new_facts": [],
            "reason": "Related but separate",
        }
        with patch("aggregator_clusterer.classification.litellm.completion") as mock_llm:
            mock_llm.return_value = _mock_response(json.dumps(payload))
            result = classify_article(article, [], db_session, _SETTINGS)

        assert result.label == ClassificationLabel.related_new_thread
        assert result.thread_id is None

    def test_confidence_clamped_to_zero_one(self, db_session):
        src = make_source(db_session, url="https://cls6.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="k1")
        payload = {
            "label": "new_thread",
            "thread_id": None,
            "confidence": 1.5,  # out of range above 1.0
            "new_facts": [],
            "reason": "Test",
        }
        with patch("aggregator_clusterer.classification.litellm.completion") as mock_llm:
            mock_llm.return_value = _mock_response(json.dumps(payload))
            result = classify_article(article, [], db_session, _SETTINGS)

        assert result.confidence == 1.0

    def test_confidence_clamped_below_zero(self, db_session):
        src = make_source(db_session, url="https://cls7.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="k1")
        payload = {
            "label": "new_thread",
            "thread_id": None,
            "confidence": -0.5,
            "new_facts": [],
            "reason": "Test",
        }
        with patch("aggregator_clusterer.classification.litellm.completion") as mock_llm:
            mock_llm.return_value = _mock_response(json.dumps(payload))
            result = classify_article(article, [], db_session, _SETTINGS)

        assert result.confidence == 0.0
