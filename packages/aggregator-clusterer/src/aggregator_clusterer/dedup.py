from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from aggregator_common.models import Article, ClassificationLabel, ThreadMembership
from aggregator_clusterer.config import ClustererSettings

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


@dataclass
class DedupResult:
    thread_id: int
    classification_label: ClassificationLabel


def _normalize_title(title: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKD", title)
    lowered = normalized.lower()
    stripped = _PUNCT_RE.sub(" ", lowered)
    return frozenset(t for t in stripped.split() if t)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = len(a | b)
    return len(a & b) / union if union > 0 else 0.0


def _raw_url(article: Article) -> Optional[str]:
    if isinstance(article.raw_payload, dict):
        return article.raw_payload.get("link") or article.raw_payload.get("url")
    return None


def check_duplicate(
    session: Session,
    article: Article,
    settings: ClustererSettings,
) -> Optional[DedupResult]:
    article_url = _raw_url(article)

    # 1. Canonical URL exact match — feedparser uses "link"; some payloads use "url"
    if article_url:
        row = session.execute(
            select(ThreadMembership.thread_id)
            .join(Article, ThreadMembership.article_id == Article.id)
            .where(
                or_(
                    Article.raw_payload["link"].astext == article_url,
                    Article.raw_payload["url"].astext == article_url,
                )
            )
            .limit(1)
        ).first()
        if row is not None:
            return DedupResult(
                thread_id=row[0],
                classification_label=ClassificationLabel.same_thread_duplicate,
            )

    # 2. Title near-duplicate via token Jaccard
    article_title = article.clean_title or article.feed_title
    if article_title:
        article_tokens = _normalize_title(article_title)
        if article_tokens:
            # Load all articles currently assigned to any thread and compare titles
            member_rows = session.execute(
                select(ThreadMembership.thread_id, Article.clean_title, Article.feed_title)
                .join(Article, ThreadMembership.article_id == Article.id)
            ).all()

            for thread_id, clean_title, feed_title in member_rows:
                candidate_title = clean_title or feed_title
                if not candidate_title:
                    continue
                candidate_tokens = _normalize_title(candidate_title)
                if not candidate_tokens:
                    continue
                if _jaccard(article_tokens, candidate_tokens) >= settings.clusterer_title_jaccard_threshold:
                    return DedupResult(
                        thread_id=thread_id,
                        classification_label=ClassificationLabel.same_thread_duplicate,
                    )

    return None
