import httpx

from aggregator_processor.config import ProcessorSettings


class FetchError(Exception):
    pass


def fetch_page(url: str, settings: ProcessorSettings) -> bytes:
    headers: dict[str, str] = {
        "User-Agent": settings.processor_user_agent,
    }

    try:
        with httpx.Client(
            follow_redirects=True,
            max_redirects=5,
            timeout=settings.processor_http_timeout_seconds,
        ) as client:
            with client.stream("GET", url, headers=headers) as response:
                if not (200 <= response.status_code < 300):
                    raise FetchError(f"HTTP {response.status_code} fetching {url}")

                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > settings.processor_max_page_bytes:
                        raise FetchError(
                            f"Response exceeds {settings.processor_max_page_bytes} bytes for {url}"
                        )
                    chunks.append(chunk)

                return b"".join(chunks)

    except FetchError:
        raise
    except httpx.TimeoutException as exc:
        raise FetchError(f"Timeout fetching {url}: {exc}") from exc
    except httpx.ConnectError as exc:
        raise FetchError(f"Connection error fetching {url}: {exc}") from exc
    except httpx.TooManyRedirects as exc:
        raise FetchError(f"Too many redirects fetching {url}: {exc}") from exc
    except httpx.HTTPError as exc:
        raise FetchError(f"HTTP error fetching {url}: {exc}") from exc
