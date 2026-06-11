from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from aggregator_retriever.http import FetchError, fetch


def _make_source(etag=None, last_modified=None, url="https://feed.example.com/feed.xml"):
    return SimpleNamespace(feed_url=url, etag=etag, last_modified=last_modified)


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    from aggregator_retriever.config import Settings as S
    return S()


class _MockResponse:
    def __init__(self, status_code=200, body=b"", response_headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = response_headers or {}

    def iter_bytes(self):
        if self._body:
            yield self._body


class _MockClient:
    def __init__(self, response=None, raise_exc=None):
        self._response = response
        self._raise_exc = raise_exc

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    @contextmanager
    def stream(self, method, url, headers=None, **kwargs):
        if self._raise_exc:
            raise self._raise_exc
        yield self._response


class TestFetch304:
    def test_returns_not_modified(self, settings):
        source = _make_source()
        resp = _MockResponse(status_code=304)
        with patch("httpx.Client", return_value=_MockClient(resp)):
            result = fetch(source, settings)
        assert result.not_modified is True
        assert result.body is None

    def test_sends_if_none_match_header(self, settings):
        source = _make_source(etag='"v1"')
        captured: dict = {}

        class _Capturing(_MockClient):
            @contextmanager
            def stream(self, method, url, headers=None, **kwargs):
                captured["headers"] = headers or {}
                yield self._response

        resp = _MockResponse(status_code=304)
        with patch("httpx.Client", return_value=_Capturing(resp)):
            fetch(source, settings)
        assert captured["headers"].get("If-None-Match") == '"v1"'

    def test_sends_if_modified_since_header(self, settings):
        source = _make_source(last_modified="Mon, 01 Jan 2024 00:00:00 GMT")
        captured: dict = {}

        class _Capturing(_MockClient):
            @contextmanager
            def stream(self, method, url, headers=None, **kwargs):
                captured["headers"] = headers or {}
                yield self._response

        resp = _MockResponse(status_code=304)
        with patch("httpx.Client", return_value=_Capturing(resp)):
            fetch(source, settings)
        assert captured["headers"].get("If-Modified-Since") == "Mon, 01 Jan 2024 00:00:00 GMT"

    def test_no_etag_no_if_none_match(self, settings):
        source = _make_source(etag=None)
        captured: dict = {}

        class _Capturing(_MockClient):
            @contextmanager
            def stream(self, method, url, headers=None, **kwargs):
                captured["headers"] = headers or {}
                yield self._response

        resp = _MockResponse(status_code=304)
        with patch("httpx.Client", return_value=_Capturing(resp)):
            fetch(source, settings)
        assert "If-None-Match" not in captured["headers"]


class TestFetch200:
    def test_returns_body(self, settings):
        source = _make_source()
        body = b"<rss>feed content</rss>"
        resp = _MockResponse(status_code=200, body=body)
        with patch("httpx.Client", return_value=_MockClient(resp)):
            result = fetch(source, settings)
        assert result.body == body
        assert result.not_modified is False

    def test_etag_round_trip(self, settings):
        source = _make_source()
        resp = _MockResponse(
            status_code=200,
            body=b"<feed/>",
            response_headers={"etag": '"v2"'},
        )
        with patch("httpx.Client", return_value=_MockClient(resp)):
            result = fetch(source, settings)
        assert result.etag == '"v2"'

    def test_last_modified_round_trip(self, settings):
        source = _make_source()
        resp = _MockResponse(
            status_code=200,
            body=b"<feed/>",
            response_headers={"last-modified": "Tue, 02 Jan 2024 00:00:00 GMT"},
        )
        with patch("httpx.Client", return_value=_MockClient(resp)):
            result = fetch(source, settings)
        assert result.last_modified == "Tue, 02 Jan 2024 00:00:00 GMT"

    def test_body_exactly_at_limit_succeeds(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
        monkeypatch.setenv("RETRIEVER_MAX_FEED_BYTES", "100")
        from aggregator_retriever.config import Settings as S
        s = S()
        source = _make_source()
        body = b"x" * 100
        resp = _MockResponse(status_code=200, body=body)
        with patch("httpx.Client", return_value=_MockClient(resp)):
            result = fetch(source, s)
        assert len(result.body) == 100

    def test_body_one_byte_over_limit_raises(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
        monkeypatch.setenv("RETRIEVER_MAX_FEED_BYTES", "100")
        from aggregator_retriever.config import Settings as S
        s = S()
        source = _make_source()
        body = b"x" * 101
        resp = _MockResponse(status_code=200, body=body)
        with patch("httpx.Client", return_value=_MockClient(resp)):
            with pytest.raises(FetchError, match="exceeds"):
                fetch(source, s)


class TestBrotliDecoding:
    def test_brotli_package_importable(self):
        """brotli decoder must be installed for httpx to decompress Content-Encoding: br responses."""
        import brotli  # noqa: F401

        assert brotli.decompress(brotli.compress(b"test")) == b"test"

    def test_fetch_decompresses_brotli_response(self, settings):
        """fetch() must return decompressed plaintext when server responds with Content-Encoding: br."""
        import brotli

        plaintext = b"<?xml version='1.0'?><rss version='2.0'><channel><title>T</title></channel></rss>"
        compressed = brotli.compress(plaintext)

        class _BrotliTransport(httpx.BaseTransport):
            def handle_request(self, request):
                return httpx.Response(
                    200,
                    content=compressed,
                    headers={"Content-Encoding": "br"},
                )

        source = _make_source()
        _real_client = httpx.Client

        def _client_with_brotli_transport(**kwargs):
            return _real_client(transport=_BrotliTransport(), **kwargs)

        with patch("httpx.Client", _client_with_brotli_transport):
            result = fetch(source, settings)

        assert result.body == plaintext


class TestFetchErrors:
    def test_404_raises_fetch_error(self, settings):
        source = _make_source()
        resp = _MockResponse(status_code=404)
        with patch("httpx.Client", return_value=_MockClient(resp)):
            with pytest.raises(FetchError, match="404"):
                fetch(source, settings)

    def test_500_raises_fetch_error(self, settings):
        source = _make_source()
        resp = _MockResponse(status_code=500)
        with patch("httpx.Client", return_value=_MockClient(resp)):
            with pytest.raises(FetchError, match="500"):
                fetch(source, settings)

    def test_read_timeout_raises_fetch_error(self, settings):
        source = _make_source()
        with patch(
            "httpx.Client",
            return_value=_MockClient(raise_exc=httpx.ReadTimeout("timed out")),
        ):
            with pytest.raises(FetchError, match="[Tt]imeout"):
                fetch(source, settings)

    def test_connect_error_raises_fetch_error(self, settings):
        source = _make_source()
        with patch(
            "httpx.Client",
            return_value=_MockClient(raise_exc=httpx.ConnectError("refused")),
        ):
            with pytest.raises(FetchError, match="[Cc]onnection"):
                fetch(source, settings)

    def test_too_many_redirects_raises_fetch_error(self, settings):
        source = _make_source()
        with patch(
            "httpx.Client",
            return_value=_MockClient(raise_exc=httpx.TooManyRedirects("too many")),
        ):
            with pytest.raises(FetchError, match="[Rr]edirect"):
                fetch(source, settings)
