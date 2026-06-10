from datetime import datetime, timedelta, timezone

import pytest

from aggregator_common.models import Article, Source
from aggregator_retriever.http import FetchResult
from aggregator_retriever.parse import NormalizedEntry
from aggregator_retriever.persist import (
    insert_articles,
    update_source_failure,
    update_source_success,
)


@pytest.fixture
def settings(db_url):
    from aggregator_retriever.config import Settings as S
    return S()


@pytest.fixture
def source(session):
    s = Source(
        name="Persist Test Source",
        feed_url="https://persist.test.example.com/feed.xml",
        enabled=True,
        refresh_interval_seconds=3600,
        consecutive_failures=0,
    )
    session.add(s)
    session.flush()
    return s


def _entry(dedup_key: str = "entry-1", title: str = "Test Article") -> NormalizedEntry:
    return NormalizedEntry(
        dedup_key=dedup_key,
        feed_url=f"https://persist.test.example.com/{dedup_key}",
        feed_title=title,
        feed_summary="Test summary",
        feed_published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        raw_payload={"title": title, "key": dedup_key},
    )


class TestInsertArticles:
    def test_empty_entries_returns_zero(self, session, source):
        assert insert_articles(session, source.id, []) == 0

    def test_new_articles_returns_correct_count(self, session, source):
        count = insert_articles(session, source.id, [_entry("k1"), _entry("k2")])
        assert count == 2

    def test_dedup_on_conflict_skips_duplicate(self, session, source):
        first = insert_articles(session, source.id, [_entry("dup")])
        second = insert_articles(session, source.id, [_entry("dup")])
        assert first == 1
        assert second == 0

    def test_idempotent_insert_does_not_raise(self, session, source):
        insert_articles(session, source.id, [_entry("idem")])
        insert_articles(session, source.id, [_entry("idem")])  # must not raise

    def test_new_articles_have_pending_processing_status(self, session, source):
        insert_articles(session, source.id, [_entry("status-check")])
        article = (
            session.query(Article)
            .filter_by(dedup_key="status-check", source_id=source.id)
            .one()
        )
        assert article.status == "pending_processing"

    def test_raw_payload_stored_as_dict(self, session, source):
        e = _entry("payload-check")
        insert_articles(session, source.id, [e])
        article = (
            session.query(Article)
            .filter_by(dedup_key="payload-check", source_id=source.id)
            .one()
        )
        assert article.raw_payload == e.raw_payload

    def test_partial_batch_with_mixed_new_and_dup(self, session, source):
        insert_articles(session, source.id, [_entry("existing")])
        count = insert_articles(session, source.id, [_entry("existing"), _entry("fresh")])
        assert count == 1


class TestUpdateSourceSuccess:
    def test_clears_consecutive_failures(self, session, source):
        source.consecutive_failures = 5
        update_source_success(session, source, FetchResult(body=b"<feed/>"))
        assert source.consecutive_failures == 0

    def test_sets_last_checked_at(self, session, source):
        before = datetime.now(tz=timezone.utc)
        update_source_success(session, source, FetchResult(body=b"<feed/>"))
        assert source.last_checked_at >= before

    def test_sets_next_check_at_from_refresh_interval(self, session, source):
        before = datetime.now(tz=timezone.utc)
        source.refresh_interval_seconds = 3600
        update_source_success(session, source, FetchResult(body=b"<feed/>"))
        assert source.next_check_at >= before + timedelta(seconds=3600 - 1)

    def test_304_preserves_etag(self, session, source):
        source.etag = '"old-etag"'
        update_source_success(session, source, FetchResult(not_modified=True))
        assert source.etag == '"old-etag"'

    def test_304_preserves_last_modified(self, session, source):
        source.last_modified = "Mon, 01 Jan 2024 00:00:00 GMT"
        update_source_success(session, source, FetchResult(not_modified=True))
        assert source.last_modified == "Mon, 01 Jan 2024 00:00:00 GMT"

    def test_200_updates_etag(self, session, source):
        source.etag = '"stale"'
        update_source_success(session, source, FetchResult(body=b"<feed/>", etag='"fresh"'))
        assert source.etag == '"fresh"'

    def test_200_updates_last_modified(self, session, source):
        update_source_success(
            session,
            source,
            FetchResult(body=b"<feed/>", last_modified="Tue, 02 Jan 2024 00:00:00 GMT"),
        )
        assert source.last_modified == "Tue, 02 Jan 2024 00:00:00 GMT"

    def test_clears_last_error(self, session, source):
        source.last_error = "prior error"
        update_source_success(session, source, FetchResult(body=b"<feed/>"))
        assert source.last_error is None


class TestUpdateSourceFailure:
    def test_increments_consecutive_failures(self, session, source, settings):
        update_source_failure(session, source, "HTTP 503", settings)
        assert source.consecutive_failures == 1

    def test_sets_last_error(self, session, source, settings):
        update_source_failure(session, source, "HTTP 503", settings)
        assert source.last_error == "HTTP 503"

    def test_backoff_jitter_bounds_first_failure(self, session, source, settings):
        base = settings.retriever_backoff_base_seconds  # default 60
        cap = settings.retriever_backoff_cap_seconds
        expected_base = min(cap, base * (2 ** 0))  # n=1
        before = datetime.now(tz=timezone.utc)
        update_source_failure(session, source, "err", settings)
        after = datetime.now(tz=timezone.utc)
        min_next = before + timedelta(seconds=expected_base * 0.9)
        max_next = after + timedelta(seconds=expected_base * 1.1)
        assert min_next <= source.next_check_at <= max_next

    def test_backoff_grows_exponentially(self, session, source, settings):
        base = settings.retriever_backoff_base_seconds
        cap = settings.retriever_backoff_cap_seconds
        source.consecutive_failures = 2  # will become 3, delay = base * 2^2 = 4*base
        expected_base = min(cap, base * (2 ** 2))
        before = datetime.now(tz=timezone.utc)
        update_source_failure(session, source, "err", settings)
        after = datetime.now(tz=timezone.utc)
        min_next = before + timedelta(seconds=expected_base * 0.9)
        max_next = after + timedelta(seconds=expected_base * 1.1)
        assert min_next <= source.next_check_at <= max_next

    def test_backoff_caps_at_maximum(self, session, source, settings):
        cap = settings.retriever_backoff_cap_seconds
        source.consecutive_failures = 100  # high enough that base_delay hits cap
        before = datetime.now(tz=timezone.utc)
        update_source_failure(session, source, "err", settings)
        after = datetime.now(tz=timezone.utc)
        min_next = before + timedelta(seconds=cap * 0.9)
        max_next = after + timedelta(seconds=cap)
        assert min_next <= source.next_check_at <= max_next

    def test_auto_disable_at_threshold(self, session, source, settings):
        threshold = settings.retriever_max_source_failures
        source.consecutive_failures = threshold - 1
        update_source_failure(session, source, "err", settings)
        assert source.enabled is False

    def test_not_disabled_below_threshold(self, session, source, settings):
        threshold = settings.retriever_max_source_failures
        source.consecutive_failures = threshold - 2
        update_source_failure(session, source, "err", settings)
        assert source.enabled is True
