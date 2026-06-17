from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass
from datetime import datetime

import trafilatura

_EXCERPT_MAX_CHARS = 300
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class ExtractionResult:
    clean_text: str | None
    clean_title: str | None
    author: str | None
    published_at: datetime | None
    language: str | None
    excerpt: str | None
    word_count: int


def _strip_html_summary(text: str) -> str | None:
    stripped = _TAG_RE.sub(" ", text)
    unescaped = _html.unescape(stripped)
    collapsed = " ".join(unescaped.split())
    return collapsed or None


def _trim_at_word_boundary(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cutoff = text.rfind(" ", 0, max_chars + 1)
    if cutoff == -1:
        return text[:max_chars]
    return text[:cutoff]


def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None


def extract_content(html: str | bytes, fallback: dict) -> ExtractionResult:
    """Extract clean text and metadata from HTML, applying fallback chains for missing fields."""
    # load_html normalises both str and bytes into a parsed LXML tree, avoiding double parsing
    tree = trafilatura.load_html(html)
    metadata = trafilatura.extract_metadata(tree) if tree is not None else None
    clean_text = trafilatura.extract(tree) if tree is not None else None

    raw_title = metadata.title if metadata and metadata.title else None
    clean_title = raw_title or fallback.get("feed_title")

    raw_author = metadata.author if metadata and metadata.author else None
    payload_author = (fallback.get("raw_payload") or {}).get("author")
    author = raw_author or payload_author or None

    raw_date = metadata.date if metadata and metadata.date else None
    published_at = _parse_date(raw_date) or fallback.get("feed_published_at")

    language = metadata.language if metadata and metadata.language else None

    if clean_text:
        excerpt = _trim_at_word_boundary(clean_text, _EXCERPT_MAX_CHARS)
    else:
        raw_summary = fallback.get("feed_summary") or None
        excerpt = _strip_html_summary(raw_summary) if raw_summary else None

    word_count = len(clean_text.split()) if clean_text else 0

    return ExtractionResult(
        clean_text=clean_text,
        clean_title=clean_title,
        author=author,
        published_at=published_at,
        language=language,
        excerpt=excerpt,
        word_count=word_count,
    )
