"""Tests for vendored frontend assets served from /static/vendor/."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import aggregator_web


def _assert_no_cdn_scripts(html: str, page: str) -> None:
    cdn_pattern = re.compile(r'<script[^>]+src=["\'][^"\']*cdn\.jsdelivr\.net', re.IGNORECASE)
    unpkg_pattern = re.compile(r'<script[^>]+src=["\'][^"\']*unpkg\.com', re.IGNORECASE)

    assert not cdn_pattern.search(html), (
        f"{page} must not reference cdn.jsdelivr.net in any script src — "
        "Alpine and HTMX must be served from /static/vendor/"
    )
    assert not unpkg_pattern.search(html), (
        f"{page} must not reference unpkg.com in any script src — "
        "all JS bundles must be served locally"
    )


def test_shell_html_has_no_cdn_script_origins(client):
    """shell.html must not reference cdn.jsdelivr.net or unpkg.com in any script src.

    After vendoring, all JS is served locally from /static/vendor/. CDN references
    would break the app on a network-restricted host (e.g. a Raspberry Pi with no
    outbound internet access) and leak request metadata to third parties.
    """
    response = client.get("/")
    assert response.status_code == 200
    _assert_no_cdn_scripts(response.text, "shell.html")


def test_podcasts_index_has_no_cdn_script_origins(client):
    """podcasts/index.html must not reference cdn.jsdelivr.net or unpkg.com in any script src."""
    response = client.get("/podcasts")
    assert response.status_code == 200
    _assert_no_cdn_scripts(response.text, "podcasts/index.html")


def test_threads_index_has_no_cdn_script_origins(client):
    """threads/index.html must not reference cdn.jsdelivr.net or unpkg.com in any script src."""
    response = client.get("/threads")
    assert response.status_code == 200
    _assert_no_cdn_scripts(response.text, "threads/index.html")


def test_vendor_alpine_has_cache_control_no_cache(client):
    """GET /static/vendor/alpine-3.14.1.min.js must return Cache-Control: no-cache.

    The middleware in app.py sets this header for all /static/vendor/* paths so
    browsers always revalidate the vendored bundle after a deploy rather than
    serving a stale cached version.
    """
    response = client.get("/static/vendor/alpine-3.14.1.min.js")
    assert response.status_code == 200, (
        f"/static/vendor/alpine-3.14.1.min.js returned {response.status_code}"
    )
    assert response.headers.get("cache-control") == "no-cache", (
        f"/static/vendor/alpine-3.14.1.min.js must have Cache-Control: no-cache, "
        f"got {response.headers.get('cache-control')!r}"
    )


def test_vendor_htmx_has_cache_control_no_cache(client):
    """GET /static/vendor/htmx-1.9.12.min.js must return Cache-Control: no-cache.

    Same policy as the Alpine bundle — the middleware applies no-cache to all
    /static/vendor/* paths so stale bundles are never served after a deploy.
    """
    response = client.get("/static/vendor/htmx-1.9.12.min.js")
    assert response.status_code == 200, (
        f"/static/vendor/htmx-1.9.12.min.js returned {response.status_code}"
    )
    assert response.headers.get("cache-control") == "no-cache", (
        f"/static/vendor/htmx-1.9.12.min.js must have Cache-Control: no-cache, "
        f"got {response.headers.get('cache-control')!r}"
    )


def test_vendor_assets_sha256():
    """Vendored JS files must be byte-identical to the upstream releases.

    Alpine source: https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js
    htmx source:  https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js
    """
    static_dir = Path(aggregator_web.__file__).parent / "static" / "vendor"

    alpine_path = static_dir / "alpine-3.14.1.min.js"
    alpine_sha256 = hashlib.sha256(alpine_path.read_bytes()).hexdigest()
    assert alpine_sha256 == "358d9afbb1ab5befa2f48061a30776e5bcd7707f410a606ba985f98bc3b1c034", (
        f"alpine-3.14.1.min.js SHA-256 mismatch — expected byte-identical copy of "
        "https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js, "
        f"got {alpine_sha256}"
    )

    htmx_path = static_dir / "htmx-1.9.12.min.js"
    htmx_sha256 = hashlib.sha256(htmx_path.read_bytes()).hexdigest()
    assert htmx_sha256 == "449317ade7881e949510db614991e195c3a099c4c791c24dacec55f9f4a2a452", (
        f"htmx-1.9.12.min.js SHA-256 mismatch — expected byte-identical copy of "
        "https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js, "
        f"got {htmx_sha256}"
    )
