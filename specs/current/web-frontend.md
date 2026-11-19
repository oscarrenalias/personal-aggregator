# web-frontend

## Requirement: Vendored Alpine.js and HTMX served locally
<!-- source: spec-42e32aaf -->

The web service SHALL serve Alpine.js and HTMX from its own `/static/vendor/` path, with no `<script src>` pointing at a third-party origin in any template.

### Scenario: Page load with no internet access
- **WHEN** the browser loads `/` with all non-origin network requests blocked
- **THEN** the sidebar renders, Alpine stores initialise, and HTMX-driven navigation works identically to the CDN-backed version

### Scenario: Script bytes match upstream releases
- **WHEN** the vendored files are compared against the CDN payloads for the pinned versions
- **THEN** `alpine-3.14.1.min.js` is byte-identical to `cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js` and `htmx-1.9.12.min.js` is byte-identical to the `unpkg.com/htmx.org@1.9.12` redirect target

### Scenario: Cache headers on vendored assets
- **WHEN** `GET /static/vendor/alpine-3.14.1.min.js` or `GET /static/vendor/htmx-1.9.12.min.js` is requested
- **THEN** the response carries `Cache-Control: no-cache`, matching the policy for `/static/app.js` and `/static/styles.css`
