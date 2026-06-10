import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from aggregator_common.models import Article, Source

# ---------------------------------------------------------------------------
# Minimal mock httpx infrastructure reused by TestRunOnce
# ---------------------------------------------------------------------------

_MINIMAL_RSS_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <link>https://run-once-test.example.com</link>
    <item>
      <title>Test Article</title>
      <link>https://run-once-test.example.com/article/1</link>
      <guid>https://run-once-test.example.com/article/1</guid>
    </item>
  </channel>
</rss>"""


class _FeedResponse:
    def __init__(self, body: bytes = _MINIMAL_RSS_FEED):
        self.status_code = 200
        self._body = body
        self.headers = {}

    def iter_bytes(self):
        yield self._body


class _FeedMockClient:
    def __init__(self, body: bytes = _MINIMAL_RSS_FEED):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    @contextmanager
    def stream(self, method, url, headers=None, **kwargs):
        yield _FeedResponse(self._body)


@pytest.fixture
def source(session):
    s = Source(
        name="Loop Test Source",
        feed_url="https://loop.test.example.com/feed.xml",
        enabled=True,
        refresh_interval_seconds=3600,
        consecutive_failures=0,
        priority=0,
    )
    session.add(s)
    session.flush()
    return s


class TestQueryDueSources:
    """Tests for _query_due_sources scheduling logic via a real DB session."""

    def test_source_with_null_next_check_at_is_selected(self, session, db_url, source):
        from aggregator_retriever.loop import _query_due_sources

        ids = _query_due_sources(session, set())
        assert source.id in ids

    def test_null_next_check_at_ordered_before_overdue(self, session, db_url):
        from aggregator_retriever.loop import _query_due_sources

        past = datetime.now(tz=timezone.utc) - timedelta(hours=1)
        s_null = Source(
            name="Null",
            feed_url="https://null.loop.example.com/feed.xml",
            enabled=True,
            priority=0,
        )
        s_past = Source(
            name="Past",
            feed_url="https://past.loop.example.com/feed.xml",
            enabled=True,
            next_check_at=past,
            priority=0,
        )
        session.add_all([s_null, s_past])
        session.flush()

        ids = _query_due_sources(session, set())
        assert s_null.id in ids
        assert s_past.id in ids
        assert ids.index(s_null.id) < ids.index(s_past.id)

    def test_in_flight_source_is_excluded(self, session, db_url, source):
        from aggregator_retriever.loop import _query_due_sources

        ids = _query_due_sources(session, {source.id})
        assert source.id not in ids

    def test_future_next_check_at_not_selected(self, session, db_url):
        from aggregator_retriever.loop import _query_due_sources

        future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
        s = Source(
            name="Future",
            feed_url="https://future.loop.example.com/feed.xml",
            enabled=True,
            next_check_at=future,
        )
        session.add(s)
        session.flush()

        ids = _query_due_sources(session, set())
        assert s.id not in ids

    def test_disabled_source_not_selected(self, session, db_url):
        from aggregator_retriever.loop import _query_due_sources

        s = Source(
            name="Disabled",
            feed_url="https://disabled.loop.example.com/feed.xml",
            enabled=False,
        )
        session.add(s)
        session.flush()

        ids = _query_due_sources(session, set())
        assert s.id not in ids

    def test_higher_priority_source_ordered_first(self, session, db_url):
        from aggregator_retriever.loop import _query_due_sources

        s_low = Source(
            name="LowPriority",
            feed_url="https://low.loop.example.com/feed.xml",
            enabled=True,
            priority=0,
        )
        s_high = Source(
            name="HighPriority",
            feed_url="https://high.loop.example.com/feed.xml",
            enabled=True,
            priority=10,
        )
        session.add_all([s_low, s_high])
        session.flush()

        ids = _query_due_sources(session, set())
        assert ids.index(s_high.id) < ids.index(s_low.id)


class TestRunOnce:
    """Integration tests for run_once using testcontainer DB and stubbed httpx."""

    def test_once_polls_only_due_sources(self, db_url, db_session_factory):
        """--once fetches due sources and skips sources whose next_check_at is in the future."""
        from sqlalchemy import select

        from aggregator_retriever.config import Settings
        from aggregator_retriever.loop import run_once

        s = db_session_factory()
        future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
        due_src = Source(
            name="RunOnce-Due",
            feed_url="https://runonce-due.example.com/feed.xml",
            enabled=True,
            refresh_interval_seconds=3600,
        )
        not_due_src = Source(
            name="RunOnce-NotDue",
            feed_url="https://runonce-notdue.example.com/feed.xml",
            enabled=True,
            refresh_interval_seconds=3600,
            next_check_at=future,
        )
        s.add_all([due_src, not_due_src])
        s.commit()
        due_id = due_src.id
        not_due_id = not_due_src.id
        s.close()

        settings = Settings()
        with patch("httpx.Client", return_value=_FeedMockClient()):
            run_once(settings)

        s2 = db_session_factory()
        due_articles = s2.execute(select(Article).where(Article.source_id == due_id)).scalars().all()
        not_due_articles = s2.execute(select(Article).where(Article.source_id == not_due_id)).scalars().all()
        s2.close()

        assert len(due_articles) >= 1, "Due source should have articles"
        assert all(a.status == "pending_processing" for a in due_articles)
        assert len(not_due_articles) == 0, "Not-due source must be skipped under plain --once"

    def test_once_source_polls_not_due_source(self, db_url, db_session_factory):
        """--once --source <id> polls the target source regardless of next_check_at."""
        from sqlalchemy import select

        from aggregator_retriever.config import Settings
        from aggregator_retriever.loop import run_once

        s = db_session_factory()
        future = datetime.now(tz=timezone.utc) + timedelta(hours=2)
        not_due_src = Source(
            name="RunOnce-SourceFlag",
            feed_url="https://runonce-sourceflag.example.com/feed.xml",
            enabled=True,
            refresh_interval_seconds=3600,
            next_check_at=future,
        )
        s.add(not_due_src)
        s.commit()
        src_id = not_due_src.id
        s.close()

        settings = Settings()
        with patch("httpx.Client", return_value=_FeedMockClient()):
            run_once(settings, source_id=src_id)

        s2 = db_session_factory()
        articles = s2.execute(select(Article).where(Article.source_id == src_id)).scalars().all()
        s2.close()

        assert len(articles) >= 1, "Source should be polled via --source even when not due"
        assert all(a.status == "pending_processing" for a in articles)

    def test_once_all_polls_all_enabled_sources(self, db_url, db_session_factory):
        """--once --all polls both due and not-due enabled sources."""
        from sqlalchemy import select

        from aggregator_retriever.config import Settings
        from aggregator_retriever.loop import run_once

        s = db_session_factory()
        future = datetime.now(tz=timezone.utc) + timedelta(hours=3)
        all_due_src = Source(
            name="RunOnce-All-Due",
            feed_url="https://runonce-all-due.example.com/feed.xml",
            enabled=True,
            refresh_interval_seconds=3600,
        )
        all_not_due_src = Source(
            name="RunOnce-All-NotDue",
            feed_url="https://runonce-all-notdue.example.com/feed.xml",
            enabled=True,
            refresh_interval_seconds=3600,
            next_check_at=future,
        )
        s.add_all([all_due_src, all_not_due_src])
        s.commit()
        all_due_id = all_due_src.id
        all_not_due_id = all_not_due_src.id
        s.close()

        settings = Settings()
        with patch("httpx.Client", return_value=_FeedMockClient()):
            run_once(settings, all_enabled=True)

        s2 = db_session_factory()
        due_articles = s2.execute(select(Article).where(Article.source_id == all_due_id)).scalars().all()
        not_due_articles = s2.execute(select(Article).where(Article.source_id == all_not_due_id)).scalars().all()
        s2.close()

        assert len(due_articles) >= 1, "Due source should be polled under --all"
        assert all(a.status == "pending_processing" for a in due_articles)
        assert len(not_due_articles) >= 1, "Not-due source should also be polled under --all"
        assert all(a.status == "pending_processing" for a in not_due_articles)


class TestSigterm:
    def test_sigterm_exits_with_code_zero(self, db_url, tmp_path):
        """SIGTERM causes the retriever loop to drain in-flight tasks and exit cleanly."""
        script = tmp_path / "run_loop.py"
        # Print "ready" after imports complete so the test knows signal handlers are about to
        # be installed — avoids the startup-race that causes returncode -15.
        script.write_text(
            f"import os, sys, signal as _sig\n"
            f"from unittest.mock import patch\n"
            f"os.environ['DATABASE_URL'] = {db_url!r}\n"
            f"os.environ['RETRIEVER_POLL_INTERVAL_SECONDS'] = '1'\n"
            f"# Spy on signal.signal so we print 'ready' the moment SIGTERM handler is wired\n"
            f"_orig = _sig.signal\n"
            f"def _spy(sig, h):\n"
            f"    r = _orig(sig, h)\n"
            f"    if sig == _sig.SIGTERM and callable(h):\n"
            f"        print('ready', flush=True)\n"
            f"    return r\n"
            f"_sig.signal = _spy\n"
            f"with patch('aggregator_retriever.loop._query_due_sources', return_value=[]):\n"
            f"    from aggregator_retriever.loop import run\n"
            f"    run()\n"
        )
        proc = subprocess.Popen(
            [sys.executable, str(script)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        # Block until subprocess confirms its SIGTERM handler is installed.
        ready = proc.stdout.readline()
        proc.stdout.close()
        if not ready.strip():
            proc.kill()
            pytest.fail("Subprocess did not emit ready signal before exiting")
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("Retriever process did not exit within 15 s after SIGTERM")
        assert proc.returncode == 0
