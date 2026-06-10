import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from aggregator_common.claim import complete, fail
from aggregator_common.db import SessionFactory
from aggregator_common.models import Article, Source
from aggregator_common.state import ArticleStatus

from aggregator_processor.config import ProcessorSettings
from aggregator_processor.extract import ExtractionResult, extract_content
from aggregator_processor.fetch import FetchError, fetch_page
from aggregator_processor.image import select_header_image
from aggregator_processor.search import update_search_vector

logger = logging.getLogger(__name__)


def _richest_feed_content(raw_payload: dict) -> str:
    # feedparser stores content:encoded / atom:content as raw_payload["content"][0]["value"]
    for item in raw_payload.get("content") or []:
        if isinstance(item, dict):
            value = item.get("value") or ""
            if value:
                return value
    return raw_payload.get("summary") or ""


def _do_process(
    session: Session,
    article: Article,
    source: Source | None,
    settings: ProcessorSettings,
    now: datetime,
) -> None:
    raw_payload = article.raw_payload or {}
    article_url = raw_payload.get("link") or ""

    fallback = {
        "feed_title": article.feed_title,
        "raw_payload": raw_payload,
        "feed_published_at": article.feed_published_at,
        "feed_summary": article.feed_summary,
    }

    feed_candidate = _richest_feed_content(raw_payload)
    feed_len = len(feed_candidate)

    page_html: bytes | None = None
    result: ExtractionResult | None = None

    if feed_len >= settings.processor_feed_content_min_chars:
        # Feed has enough content — extract directly without an HTTP fetch
        result = extract_content(feed_candidate, fallback)
    else:
        # Attempt page fetch
        fetch_succeeded = False
        try:
            page_html = fetch_page(article_url, settings)
            fetch_succeeded = True
        except FetchError as exc:
            logger.warning("Fetch failed for article %d (%s): %s", article.id, article_url, exc)

        if fetch_succeeded:
            page_result = extract_content(page_html, fallback)  # type: ignore[arg-type]
            page_text_len = len(page_result.clean_text or "")
            if feed_candidate and page_text_len < feed_len:
                # Page extraction produced less text than the feed candidate — fall back
                result = extract_content(feed_candidate, fallback)
            else:
                result = page_result
        elif feed_candidate:
            # FetchError with usable feed content — fall back to feed
            result = extract_content(feed_candidate, fallback)
        # else: fetch failed and no feed content — result stays None

    clean_text = result.clean_text if result is not None else None
    has_usable_feed = feed_len >= settings.processor_min_content_chars

    if (not clean_text or len(clean_text) < settings.processor_min_content_chars) and not has_usable_feed:
        article.last_error = (
            f"Insufficient content: extracted {len(clean_text or '')} chars, "
            f"feed candidate {feed_len} chars"
        )
        complete(session, article, ArticleStatus.skipped)
        return

    # Safety fallback: should be unreachable, but ensures result is non-None below
    if result is None:
        result = extract_content(feed_candidate, fallback)

    article.clean_title = result.clean_title
    article.clean_text = result.clean_text
    article.excerpt = result.excerpt
    article.author = result.author
    article.published_at = result.published_at
    article.word_count = result.word_count
    article.language = result.language
    article.header_image_url = select_header_image(
        page_html,
        raw_payload,
        source.default_image_url if source else None,
    )
    article.processed_at = now

    session.flush()
    update_search_vector(session, article.id, article.clean_title or "", article.clean_text)
    complete(session, article, ArticleStatus.pending_ranking)


def process_article(article_id: int, settings: ProcessorSettings) -> None:
    session: Session = SessionFactory()
    try:
        article = session.get(Article, article_id)
        if article is None:
            logger.warning("Article %d not found, skipping", article_id)
            return

        source = session.get(Source, article.source_id)
        now = datetime.now(timezone.utc)

        try:
            _do_process(session, article, source, settings, now)
            session.commit()
        except Exception as exc:
            session.rollback()
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.exception("Unhandled error processing article %d", article_id)
            fail(
                session,
                article,
                error_msg,
                settings.processor_max_retries,
                float(settings.processor_backoff_base_seconds),
                datetime.now(timezone.utc),
            )
            session.commit()
    finally:
        session.close()
