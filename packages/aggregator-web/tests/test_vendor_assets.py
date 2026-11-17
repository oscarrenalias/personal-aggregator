"""Tests for vendored frontend assets served from /static/vendor/."""

from __future__ import annotations

import re


def test_shell_html_has_no_cdn_script_origins(client):
    """shell.html must not reference cdn.jsdelivr.net or unpkg.com in any script src.

    After vendoring, all JS is served locally from /static/vendor/. CDN references
    would break the app on a network-restricted host (e.g. a Raspberry Pi with no
    outbound internet access) and leak request metadata to third parties.
    """
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    cdn_pattern = re.compile(r'<script[^>]+src=["\'][^"\']*cdn\.jsdelivr\.net', re.IGNORECASE)
    unpkg_pattern = re.compile(r'<script[^>]+src=["\'][^"\']*unpkg\.com', re.IGNORECASE)

    assert not cdn_pattern.search(html), (
        "shell.html must not reference cdn.jsdelivr.net in any script src — "
        "Alpine and HTMX must be served from /static/vendor/"
    )
    assert not unpkg_pattern.search(html), (
        "shell.html must not reference unpkg.com in any script src — "
        "all JS bundles must be served locally"
    )


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


def test_vendor_alpine_has_version_banner(client):
    """GET /static/vendor/alpine-3.14.1.min.js must begin with 'Alpine.js v3.14.1' banner.

    The banner identifies the vendored library version and origin at a glance
    without having to parse the minified source.
    """
    response = client.get("/static/vendor/alpine-3.14.1.min.js")
    assert response.status_code == 200
    assert response.text.startswith("// Alpine.js v3.14.1"), (
        "alpine-3.14.1.min.js must begin with '// Alpine.js v3.14.1' version banner"
    )


def test_vendor_htmx_has_version_banner(client):
    """GET /static/vendor/htmx-1.9.12.min.js must begin with 'htmx.org' banner.

    The banner identifies the vendored library version and origin at a glance
    without having to parse the minified source.
    """
    response = client.get("/static/vendor/htmx-1.9.12.min.js")
    assert response.status_code == 200
    assert response.text.startswith("// htmx.org"), (
        "htmx-1.9.12.min.js must begin with '// htmx.org' version banner"
    )
