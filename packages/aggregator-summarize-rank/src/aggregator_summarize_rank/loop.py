import logging
import os
import signal
import socket
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from aggregator_common.claim import claim_batch, reap_stale_claims
from aggregator_common.db import SessionFactory
from aggregator_common.models import Article, InterestProfile
from aggregator_common.state import ArticleStatus

from aggregator_summarize_rank.config import SummarizeRankSettings
from aggregator_summarize_rank.rank import process_article

logger = logging.getLogger(__name__)


def _read_interest_profile(session: Session) -> str:
    row = session.get(InterestProfile, True)
    return row.profile_text if row and row.profile_text else ""


def run(settings: SummarizeRankSettings) -> None:
    worker_id = f"summarize-rank-{socket.gethostname()}-{os.getpid()}"
    stop_event = threading.Event()

    def _handle_signal(signum: int, _: object) -> None:
        logger.info("Signal %d received, stopping after in-flight futures drain", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info("Summarize-rank daemon starting (worker_id=%s)", worker_id)

    with ThreadPoolExecutor(max_workers=settings.summarize_rank_max_workers) as executor:
        pending_futures: list[Future] = []

        while not stop_event.is_set():
            pending_futures = [f for f in pending_futures if not f.done()]

            articles: list[Article] = []
            interest_profile_text = ""
            session = SessionFactory()
            try:
                now = datetime.now(timezone.utc)
                reaped = reap_stale_claims(session, settings.claim_lease_seconds, now)
                if reaped:
                    logger.info("Reaped %d stale claim(s)", reaped)
                articles = claim_batch(
                    session,
                    ArticleStatus.pending_ranking,
                    worker_id,
                    settings.summarize_rank_batch_size,
                    now,
                )
                interest_profile_text = _read_interest_profile(session)
                session.commit()
            except Exception:
                session.rollback()
                logger.exception("Error during claim cycle")
            finally:
                session.close()

            if not articles:
                stop_event.wait(timeout=settings.summarize_rank_poll_interval_seconds)
                continue

            for article in articles:
                if stop_event.is_set():
                    break
                pending_futures.append(
                    executor.submit(
                        process_article, article.id, interest_profile_text, settings, SessionFactory
                    )
                )

        logger.info("Draining %d in-flight future(s)...", len(pending_futures))
        for f in pending_futures:
            try:
                f.result()
            except Exception:
                logger.exception("Error draining future during shutdown")

    logger.info("Summarize-rank daemon stopped cleanly")


def run_once(settings: SummarizeRankSettings) -> None:
    worker_id = f"summarize-rank-{socket.gethostname()}-{os.getpid()}"
    article_ids: list[int] = []
    interest_profile_text = ""

    session = SessionFactory()
    try:
        now = datetime.now(timezone.utc)
        reaped = reap_stale_claims(session, settings.claim_lease_seconds, now)
        if reaped:
            logger.info("Reaped %d stale claim(s)", reaped)
        articles = claim_batch(
            session,
            ArticleStatus.pending_ranking,
            worker_id,
            settings.summarize_rank_batch_size,
            now,
        )
        article_ids = [a.id for a in articles]
        interest_profile_text = _read_interest_profile(session)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Error during claim")
    finally:
        session.close()

    with ThreadPoolExecutor(max_workers=settings.summarize_rank_max_workers) as executor:
        futures = [
            executor.submit(process_article, aid, interest_profile_text, settings, SessionFactory)
            for aid in article_ids
        ]
        for f in futures:
            try:
                f.result()
            except Exception:
                logger.exception("Unexpected error in worker thread")

    ranked = failed = skipped = 0
    if article_ids:
        session = SessionFactory()
        try:
            rows = list(
                session.scalars(select(Article).where(Article.id.in_(article_ids))).all()
            )
            for row in rows:
                if row.status == ArticleStatus.ready:
                    ranked += 1
                elif row.status == ArticleStatus.failed_ranking:
                    failed += 1
                elif row.status == ArticleStatus.skipped:
                    skipped += 1
        finally:
            session.close()

    print(f"run_once: ranked={ranked} failed={failed} skipped={skipped}")
