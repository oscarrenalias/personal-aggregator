"""Tests for aggregator_clusterer.upsert module (process_classification idempotency)."""
from __future__ import annotations

from sqlalchemy import select

from aggregator_clusterer.classification import ClassificationResult, _TITLE_LIMIT
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

    # --- thread_title regression tests ---

    def test_new_thread_uses_llm_title_when_provided(self, db_session):
        """LLM-synthesized thread_title takes precedence over article headline on creation."""
        src = make_source(db_session, url="https://upsert7.test/feed.xml")
        article = make_article(
            db_session, source_id=src.id, dedup_key="k1",
            clean_title="SHOCKING: AI Will DESTROY Humanity!!!",
        )
        result = ClassificationResult(
            label=ClassificationLabel.new_thread,
            thread_id=None,
            confidence=0.9,
            new_facts=[],
            reason="New story",
            thread_title="AI development raises concerns among researchers",
        )
        process_classification(db_session, article, result, _SETTINGS)
        db_session.flush()

        membership = db_session.execute(
            select(ThreadMembership).where(ThreadMembership.article_id == article.id)
        ).scalar_one()
        thread = db_session.get(Thread, membership.thread_id)
        assert thread.representative_title == "AI development raises concerns among researchers"
        assert thread.representative_title != "SHOCKING: AI Will DESTROY Humanity!!!"

    def test_new_thread_falls_back_to_article_title_when_no_llm_title(self, db_session):
        """Without thread_title, article headline is used as before."""
        src = make_source(db_session, url="https://upsert8.test/feed.xml")
        article = make_article(
            db_session, source_id=src.id, dedup_key="k1",
            clean_title="Original Headline",
        )
        result = ClassificationResult(
            label=ClassificationLabel.new_thread,
            thread_id=None,
            confidence=0.9,
            new_facts=[],
            reason="New story",
            thread_title=None,
        )
        process_classification(db_session, article, result, _SETTINGS)
        db_session.flush()

        membership = db_session.execute(
            select(ThreadMembership).where(ThreadMembership.article_id == article.id)
        ).scalar_one()
        thread = db_session.get(Thread, membership.thread_id)
        assert thread.representative_title == "Original Headline"

    def test_material_update_refreshes_title(self, db_session):
        """same_thread_new_fact with thread_title updates representative_title."""
        src = make_source(db_session, url="https://upsert9.test/feed.xml")
        thread = make_thread(db_session, title="Initial thread title")
        article = make_article(db_session, source_id=src.id, dedup_key="k1")
        result = ClassificationResult(
            label=ClassificationLabel.same_thread_new_fact,
            thread_id=thread.id,
            confidence=0.85,
            new_facts=["New development X"],
            reason="Adds new info",
            thread_title="Thread title updated with new development",
        )
        process_classification(db_session, article, result, _SETTINGS)
        db_session.flush()
        db_session.refresh(thread)

        assert thread.representative_title == "Thread title updated with new development"

    def test_correction_refreshes_title(self, db_session):
        """correction_or_clarification with thread_title updates representative_title."""
        src = make_source(db_session, url="https://upsert10.test/feed.xml")
        thread = make_thread(db_session, title="Story with erroneous figure")
        article = make_article(db_session, source_id=src.id, dedup_key="k1")
        result = ClassificationResult(
            label=ClassificationLabel.correction_or_clarification,
            thread_id=thread.id,
            confidence=0.95,
            new_facts=["Correction: figure corrected to 50"],
            reason="Corrects prior reporting",
            thread_title="Story corrects earlier figure to 50",
        )
        process_classification(db_session, article, result, _SETTINGS)
        db_session.flush()
        db_session.refresh(thread)

        assert thread.representative_title == "Story corrects earlier figure to 50"

    def test_new_thread_fallback_article_title_truncated_at_word_boundary(self, db_session):
        """When no LLM thread_title is provided, a long article clean_title is word-boundary truncated."""
        src = make_source(db_session, url="https://upsert-trunc.test/feed.xml")
        # Craft a clean_title longer than _TITLE_LIMIT with spaces so word-boundary truncation applies.
        long_clean_title = "UK to ban social media for under-16s, with exemptions for messaging apps and new AI chatbots"
        article = make_article(
            db_session, source_id=src.id, dedup_key="trunc1",
            clean_title=long_clean_title,
        )
        result = ClassificationResult(
            label=ClassificationLabel.new_thread,
            thread_id=None,
            confidence=0.9,
            new_facts=[],
            reason="New story",
            thread_title=None,  # no LLM title; must fall back to clean_title with truncation
        )
        process_classification(db_session, article, result, _SETTINGS)
        db_session.flush()

        membership = db_session.execute(
            select(ThreadMembership).where(ThreadMembership.article_id == article.id)
        ).scalar_one()
        thread = db_session.get(Thread, membership.thread_id)
        assert thread.representative_title is not None
        assert len(thread.representative_title) <= _TITLE_LIMIT + 1
        assert thread.representative_title.endswith("…")
        # Must not be a mid-word cut: the char before '…' should complete a word (not be mid-token)
        assert not thread.representative_title[:-1].endswith(" ")

    def test_duplicate_does_not_change_title(self, db_session):
        """DedupResult (duplicate path) leaves representative_title unchanged."""
        src = make_source(db_session, url="https://upsert11.test/feed.xml")
        thread = make_thread(db_session, title="Stable thread title")
        article = make_article(db_session, source_id=src.id, dedup_key="k1")
        result = DedupResult(
            thread_id=thread.id,
            classification_label=ClassificationLabel.same_thread_duplicate,
        )
        process_classification(db_session, article, result, _SETTINGS)
        db_session.flush()
        db_session.refresh(thread)

        assert thread.representative_title == "Stable thread title"

    def test_background_article_does_not_change_title(self, db_session):
        """same_thread_background_only does not refresh representative_title."""
        src = make_source(db_session, url="https://upsert12.test/feed.xml")
        thread = make_thread(db_session, title="Stable thread title")
        article = make_article(db_session, source_id=src.id, dedup_key="k1")
        result = ClassificationResult(
            label=ClassificationLabel.same_thread_background_only,
            thread_id=thread.id,
            confidence=0.7,
            new_facts=[],
            reason="Background only",
            thread_title="This should be ignored",
        )
        process_classification(db_session, article, result, _SETTINGS)
        db_session.flush()
        db_session.refresh(thread)

        assert thread.representative_title == "Stable thread title"
