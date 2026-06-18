---
name: JSON API for mobile and TUI clients (no auth)
id: spec-0a1c22b3
description: "JSON API for a mobile app / TUI, delivered as a new aggregator-api package mounted into the web service at /api/v1 (no new container). Thin layer over aggregator_common.queries (reads) + management (reader-state writes). Exposes reads (articles/threads/brief/sources/categories, paginated) AND non-destructive reader-state writes (mark read/unread, save/unsave, thread dismiss/restore). NO destructive/admin mutations and NO auth in this phase (auth deferred to a later Cloudflare Access phase; keep behind the network perimeter)."
dependencies: null
priority: medium
complexity: null
status: done
tags:
- api
- web
- mobile
- tui
- json
- fastapi
scope:
  in: null
  out: null
feature_root_id: null
---
# JSON API for mobile and TUI clients (no auth)

## Objective

Expose a JSON API so a native mobile app or a TUI can both **render** the aggregator's reading
experience (feeds, articles, threads, the daily brief, sources/categories) **and** perform the
**non-destructive reader-state actions** an app needs (mark read/unread, save/unsave, thread
dismiss/restore) — without scraping the HTML web UI. The API is a thin JSON layer over the
existing shared helpers in `aggregator_common.queries` (reads) and `aggregator_common.management`
(the reader-state writes); it adds no business logic. Delivered as a separate `aggregator-api`
package **mounted into the existing web service** (no new container).

**Scope = reads + non-destructive reader-state writes.** Exposed writes are limited to per-article
reader state (read/unread, save/unsave) and reversible thread curation (dismiss/restore).
**Destructive / admin mutations are NOT exposed** (no source or category add/remove/rename, no
config changes). **No authentication** in this phase — deferred to a later auth phase
(Cloudflare Access JWT).

## Problems to Fix

- The web module only returns HTML / HTMX fragments (every content route renders a template; the
  only JSON endpoint is `/healthz`). A mobile app or TUI has no machine-readable surface to read
  from short of scraping HTML.
- The MCP server exists but is an agent/LLM interface (JSON-RPC tool-call semantics), not an
  ergonomic REST/JSON API for a conventional client.
- The read logic is already factored into `aggregator_common.queries` (used by both web and MCP),
  so a JSON API is low-risk and thin — but there is no HTTP/JSON contract exposing it.

## Changes

### 1. New `aggregator-api` package (mounted sub-app, no new container)

- New workspace package `packages/aggregator-api/` depending on `aggregator-common`, exposing a
  self-contained FastAPI **sub-application** (`aggregator_api.app`) whose routes live under no
  internal prefix (the mount supplies the prefix).
- The **web service mounts it as a sub-app** (decided): in `aggregator-web/app.py`,
  `app.mount("/api/v1", aggregator_api.app)`. Chosen over `include_router` so the API gets its own
  isolated OpenAPI/docs at **`/api/v1/docs`** and its own middleware stack (CORS attaches to the
  sub-app, not the HTML web app). It ships in the existing web container; no separate
  Dockerfile/entrypoint/compose service in this phase. (Self-contained, so promotable to its own
  service later by adding an entrypoint/Dockerfile — no rewrite.)
- Reuse the shared DB session dependency the web service already uses (`get_db`), or a small
  equivalent in the api package backed by `aggregator_common.db`.

### 2. Stable Pydantic response models

- Define Pydantic response schemas in `aggregator-api` mirroring the `@dataclass` result types in
  `queries.py` (`ArticleResult`, `ThreadResult`, `ThreadMemberResult`, `BriefResult`,
  `BriefTopicResult`, `SourceResult`, `CategoryResult`). This decouples the wire contract from the
  internal dataclasses (internal refactors don't silently break clients) and gives FastAPI a clean
  OpenAPI schema.
- **Conversion:** Pydantic models with `model_config = ConfigDict(from_attributes=True)`,
  constructed directly from the dataclass instances (`Model.model_validate(result)`). Add a
  **contract test per response model** asserting the exact JSON keys, so a dataclass field
  rename/shape change fails the test instead of silently changing the wire contract.
- A reusable paginated envelope: `{ "items": [...], "next_cursor": <str|null> }`.

### 3. Read endpoints (GET, prefix `/api/v1`)

Backed 1:1 by existing `queries.py` helpers:

| Endpoint | Backed by | Notes |
|---|---|---|
| `GET /articles` | `list_articles` | query params: `view` (all/unread/important/saved/uncategorized/today), `category`, `source_id`, `unread_only`, `limit`, `cursor` |
| `GET /articles/search?q=` | `search_articles` | full-text search |
| `GET /articles/{id}` | `get_article` | 404 if missing |
| `GET /threads` | `list_threads` | params: `sort`, `show_dismissed`, `limit`/`cursor`; results carry `dismissed` + `has_updates` |
| `GET /threads/{id}` | `get_thread` | 404 if missing; **must NOT** stamp `last_viewed_at` (read is passive — same rule the MCP `get_thread` follows) |
| `GET /threads/{id}/members` | `get_thread_members` | thread member articles |
| `GET /brief/today` | `get_latest_brief` | latest ready brief; 404/empty when none |
| `GET /sources` | `list_sources` | |
| `GET /categories` | `list_categories` | |
| `GET /interest-profile` | `get_interest_profile` | read-only text |
| `GET /healthz` | (existing) | version + db check (may reuse the web one) |

### 3b. Reader-state write endpoints (non-destructive, prefix `/api/v1`)

Backed 1:1 by existing `queries.py` / `management.py` helpers. All are reversible and affect only
reader state — no destructive/admin operations.

| Endpoint | Backed by | Notes |
|---|---|---|
| `POST /articles/{id}/read` | `queries.mark_read` | mark article read |
| `POST /articles/{id}/unread` | `queries.mark_unread` | mark article unread |
| `POST /articles/{id}/save` | `queries.save_article` | save/bookmark |
| `POST /articles/{id}/unsave` | `queries.unsave_article` | remove save |
| `POST /threads/{id}/dismiss` | `management.set_thread_dismissed(…, True)` | hide thread (reversible) |
| `POST /threads/{id}/restore` | `management.set_thread_dismissed(…, False)` | un-dismiss |

- Use `POST` (state-changing) with empty/minimal bodies; return the updated resource or a small
  status JSON. `404` if the target id doesn't exist.
- These mirror exactly what the web UI already lets an unauthenticated perimeter user do, so they
  introduce no new authority beyond the existing UI — but see the no-auth caveat in §6.

### 4. Pagination

- Use keyset cursors for `GET /articles` (and search/threads), reusing the **same ordering the web
  feed uses** (`_default_order` / `app.py` `_render_feed`/`_next_url`) so web and API paginate
  identically.
- **Cursor is opaque to clients**: base64 of the last item's sort key (the `(feed_published_at, id)`
  tuple that the feed orders by). Clients pass `next_cursor` back verbatim; they must not parse it.
- **Mechanism (decided):** add an optional `cursor` parameter to the relevant `queries.py`
  functions (`list_articles`, `search_articles`, `list_threads`) so web/MCP/API share one keyset
  implementation rather than duplicating it in the api layer.
- Expose `next_cursor` in the envelope (null on the last page).
- **Test:** paginate a >1-page result set across the cursor boundary and assert no gaps/overlaps.

### 5. Errors, docs, CORS

- Consistent JSON error body (FastAPI default `{ "detail": ... }` is fine); `404` for missing
  resources, `422` for bad params (FastAPI validation).
- FastAPI auto-generates OpenAPI + interactive docs for the mounted app (e.g. `/api/v1/docs`) —
  useful for client developers.
- **CORS:** add `CORSMiddleware` on the sub-app, with allowed origins from a setting
  `API_CORS_ALLOW_ORIGINS` (comma-separated), **default `*`** with `allow_credentials=False` (safe
  with `*` since there are no cookies/auth). Native apps and TUIs don't enforce CORS, so this is
  mainly for any future browser client.
- **Security caveat (write it in code comment + docs):** unauthenticated state-changing `POST`s
  plus permissive CORS are acceptable **only** behind the network perimeter (Tailscale /
  `127.0.0.1`). The later Cloudflare Access auth phase must add real auth and tighten CORS / add
  origin/CSRF enforcement before any public exposure.

### 6. Explicitly out of scope (this phase)

- **Authentication / authorization** — none. The API inherits the web service's network-perimeter
  exposure (bound to `127.0.0.1` / Tailscale); it must **not** be exposed publicly until the
  planned auth phase (Cloudflare Access JWT) lands. The exposed writes are non-destructive
  reader-state only and match what the existing unauthenticated web UI already permits, so no new
  authority is added — but it still must stay behind the perimeter until auth exists.
- **Destructive / admin mutations** — source/category add/remove/rename/enable/disable, interest-
  profile edits, and any config changes are **not** exposed.
- **Content-generation triggers** — brief refresh/enqueue and recluster are **not** exposed (they
  incur LLM cost / are operational actions; keep to admin CLI / MCP). See Pending Decisions.
- No separate deployable container/service yet.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-api/` (new) | new package: FastAPI router/sub-app, Pydantic response models, pagination envelope, read GET routes + non-destructive reader-state POST routes, dependencies; `pyproject.toml` (workspace member, depends on `aggregator-common`) |
| `packages/aggregator-web/.../app.py` | mount the api router/sub-app at `/api/v1` |
| `packages/aggregator-web/pyproject.toml` | depend on `aggregator-api` (workspace) |
| `packages/aggregator-common/.../queries.py` | (if chosen) add optional `cursor` param for keyset pagination shared by web/api |
| `packages/aggregator-api/tests/` | endpoint tests (testcontainers harness): list/search/get articles, threads + members, brief, sources/categories, pagination, 404s |
| `.env.example`, `CLAUDE.md` | document the API surface, the `/api/v1` mount, CORS setting, and the no-auth caveat |

## Acceptance Criteria

- A client can, over `GET /api/v1/...`, list/search/get articles, list/get threads + members, get
  the latest brief, and list sources/categories — all as JSON with stable Pydantic-defined shapes.
- `GET /api/v1/articles` supports the same views/filters as the web feed and returns a paginated
  envelope with a working `next_cursor`.
- `GET /api/v1/threads/{id}` does **not** modify `last_viewed_at` (passive read).
- A client can perform the non-destructive reader-state writes via `POST`: mark article
  read/unread, save/unsave, and dismiss/restore a thread; each returns the updated state and `404`s
  on a missing id.
- **No destructive/admin mutation endpoint exists** in `/api/v1` (no source/category management,
  no config edits, no brief refresh/recluster).
- The API is served from the existing web container (no new service); OpenAPI docs are available.
- Missing resources return `404`; invalid params return `422`.
- Tests cover the read endpoints, the reader-state write endpoints, and pagination; full gate green.
- Docs note the no-auth status and that the API must not be publicly exposed until auth lands.

## Resolved Decisions

- **Mount style:** mounted sub-app `app.mount("/api/v1", aggregator_api.app)` — isolated OpenAPI at
  `/api/v1/docs` and sub-app-scoped CORS (see §1).
- **Cursor:** opaque base64 of `(feed_published_at, id)`; add `cursor` to the shared `queries.py`
  functions so web/MCP/API share one keyset mechanism (see §4).
- **Serialization:** Pydantic `from_attributes=True` from the dataclass results + per-model
  contract test (see §2).
- **CORS / no-auth:** `CORSMiddleware`, `API_CORS_ALLOW_ORIGINS` default `*`,
  `allow_credentials=False`; tolerated only behind the perimeter, to be tightened in the auth phase
  (see §5).

## Pending Decisions

- **`/healthz`:** reuse the web service's, or expose an api-specific one under `/api/v1`. (Minor —
  decide at implementation.)
- **Brief refresh:** kept out this phase (LLM-cost action). If an app wants a "refresh today's
  brief" button later, expose `POST /brief/refresh` (→ `queries.enqueue_brief`) then — likely
  alongside auth, since it triggers cost.
