import logging
import signal
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from aggregator_common.db import engine, get_session
from aggregator_common.models import Source
from aggregator_retriever.config import Settings
from aggregator_retriever.http import FetchError, fetch
from aggregator_retriever.parse import parse_feed
from aggregator_retriever.persist import insert_articles, update_source_failure, update_source_success

logger = logging.getLogger(__name__)


def _query_due_sources(session: Session, exclude_ids: set[int]) -> list[int]:
    now = datetime.now(tz=timezone.utc)
    stmt = (
        select(Source.id)
        .where(Source.enabled == True)  # noqa: E712
        .where(or_(Source.next_check_at == None, Source.next_check_at <= now))  # noqa: E711
        .order_by(Source.priority.desc(), Source.next_check_at.asc().nulls_first())
    )
    rows = session.execute(stmt).scalars().all()
    return [sid for sid in rows if sid not in exclude_ids]


def _process_source(source_id: int, settings: Settings) -> None:
    with get_session() as session:
        source = session.get(Source, source_id)
        if source is None:
            logger.warning("Source %s not found, skipping", source_id)
            return

        try:
            fetch_result = fetch(source, settings)
            if not fetch_result.not_modified and fetch_result.body is not None:
                entries = parse_feed(fetch_result.body, source_id)
                new_count = insert_articles(session, source_id, entries)
                logger.info(
                    "Source %s fetched %d entries, %d new",
                    source_id,
                    len(entries),
                    new_count,
                )
            else:
                logger.debug("Source %s not modified", source_id)
            update_source_success(session, source, fetch_result)
        except FetchError as exc:
            logger.warning("Source %s fetch failed: %s", source_id, exc)
            update_source_failure(session, source, str(exc), settings)


def run() -> None:
    settings = Settings()
    shutdown = threading.Event()

    def _handle_signal(signum, _frame):
        logger.info("Received signal %s, shutting down", signum)
        shutdown.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    in_flight: set[int] = set()
    lock = threading.Lock()

    executor = ThreadPoolExecutor(max_workers=settings.retriever_max_workers)

    logger.info(
        "Retriever started (poll_interval=%ds, max_workers=%d)",
        settings.retriever_poll_interval_seconds,
        settings.retriever_max_workers,
    )

    def _make_done_callback(source_id: int):
        def _done(future: Future):
            with lock:
                in_flight.discard(source_id)
            exc = future.exception()
            if exc is not None:
                logger.error(
                    "Source %s raised an unexpected exception",
                    source_id,
                    exc_info=exc,
                )

        return _done

    try:
        while not shutdown.is_set():
            tick_start = time.monotonic()

            with get_session() as session:
                with lock:
                    exclude = set(in_flight)
                due_ids = _query_due_sources(session, exclude)

            for source_id in due_ids:
                if shutdown.is_set():
                    break
                with lock:
                    in_flight.add(source_id)
                future = executor.submit(_process_source, source_id, settings)
                future.add_done_callback(_make_done_callback(source_id))

            elapsed = time.monotonic() - tick_start
            wait_secs = settings.retriever_poll_interval_seconds - elapsed
            if wait_secs > 0:
                shutdown.wait(timeout=wait_secs)

    finally:
        logger.info("Draining %d in-flight task(s)…", len(in_flight))
        executor.shutdown(wait=True)
        engine.dispose()
        logger.info("Retriever stopped cleanly")
