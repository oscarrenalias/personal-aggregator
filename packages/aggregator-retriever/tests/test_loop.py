import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

from aggregator_common.models import Source


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
