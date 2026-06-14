"""Regression tests: classification failures must not create threads or memberships.

Root cause: _error_result() returned label=new_thread, causing worker._run_one_cycle
to call process_classification unconditionally, which created a junk single-article
thread for every failed LLM call. The article then gained a ThreadMembership and was
never retried (list_unassigned_ready_articles requires no membership).

Fix: ClassificationResult gains is_error=True for all error paths; worker skips
process_classification when is_error is set, leaving the article unassigned for retry.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from aggregator_clusterer.config import ClustererSettings
from aggregator_clusterer.worker import _run_one_cycle
from aggregator_common.models import Thread, ThreadMembership

from .conftest import make_article, make_source


_SETTINGS = ClustererSettings()
_NOW = datetime.now(tz=timezone.utc)


def _mock_llm_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage = None
    return resp


def _run_cycle(db_engine) -> None:
    from sqlalchemy.orm import sessionmaker
    factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    _run_one_cycle(_SETTINGS, factory, threading.Event())


class TestClassificationErrorLeavesArticleUnassigned:
    """Hard classification failures must not create any Thread or ThreadMembership.

    Each test:
    - Fails without the fix (process_classification was called unconditionally).
    - Passes with the fix (is_error guard skips process_classification).
    """

    def _membership_count(self, session, article_id: int) -> int:
        return (
            session.query(ThreadMembership)
            .filter(ThreadMembership.article_id == article_id)
            .count()
        )

    def _thread_count(self, session) -> int:
        return session.query(Thread).count()

    def test_llm_exception_no_thread_or_membership(self, db_session, db_engine):
        """LLM exception → article stays unassigned; no thread or membership created."""
        src = make_source(db_session, url="https://weh1.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="weh-exc-1", feed_published_at=_NOW)

        with patch(
            "aggregator_clusterer.classification.litellm.completion",
            side_effect=Exception("API budget exceeded"),
        ):
            _run_cycle(db_engine)

        db_session.expire_all()
        assert self._membership_count(db_session, article.id) == 0
        assert self._thread_count(db_session) == 0

    def test_empty_llm_response_no_thread_or_membership(self, db_session, db_engine):
        """Empty LLM response → article stays unassigned; no thread or membership created."""
        src = make_source(db_session, url="https://weh2.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="weh-empty-1", feed_published_at=_NOW)

        with patch(
            "aggregator_clusterer.classification.litellm.completion",
            return_value=_mock_llm_response(""),
        ):
            _run_cycle(db_engine)

        db_session.expire_all()
        assert self._membership_count(db_session, article.id) == 0
        assert self._thread_count(db_session) == 0

    def test_json_parse_error_no_thread_or_membership(self, db_session, db_engine):
        """JSON parse error → article stays unassigned; no thread or membership created."""
        src = make_source(db_session, url="https://weh3.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="weh-json-1", feed_published_at=_NOW)

        with patch(
            "aggregator_clusterer.classification.litellm.completion",
            return_value=_mock_llm_response("not valid json {{{"),
        ):
            _run_cycle(db_engine)

        db_session.expire_all()
        assert self._membership_count(db_session, article.id) == 0
        assert self._thread_count(db_session) == 0

    def test_invalid_label_no_thread_or_membership(self, db_session, db_engine):
        """Invalid label in LLM response → article stays unassigned; no thread or membership."""
        src = make_source(db_session, url="https://weh4.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="weh-label-1", feed_published_at=_NOW)

        payload = {
            "label": "completely_invalid_label",
            "thread_id": None,
            "confidence": 0.9,
            "new_facts": [],
            "reason": "Test",
        }
        with patch(
            "aggregator_clusterer.classification.litellm.completion",
            return_value=_mock_llm_response(json.dumps(payload)),
        ):
            _run_cycle(db_engine)

        db_session.expire_all()
        assert self._membership_count(db_session, article.id) == 0
        assert self._thread_count(db_session) == 0

    def test_valid_new_thread_creates_thread_and_membership(self, db_session, db_engine):
        """A valid new_thread response still creates a thread and membership (unchanged)."""
        src = make_source(db_session, url="https://weh5.test/feed.xml")
        article = make_article(
            db_session, source_id=src.id, dedup_key="weh-valid-1",
            clean_title="Valid New Story", summary="Something happened.",
            feed_published_at=_NOW,
        )

        payload = {
            "label": "new_thread",
            "thread_id": None,
            "confidence": 0.9,
            "new_facts": ["Key fact"],
            "reason": "Distinct new story",
            "thread_title": "Valid New Story",
        }
        with patch(
            "aggregator_clusterer.classification.litellm.completion",
            return_value=_mock_llm_response(json.dumps(payload)),
        ):
            _run_cycle(db_engine)

        db_session.expire_all()
        assert self._membership_count(db_session, article.id) == 1
        assert self._thread_count(db_session) == 1
