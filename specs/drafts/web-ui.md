---
name: Web UI
id: spec-b85ac9a3
description: "aggregator-web: Feedly-style FastAPI + HTMX + Alpine PWA. Left pane feeds (smart views + categories + sources), article list, single-article reader, full-text search, read/unread + save, j/k/v keyboard shortcuts. Responsive desktop+mobile, hand-rolled CSS, Tailscale-only (binds 127.0.0.1, no app auth). Adds web to Dockerfile/compose/CI. Depends on foundation + categorization."
dependencies: null
priority: high
complexity: null
status: draft
tags:
- web
- ui
- fastapi
- htmx
- pwa
- tailscale
scope:
  in: null
  out: null
feature_root_id: null
---
# Web UI

## Objective

Implement `aggregator-web`: a fast, responsive, Feedly-style web app to browse the ranked articles. Left pane = **feeds** (smart views + topic categories + sources); main pane = the article list for the selected feed; a single-article reader; full-text search; read/unread + save; keyboard shortcuts. Works on desktop and iOS Safari, installable as a PWA. Reached privately over **Tailscale** (no auth provider). Replaces the `aggregator-web` stub.

It is a read + reader-interaction surface only: it does **not** fetch feeds, extract, or call the LLM.

## Dependencies

- **Foundation** (`spec-9e974b88`): `articles`/`sources` models, `search_vector` (tsvector + GIN), interaction columns (`is_read`/`read_at`/`is_saved`/`is_hidden`), `db`/`load_env`/`configure_logging`.
- **Article Categorization** (`spec-2eabb3e7`): the `categories` table + `articles.categories` jsonb (+ GIN) — powers topic feeds. (Building in parallel; merged before web is built.)
- **summarize-rank** output already on articles: `summary`, `topics`, `importance_score`, `importance_reason`, `header_image_url`, `clean_title`/`clean_text`/`excerpt`.

## Background / Decisions

Decided with the user; implement to them.

- **Stack:** FastAPI + Jinja2 + **HTMX** for partial swaps/infinite scroll, **Alpine.js** for keyboard shortcuts + list selection. **Hand-rolled responsive CSS — no build step.** Shipped as a **PWA** (manifest + minimal service worker) so iOS "Add to Home Screen" gives an app-like, full-screen experience.
- **Responsive layout (must work desktop + mobile):** CSS grid/flex + media queries. Desktop = multi-pane (sidebar │ list │ reader); narrow = single column with a slide-in drawer sidebar and full-screen article. Same HTML, breakpoint-driven.
- **Security: Tailscale-only.** The service binds to `127.0.0.1` by default (config `WEB_HOST`/`WEB_PORT`); it is **never published to the public internet**. Exposure to the user's devices is via Tailscale (Tailscale Serve for HTTPS is an operator/deploy step). **No application auth** — the tailnet is the boundary.
- **"Feed" = a filtered article view.** Left pane groups: **Smart views** (All / Unread / Saved / Important) + **Categories** (topic feeds) + **Sources**.
- v1 shows **`ready`** articles (summarized + ranked). Graceful display of not-yet-ranked articles is deferred.
- Long-running `uvicorn` process; reader writes limited to interaction columns.

## Changes

### 1. Package + entrypoint

Replace the web stub. `packages/aggregator-web/` — deps: `fastapi`, `uvicorn[standard]`, `jinja2`, `python-multipart`, `aggregator-common`.

```
src/aggregator_web/
  __init__.py
  config.py        # WebSettings: WEB_HOST=127.0.0.1, WEB_PORT=8000, WEB_PAGE_SIZE=50, WEB_IMPORTANT_THRESHOLD=70
  app.py           # FastAPI app + routes
  feeds.py         # feed-query logic (smart/category/source/search) + counts
  reader.py        # read/unread/save toggles
  templates/       # Jinja: shell.html, _sidebar.html, _article_list.html, _article_card.html, _article_detail.html, _search.html
  static/          # styles.css, app.js (htmx+alpine wiring), manifest.webmanifest, sw.js, icons/
  __main__.py      # entrypoint: load_env() -> configure_logging(stdout) -> uvicorn.run(host=WEB_HOST, port=WEB_PORT)
```

Console script `aggregator-web`.

### 2. Routes

Server-rendered HTML + HTMX partials:

| Route | Purpose |
|---|---|
| `GET /` | App shell: sidebar + main; default feed = Unread. |
| `GET /sidebar` | Sidebar partial: Smart views, enabled categories (by `sort_order`) **plus an "Uncategorized" entry** (articles with empty/absent `categories`), enabled sources — each with an unread count. |
| `GET /feed/smart/{all\|unread\|saved\|important\|uncategorized}` | Article-list partial for a smart view (`uncategorized` = `ready` articles whose `categories` is empty/null). |
| `GET /feed/category/{name}` | Articles where `categories ? name` (GIN). |
| `GET /feed/source/{id}` | Articles for a source. |
| `GET /article/{id}` | Single-article view (HTMX into reader pane on desktop; full page on mobile). |
| `POST /article/{id}/read` · `/unread` | Toggle `is_read`/`read_at`; return updated card partial. |
| `POST /article/{id}/save` · `/unsave` | Toggle `is_saved`. |
| `POST /feed/smart/{view}/read-all` · `/feed/category/{name}/read-all` · `/feed/source/{id}/read-all` | Mark **every** article in that feed read (the feed's full membership, regardless of the current `?unread` filter). |
| `GET /search?q=` | Full-text search via `to_tsquery` over `search_vector`; rendered as a feed list. |
| `GET /healthz` | 200 + `{version, db: ok}` (uses `aggregator_common.version()`). |

All feed lists: default sort **`importance_score DESC, feed_published_at DESC, id DESC`** with a recency toggle; **keyset-cursor pagination over `(importance_score, feed_published_at, id)`** (robust under concurrent inserts) driven by **HTMX infinite scroll**; `?unread=1` filter. Page size from config.

### 3. Article list + card

Each card: header-image thumbnail (`header_image_url`, lazy-loaded, graceful when null), `clean_title` (fallback `feed_title`), source name, published date, **importance score badge**, category/topic chips, summary snippet, read/unread state, save star. Read articles visually de-emphasized.

### 4. Single-article view

Header image, title, source name + **"Open source" link (`feed_url`, `target=_blank`, `rel=noopener`)**, author/date, **importance score + reason**, category + topic chips, the `summary`, then `clean_text`. Actions: mark read/unread, save/unsave, open source.

### 5. Keyboard shortcuts (Alpine/vanilla JS)

- **`j` / `k`** — select next / previous article in the list (scroll into view; on desktop, load it into the reader pane).
- **`v`** — open the selected article's source URL in a new tab.
- **`m`** — toggle read/unread on the selected article (bonus).
- `/` — focus search (bonus).
Selection state + handlers live in a small Alpine component; the data operations go through the HTMX endpoints above.

### 6. Responsive + PWA

- CSS grid app shell: ≥1024px → 3-pane (sidebar │ list │ reader); 640–1024px → 2-pane (sidebar │ list, article as overlay); <640px → single column, sidebar as a drawer (hamburger), article full-screen. Hand-rolled, no framework/build.
- PWA: `manifest.webmanifest` (name, icons, `display: standalone`, theme color), a minimal `sw.js` (cache the app shell + static assets for installability; full offline deferred), and iOS meta tags (`apple-mobile-web-app-capable`, status-bar style).

### 7. Deployment integration

Web becomes the 5th deployable:
- **`packages/aggregator-web/Dockerfile`** — same multi-stage pattern as the others (build venv at `/app`, `uv sync --no-editable --package aggregator-web`, non-root, `ENTRYPOINT ["aggregator-web"]`), `EXPOSE 8000`.
- **`docker-compose.prod.yml`** — add a `web` service: the image, env from `.env`, `depends_on` postgres healthy + migrate completed, `restart: unless-stopped`, **port bound to `127.0.0.1:${WEB_PORT}`** (not `0.0.0.0`), `healthcheck` hitting `/healthz`.
- **`scripts/build-images.sh`** + **`.github/workflows/ci.yml`** build matrix — add `web`.
- `deploy/README.md` — note Tailscale Serve to expose `web` over the tailnet with HTTPS.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-web/pyproject.toml` | real deps + `aggregator-web` entrypoint |
| `src/aggregator_web/{config,app,feeds,reader}.py`, `templates/**`, `static/**`, `__main__.py` | the app |
| `packages/aggregator-web/Dockerfile` | service image |
| `docker-compose.prod.yml` | `web` service (127.0.0.1 bind, healthcheck) |
| `scripts/build-images.sh`, `.github/workflows/ci.yml` | add `web` to build matrix |
| `deploy/README.md`, `CLAUDE.md` | web + Tailscale Serve docs |
| `packages/aggregator-web/tests/**` | route/feed/reader/search tests (FastAPI TestClient + testcontainers) |

## Acceptance Criteria

- `GET /` renders the shell; `GET /sidebar` lists Smart views, enabled categories (in `sort_order`), an **"Uncategorized"** entry, and enabled sources, each with an unread count; `GET /feed/smart/uncategorized` returns `ready` articles with empty/null `categories`.
- `GET /feed/source/{id}`, `/feed/category/{name}`, `/feed/smart/{unread|saved|important|all}` return the correctly filtered list, default-sorted `importance_score DESC` (asserted on membership + order); `?unread=1` filters to unread; infinite-scroll pagination returns the next page partial.
- `GET /article/{id}` includes summary, header image, importance score + reason, topic/category chips, and an "Open source" link with `target=_blank rel=noopener` to `feed_url`.
- `POST /article/{id}/read|unread|save|unsave` update the columns and return a partial reflecting new state; `read-all` marks every article in the feed read.
- `GET /search?q=<term>` returns articles matching `search_vector` via `to_tsquery` (GIN path), as a feed list.
- Keyboard wiring present: the list markup exposes selection hooks and the Alpine handlers bind `j`/`k`/`v` (and `m`); the "Open source" action targets `feed_url` in a new tab. (Behavioral JS asserted via served markup/attributes; not a headless-browser test.)
- Responsive CSS defines the documented breakpoints; `manifest.webmanifest` + `sw.js` + icons are served; `GET /healthz` returns 200 with the version.
- Service binds to `WEB_HOST` (default `127.0.0.1`); the prod compose maps the port to `127.0.0.1` only (no public `0.0.0.0` exposure) — asserted by inspecting the compose service.
- The `web` image builds for `linux/arm64`; `web` is in `build-images.sh` + the CI matrix + `docker-compose.prod.yml` with a `/healthz` healthcheck.
- Full suite green via `uv run pytest` (testcontainers Postgres + FastAPI TestClient; no network).

## Deferred / Settled (not open questions)

- v1 shows `ready` articles only; degraded display of `pending_ranking`/`pending_processing` deferred.
- `WEB_IMPORTANT_THRESHOLD` default 70 (Important smart view), configurable.
- Full offline PWA caching deferred (installable shell only; the service worker just caches the shell + static assets).
- Tailscale Serve / HTTPS is an operator deploy step (documented), not app code.
- Hiding/dismissing articles (`is_hidden`) deferred unless trivially added.

(The "Uncategorized" feed is now in scope — see §2 and acceptance criteria.)
