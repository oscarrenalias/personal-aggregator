import datetime
import logging
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import feedparser

from aggregator_retriever.normalize import dedup_key as _build_dedup_key, serialize_entry

logger = logging.getLogger(__name__)


@dataclass
class NormalizedEntry:
    dedup_key: str
    feed_url: Optional[str]
    feed_title: Optional[str]
    feed_summary: Optional[str]
    feed_published_at: Optional[datetime.datetime]
    comments_url: Optional[str]
    raw_payload: dict


def _get(entry, attr: str) -> Any:
    if hasattr(entry, "get"):
        return entry.get(attr)
    return getattr(entry, attr, None)


def _parse_published_at(entry) -> Optional[datetime.datetime]:
    for attr in ("published_parsed", "updated_parsed"):
        struct = _get(entry, attr)
        if isinstance(struct, time.struct_time):
            try:
                return datetime.datetime(*struct[:6], tzinfo=datetime.timezone.utc)
            except (ValueError, TypeError):
                pass
    return None


# Hosts that are Reddit-owned — excluded when scanning for the external article URL.
_REDDIT_HOSTS = frozenset({
    "reddit.com", "www.reddit.com", "old.reddit.com", "new.reddit.com",
    "redd.it",
    "out.reddit.com",
    "i.redd.it", "v.redd.it", "preview.redd.it",
    "redditmedia.com", "www.redditmedia.com",
    "thumbs.redditmedia.com", "b.thumbs.redditmedia.com", "a.thumbs.redditmedia.com",
    "redditstatic.com", "www.redditstatic.com",
    "reddit-static.com",
})


def _is_reddit_comments_url(url: str) -> bool:
    """Return True if url is a Reddit comments/entry page URL."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        return (
            host in {"reddit.com", "www.reddit.com", "old.reddit.com", "new.reddit.com"}
            and "/comments/" in parsed.path
        )
    except Exception:
        return False


def _unwrap_reddit_outbound(url: str) -> str:
    """Unwrap out.reddit.com?url=... redirect wrappers."""
    try:
        parsed = urlparse(url)
        if (parsed.hostname or "").lower() == "out.reddit.com":
            target = parse_qs(parsed.query).get("url", [])
            if target:
                return target[0]
    except Exception:
        pass
    return url


def _extract_reddit_article_url(html_content: str) -> Optional[str]:
    """Return the first external (non-Reddit) href from Reddit RSS entry HTML, or None."""

    class _LinkParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.found: Optional[str] = None

        def handle_starttag(self, tag: str, attrs: list) -> None:
            if tag != "a" or self.found is not None:
                return
            href = dict(attrs).get("href", "")
            if not href:
                return
            href = _unwrap_reddit_outbound(href)
            try:
                parsed = urlparse(href)
                host = (parsed.hostname or "").lower()
                path = parsed.path
                if not host:
                    return
                if host in _REDDIT_HOSTS:
                    return
                if "/user/" in path or "/comments/" in path:
                    return
                self.found = href
            except Exception:
                pass

    parser = _LinkParser()
    try:
        parser.feed(html_content)
    except Exception:
        pass
    return parser.found


def parse_feed(body: bytes, source_id: int) -> list[NormalizedEntry]:
    parsed = feedparser.parse(body)

    if body and not parsed.entries:
        bozo = getattr(parsed, "bozo", False)
        version = getattr(parsed, "version", "")
        # Warn when the body is not empty but produced no entries AND either feedparser
        # flagged it as malformed (bozo=True) OR it couldn't identify any feed format
        # (version='').  A valid but empty feed always has a non-empty version string.
        if bozo or not version:
            logger.warning(
                "feedparser returned 0 entries for source_id=%s (bozo=%s, version=%r); "
                "body may be undecodable or corrupt "
                "(e.g. missing Content-Encoding decoder). bozo_exception=%r",
                source_id,
                bozo,
                version,
                getattr(parsed, "bozo_exception", None),
            )

    entries: list[NormalizedEntry] = []

    for entry in parsed.entries:
        try:
            key = _build_dedup_key(entry, str(source_id))
            if key is None:
                logger.warning(
                    "Skipping entry with no derivable dedup_key (source_id=%s, title=%r)",
                    source_id,
                    _get(entry, "title"),
                )
                continue

            serialized = serialize_entry(entry)
            entry_link = _get(entry, "link") or ""
            comments_url = _get(entry, "comments") or None

            # For Reddit link posts the entry link is the comments page, not the article.
            # Resolve the real external article URL from the entry's HTML content so the
            # processor fetches the source article instead of the comments page.
            if _is_reddit_comments_url(entry_link):
                html_content = ""
                for item in (serialized.get("content") or []):
                    if isinstance(item, dict) and item.get("value"):
                        html_content = item["value"]
                        break
                if not html_content:
                    html_content = serialized.get("summary") or ""

                external_url = _extract_reddit_article_url(html_content)
                if external_url:
                    # Patch raw_payload so the processor fetches the external article.
                    serialized["link"] = external_url
                    comments_url = entry_link  # reddit comments page
                    entry_link = external_url

            entries.append(
                NormalizedEntry(
                    dedup_key=key,
                    feed_url=entry_link or None,
                    feed_title=_get(entry, "title"),
                    feed_summary=_get(entry, "summary"),
                    feed_published_at=_parse_published_at(entry),
                    comments_url=comments_url,
                    raw_payload=serialized,
                )
            )
        except Exception:
            logger.warning(
                "Skipping malformed entry (source_id=%s)",
                source_id,
                exc_info=True,
            )

    return entries
