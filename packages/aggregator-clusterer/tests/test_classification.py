"""Tests for aggregator_clusterer.classification module."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from aggregator_clusterer.candidates import CandidateMatch
from aggregator_clusterer.classification import ClassificationResult, classify_article, is_section_title_blocked
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
        assert result.is_error is False

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
        assert result.is_error is True

    def test_json_parse_error_returns_new_thread_fallback(self, db_session):
        src = make_source(db_session, url="https://cls3.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="k1")
        with patch("aggregator_clusterer.classification.litellm.completion") as mock_llm:
            mock_llm.return_value = _mock_response("not valid json {{{")
            result = classify_article(article, [], db_session, _SETTINGS)

        assert result.label == ClassificationLabel.new_thread
        assert result.confidence == 0.0
        assert result.is_error is True

    def test_empty_llm_response_signals_error(self, db_session):
        src = make_source(db_session, url="https://cls3b.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="k1-empty")
        with patch("aggregator_clusterer.classification.litellm.completion") as mock_llm:
            mock_llm.return_value = _mock_response("")
            result = classify_article(article, [], db_session, _SETTINGS)

        assert result.is_error is True
        assert result.confidence == 0.0

    def test_invalid_label_signals_error(self, db_session):
        src = make_source(db_session, url="https://cls3c.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="k1-invalid")
        payload = {
            "label": "not_a_real_label",
            "thread_id": None,
            "confidence": 0.9,
            "new_facts": [],
            "reason": "Test",
        }
        with patch("aggregator_clusterer.classification.litellm.completion") as mock_llm:
            mock_llm.return_value = _mock_response(json.dumps(payload))
            result = classify_article(article, [], db_session, _SETTINGS)

        assert result.is_error is True
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

    def test_thread_title_parsed_from_llm_response(self, db_session):
        """thread_title field is extracted from the LLM response."""
        src = make_source(db_session, url="https://cls8.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="k1")
        payload = {
            "label": "new_thread",
            "thread_id": None,
            "confidence": 0.9,
            "new_facts": [],
            "reason": "New story",
            "thread_title": "Researchers publish findings on climate adaptation",
        }
        with patch("aggregator_clusterer.classification.litellm.completion") as mock_llm:
            mock_llm.return_value = _mock_response(json.dumps(payload))
            result = classify_article(article, [], db_session, _SETTINGS)

        assert result.thread_title == "Researchers publish findings on climate adaptation"

    def test_thread_title_absent_in_response_yields_none(self, db_session):
        """Missing thread_title in LLM response produces None (backwards-compatible)."""
        src = make_source(db_session, url="https://cls9.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="k1")
        payload = {
            "label": "new_thread",
            "thread_id": None,
            "confidence": 0.8,
            "new_facts": [],
            "reason": "Test",
        }
        with patch("aggregator_clusterer.classification.litellm.completion") as mock_llm:
            mock_llm.return_value = _mock_response(json.dumps(payload))
            result = classify_article(article, [], db_session, _SETTINGS)

        assert result.thread_title is None

    def test_thread_title_truncated_to_90_chars(self, db_session):
        """thread_title longer than 90 chars is truncated."""
        src = make_source(db_session, url="https://cls10.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="k1")
        long_title = "A" * 120
        payload = {
            "label": "new_thread",
            "thread_id": None,
            "confidence": 0.8,
            "new_facts": [],
            "reason": "Test",
            "thread_title": long_title,
        }
        with patch("aggregator_clusterer.classification.litellm.completion") as mock_llm:
            mock_llm.return_value = _mock_response(json.dumps(payload))
            result = classify_article(article, [], db_session, _SETTINGS)

        assert result.thread_title is not None
        assert len(result.thread_title) == 90

    def test_classifier_attaches_to_non_first_candidate(self, db_session):
        """LLM picks the 2nd-ranked candidate; result carries that thread_id, not the top one."""
        src = make_source(db_session, url="https://nonfirst.test/feed.xml")
        article = make_article(
            db_session, source_id=src.id, dedup_key="nf1",
            clean_title="Update", summary="New detail.",
        )
        thread1 = make_thread(db_session, title="Thread One")
        thread2 = make_thread(db_session, title="Thread Two")
        thread3 = make_thread(db_session, title="Thread Three")

        candidates = [
            CandidateMatch(thread_id=thread1.id, composite_score=0.9, signals={}),
            CandidateMatch(thread_id=thread2.id, composite_score=0.7, signals={}),
            CandidateMatch(thread_id=thread3.id, composite_score=0.5, signals={}),
        ]
        payload = {
            "label": "same_thread_new_fact",
            "thread_id": thread2.id,
            "confidence": 0.85,
            "new_facts": ["New detail"],
            "reason": "Continues thread two",
        }
        with patch("aggregator_clusterer.classification.litellm.completion") as mock_llm:
            mock_llm.return_value = _mock_response(json.dumps(payload))
            result = classify_article(article, candidates, db_session, _SETTINGS)

        assert result.label == ClassificationLabel.same_thread_new_fact
        assert result.thread_id == thread2.id

    def test_thread_id_not_in_presented_candidates_yields_no_attachment(self, db_session):
        """LLM returns a thread_id outside the presented candidate set; classify_article rejects it."""
        src = make_source(db_session, url="https://hallucin.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="hall1")
        thread1 = make_thread(db_session, title="Thread One")

        candidates = [
            CandidateMatch(thread_id=thread1.id, composite_score=0.9, signals={}),
        ]
        payload = {
            "label": "same_thread_new_fact",
            "thread_id": 99999,  # not in presented_ids
            "confidence": 0.8,
            "new_facts": [],
            "reason": "Hallucinated thread id",
        }
        with patch("aggregator_clusterer.classification.litellm.completion") as mock_llm:
            mock_llm.return_value = _mock_response(json.dumps(payload))
            result = classify_article(article, candidates, db_session, _SETTINGS)

        assert result.thread_id is None

    def test_candidates_beyond_max_classifier_limit_are_excluded(self, db_session):
        """Candidates beyond clusterer_max_classifier_candidates are not presented to the LLM;
        a thread_id from them is treated as out-of-set and rejected."""
        src = make_source(db_session, url="https://maxcap.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="maxcap1")
        threads = [make_thread(db_session, title=f"Thread {i}") for i in range(5)]

        candidates = [
            CandidateMatch(thread_id=t.id, composite_score=float(5 - i), signals={})
            for i, t in enumerate(threads)
        ]
        settings = ClustererSettings(clusterer_max_classifier_candidates=2)

        # LLM returns thread_id of the 3rd candidate (index 2), which is beyond the cap of 2.
        payload = {
            "label": "same_thread_new_fact",
            "thread_id": threads[2].id,
            "confidence": 0.8,
            "new_facts": [],
            "reason": "Test",
        }
        with patch("aggregator_clusterer.classification.litellm.completion") as mock_llm:
            mock_llm.return_value = _mock_response(json.dumps(payload))
            result = classify_article(article, candidates, db_session, settings)

        assert result.thread_id is None

    def test_llm_exception_with_candidates_returns_new_thread(self, db_session):
        """LLM exception while candidates are present still falls back to new_thread without raising."""
        src = make_source(db_session, url="https://exc-cand.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="exc-c1")
        thread = make_thread(db_session, title="Some thread")
        candidates = [CandidateMatch(thread_id=thread.id, composite_score=0.8, signals={})]

        with patch(
            "aggregator_clusterer.classification.litellm.completion",
            side_effect=Exception("network error"),
        ):
            result = classify_article(article, candidates, db_session, _SETTINGS)

        assert result.label == ClassificationLabel.new_thread
        assert result.thread_id is None
        assert result.is_error is True

    def test_malformed_json_with_candidates_returns_new_thread(self, db_session):
        """Malformed LLM JSON while candidates are present still falls back to new_thread without raising."""
        src = make_source(db_session, url="https://badjson-cand.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="bj-c1")
        thread = make_thread(db_session, title="Some thread")
        candidates = [CandidateMatch(thread_id=thread.id, composite_score=0.8, signals={})]

        with patch("aggregator_clusterer.classification.litellm.completion") as mock_llm:
            mock_llm.return_value = _mock_response("{not valid json")
            result = classify_article(article, candidates, db_session, _SETTINGS)

        assert result.label == ClassificationLabel.new_thread
        assert result.thread_id is None
        assert result.is_error is True


class TestIsSectionTitleBlocked:
    def _article(self, *, clean_title=None, feed_title=None):
        from datetime import datetime, timezone
        from aggregator_common.models import Article
        return Article(
            source_id=1,
            dedup_key="stb-dummy",
            status="ready",
            raw_payload={},
            retrieved_at=datetime.now(tz=timezone.utc),
            clean_title=clean_title,
            feed_title=feed_title,
        )

    def test_exact_match_top_stories_returns_true(self):
        article = self._article(feed_title="top stories")
        assert is_section_title_blocked(article, _SETTINGS) is True

    def test_case_insensitive_match_returns_true(self):
        article = self._article(feed_title="Top Stories")
        assert is_section_title_blocked(article, _SETTINGS) is True

    def test_homepage_exact_match_returns_true(self):
        article = self._article(feed_title="homepage")
        assert is_section_title_blocked(article, _SETTINGS) is True

    def test_breaking_news_exact_match_returns_true(self):
        article = self._article(feed_title="breaking news")
        assert is_section_title_blocked(article, _SETTINGS) is True

    def test_heuristic_match_single_word_in_blocklist_returns_true(self):
        # "latest" is a single-word blocked phrase; heuristic also fires for short titles
        article = self._article(feed_title="latest")
        assert is_section_title_blocked(article, _SETTINGS) is True

    def test_real_headline_not_blocked(self):
        article = self._article(feed_title="Scientists Discover New Alzheimer's Treatment")
        assert is_section_title_blocked(article, _SETTINGS) is False

    def test_clean_title_checked_before_feed_title(self):
        # clean_title="top stories" fires even though feed_title is a real headline
        article = self._article(
            clean_title="top stories",
            feed_title="Scientists Discover New Treatment",
        )
        assert is_section_title_blocked(article, _SETTINGS) is True

    def test_custom_blocklist_used(self):
        from aggregator_clusterer.config import ClustererSettings as CS
        custom = CS(clusterer_section_title_blocklist=["custom section"])
        article = self._article(feed_title="custom section")
        assert is_section_title_blocked(article, custom) is True
        assert is_section_title_blocked(article, _SETTINGS) is False

    def test_empty_title_not_blocked(self):
        article = self._article(feed_title=None, clean_title=None)
        assert is_section_title_blocked(article, _SETTINGS) is False

    def test_section_title_article_has_no_thread_membership(self, db_session):
        """Guard returning True means no ThreadMembership is created for the article."""
        src = make_source(db_session, url="https://stb-db.test/feed.xml")
        article = make_article(
            db_session,
            source_id=src.id,
            dedup_key="stb-db-ts1",
            feed_title="Top Stories",
        )
        assert is_section_title_blocked(article, _SETTINGS) is True
        from aggregator_common.models import ThreadMembership
        membership = (
            db_session.query(ThreadMembership)
            .filter(ThreadMembership.article_id == article.id)
            .first()
        )
        assert membership is None


class TestClustererSettings:
    def test_max_classifier_candidates_default(self):
        settings = ClustererSettings()
        assert settings.clusterer_max_classifier_candidates == 5

    def test_max_classifier_candidates_from_env(self, monkeypatch):
        monkeypatch.setenv("CLUSTERER_MAX_CLASSIFIER_CANDIDATES", "3")
        settings = ClustererSettings()
        assert settings.clusterer_max_classifier_candidates == 3
