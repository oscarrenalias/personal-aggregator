import datetime
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

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

            entries.append(
                NormalizedEntry(
                    dedup_key=key,
                    feed_url=_get(entry, "link"),
                    feed_title=_get(entry, "title"),
                    feed_summary=_get(entry, "summary"),
                    feed_published_at=_parse_published_at(entry),
                    raw_payload=serialize_entry(entry),
                )
            )
        except Exception:
            logger.warning(
                "Skipping malformed entry (source_id=%s)",
                source_id,
                exc_info=True,
            )

    return entries
