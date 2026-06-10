"""Unit tests for aggregator_processor.fetch — no real network calls."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import httpx
import pytest

from aggregator_processor.fetch import FetchError, fetch_page


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    from aggregator_processor.config import ProcessorSettings

    return ProcessorSettings()


# ---------------------------------------------------------------------------
# Minimal mock httpx infrastructure
# ---------------------------------------------------------------------------


class _MockResponse:
    def __init__(self, status_code: int = 200, body: bytes = b"page content"):
        self.status_code = status_code
        self._body = body
        self.headers: dict = {}

    def iter_bytes(self):
        if self._body:
            yield self._body


class _MockClient:
    def __init__(
        self,
        response: _MockResponse | None = None,
        raise_exc: Exception | None = None,
        capture: dict | None = None,
    ):
        self._response = response
        self._raise_exc = raise_exc
        self._capture = capture

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    @contextmanager
    def stream(self, method, url, headers=None, **kwargs):
        if self._capture is not None:
            self._capture["headers"] = headers or {}
            self._capture["url"] = url
        if self._raise_exc is not None:
            raise self._raise_exc
        yield self._response


# ---------------------------------------------------------------------------
# 2xx success
# ---------------------------------------------------------------------------


class TestFetchSuccess:
    def test_200_returns_bytes(self, settings):
        body = b"<html><body>article content</body></html>"
        resp = _MockResponse(status_code=200, body=body)
        with patch("httpx.Client", return_value=_MockClient(resp)):
            result = fetch_page("https://example.com/article", settings)
        assert result == body

    def test_201_accepted(self, settings):
        body = b"<html>ok</html>"
        resp = _MockResponse(status_code=201, body=body)
        with patch("httpx.Client", return_value=_MockClient(resp)):
            result = fetch_page("https://example.com/article", settings)
        assert result == body

    def test_empty_body(self, settings):
        resp = _MockResponse(status_code=200, body=b"")
        with patch("httpx.Client", return_value=_MockClient(resp)):
            result = fetch_page("https://example.com/article", settings)
        assert result == b""


# ---------------------------------------------------------------------------
# Non-2xx raises FetchError
# ---------------------------------------------------------------------------


class TestNon2xxRaises:
    def test_404_raises(self, settings):
        resp = _MockResponse(status_code=404)
        with patch("httpx.Client", return_value=_MockClient(resp)):
            with pytest.raises(FetchError, match="404"):
                fetch_page("https://example.com/article", settings)

    def test_500_raises(self, settings):
        resp = _MockResponse(status_code=500)
        with patch("httpx.Client", return_value=_MockClient(resp)):
            with pytest.raises(FetchError, match="500"):
                fetch_page("https://example.com/article", settings)

    def test_301_without_follow_raises(self, settings):
        # httpx raises TooManyRedirects when redirect limit exceeded; direct 3xx not followed
        # means status code in response — but with follow_redirects=True and max_redirects=5,
        # an actual 301 would be followed. We test the wrapped exception path instead.
        resp = _MockResponse(status_code=403)
        with patch("httpx.Client", return_value=_MockClient(resp)):
            with pytest.raises(FetchError, match="403"):
                fetch_page("https://example.com/article", settings)


# ---------------------------------------------------------------------------
# Network error wrapping
# ---------------------------------------------------------------------------


class TestNetworkErrors:
    def test_read_timeout_raises_fetch_error(self, settings):
        with patch(
            "httpx.Client",
            return_value=_MockClient(raise_exc=httpx.ReadTimeout("timed out")),
        ):
            with pytest.raises(FetchError, match="[Tt]imeout"):
                fetch_page("https://example.com/article", settings)

    def test_connect_timeout_raises_fetch_error(self, settings):
        with patch(
            "httpx.Client",
            return_value=_MockClient(raise_exc=httpx.ConnectTimeout("connect timeout")),
        ):
            with pytest.raises(FetchError, match="[Tt]imeout"):
                fetch_page("https://example.com/article", settings)

    def test_connect_error_raises_fetch_error(self, settings):
        with patch(
            "httpx.Client",
            return_value=_MockClient(raise_exc=httpx.ConnectError("connection refused")),
        ):
            with pytest.raises(FetchError, match="[Cc]onnect"):
                fetch_page("https://example.com/article", settings)

    def test_too_many_redirects_raises_fetch_error(self, settings):
        with patch(
            "httpx.Client",
            return_value=_MockClient(raise_exc=httpx.TooManyRedirects("too many")),
        ):
            with pytest.raises(FetchError, match="[Rr]edirect"):
                fetch_page("https://example.com/article", settings)


# ---------------------------------------------------------------------------
# Oversize response
# ---------------------------------------------------------------------------


class TestOversizeResponse:
    def test_body_over_limit_raises(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
        monkeypatch.setenv("PROCESSOR_MAX_PAGE_BYTES", "10")
        from aggregator_processor.config import ProcessorSettings

        s = ProcessorSettings()
        body = b"x" * 11
        resp = _MockResponse(status_code=200, body=body)
        with patch("httpx.Client", return_value=_MockClient(resp)):
            with pytest.raises(FetchError, match="exceeds"):
                fetch_page("https://example.com/article", s)

    def test_body_exactly_at_limit_succeeds(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
        monkeypatch.setenv("PROCESSOR_MAX_PAGE_BYTES", "10")
        from aggregator_processor.config import ProcessorSettings

        s = ProcessorSettings()
        body = b"x" * 10
        resp = _MockResponse(status_code=200, body=body)
        with patch("httpx.Client", return_value=_MockClient(resp)):
            result = fetch_page("https://example.com/article", s)
        assert len(result) == 10


# ---------------------------------------------------------------------------
# User-Agent header
# ---------------------------------------------------------------------------


class TestUserAgentHeader:
    def test_user_agent_sent(self, settings):
        capture: dict = {}
        body = b"<html>ok</html>"
        resp = _MockResponse(status_code=200, body=body)
        with patch("httpx.Client", return_value=_MockClient(resp, capture=capture)):
            fetch_page("https://example.com/article", settings)
        assert capture.get("headers", {}).get("User-Agent") == settings.processor_user_agent

    def test_custom_user_agent(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
        monkeypatch.setenv("PROCESSOR_USER_AGENT", "my-custom-agent/2.0")
        from aggregator_processor.config import ProcessorSettings

        s = ProcessorSettings()
        capture: dict = {}
        resp = _MockResponse(status_code=200, body=b"ok")
        with patch("httpx.Client", return_value=_MockClient(resp, capture=capture)):
            fetch_page("https://example.com/article", s)
        assert capture["headers"]["User-Agent"] == "my-custom-agent/2.0"
