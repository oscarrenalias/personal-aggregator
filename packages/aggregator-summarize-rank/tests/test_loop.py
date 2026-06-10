"""Integration tests for loop.py and __main__.py.

Covers:
- run_once prints ranked/failed/skipped counts
- run_once with empty DB prints all zeros
- reap_stale_claims fires before claim_batch (stale article becomes available)
- concurrent run_once calls process each article exactly once (SKIP LOCKED)
- SIGTERM handler fires stop_event, run() exits cleanly
- __main__ --once flag calls run_once; daemon path calls run
- configure_logging called before loop dispatch
"""

from __future__ import annotations

import json
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from aggregator_common.models import Article
from aggregator_common.state import ArticleStatus

from .conftest import make_article, make_source


def _make_litellm_response(score: int = 75) -> SimpleNamespace:
    content = json.dumps({
        "summary": "A good article.",
        "topics": ["tech"],
        "importance_score": score,
        "importance_reason": "Relevant.",
    })
    message = SimpleNamespace(content=content, parsed=None)
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(prompt_tokens=80, completion_tokens=40)
    return SimpleNamespace(choices=[choice], usage=usage, model="claude-sonnet-4-6")


_ENOUGH_TEXT = "word " * 60  # 300 chars > 200 minimum


@pytest.fixture
def src(db_session):
    return make_source(db_session, url="https://loop.example.com/feed.xml")


# ─── run_once output ──────────────────────────────────────────────────────────


class TestRunOnce:
    def test_prints_ranked_failed_skipped_counts(self, db_session, src, settings, capsys):
        make_article(
            db_session, source_id=src.id, dedup_key="once-1", clean_text=_ENOUGH_TEXT
        )

        with (
            patch("litellm.completion", return_value=_make_litellm_response()),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            from aggregator_summarize_rank.loop import run_once
            run_once(settings)

        out = capsys.readouterr().out
        assert "run_once:" in out
        assert "ranked=" in out
        assert "failed=" in out
        assert "skipped=" in out

    def test_successful_article_counted_as_ranked(self, db_session, src, settings, capsys):
        make_article(
            db_session, source_id=src.id, dedup_key="once-ranked", clean_text=_ENOUGH_TEXT
        )

        with (
            patch("litellm.completion", return_value=_make_litellm_response()),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            from aggregator_summarize_rank.loop import run_once
            run_once(settings)

        out = capsys.readouterr().out
        assert "ranked=1" in out

    def test_skipped_article_counted_as_skipped(self, db_session, src, settings, capsys):
        make_article(
            db_session,
            source_id=src.id,
            dedup_key="once-skip",
            clean_text=None,
            excerpt=None,
            feed_summary=None,
        )

        from aggregator_summarize_rank.loop import run_once
        run_once(settings)

        out = capsys.readouterr().out
        assert "skipped=1" in out
        assert "ranked=0" in out

    def test_empty_db_prints_all_zeros(self, settings, capsys):
        from aggregator_summarize_rank.loop import run_once
        run_once(settings)

        out = capsys.readouterr().out
        assert "ranked=0" in out
        assert "failed=0" in out
        assert "skipped=0" in out


# ─── Reap before claim ────────────────────────────────────────────────────────


class TestReapBeforeClaim:
    def test_stale_claim_reaped_then_processed(self, db_session, src, settings, capsys):
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=700)
        article = make_article(
            db_session,
            source_id=src.id,
            dedup_key="reap-then-rank",
            clean_text=_ENOUGH_TEXT,
            claimed_by="dead-worker",
            claimed_at=stale_time,
        )

        with (
            patch("litellm.completion", return_value=_make_litellm_response()),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            from aggregator_summarize_rank.loop import run_once
            run_once(settings)

        db_session.expire(article)
        db_session.refresh(article)
        assert article.status == ArticleStatus.ready

    def test_fresh_claimed_article_not_poached(self, db_session, src, settings, capsys):
        """An article with a fresh (non-stale) claim must not be re-claimed."""
        fresh_time = datetime.now(timezone.utc) - timedelta(seconds=10)
        article = make_article(
            db_session,
            source_id=src.id,
            dedup_key="fresh-claim",
            clean_text=_ENOUGH_TEXT,
            claimed_by="live-worker",
            claimed_at=fresh_time,
        )

        from aggregator_summarize_rank.loop import run_once
        run_once(settings)

        db_session.expire(article)
        db_session.refresh(article)
        assert article.claimed_by == "live-worker"
        assert article.status == ArticleStatus.pending_ranking


# ─── Concurrent claim safety (SKIP LOCKED) ────────────────────────────────────


class TestConcurrentRunOnce:
    def test_two_concurrent_workers_process_each_article_exactly_once(
        self, db_session, src, db_engine, settings
    ):
        """Two concurrent run_once calls must collectively process every article exactly once.

        SKIP LOCKED ensures no article is claimed by both workers simultaneously.
        """
        articles = [
            make_article(
                db_session,
                source_id=src.id,
                dedup_key=f"conc-{i}",
                clean_text=_ENOUGH_TEXT,
            )
            for i in range(6)
        ]
        article_ids = {a.id for a in articles}

        errors: list[Exception] = []

        def _worker():
            try:
                from aggregator_summarize_rank.loop import run_once
                run_once(settings)
            except Exception as exc:
                errors.append(exc)

        # Patch once in the main thread so the mock stays installed for the whole
        # concurrent run. Patching the global litellm.completion from inside each
        # worker thread races: one thread restores the original while the other's
        # pool sub-threads are still calling it.
        with (
            patch("litellm.completion", return_value=_make_litellm_response()),
            patch("litellm.completion_cost", return_value=0.001),
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(_worker) for _ in range(2)]
                for f in futures:
                    f.result()

        assert not errors, f"Worker errors: {errors}"

        factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
        session = factory()
        try:
            rows = {
                row.id: row.status
                for row in session.query(Article).where(Article.id.in_(article_ids)).all()
            }
        finally:
            session.close()

        for aid, status in rows.items():
            assert status in (ArticleStatus.ready, ArticleStatus.skipped), (
                f"Article {aid} ended in unexpected status {status!r}"
            )

        assert set(rows) == article_ids, "Some articles were not processed"


# ─── Worker ID format ─────────────────────────────────────────────────────────


class TestWorkerIdFormat:
    def test_worker_id_contains_hostname_and_pid(self, settings):
        """run_once must claim articles with a worker_id containing hostname and PID."""
        import os
        import socket

        claimed_worker_ids: list[str] = []

        original_claim = None

        def _capture_claim(session, status, worker_id, limit, now):
            claimed_worker_ids.append(worker_id)
            return []

        with patch("aggregator_summarize_rank.loop.claim_batch", side_effect=_capture_claim):
            with patch("aggregator_summarize_rank.loop.reap_stale_claims", return_value=0):
                from aggregator_summarize_rank.loop import run_once
                run_once(settings)

        assert claimed_worker_ids, "claim_batch was never called"
        wid = claimed_worker_ids[0]
        assert socket.gethostname() in wid
        assert str(os.getpid()) in wid


# ─── SIGTERM drain ────────────────────────────────────────────────────────────


class TestSigtermDrain:
    def test_run_exits_cleanly_when_stop_event_set(self, settings):
        """Firing the SIGTERM handler causes run() to exit without processing any articles."""
        captured_handlers: dict[int, object] = {}

        def _capture_signal(signum: int, handler: object) -> None:
            captured_handlers[signum] = handler

        done = threading.Event()
        exception_holder: list[Exception] = []

        def _run_thread():
            try:
                from aggregator_summarize_rank.loop import run
                run(settings)
            except Exception as exc:
                exception_holder.append(exc)
            finally:
                done.set()

        with (
            patch("signal.signal", side_effect=_capture_signal),
            patch("aggregator_summarize_rank.loop.reap_stale_claims", return_value=0),
            patch("aggregator_summarize_rank.loop.claim_batch", return_value=[]),
        ):
            t = threading.Thread(target=_run_thread, daemon=True)
            t.start()

            # Wait for the SIGTERM handler to be registered (up to 2s)
            deadline = time.monotonic() + 2.0
            while signal.SIGTERM not in captured_handlers and time.monotonic() < deadline:
                time.sleep(0.01)

            assert signal.SIGTERM in captured_handlers, "SIGTERM handler was never registered"

            # Fire the SIGTERM handler — this sets stop_event inside run()
            captured_handlers[signal.SIGTERM](signal.SIGTERM, None)  # type: ignore[call-arg]

            assert done.wait(timeout=5), "run() did not exit within 5s after SIGTERM"

        assert not exception_holder, f"run() raised: {exception_holder}"

    def test_sigint_handler_also_registered(self, settings):
        """Both SIGINT and SIGTERM handlers must be registered."""
        captured: dict[int, object] = {}

        def _capture(signum: int, handler: object) -> None:
            captured[signum] = handler

        done = threading.Event()

        def _run_thread():
            try:
                from aggregator_summarize_rank.loop import run
                run(settings)
            finally:
                done.set()

        with (
            patch("signal.signal", side_effect=_capture),
            patch("aggregator_summarize_rank.loop.reap_stale_claims", return_value=0),
            patch("aggregator_summarize_rank.loop.claim_batch", return_value=[]),
        ):
            t = threading.Thread(target=_run_thread, daemon=True)
            t.start()

            deadline = time.monotonic() + 2.0
            while signal.SIGTERM not in captured and time.monotonic() < deadline:
                time.sleep(0.01)

            assert signal.SIGINT in captured
            assert signal.SIGTERM in captured

            # Trigger stop
            captured[signal.SIGTERM](signal.SIGTERM, None)  # type: ignore[call-arg]
            done.wait(timeout=5)


# ─── Entrypoint (__main__.py) ─────────────────────────────────────────────────


class TestEntrypoint:
    def test_once_flag_calls_run_once(self, db_engine):
        with (
            patch("sys.argv", ["aggregator-summarize-rank", "--once"]),
            patch("aggregator_summarize_rank.loop.run_once") as mock_run_once,
        ):
            from aggregator_summarize_rank.__main__ import main
            main()

        mock_run_once.assert_called_once()

    def test_daemon_flag_calls_run(self, db_engine):
        with (
            patch("sys.argv", ["aggregator-summarize-rank"]),
            patch("aggregator_summarize_rank.loop.run") as mock_run,
        ):
            from aggregator_summarize_rank.__main__ import main
            main()

        mock_run.assert_called_once()

    def test_configure_logging_called_before_dispatch(self, db_engine):
        call_order: list[str] = []

        def _log(*args, **kwargs):
            call_order.append("log")

        def _run_once(*args, **kwargs):
            call_order.append("run_once")

        with (
            patch("sys.argv", ["aggregator-summarize-rank", "--once"]),
            patch("aggregator_summarize_rank.__main__.configure_logging", side_effect=_log),
            patch("aggregator_summarize_rank.loop.run_once", side_effect=_run_once),
        ):
            from aggregator_summarize_rank.__main__ import main
            main()

        assert call_order.index("log") < call_order.index("run_once"), (
            "configure_logging must be called before run_once"
        )

    def test_once_exits_without_error_when_no_articles(self, db_engine, capsys):
        """--once with empty DB must complete without raising."""
        with patch("sys.argv", ["aggregator-summarize-rank", "--once"]):
            from aggregator_summarize_rank.__main__ import main
            main()

        out = capsys.readouterr().out
        assert "ranked=0" in out
