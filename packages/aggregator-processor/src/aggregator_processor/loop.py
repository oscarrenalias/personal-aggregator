import logging
import os
import signal
import socket
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import select

from aggregator_common.claim import claim_batch, reap_stale_claims
from aggregator_common.db import SessionFactory
from aggregator_common.models import Article
from aggregator_common.state import ArticleStatus

from aggregator_processor.config import ProcessorSettings
from aggregator_processor.process import process_article

logger = logging.getLogger(__name__)


def run(settings: ProcessorSettings) -> None:
    worker_id = f"processor-{socket.gethostname()}-{os.getpid()}"
    stop_event = threading.Event()

    def _handle_signal(signum: int, _: object) -> None:
        logger.info("Signal %d received, stopping after in-flight futures drain", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info("Processor daemon starting (worker_id=%s)", worker_id)

    with ThreadPoolExecutor(max_workers=settings.processor_max_workers) as executor:
        pending_futures: list[Future] = []

        while not stop_event.is_set():
            pending_futures = [f for f in pending_futures if not f.done()]

            articles: list[Article] = []
            session = SessionFactory()
            try:
                now = datetime.now(timezone.utc)
                reaped = reap_stale_claims(session, settings.claim_lease_seconds, now)
                if reaped:
                    logger.info("Reaped %d stale claim(s)", reaped)
                articles = claim_batch(
                    session,
                    ArticleStatus.pending_processing,
                    worker_id,
                    settings.processor_batch_size,
                    now,
                )
                session.commit()
            except Exception:
                session.rollback()
                logger.exception("Error during claim cycle")
            finally:
                session.close()

            if not articles:
                stop_event.wait(timeout=settings.processor_poll_interval_seconds)
                continue

            for article in articles:
                if stop_event.is_set():
                    break
                pending_futures.append(executor.submit(process_article, article.id, settings))

        logger.info("Draining %d in-flight future(s)...", len(pending_futures))
        for f in pending_futures:
            try:
                f.result()
            except Exception:
                logger.exception("Error draining future during shutdown")

    logger.info("Processor daemon stopped cleanly")


def run_once(settings: ProcessorSettings) -> None:
    worker_id = f"processor-{socket.gethostname()}-{os.getpid()}"
    article_ids: list[int] = []

    session = SessionFactory()
    try:
        now = datetime.now(timezone.utc)
        reaped = reap_stale_claims(session, settings.claim_lease_seconds, now)
        if reaped:
            logger.info("Reaped %d stale claim(s)", reaped)
        articles = claim_batch(
            session,
            ArticleStatus.pending_processing,
            worker_id,
            settings.processor_batch_size,
            now,
        )
        article_ids = [a.id for a in articles]
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Error during claim")
    finally:
        session.close()

    with ThreadPoolExecutor(max_workers=settings.processor_max_workers) as executor:
        futures = [executor.submit(process_article, aid, settings) for aid in article_ids]
        for f in futures:
            try:
                f.result()
            except Exception:
                logger.exception("Unexpected error in worker thread")

    processed = failed = skipped = 0
    if article_ids:
        session = SessionFactory()
        try:
            rows = list(
                session.scalars(select(Article).where(Article.id.in_(article_ids))).all()
            )
            for row in rows:
                if row.status == ArticleStatus.pending_ranking:
                    processed += 1
                elif row.status == ArticleStatus.failed_processing:
                    failed += 1
                elif row.status == ArticleStatus.skipped:
                    skipped += 1
        finally:
            session.close()

    print(f"run_once: processed={processed} failed={failed} skipped={skipped}")
