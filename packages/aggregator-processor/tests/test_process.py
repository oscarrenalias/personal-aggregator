"""Integration tests for the per-article processing pipeline and loop operations.

Covers:
- feed-first path: no fetch when feed content >= processor_feed_content_min_chars
- thin-feed path: page fetch + extraction stored in clean_text
- FetchError fallback: article reaches pending_ranking via feed-derived text
- failure + retry backoff: intermediate state, then failed_processing at exhaustion
- skip: content < processor_min_content_chars → skipped with reason
- concurrent claim: SKIP LOCKED ensures no overlap between two workers
- reaper: stale claimed article is released and re-claimable
- run_once: prints summary line with correct counts
- per-article isolation: one article failing does not prevent others completing
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from aggregator_common.claim import claim_batch, reap_stale_claims
from aggregator_common.models import Article
from aggregator_common.state import ArticleStatus
from aggregator_processor.extract import ExtractionResult
from aggregator_processor.fetch import FetchError
from aggregator_processor.loop import run_once
from aggregator_processor.process import process_article

from .conftest import make_article, make_source


def _good_result(clean_text: str | None = None) -> ExtractionResult:
    text = clean_text if clean_text is not None else ("word " * 60).rstrip()  # 300 chars > 200 min
    return ExtractionResult(
        clean_text=text,
        clean_title="Test Article Title",
        author="Test Author",
        published_at=None,
        language="en",
        excerpt=text[:100] if text else None,
        word_count=len(text.split()) if text else 0,
    )


@pytest.fixture
def src(db_session):
    return make_source(db_session, url="https://proc.example.com/feed.xml")


# ─── Feed-first ─────────────────────────────────────────────────────────────


class TestFeedFirst:
    def test_no_fetch_when_feed_content_sufficient(self, db_session, src, processor_settings):
        """fetch_page must never be called when feed content >= processor_feed_content_min_chars."""
        feed_text = "word " * 400  # 2 000 chars > 1 500 default minimum
        raw_payload = {"content": [{"value": feed_text}]}
        article = make_article(
            db_session, source_id=src.id, dedup_key="ff-nofetch", raw_payload=raw_payload
        )

        with (
            patch("aggregator_processor.process.fetch_page") as mock_fetch,
            patch("aggregator_processor.process.extract_content", return_value=_good_result(feed_text)),
            patch("aggregator_processor.process.update_search_vector"),
        ):
            process_article(article.id, processor_settings)

        mock_fetch.assert_not_called()
        db_session.expire(article)
        db_session.refresh(article)
        assert article.status == ArticleStatus.pending_ranking


# ─── Thin-feed ──────────────────────────────────────────────────────────────


class TestThinFeed:
    def test_page_text_stored_in_clean_text(self, db_session, src, processor_settings):
        """When feed is thin, fetched page text must be extracted and stored in clean_text."""
        raw_payload = {"link": "https://example.com/article/thin"}  # no content key → thin feed
        article = make_article(
            db_session, source_id=src.id, dedup_key="tf-pagetext", raw_payload=raw_payload
        )

        expected_text = "word " * 60  # 300 chars, sufficient
        page_html = b"<html><body><p>Full article page body.</p></body></html>"

        with (
            patch("aggregator_processor.process.fetch_page", return_value=page_html),
            patch("aggregator_processor.process.extract_content", return_value=_good_result(expected_text)),
            patch("aggregator_processor.process.update_search_vector"),
        ):
            process_article(article.id, processor_settings)

        db_session.expire(article)
        db_session.refresh(article)
        assert article.clean_text == expected_text
        assert article.status == ArticleStatus.pending_ranking


# ─── FetchError fallback ─────────────────────────────────────────────────────


class TestFetchErrorFallback:
    def test_article_reaches_pending_ranking_via_feed_text(self, db_session, src, processor_settings):
        """When fetch raises FetchError but feed has usable content, article reaches pending_ranking."""
        # 300 chars: > 200 min (usable) but < 1 500 threshold (triggers fetch attempt)
        feed_text = "word " * 60
        raw_payload = {"content": [{"value": feed_text}], "link": "https://example.com/article"}
        article = make_article(
            db_session, source_id=src.id, dedup_key="fe-fallback", raw_payload=raw_payload
        )

        with (
            patch("aggregator_processor.process.fetch_page", side_effect=FetchError("connection refused")),
            patch("aggregator_processor.process.extract_content", return_value=_good_result(feed_text)),
            patch("aggregator_processor.process.update_search_vector"),
        ):
            process_article(article.id, processor_settings)

        db_session.expire(article)
        db_session.refresh(article)
        assert article.status == ArticleStatus.pending_ranking
        assert article.clean_text is not None


# ─── Failure + retry backoff ─────────────────────────────────────────────────


class TestFailureAndRetry:
    def test_first_failure_sets_last_error_and_next_retry_at(
        self, db_session, src, monkeypatch
    ):
        """First unhandled exception sets last_error and next_retry_at; article stays pending_processing."""
        monkeypatch.setenv("PROCESSOR_MAX_RETRIES", "3")
        monkeypatch.setenv("PROCESSOR_BACKOFF_BASE_SECONDS", "10")
        from aggregator_processor.config import ProcessorSettings

        settings = ProcessorSettings()
        raw_payload = {"link": "https://example.com/article/fail1"}
        article = make_article(
            db_session, source_id=src.id, dedup_key="fail-first", raw_payload=raw_payload
        )

        # RuntimeError is not caught by _do_process — bubbles up to fail()
        with patch("aggregator_processor.process.fetch_page", side_effect=RuntimeError("unexpected")):
            process_article(article.id, settings)

        db_session.expire(article)
        db_session.refresh(article)
        assert article.retry_count == 1
        assert article.last_error is not None and "RuntimeError" in article.last_error
        assert article.next_retry_at is not None
        assert article.status == ArticleStatus.pending_processing  # still retrying

    def test_retries_exhausted_transitions_to_failed_processing(
        self, db_session, src, monkeypatch
    ):
        """After max_retries failures, article transitions to failed_processing."""
        monkeypatch.setenv("PROCESSOR_MAX_RETRIES", "2")
        monkeypatch.setenv("PROCESSOR_BACKOFF_BASE_SECONDS", "1")
        from aggregator_processor.config import ProcessorSettings

        settings = ProcessorSettings()
        raw_payload = {"link": "https://example.com/article/exhaust"}
        article = make_article(
            db_session, source_id=src.id, dedup_key="fail-exhaust", raw_payload=raw_payload
        )

        with patch("aggregator_processor.process.fetch_page", side_effect=RuntimeError("fail")):
            # First call: retry_count → 1, still pending_processing
            process_article(article.id, settings)
            db_session.expire(article)
            db_session.refresh(article)
            assert article.retry_count == 1
            assert article.status == ArticleStatus.pending_processing
            assert article.next_retry_at is not None

            # Second call: retry_count → 2 >= max_retries=2 → failed_processing
            process_article(article.id, settings)

        db_session.expire(article)
        db_session.refresh(article)
        assert article.retry_count == 2
        assert article.status == ArticleStatus.failed_processing
        assert article.last_error is not None
        assert article.next_retry_at is None


# ─── Skip ────────────────────────────────────────────────────────────────────


class TestSkip:
    def test_short_content_article_skipped_with_reason(self, db_session, src, processor_settings):
        """Extracted content < processor_min_content_chars with no usable feed → skipped."""
        raw_payload = {"link": "https://example.com/article/short"}  # no feed content
        article = make_article(
            db_session, source_id=src.id, dedup_key="skip-short", raw_payload=raw_payload
        )

        short_result = ExtractionResult(
            clean_text="short",  # 5 chars < 200 min
            clean_title="Short Title",
            author=None,
            published_at=None,
            language=None,
            excerpt="short",
            word_count=1,
        )

        with (
            patch("aggregator_processor.process.fetch_page", return_value=b"<html>short</html>"),
            patch("aggregator_processor.process.extract_content", return_value=short_result),
        ):
            process_article(article.id, processor_settings)

        db_session.expire(article)
        db_session.refresh(article)
        assert article.status == ArticleStatus.skipped
        assert article.last_error is not None
        assert "Insufficient" in article.last_error


# ─── Concurrent claim (SKIP LOCKED) ─────────────────────────────────────────


class TestConcurrentClaim:
    def test_skip_locked_no_overlap(self, db_engine, db_session, src):
        """Two concurrent workers must not claim the same article (SKIP LOCKED guarantee).

        Worker 0 acquires locks and holds its transaction open while worker 1 tries to claim
        from the same pool.  SKIP LOCKED means worker 1 must receive only unclaimed rows.
        """
        articles = [
            make_article(db_session, source_id=src.id, dedup_key=f"cc-{i}")
            for i in range(5)
        ]
        all_ids = {a.id for a in articles}

        factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
        now = datetime.now(timezone.utc)
        results: list[set[int]] = [set(), set()]
        errors: list[Exception] = []

        # w0_locked: signals that worker 0 has flushed (row locks are held)
        # w1_done: signals that worker 1 has finished claiming
        w0_locked = threading.Event()
        w1_done = threading.Event()

        def worker_0() -> None:
            session = factory()
            try:
                claimed = claim_batch(
                    session, ArticleStatus.pending_processing, "worker-0", 10, now
                )
                # flush writes the row updates and holds the locks inside the open transaction
                # claim_batch already calls session.flush(), so locks are held here
                results[0] = {a.id for a in claimed}
                w0_locked.set()   # tell worker 1 locks are held
                w1_done.wait(timeout=10)  # hold locks while worker 1 claims
                session.commit()
            except Exception as exc:
                errors.append(exc)
                session.rollback()
                w0_locked.set()  # unblock worker 1 even on error
            finally:
                session.close()

        def worker_1() -> None:
            w0_locked.wait(timeout=10)  # wait until worker 0 holds locks
            session = factory()
            try:
                claimed = claim_batch(
                    session, ArticleStatus.pending_processing, "worker-1", 10, now
                )
                results[1] = {a.id for a in claimed}
                session.commit()
            except Exception as exc:
                errors.append(exc)
                session.rollback()
            finally:
                session.close()
                w1_done.set()  # release worker 0

        t0 = threading.Thread(target=worker_0)
        t1 = threading.Thread(target=worker_1)
        t0.start()
        t1.start()
        t0.join(timeout=15)
        t1.join(timeout=15)

        assert not errors, f"Worker errors: {errors}"
        overlap = results[0] & results[1]
        assert not overlap, f"SKIP LOCKED violated: articles {overlap} claimed by both workers"
        combined = results[0] | results[1]
        assert combined == all_ids, "All articles must be claimed exactly once across both workers"


# ─── Reaper ──────────────────────────────────────────────────────────────────


class TestReaper:
    def test_stale_claim_released_and_reclaimable(self, db_engine, db_session, src):
        """A stale-claimed article must be released by reap_stale_claims and become claimable again."""
        stale_claimed_at = datetime.now(timezone.utc) - timedelta(seconds=700)
        article = make_article(
            db_session,
            source_id=src.id,
            dedup_key="reap-stale",
            claimed_by="dead-worker",
            claimed_at=stale_claimed_at,
        )

        factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
        now = datetime.now(timezone.utc)

        session = factory()
        try:
            reaped = reap_stale_claims(session, 600, now)  # lease=600s, article claimed 700s ago
            session.commit()
        finally:
            session.close()

        assert reaped == 1

        session2 = factory()
        try:
            claimed = claim_batch(
                session2, ArticleStatus.pending_processing, "new-worker", 10, now
            )
            session2.commit()
            claimed_ids = {a.id for a in claimed}
        finally:
            session2.close()

        assert article.id in claimed_ids, "Reaped article must be re-claimable"


# ─── run_once summary output ─────────────────────────────────────────────────


class TestRunOnce:
    def test_prints_summary_line(self, db_session, src, processor_settings, capsys):
        """run_once must print a summary line with processed/failed/skipped counts."""
        feed_text = "word " * 400  # sufficient feed content — avoids real network calls
        raw_payload = {"content": [{"value": feed_text}]}
        make_article(db_session, source_id=src.id, dedup_key="once-1", raw_payload=raw_payload)

        with (
            patch("aggregator_processor.process.extract_content", return_value=_good_result(feed_text)),
            patch("aggregator_processor.process.update_search_vector"),
        ):
            run_once(processor_settings)

        captured = capsys.readouterr()
        assert "run_once:" in captured.out
        assert "processed=" in captured.out
        assert "failed=" in captured.out
        assert "skipped=" in captured.out

    def test_successful_article_counted_as_processed(self, db_session, src, processor_settings, capsys):
        """A successfully processed article must appear in the processed count."""
        feed_text = "word " * 400
        raw_payload = {"content": [{"value": feed_text}]}
        make_article(db_session, source_id=src.id, dedup_key="once-success", raw_payload=raw_payload)

        with (
            patch("aggregator_processor.process.extract_content", return_value=_good_result(feed_text)),
            patch("aggregator_processor.process.update_search_vector"),
        ):
            run_once(processor_settings)

        captured = capsys.readouterr()
        assert "processed=1" in captured.out

    def test_empty_batch_prints_all_zeros(self, processor_settings, capsys):
        """run_once with no pending articles must print processed=0 failed=0 skipped=0."""
        run_once(processor_settings)
        captured = capsys.readouterr()
        assert "processed=0" in captured.out
        assert "failed=0" in captured.out
        assert "skipped=0" in captured.out


# ─── Per-article isolation ───────────────────────────────────────────────────


class TestPerArticleIsolation:
    def test_one_failure_does_not_block_others(self, db_session, src, monkeypatch):
        """One article raising during extraction must not prevent the others from completing."""
        monkeypatch.setenv("PROCESSOR_MAX_RETRIES", "1")
        from aggregator_processor.config import ProcessorSettings

        settings = ProcessorSettings()

        feed_text = "word " * 400  # sufficient — no network calls needed
        raw_payload = {"content": [{"value": feed_text}]}
        a1 = make_article(db_session, source_id=src.id, dedup_key="iso-pass-1", raw_payload=raw_payload)
        a2 = make_article(db_session, source_id=src.id, dedup_key="iso-fail-2", raw_payload=raw_payload)
        a3 = make_article(db_session, source_id=src.id, dedup_key="iso-pass-3", raw_payload=raw_payload)

        call_count = [0]
        lock = threading.Lock()

        def mock_extract(_html, _fallback):
            with lock:
                call_count[0] += 1
                n = call_count[0]
            if n == 2:
                raise RuntimeError("Simulated extraction failure for isolation test")
            return _good_result(feed_text)

        with (
            patch("aggregator_processor.process.extract_content", side_effect=mock_extract),
            patch("aggregator_processor.process.update_search_vector"),
        ):
            run_once(settings)

        db_session.expire_all()
        statuses = [
            db_session.get(Article, a1.id).status,
            db_session.get(Article, a2.id).status,
            db_session.get(Article, a3.id).status,
        ]

        assert statuses.count(ArticleStatus.pending_ranking) == 2, (
            f"Expected 2 pending_ranking, got {statuses}"
        )
        assert ArticleStatus.failed_processing in statuses, (
            f"Expected 1 failed_processing, got {statuses}"
        )
