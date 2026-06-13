"""Tests for aggregator_clusterer.dedup module."""
from __future__ import annotations

from datetime import datetime, timezone

from aggregator_clusterer.config import ClustererSettings
from aggregator_clusterer.dedup import _normalize_title, check_duplicate
from aggregator_common.models import ClassificationLabel, ThreadMembership

from .conftest import make_article, make_source, make_thread

_SETTINGS = ClustererSettings()


class TestNormalizeTitle:
    def test_punctuation_only_gives_empty_frozenset(self):
        assert _normalize_title("---") == frozenset()

    def test_strips_punctuation_and_lowercases(self):
        tokens = _normalize_title("Hello, World!")
        assert "hello" in tokens
        assert "world" in tokens


class TestCheckDuplicate:
    def test_url_exact_match_returns_duplicate(self, db_session):
        src = make_source(db_session, url="https://dedup1.test/feed.xml")
        article_url = "https://dedup1.test/article/1"
        existing = make_article(
            db_session, source_id=src.id, dedup_key="k1",
            raw_payload={"link": article_url},
        )
        thread = make_thread(db_session)
        db_session.add(ThreadMembership(
            thread_id=thread.id,
            article_id=existing.id,
            classification_label=ClassificationLabel.new_thread.value,
            suppressed=False,
            assigned_at=datetime.now(tz=timezone.utc),
        ))
        db_session.commit()

        new_art = make_article(
            db_session, source_id=src.id, dedup_key="k2",
            raw_payload={"link": article_url},
        )
        result = check_duplicate(db_session, new_art, _SETTINGS)
        assert result is not None
        assert result.thread_id == thread.id
        assert result.classification_label == ClassificationLabel.same_thread_duplicate

    def test_url_miss_returns_none_when_no_threads(self, db_session):
        src = make_source(db_session, url="https://dedup2.test/feed.xml")
        article = make_article(
            db_session, source_id=src.id, dedup_key="k1",
            raw_payload={"link": "https://dedup2.test/article/1"},
        )
        result = check_duplicate(db_session, article, _SETTINGS)
        assert result is None

    def test_url_miss_unmatched_returns_none(self, db_session):
        src = make_source(db_session, url="https://dedup3.test/feed.xml")
        # Distinct titles so neither the URL nor the title-Jaccard check matches —
        # this isolates the genuinely-unmatched path (make_article defaults both to
        # the same feed_title, which would otherwise trip the title-dedup check).
        existing = make_article(
            db_session, source_id=src.id, dedup_key="k1",
            feed_title="Apple announces record quarterly earnings beat",
            raw_payload={"link": "https://dedup3.test/article/OLD"},
        )
        thread = make_thread(db_session)
        db_session.add(ThreadMembership(
            thread_id=thread.id,
            article_id=existing.id,
            classification_label=ClassificationLabel.new_thread.value,
            suppressed=False,
            assigned_at=datetime.now(tz=timezone.utc),
        ))
        db_session.commit()

        new_art = make_article(
            db_session, source_id=src.id, dedup_key="k2",
            feed_title="Helsinki tram strike enters its second week",
            raw_payload={"link": "https://dedup3.test/article/NEW"},
        )
        result = check_duplicate(db_session, new_art, _SETTINGS)
        assert result is None

    def test_title_above_jaccard_threshold_returns_duplicate(self, db_session):
        src = make_source(db_session, url="https://dedup4.test/feed.xml")
        title_a = "Apple Announces iPhone Release Next Month"
        title_b = "Apple Announces iPhone Release Next Month Event"

        existing = make_article(
            db_session, source_id=src.id, dedup_key="k1",
            clean_title=title_a, raw_payload={},
        )
        thread = make_thread(db_session)
        db_session.add(ThreadMembership(
            thread_id=thread.id,
            article_id=existing.id,
            classification_label=ClassificationLabel.new_thread.value,
            suppressed=False,
            assigned_at=datetime.now(tz=timezone.utc),
        ))
        db_session.commit()

        # threshold=0.7; these two titles share most tokens → above threshold
        settings = ClustererSettings(clusterer_title_jaccard_threshold=0.7)
        new_art = make_article(
            db_session, source_id=src.id, dedup_key="k2",
            clean_title=title_b, raw_payload={},
        )
        result = check_duplicate(db_session, new_art, settings)
        assert result is not None
        assert result.classification_label == ClassificationLabel.same_thread_duplicate

    def test_title_below_jaccard_threshold_returns_none(self, db_session):
        src = make_source(db_session, url="https://dedup5.test/feed.xml")
        existing = make_article(
            db_session, source_id=src.id, dedup_key="k1",
            clean_title="Quantum Computing Achieves Breakthrough Milestone",
            raw_payload={},
        )
        thread = make_thread(db_session)
        db_session.add(ThreadMembership(
            thread_id=thread.id,
            article_id=existing.id,
            classification_label=ClassificationLabel.new_thread.value,
            suppressed=False,
            assigned_at=datetime.now(tz=timezone.utc),
        ))
        db_session.commit()

        settings = ClustererSettings(clusterer_title_jaccard_threshold=0.7)
        new_art = make_article(
            db_session, source_id=src.id, dedup_key="k2",
            clean_title="Celebrity Spotted at Film Premiere in London",
            raw_payload={},
        )
        result = check_duplicate(db_session, new_art, settings)
        assert result is None

    def test_none_article_url_skips_url_check(self, db_session):
        src = make_source(db_session, url="https://dedup6.test/feed.xml")
        article = make_article(
            db_session, source_id=src.id, dedup_key="k1",
            raw_payload={},  # no "link" or "url" key
            clean_title=None,
            feed_title=None,
        )
        result = check_duplicate(db_session, article, _SETTINGS)
        assert result is None

    def test_none_article_title_skips_title_dedup(self, db_session):
        src = make_source(db_session, url="https://dedup7.test/feed.xml")
        existing = make_article(
            db_session, source_id=src.id, dedup_key="k1",
            clean_title="Some Existing Article Title Here",
            raw_payload={},
        )
        thread = make_thread(db_session)
        db_session.add(ThreadMembership(
            thread_id=thread.id,
            article_id=existing.id,
            classification_label=ClassificationLabel.new_thread.value,
            suppressed=False,
            assigned_at=datetime.now(tz=timezone.utc),
        ))
        db_session.commit()

        # Article with no title — title dedup path skipped
        new_art = make_article(
            db_session, source_id=src.id, dedup_key="k2",
            clean_title=None, feed_title=None, raw_payload={},
        )
        result = check_duplicate(db_session, new_art, _SETTINGS)
        assert result is None

    def test_empty_tokens_after_normalization_skips_title_check(self, db_session):
        src = make_source(db_session, url="https://dedup8.test/feed.xml")
        existing = make_article(
            db_session, source_id=src.id, dedup_key="k1",
            clean_title="Normal Article Title Here",
            raw_payload={},
        )
        thread = make_thread(db_session)
        db_session.add(ThreadMembership(
            thread_id=thread.id,
            article_id=existing.id,
            classification_label=ClassificationLabel.new_thread.value,
            suppressed=False,
            assigned_at=datetime.now(tz=timezone.utc),
        ))
        db_session.commit()

        # Title that normalizes to empty token set
        new_art = make_article(
            db_session, source_id=src.id, dedup_key="k2",
            clean_title="---", raw_payload={},
        )
        result = check_duplicate(db_session, new_art, _SETTINGS)
        assert result is None
