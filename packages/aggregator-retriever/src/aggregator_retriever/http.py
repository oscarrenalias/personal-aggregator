from dataclasses import dataclass, field
from typing import Optional

import httpx

from aggregator_common.models import Source
from aggregator_retriever.config import Settings


@dataclass
class FetchResult:
    not_modified: bool = False
    body: Optional[bytes] = field(default=None)
    etag: Optional[str] = field(default=None)
    last_modified: Optional[str] = field(default=None)


class FetchError(Exception):
    pass


def fetch(source: Source, settings: Settings) -> FetchResult:
    headers: dict[str, str] = {
        "User-Agent": settings.retriever_user_agent,
        "Accept-Encoding": "gzip, deflate, br",
    }
    if source.etag is not None:
        headers["If-None-Match"] = source.etag
    if source.last_modified is not None:
        headers["If-Modified-Since"] = source.last_modified

    try:
        with httpx.Client(
            follow_redirects=True,
            max_redirects=5,
            timeout=settings.retriever_http_timeout_seconds,
        ) as client:
            with client.stream("GET", source.feed_url, headers=headers) as response:
                if response.status_code == 304:
                    return FetchResult(not_modified=True)

                if not (200 <= response.status_code < 300):
                    raise FetchError(
                        f"HTTP {response.status_code} fetching {source.feed_url}"
                    )

                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > settings.retriever_max_feed_bytes:
                        raise FetchError(
                            f"Feed response exceeds {settings.retriever_max_feed_bytes} bytes for {source.feed_url}"
                        )
                    chunks.append(chunk)

                body = b"".join(chunks)
                etag = response.headers.get("etag")
                last_modified = response.headers.get("last-modified")
                return FetchResult(body=body, etag=etag, last_modified=last_modified)

    except FetchError:
        raise
    except httpx.TimeoutException as exc:
        raise FetchError(f"Timeout fetching {source.feed_url}: {exc}") from exc
    except httpx.TransportError as exc:
        raise FetchError(f"Connection error fetching {source.feed_url}: {exc}") from exc
    except httpx.TooManyRedirects as exc:
        raise FetchError(f"Too many redirects fetching {source.feed_url}: {exc}") from exc
    except httpx.HTTPError as exc:
        raise FetchError(f"HTTP error fetching {source.feed_url}: {exc}") from exc
