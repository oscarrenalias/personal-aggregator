from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Sequence

from aggregator_common.claim import complete, fail
from aggregator_common.models import Article, Source
from aggregator_common.state import ArticleStatus

from .config import SummarizeRankSettings
from .prompt import _CategoryEntry
from .ranker import rank

logger = logging.getLogger(__name__)


def process_article(
    article_id: int,
    interest_profile_text: str,
    enabled_categories: Sequence[_CategoryEntry],
    settings: SummarizeRankSettings,
    session_factory: Any,
) -> None:
    with session_factory() as session:
        article = session.get(Article, article_id)
        if article is None:
            logger.warning("Article %d not found, skipping", article_id)
            return

        usable_text = article.clean_text or article.excerpt or article.feed_summary or ""

        if len(usable_text) < settings.summarize_rank_min_content_chars:
            logger.info(
                "Article %d has insufficient content (%d chars), skipping",
                article_id,
                len(usable_text),
            )
            complete(session, article, ArticleStatus.skipped)
            session.commit()
            return

        source = session.get(Source, article.source_id)
        article_input: dict[str, Any] = {
            "source_name": source.name if source else None,
            "title": article.clean_title or article.feed_title,
            "clean_text": article.clean_text,
            "excerpt": article.excerpt,
            "feed_summary": article.feed_summary,
        }

        now = datetime.now(UTC)
        try:
            result, usage_dict = rank(article_input, interest_profile_text, settings, enabled_categories)
        except Exception as exc:
            logger.warning("Ranking failed for article %d: %s", article_id, exc)
            fail(
                session,
                article,
                str(exc),
                settings.summarize_rank_max_retries,
                settings.summarize_rank_backoff_base_seconds,
                now,
            )
            session.commit()
            return

        article.summary = result.summary
        article.topics = result.topics
        article.importance_score = result.importance_score
        article.importance_reason = result.importance_reason
        article.llm_meta = usage_dict
        article.summarized_at = datetime.now(UTC)
        complete(session, article, ArticleStatus.ready)
        session.commit()
        logger.info("Article %d ranked successfully: score=%d", article_id, result.importance_score)
