---
name: Vendorize frontend JS dependencies
id: spec-42e32aaf
description: Serve Alpine.js and HTMX from the aggregator-web static directory instead of third-party CDNs, so the UI works without internet access and keeps running if a CDN is unreachable.
dependencies: null
priority: medium
complexity: low
status: planned
tags: [frontend, web]
scope:
  in: Static asset delivery for aggregator-web; shell.html script tags; cache handling for the vendored files; version pinning and update procedure.
  out: Alpine.js or HTMX version upgrades themselves (pinned to current versions only); SRI/CSP changes beyond the vendored scripts.
feature_root_id: B-fe464840
---
# Vendorize frontend JS dependencies

## Objective

Serve the two third-party JS libraries used by the web UI — Alpine.js (3.14.1) and HTMX (1.9.12) — from the `aggregator-web` static directory instead of loading them at runtime from `cdn.jsdelivr.net` and `unpkg.com`. The UI must remain fully functional with no outbound internet access from the browser (other than the aggregator host itself), including on Tailscale clients behind captive portals or during CDN outages.

## Problems to Fix

1. **CDN dependency breaks the offline/self-hosted story.** `shell.html` currently loads:
   - `https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js`
   - `https://unpkg.com/htmx.org@1.9.12`
   If the browser cannot reach these hosts (captive portal, DNS failure, CDN outage, egress filtering), the page renders but is non-interactive: sidebar navigation, article cards, keyboard shortcuts, and the reader pane all depend on Alpine stores and HTMX requests. `app.js` is local, but it only defines factories — nothing works without Alpine bootstrapping them.
2. **No offline PWA capability.** The manifest makes the app installable, but the two largest JS payloads are CDN-hosted and cannot be cached by a service worker scope-limited to the aggregator origin.
3. **Version drift risk.** The pinned versions live only as URL strings in a template. There is no single place to audit which frontend dependency versions are in use.

## Changes

1. **Download the two vendored files into the web static directory.**
   - `packages/aggregator-web/src/aggregator_web/static/vendor/alpine-3.14.1.min.js` — exact bytes of `https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js`
   - `packages/aggregator-web/src/aggregator_web/static/vendor/htmx-1.9.12.min.js` — exact bytes of `https://unpkg.com/htmx.org@1.9.12` (the unpkg redirect target `htmx.org@1.9.12/dist/htmx.min.js`; download and verify the contents are the minified HTMX bundle)
   - File names include the version so future upgrades are additive (old files removed, new files added; no in-place overwrite that could be masked by caching).
   - Record the SHA-256 of each downloaded file and the source URL in this spec's review notes (not in the repo) to prove byte-equivalence.
2. **Update all three templates that reference CDN-hosted scripts.** The CDN `<script>` tags appear in three templates: `shell.html` (the PWA shell) and `podcasts/index.html` and `threads/index.html` (standalone full-page templates for deep-link navigation). Each pair of `<script>` tags SHALL be replaced with the local vendor path and `?v={{ app_version }}`:
   ```html
   <script defer src="/static/vendor/alpine-3.14.1.min.js?v={{ app_version }}"></script>
   <script src="/static/vendor/htmx-1.9.12.min.js?v={{ app_version }}" defer></script>
   ```
   Keep `app.js` before Alpine (unchanged) and keep the `alpine:init` store-seeding inline script where it is. Load order must remain: `app.js` → seeding script → Alpine (defer) → HTMX (defer).
3. **Extend the `_static_cache_control` middleware** in `app.py` so the two vendored paths get the same `Cache-Control: no-cache` header currently applied to `/static/app.js` and `/static/styles.css`. Because the `app_version` query param already busts caches on release, `no-cache` (revalidate) is the correct policy — identical to the existing mutable assets.
4. **Add a `docs` note on updating vendored JS.** A short paragraph in the spec (or in the PR description when implemented) recording: download from the canonical URL, record its SHA-256 and update the expected hash in `tests/test_vendor_assets.py` (upstream minified builds carry no comment banner, so a banner check does not work; never edit the downloaded bytes), place under `static/vendor/` with the version in the filename, and update the two `<script>` tags. No automation needed — these update a few times per year at most.
5. **Verify no other CDN references remain** in `aggregator-web` templates or static files. Search for `http://` and `https://` in `packages/aggregator-web/src/aggregator_web/{templates,static}/` and confirm every remaining hit is either a comment, documentation, or an intentional outbound link (e.g. article `feed_url` values rendered from the DB, not asset loads). `srcset`/`href` attributes pointing off-origin must be justified in the PR.

## Requirements

### ADDED: web-frontend / Vendored Alpine.js and HTMX served locally

The web service SHALL serve Alpine.js and HTMX from its own `/static/vendor/` path, with no `<script src>` pointing at a third-party origin in any template.

#### Scenario: Page load with no internet access
- **WHEN** the browser loads `/` with all non-origin network requests blocked
- **THEN** the sidebar renders, Alpine stores initialise, and HTMX-driven navigation works identically to the CDN-backed version

#### Scenario: Script bytes match upstream releases
- **WHEN** the vendored files are compared against the CDN payloads for the pinned versions
- **THEN** `alpine-3.14.1.min.js` is byte-identical to `cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js` and `htmx-1.9.12.min.js` is byte-identical to the `unpkg.com/htmx.org@1.9.12` redirect target

#### Scenario: Cache headers on vendored assets
- **WHEN** `GET /static/vendor/alpine-3.14.1.min.js` or `GET /static/vendor/htmx-1.9.12.min.js` is requested
- **THEN** the response carries `Cache-Control: no-cache`, matching the policy for `/static/app.js` and `/static/styles.css`

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-web/src/aggregator_web/static/vendor/alpine-3.14.1.min.js` | New: vendored Alpine.js bundle (exact upstream bytes) |
| `packages/aggregator-web/src/aggregator_web/static/vendor/htmx-1.9.12.min.js` | New: vendored HTMX bundle (exact upstream bytes) |
| `packages/aggregator-web/src/aggregator_web/templates/shell.html` | Point the two `<script>` tags at the local vendor paths with `?v={{ app_version }}` |
| `packages/aggregator-web/src/aggregator_web/templates/podcasts/index.html` | Same CDN → local script swap as shell.html |
| `packages/aggregator-web/src/aggregator_web/templates/threads/index.html` | Same CDN → local script swap as shell.html |
| `packages/aggregator-web/src/aggregator_web/app.py` | Extend `_static_cache_control` to cover the two vendored paths |
| `packages/aggregator-web/tests/test_routes.py` (or new test file) | Add coverage: shell HTML contains no third-party script origins; vendored paths return `no-cache` |

## Acceptance Criteria

- [ ] `grep -rn "cdn.jsdelivr\|unpkg.com" packages/aggregator-web/src/aggregator_web/{templates,static}/` returns no hits (excluding this spec text if quoted).
- [ ] Page loads and is fully interactive with CDN hosts blocked (verify manually once via devtools request blocking; automated test asserts the markup contains only same-origin script sources).
- [ ] New test asserts `Cache-Control: no-cache` on both vendored asset responses.
- [ ] Load order preserved in all three templates: `app.js` → seeding inline script → Alpine → HTMX (check the rendered `<head>` in the test or by inspection).
- [ ] No version change: Alpine stays at 3.14.1, HTMX stays at 1.9.12 — this spec is a pure hosting move, not an upgrade.

## Pending Decisions

- None. Versions are pinned to the current CDN URLs; upgrade policy is out of scope.

## Review Notes

Recorded during implementation review (2026-11-19). Hashes verified against a fresh download of each URL and enforced by `test_vendor_assets.py`.

| File | Source URL | SHA-256 |
|---|---|---|
| `alpine-3.14.1.min.js` | `https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js` | `358d9afbb1ab5befa2f48061a30776e5bcd7707f410a606ba985f98bc3b1c034` |
| `htmx-1.9.12.min.js` | `https://unpkg.com/htmx.org@1.9.12` (→ `dist/htmx.min.js`) | `449317ade7881e949510db614991e195c3a099c4c791c24dacec55f9f4a2a452` |

**Update procedure:** download the new version from the canonical URL into `static/vendor/` with the version in the filename, delete the old file, update the two `<script>` tags in `shell.html`, `podcasts/index.html` and `threads/index.html`, and update the filename and expected SHA-256 in `test_vendor_assets.py`.

**Implementation fix:** an earlier corrective bead prepended hand-written banners to both files to satisfy the old banner check, which broke byte-equivalence. B-f6e33516 restores the original bytes and replaces the banner tests with SHA-256 tests.
