---
name: Unify sidebar search and add mark-read-and-next shortcut
id: spec-9dfb2084
description: "aggregator-web: collapse the confusing two-search-box UX into a single real search field in the sidebar (the middle pane shows results only), and add an 'n' keyboard shortcut that marks the selected article read and advances to the next. aggregator-web only; no schema/pipeline changes."
dependencies: null
priority: medium
complexity: medium
status: done
tags:
- web
- ui
- search
- htmx
- shortcuts
scope:
  in: "aggregator-web only: _sidebar.html (search input), _search.html (results-only), app.js (focusSearch + markReadAndNext), _article_list.html (n binding), shell.html (shortcuts overlay row), styles.css, /search route (results-only render). Reuses existing search query + article-list rendering."
  out: "No schema/pipeline changes, no change to the search query/ranking itself, no new backend search features. Reader-pane behavior unchanged."
feature_root_id: B-258d3f6e
---
# Unify sidebar search and add mark-read-and-next shortcut

## Objective

Two `aggregator-web`-only UI changes from live testing:

1. **One search box, not two.** Make the sidebar search affordance the *actual* search input; the middle pane renders search **results only**.
2. **`n` = mark read and advance.** Add a keyboard shortcut that marks the selected article read and moves selection to the next article in one keystroke.

## Problems to Fix

1. **Duplicate, confusing search.** The sidebar "Search" affordance (`_sidebar.html`, `.sidebar-search-btn`) is a *button* that calls `focusSearch()` (`app.js`), which loads `/search` into `#article-list`; `_search.html` then renders its *own* `<input id="search-input">` (small, top-left of the middle pane). The result is two search boxes — the sidebar one looks like a field but isn't, and the real input appears awkwardly in the content pane. Users reasonably expect the sidebar field to *be* the search box.

2. **No "mark read and next".** Clearing an article requires `m` (toggle read) then `j` (next) as two keystrokes. A single `n` to mark the selected article read and advance would speed up triage. (`m` and `j` remain.)

## Background (verified against the codebase)

- Sidebar search: `_sidebar.html` renders `.sidebar-search-wrap > button.sidebar-search-btn` (magnifier SVG + "Search" label + `/` hint) with `@click="focusSearch()"`.
- `focusSearch()` (`app.js`, on the `aggregatorApp()` root component): `htmx.ajax('GET','/search',{target:'#article-list',swap:'innerHTML'})` then focuses `#search-input`.
- `_search.html`: a `.search-pane` containing a `.search-form` (`hx-get="/search"`, debounced `input changed` from `#search-input`) with `<input id="search-input" name="q">` + a submit button, followed by results (`{% include "_article_list.html" %}` or a "No results" message).
- `/search` route (`app.py`): runs the full-text query (`websearch_to_tsquery`) and returns `_search.html`.
- The sidebar `<nav id="sidebar">` re-fetches `/sidebar` on `hx-trigger="load, refreshSidebar from:body, every 60s"` with `hx-swap="innerHTML"` — so anything inside it is re-rendered every 60s. A stateless button is fine to replace; a live text input is **not** (it would lose focus and the typed query mid-use).
- Keyboard shortcuts: `_article_list.html` binds `@keydown.{j,k,v,m}.window` to `articleList()` methods (`selectNext`, `selectPrev`, `openSelected`, `toggleReadSelected`); `_inputFocused()` guards them. The `?` help overlay lives in `shell.html`.

## Changes

### A. Sidebar field becomes the real search input
- In `_sidebar.html`, replace the `.sidebar-search-btn` button with a real search input, e.g.:
  ```
  <input type="search" id="sidebar-search-input" name="q" class="sidebar-search-input"
         placeholder="Search…" autocomplete="off"
         hx-get="/search" hx-target="#article-list" hx-swap="innerHTML" hx-push-url="true"
         hx-trigger="input changed delay:400ms, search"
         hx-preserve="true">
  ```
  Keep the magnifier icon and the `/` hint as adornments. Typing performs a debounced search whose results replace `#article-list` (the middle pane).
- **Survive the 60s sidebar refresh:** the input MUST NOT be wiped/refocused when `#sidebar` re-renders. Use `hx-preserve="true"` with a stable `id` on the input so HTMX keeps the live element (value + focus) across the ancestor `/sidebar` swaps. **Note:** `hx-preserve` matches by `id`, so `_sidebar.html` (the `/sidebar` partial) must keep rendering `#sidebar-search-input` on *every* refresh — if a future change drops it from the refreshed markup, preserve silently fails. (If `hx-preserve` proves insufficient, the acceptable alternative is to render the search input in `shell.html` *outside* the polled `#sidebar` innerHTML region.) Verify a sidebar refresh does not clear an in-progress query or steal focus.

### B. Middle pane = results only
- In `_search.html`, remove the `.search-form`, `#search-input`, and submit button. Render **only**: the `_article_list.html` results include when there are matches; the "No results for …" message when `query` is non-empty with no matches; and nothing (or a subtle "Type to search" hint) when `query` is empty.
- `/search` route: keep the query logic unchanged; it continues to return `_search.html` (now results-only). Empty `q` returns the empty/hint state.
- Remove the now-unused `#search-input`-driven form wiring.

### C. `focusSearch()` simplification
- Change `focusSearch()` in `app.js` to simply focus (and select) `#sidebar-search-input` — the input is always present in the sidebar, so no pane-loading is needed. Keep the `/` shortcut (`handleKey`) and the mobile `mobile-search-btn` both calling `focusSearch()`.

### D. `n` = mark read and advance
- Add a `markReadAndNext()` method to `articleList()` in `app.js`: guarded by `_inputFocused()`; if an article is selected, mark it read (POST `/article/{id}/read`, same as the read path, only when not already read), then advance selection to the next article (as `selectNext()` does). Ensure the advance still lands on the correct next card given the card re-render.
- Bind it in `_article_list.html`: `@keydown.n.window="markReadAndNext()"` alongside the existing j/k/v/m bindings.

### E. Shortcuts overlay + styling
- Add a row to the `?` help overlay in `shell.html`: `<tr><td><kbd>n</kbd></td><td>Mark read &amp; next</td></tr>`.
- In `styles.css`, style `.sidebar-search-input` as a proper full-width sidebar field (reuse the look/box of the old `.sidebar-search-btn`); remove or repurpose the now-unused `.sidebar-search-btn` and the middle-pane `.search-form` / `.search-input` / `.search-submit` rules.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-web/src/aggregator_web/templates/_sidebar.html` | Replace search button with a real `#sidebar-search-input` (hx-get /search → #article-list, debounced, `hx-preserve`) |
| `packages/aggregator-web/src/aggregator_web/templates/_search.html` | Render results only (drop the form/input/submit) |
| `packages/aggregator-web/src/aggregator_web/static/app.js` | `focusSearch()` focuses `#sidebar-search-input`; add `markReadAndNext()` to `articleList()` |
| `packages/aggregator-web/src/aggregator_web/templates/_article_list.html` | Add `@keydown.n.window="markReadAndNext()"` |
| `packages/aggregator-web/src/aggregator_web/templates/shell.html` | Add the `n` row to the shortcuts overlay |
| `packages/aggregator-web/src/aggregator_web/static/styles.css` | Style `.sidebar-search-input`; remove unused button/pane-input rules |
| `packages/aggregator-web/tests/` | Tests (see Acceptance Criteria) |

## Acceptance Criteria

- **Single search input:** `GET /sidebar` renders an `<input id="sidebar-search-input">` with `name="q"`, `hx-get="/search"`, `hx-target="#article-list"`, and `hx-preserve` present; the old `.sidebar-search-btn` button is gone. A test asserts these.
- **Results-only pane:** `GET /search?q=<term>` returns markup that contains **no** search input/form (assert no `id="search-input"` and no `.search-form`); with matching articles it includes article cards; with a non-empty `q` and no matches it includes the "No results" message. Tests assert all three.
- **`n` shortcut wired:** `_article_list.html` (rendered via a feed, e.g. `GET /feed/smart/all`) contains `@keydown.n.window` bound to the mark-read-and-next handler; `app.js` defines `markReadAndNext` on `articleList()`. Tests assert the binding is present and that `POST /article/{id}/read` still marks an article read (the endpoint is unchanged).
- **Overlay updated:** `GET /` shows an `n` entry labelled mark-read-and-next in the shortcuts overlay.
- **focusSearch target:** `app.js` `focusSearch()` references `#sidebar-search-input`.
- All existing `aggregator-web` tests pass; `bash scripts/run-tests.sh` is green.
- *(Browser-only, verified manually: typing in the sidebar field shows results in the middle pane with no second box; the field keeps its value/focus across a 60s sidebar refresh; `/` focuses it; `n` marks the selected article read and moves to the next.)*

## Pending Decisions

None — scope is fixed and confined to `aggregator-web`. Behavior when the query is cleared (empty `q`) shows the empty/hint state rather than auto-restoring the previous feed; restoring the prior feed on clear is out of scope.
