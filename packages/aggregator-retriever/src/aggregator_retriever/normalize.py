import base64
import hashlib
import json
import logging
import time
from typing import Any
from urllib.parse import urlparse, urlunparse, urlencode, parse_qsl

logger = logging.getLogger(__name__)

_TRACKING_PARAMS = frozenset(
    {"fbclid", "gclid", "mc_eid", "igshid"}
)
_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(url: str) -> str:
    parsed = urlparse(url)

    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""
    port = parsed.port

    if port and _DEFAULT_PORTS.get(scheme) == port:
        port = None

    netloc = host if port is None else f"{host}:{port}"

    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k != "" and not k.startswith("utm_") and k not in _TRACKING_PARAMS
    ]
    query_pairs.sort(key=lambda kv: kv[0])
    query = urlencode(query_pairs)

    path = parsed.path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    return urlunparse((scheme, netloc, path, parsed.params, query, ""))


def dedup_key(entry: Any, source_id: str) -> str | None:
    """Return a stable dedup key for a feedparser entry, or None if not derivable."""
    entry_id = getattr(entry, "id", None) or (entry.get("id") if hasattr(entry, "get") else None)
    if entry_id:
        return str(entry_id)

    link = getattr(entry, "link", None) or (entry.get("link") if hasattr(entry, "get") else None)
    if link:
        try:
            return normalize_url(link)
        except Exception:
            pass

    title = getattr(entry, "title", None) or (entry.get("title") if hasattr(entry, "get") else None)
    published = (
        getattr(entry, "published", None)
        or (entry.get("published") if hasattr(entry, "get") else None)
    )
    if title is not None or published is not None:
        title_str = str(title) if title is not None else ""
        published_str = str(published) if published is not None else ""
        digest_input = f"{source_id}\n{title_str}\n{published_str}"
        return hashlib.sha256(digest_input.encode()).hexdigest()

    return None


def _serialize_value(value: Any, key: str) -> Any:
    if isinstance(value, time.struct_time):
        try:
            import datetime
            return datetime.datetime(*value[:6], tzinfo=datetime.timezone.utc).isoformat()
        except Exception:
            return None
    if isinstance(value, bytes):
        try:
            return base64.b64encode(value).decode("ascii")
        except Exception:
            return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {k: _serialize_value(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_value(item, key) for item in value]
    # Drop non-serializable values with a warning
    logger.warning("Dropping non-serializable field %r of type %s", key, type(value).__name__)
    return None


def serialize_entry(entry: Any) -> dict:
    """Convert a feedparser entry to a JSON-safe dict."""
    if hasattr(entry, "items"):
        raw = dict(entry.items())
    elif hasattr(entry, "__dict__"):
        raw = vars(entry)
    else:
        raw = {}

    result = {k: _serialize_value(v, k) for k, v in raw.items()}

    # Verify round-trip safety; drop any keys that still fail
    safe: dict = {}
    for k, v in result.items():
        try:
            json.dumps(v)
            safe[k] = v
        except (TypeError, ValueError):
            logger.warning("Dropping field %r: still not JSON-serializable after conversion", k)
    return safe
