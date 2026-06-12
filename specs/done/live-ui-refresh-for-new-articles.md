---
name: Live UI refresh for new articles
id: spec-3d210ada
description: "aggregator-web live refresh: sidebar unread counters auto-refresh on a 60s timer, and the article list shows a non-disruptive 'N new articles' pill (polled every 60s) that loads new items on click instead of silently rewriting the list. Reuses existing /sidebar + feed query helpers; adds /updates count endpoints mirroring the /read-all pattern. aggregator-web only; no schema/pipeline changes."
dependencies: null
priority: medium
complexity: medium
status: done
tags:
- web
- ui
- htmx
- live-refresh
scope:
  in: "aggregator-web only: shell.html (sidebar poll), _article_list.html (+ a small pill fragment template), app.py (/updates route variants), feeds.py (new-article count helpers reusing existing view filters), styles.css (pill styling), and tests."
  out: "No schema/migration changes, no changes to retriever/processor/summarize-rank, no websockets/SSE (HTMX polling only), no auto-replacement of the article list (the pill is click-to-load to preserve reading position). Reader pane is not auto-refreshed."
feature_root_id: B-8eb5c6c2
---
# Live UI refresh for new articles

## Objective

Make the web UI reflect new articles produced by the background jobs without a manual page reload, while preserving the reader's place. Two parts, both `aggregator-web`-only, no schema/pipeline changes:

1. **Sidebar counters auto-refresh** every 60s.
2. **Article list "N new articles" pill** — polled every 60s; clicking it loads the new items. The list is never silently rewritten (that would disrupt scroll position, the selected article, and infinite scroll).

## Problems to Fix

Today nothing in the UI polls. The sidebar (`shell.html`) uses `hx-trigger="load, refreshSidebar from:body"` and the article list uses `hx-trigger="load"` — there is no timer. So when the retriever/processor/summarize-rank pull and rank new articles, the unread counters and the article list only change on a full page reload (or, for the sidebar, after the user marks something read/saved, which fires `refreshSidebar`). The user has no live indication that new content has arrived.

## Background (verified against the codebase)

- Sidebar: `shell.html` `<nav id="sidebar" hx-get="/sidebar" hx-trigger="load, refreshSidebar from:body" hx-swap="innerHTML">`. The `/sidebar` route + `get_sidebar_counts()` (feeds.py) already compute all counts.
- Article list: `<main id="article-list" hx-get="/feed/smart/unread" hx-trigger="load" hx-swap="innerHTML">`.
- Feed rendering: `_render_feed(request, page, session, base_url, unread_only, hx_request, cursor)` (app.py ~142) already receives `base_url` (e.g. `/feed/smart/unread`, `/feed/category/<name>`, `/feed/source/<id>`) and renders `_article_list.html` with `{articles, next_url}`. `page` is a `FeedPage` (feeds.py) with `.articles` (ORM rows) and `.next_cursor`.
- Per-view queries live in `feeds.py`: `smart_feed(view,…)`, `category_feed(name,…)`, `source_feed(source_id,…)`, each composing `_ready_base()` + a view filter (+ optional `Article.is_read == False`). `get_sidebar_counts()` shows the count pattern: `select(func.count(Article.id)).where(...)`.
- There is an established pattern of view-mirrored POST routes: `/feed/smart/{view}/read-all`, `/feed/category/{name}/read-all`, `/feed/source/{source_id}/read-all` (app.py ~289-308). The new `/updates` endpoints should mirror this exact structure.
- Article `id` is a monotonic Identity PK assigned at retrieval, so "new since load" can be expressed as `Article.id > <max id currently shown>`.

## Changes

### A. Sidebar counters auto-refresh (trivial)
- In `shell.html`, add a 60s poll to the existing sidebar trigger: `hx-trigger="load, refreshSidebar from:body, every 60s"`. No route changes — it re-fetches `/sidebar` (which recomputes counts) every 60s, plus the existing load/refreshSidebar triggers.

### B. "N new articles" pill on the article list
**Marker:** the newest article id currently displayed. In `_render_feed`, compute `newest_id = max((a.id for a in page.articles), default=0)` from the **displayed (post-filter)** articles and pass `base_url` and `newest_id` into the `_article_list.html` context. (Only needs to be added to the full-list render path, not the infinite-scroll cursor path.) The `/updates` count query independently re-applies the **identical** view + unread filter with `Article.id > since`, so the count stays consistent with what the list would show on reload (e.g. under the unread filter, only newer *unread* matching articles are counted).

**Cumulative-since-load semantics (intended):** the poller embeds the load-time `since={{ newest_id }}` and reuses that fixed value on every poll, so the pill shows the **total** new-since-load count (it keeps growing until the user clicks to reload). This is deliberate — do not turn it into a per-poll delta. Clicking the pill reloads the feed, which re-renders with a fresh `newest_id`, resetting the baseline.

**Poller + pill container:** in `_article_list.html`, add a banner/poller element at the top of the list, e.g.:
```
<div id="new-articles-banner"
     hx-get="{{ base_url }}/updates?since={{ newest_id }}{% if unread_only %}&unread=1{% endif %}"
     hx-trigger="every 60s"
     hx-swap="innerHTML"></div>
```
The poller hits the matching `/updates` endpoint every 60s and swaps in the pill fragment (or empty).

**`/updates` endpoints** (mirror the `/read-all` route trio) in `app.py`, each accepting `since: int` (and the existing `unread` query param), returning the pill fragment:
- `GET /feed/smart/{view}/updates`
- `GET /feed/category/{name}/updates`
- `GET /feed/source/{source_id}/updates`

Each computes the count of **ready** articles matching that view's filters **with `Article.id > since`**. Add count helpers in `feeds.py` that reuse the exact same filter composition as `smart_feed`/`category_feed`/`source_feed` (i.e. `_ready_base()` + the view filter + optional unread), adding `Article.id > since`, and return `select(func.count(Article.id)).where(...)`. Do not duplicate filter logic — factor/reuse the existing helpers (`_ready_base`, `_smart_extra_filter`, the category `.contains([name])`, the `source_id ==` filter).

**Pill fragment** (`_new_articles_pill.html`, rendered by the `/updates` endpoints with `{count, base_url, unread_only}`):
- If `count > 0`: render a clickable pill that reloads the feed —
  ```
  <button class="new-articles-pill"
          hx-get="{{ base_url }}{% if unread_only %}?unread=1{% endif %}"
          hx-target="#article-list"
          hx-swap="innerHTML">{{ count }} new article{{ '' if count == 1 else 's' }} ↑</button>
  ```
- If `count == 0`: render empty (so a stale pill clears once the user has loaded/caught up).

**Click behavior:** clicking the pill re-issues the feed request into `#article-list`, which re-renders the list (new articles included, ordered as usual) and resets `newest_id`, so the pill disappears and scroll returns to top. This is the intended "load new items" action — no surgical prepend needed.

### C. Styling
- Add a `.new-articles-pill` style in `styles.css` — a small, centered, accent-colored pill at the top of the list. Reuse existing color/spacing tokens; the empty state (no pill) must take no vertical space.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-web/src/aggregator_web/templates/shell.html` | Add `every 60s` to the `#sidebar` `hx-trigger` |
| `packages/aggregator-web/src/aggregator_web/app.py` | Pass `base_url` + `newest_id` into `_article_list.html`; add `/feed/{smart,category,source}/…/updates` GET routes returning the pill fragment |
| `packages/aggregator-web/src/aggregator_web/feeds.py` | Add new-article count helpers (per view) reusing existing filter composition + `Article.id > since` |
| `packages/aggregator-web/src/aggregator_web/templates/_article_list.html` | Add the `#new-articles-banner` poller element |
| `packages/aggregator-web/src/aggregator_web/templates/_new_articles_pill.html` | **New** — pill fragment (count>0 → clickable reload button; else empty) |
| `packages/aggregator-web/src/aggregator_web/static/styles.css` | `.new-articles-pill` styling |
| `packages/aggregator-web/tests/` | Tests (see Acceptance Criteria) |

## Acceptance Criteria

- **Sidebar poll:** `GET /` renders the `#sidebar` element with `every 60s` present in its `hx-trigger` (test asserts the substring on the sidebar element). The `/sidebar` endpoint behavior is otherwise unchanged.
- **List poller present:** `GET /feed/smart/all` (full list render) includes a `#new-articles-banner` element whose `hx-get` targets the matching `/feed/smart/all/updates` endpoint with a `since=` param and `hx-trigger` containing `every 60s`. The `since` value equals the max article id in the rendered list (or 0 when empty).
- **Updates count — smart:** with the latest ready article id = M, `GET /feed/smart/all/updates?since=M` returns an **empty** pill fragment (no `new-articles-pill`); after inserting a newer ready article (id > M), the same call returns a fragment containing a `new-articles-pill` whose text includes the count (1) and whose `hx-get` reloads `/feed/smart/all` into `#article-list`. A test asserts both states.
- **`since=0` boundary:** with ready articles present, `GET /feed/smart/all/updates?since=0` returns a positive count (all ready articles are "newer than 0") — locks in the empty-list-then-populated path. A test asserts this.
- **View filters respected:** `category`/`source` `/updates` count only articles matching that category/source (a new ready article in a different category/source does not increase the count); the `unread` param restricts to unread. Tests assert this for category and source, and for the unread filter.
- **Singular/plural:** the pill text is "1 new article" vs "N new articles" (test asserts both).
- All existing `aggregator-web` tests continue to pass; `bash scripts/run-tests.sh` is green.
- *(Browser-only, verified manually: the pill appears within ~60s of new articles being ranked, clicking it loads them and clears the pill, and the sidebar counts tick up on the timer — none of which rewrites the list mid-read.)*

## Pending Decisions

None — scope is fixed and confined to `aggregator-web`. Reader-pane live refresh, websockets/SSE, and surgical card prepending are explicitly out of scope.
