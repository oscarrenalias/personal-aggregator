"""Tests for Cloudflare Access service-token header wiring.

Covers the three layers: the ApiClient sends supplied headers, the app builds
the CF-Access headers from credentials (only when both are present), and the
entrypoint sources them from flags/env vars.
"""
from __future__ import annotations

import sys

import aggregator_tui.__main__ as main_mod
from aggregator_tui.api_client import ApiClient
from aggregator_tui.app import AggregatorApp


# ---------------------------------------------------------------------------
# ApiClient
# ---------------------------------------------------------------------------


def test_api_client_sends_supplied_headers() -> None:
    client = ApiClient(
        "http://test/api/v1",
        headers={"CF-Access-Client-Id": "abc", "CF-Access-Client-Secret": "xyz"},
    )
    headers = client._client.headers  # httpx lowercases keys, case-insensitive lookup
    assert headers["cf-access-client-id"] == "abc"
    assert headers["cf-access-client-secret"] == "xyz"


def test_api_client_has_no_cf_headers_by_default() -> None:
    client = ApiClient("http://test/api/v1")
    assert "cf-access-client-id" not in client._client.headers


# ---------------------------------------------------------------------------
# AggregatorApp builds the headers from credentials
# ---------------------------------------------------------------------------


def test_app_wires_both_cf_credentials_into_client() -> None:
    app = AggregatorApp(
        api_url="http://test/api/v1",
        cf_access_client_id="id123",
        cf_access_client_secret="sec456",
    )
    headers = app.api_client._client.headers
    assert headers["cf-access-client-id"] == "id123"
    assert headers["cf-access-client-secret"] == "sec456"


def test_app_omits_cf_headers_when_only_id_present() -> None:
    # Both are required; a lone id must not produce a partial header set.
    app = AggregatorApp(api_url="http://test/api/v1", cf_access_client_id="id123")
    assert "cf-access-client-id" not in app.api_client._client.headers


def test_app_omits_cf_headers_when_none_present() -> None:
    app = AggregatorApp(api_url="http://test/api/v1")
    assert "cf-access-client-id" not in app.api_client._client.headers


# ---------------------------------------------------------------------------
# Entrypoint flag / env-var resolution
# ---------------------------------------------------------------------------


class _FakeApp:
    last_kwargs: dict = {}

    def __init__(self, **kwargs: object) -> None:
        _FakeApp.last_kwargs = dict(kwargs)

    def run(self) -> None:  # pragma: no cover - no-op
        pass


def test_main_sources_cf_creds_from_env(monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "AggregatorApp", _FakeApp)
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "envid")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "envsec")
    monkeypatch.setattr(sys, "argv", ["aggregator-tui", "--api-url", "http://x/api/v1"])

    main_mod.main()

    assert _FakeApp.last_kwargs["api_url"] == "http://x/api/v1"
    assert _FakeApp.last_kwargs["cf_access_client_id"] == "envid"
    assert _FakeApp.last_kwargs["cf_access_client_secret"] == "envsec"


def test_main_flags_override_env(monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "AggregatorApp", _FakeApp)
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "envid")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "envsec")
    monkeypatch.setattr(
        sys,
        "argv",
        ["aggregator-tui", "--cf-access-id", "flagid", "--cf-access-secret", "flagsec"],
    )

    main_mod.main()

    assert _FakeApp.last_kwargs["cf_access_client_id"] == "flagid"
    assert _FakeApp.last_kwargs["cf_access_client_secret"] == "flagsec"
