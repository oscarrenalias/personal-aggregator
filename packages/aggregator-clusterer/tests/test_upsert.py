"""Tests for aggregator_clusterer.upsert module (process_classification idempotency)."""
from __future__ import annotations

from sqlalchemy import select

from aggregator_clusterer.classification import ClassificationResult
from aggregator_clusterer.config import ClustererSettings
from aggregator_clusterer.dedup import DedupResult
from aggregator_clusterer.upsert import process_classification
from aggregator_common.models import ClassificationLabel, Thread, ThreadMembership

from .conftest import make_article, make_source, make_thread

_SETTINGS = ClustererSettings()


class TestProcessClassification:
    def test_new_thread_creates_thread_and_membership(self, db_session):
        src = make_source(db_session, url="https://upsert1.test/feed.xml")
        article = make_article(
            db_session, source_id=src.id, dedup_key="k1",
            clean_title="New Story", summary="Summary text.",
        )
        result = ClassificationResult(
            label=ClassificationLabel.new_thread,
            thread_id=None,
            confidence=0.9,
            new_facts=[],
            reason="Distinct story",
        )
        process_classification(db_session, article, result, _SETTINGS)
        db_session.flush()

        memberships = db_session.execute(
            select(ThreadMembership).where(ThreadMembership.article_id == article.id)
        ).scalars().all()
        assert len(memberships) == 1
        assert memberships[0].suppressed is False

        thread = db_session.get(Thread, memberships[0].thread_id)
        assert thread is not None
        assert thread.representative_title == "New Story"

    def test_same_thread_new_fact_appends_facts_and_delta(self, db_session):
        src = make_source(db_session, url="https://upsert2.test/feed.xml")
        thread = make_thread(db_session, title="Ongoing Thread", known_facts=["Initial fact"])
        article = make_article(
            db_session, source_id=src.id, dedup_key="k1",
            clean_title="Update Article",
        )
        result = ClassificationResult(
            label=ClassificationLabel.same_thread_new_fact,
            thread_id=thread.id,
            confidence=0.85,
            new_facts=["New fact A", "New fact B"],
            reason="Adds new information",
        )
        process_classification(db_session, article, result, _SETTINGS)
        db_session.flush()
        db_session.refresh(thread)

        assert "New fact A" in thread.known_facts
        assert "New fact B" in thread.known_facts
        assert "Initial fact" in thread.known_facts
        assert thread.deltas is not None and len(thread.deltas) >= 1

    def test_same_thread_duplicate_creates_suppressed_membership(self, db_session):
        src = make_source(db_session, url="https://upsert3.test/feed.xml")
        thread = make_thread(db_session)
        article = make_article(db_session, source_id=src.id, dedup_key="k1")
        result = DedupResult(
            thread_id=thread.id,
            classification_label=ClassificationLabel.same_thread_duplicate,
        )
        process_classification(db_session, article, result, _SETTINGS)
        db_session.flush()

        membership = db_session.execute(
            select(ThreadMembership).where(ThreadMembership.article_id == article.id)
        ).scalar_one()
        assert membership.suppressed is True

    def test_correction_appends_facts_and_creates_delta(self, db_session):
        src = make_source(db_session, url="https://upsert4.test/feed.xml")
        thread = make_thread(db_session, title="Story with Error")
        article = make_article(db_session, source_id=src.id, dedup_key="k1")
        result = ClassificationResult(
            label=ClassificationLabel.correction_or_clarification,
            thread_id=thread.id,
            confidence=0.95,
            new_facts=["Correction: figure was 50, not 500"],
            reason="Corrects earlier reporting",
        )
        process_classification(db_session, article, result, _SETTINGS)
        db_session.flush()
        db_session.refresh(thread)

        assert "Correction: figure was 50, not 500" in thread.known_facts
        assert thread.deltas and len(thread.deltas) >= 1
        assert thread.deltas[-1]["label"] == "correction_or_clarification"

    def test_process_classification_idempotent_exactly_one_membership(self, db_session):
        src = make_source(db_session, url="https://upsert5.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="k1")
        result = ClassificationResult(
            label=ClassificationLabel.new_thread,
            thread_id=None,
            confidence=0.8,
            new_facts=[],
            reason="",
        )
        # Call process_classification twice for the same article
        process_classification(db_session, article, result, _SETTINGS)
        db_session.flush()
        process_classification(db_session, article, result, _SETTINGS)
        db_session.flush()

        count = db_session.execute(
            select(ThreadMembership).where(ThreadMembership.article_id == article.id)
        ).scalars().all()
        assert len(count) == 1

    def test_new_thread_source_diversity_single_source(self, db_session):
        src = make_source(db_session, url="https://upsert6.test/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="k1")
        result = ClassificationResult(
            label=ClassificationLabel.new_thread,
            thread_id=None,
            confidence=0.8,
            new_facts=[],
            reason="",
        )
        process_classification(db_session, article, result, _SETTINGS)
        db_session.flush()

        membership = db_session.execute(
            select(ThreadMembership).where(ThreadMembership.article_id == article.id)
        ).scalar_one()
        thread = db_session.get(Thread, membership.thread_id)
        assert thread.source_diversity == 1.0
