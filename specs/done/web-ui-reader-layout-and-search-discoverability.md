---
name: Web UI reader layout and search discoverability
id: spec-272201e1
description: "aggregator-web layout pass: make the existing full-text search discoverable via a visible sidebar entry (desktop + drawer), move article-card and reader actions into compact top-right icon toolbars, de-duplicate the reader's 'Open source' control, hide the LLM importance reason behind a tooltip, and constrain the reader to a comfortable centered max-width on wide windows. Presentation + small HTMX/JS wiring within aggregator-web only. No pipeline/schema/data changes."
dependencies: null
priority: medium
complexity: low
status: done
tags:
- web
- ui
- htmx
- search
- polish
scope:
  in: "aggregator-web only: shell.html, _sidebar.html, _article_card.html, _article_detail.html, app.js, styles.css. Reuses the existing /search route + _search.html and the existing focusSearch() flow."
  out: "No backend search changes (the /search route and websearch_to_tsquery query are unchanged), no new search features beyond exposing what exists, no pipeline/summarize-rank/schema/data changes, no change to article content. The article cards' content and the reader's content are unchanged — only action placement, the importance-reason display, and reader width change."
feature_root_id: B-c25410f1
---
# Web UI reader layout and search discoverability

## Objective

A second round of `aggregator-web` UI polish from live testing. Five presentation/wiring changes, all confined to `aggregator-web`, no backend/pipeline/schema/data changes:

1. Make the (already-built) full-text search **discoverable** on desktop.
2. Move article-**card** Mark-read/Save into a compact **top-right icon toolbar**.
3. Move the **reader** (detail view) actions into a top-right **icon toolbar** and remove the duplicate "Open source".
4. Hide the LLM **importance reason** behind a tooltip in the reader (free up content space).
5. Constrain the **reader width** so long articles don't span the full width of wide windows.
6. Add a **keyboard-shortcuts help overlay** (toggled with `?`) so the existing shortcuts are discoverable.
7. **Reset the reader scroll position** to the top when a new article is loaded.

## Problems to Fix

1. **Search is undiscoverable on desktop.** Full-text search is fully implemented — a `GET /search` route (Postgres `websearch_to_tsquery`), `_search.html` form/results, and `focusSearch()` in `app.js` that loads the search pane into `#article-list` and focuses `#search-input`. But the only desktop trigger is the `/` keyboard shortcut; the sole visible affordance is `mobile-search-btn` in the mobile header (`shell.html:40`), hidden on desktop. Users on desktop have no way to find search.

2. **Card actions sit at the bottom and use text.** `_article_card.html` puts Mark-read/Save in a `.card-actions` row at the bottom of the card body. Requested: move them to the **top-right of the card** as icon-only controls (the cards are otherwise fine).

3. **Reader actions clutter the content column, and "Open source" is duplicated.** `_article_detail.html` renders "Open source" twice — a text link in the meta row (lines 36–40, `.open-source-link`) and a button in the actions row (lines 107–112, `.btn-open-source`). All actions (Mark read / Save / Open source) sit in a `.detail-actions` row (lines 87–113) below the chips, consuming content space. Requested: a single compact **icon toolbar pinned to the top-right** of the reader header, with no duplicate.

4. **The LLM importance reason takes visible space in the reader.** `_article_detail.html` lines 62–64 render `article.importance_reason` as visible text (`.importance-reason`) next to the score. The reasoning is not useful while reading. Requested: drop the visible text; expose it only as the score badge's hover tooltip. (The article cards already show only the badge with a tooltip — no change needed there.)

5. **The reader is too wide on large windows.** The reader content is unconstrained: `.article-detail` (the detail template root, `_article_detail.html:14`) has **no** width rule, and the existing `max-width: 680px` rule targets `.reader-pane article` (`styles.css:659`) — an `<article>` element the detail template does not use (its root is a `<div class="article-detail">`), so it never applies. Long articles span the full pane width, which is uncomfortable to read on wide monitors.

## Changes

### A. Make search discoverable (desktop + drawer)
- Add a visible search affordance at the **top of the sidebar content** (`_sidebar.html`, above "Smart Views") — a search box/button that, when clicked or focused, opens the existing search pane via the same path as the `/` shortcut (call `focusSearch()` / load `GET /search` into `#article-list` and focus `#search-input`). It must be visible on desktop and within the mobile drawer.
- Because `#sidebar` re-fetches `/sidebar` on `load` and `refreshSidebar` (`shell.html:61–63`, `hx-swap="innerHTML"`), placing the affordance inside `_sidebar.html` keeps it present after every sidebar refresh.
- Keep the existing `/` keyboard shortcut and the `mobile-search-btn`. Reuse the existing `/search` route and `_search.html` unchanged.
- The affordance must be keyboard-focusable and have an accessible label.

### B. Card actions → top-right icon toolbar
- In `_article_card.html`, move the Mark-read and Save controls out of the bottom `.card-actions` row into a compact action group **pinned to the top-right of the card**, rendered as **icon-only** buttons (no text labels), each with an `aria-label` and a `title` tooltip.
- Preserve existing HTMX behavior exactly: same `hx-post` targets (`/article/{id}/read|unread|save|unsave`), `hx-target="closest article"`, `hx-swap="outerHTML"`, and the read/saved visual states.

### C. Reader actions → top-right icon toolbar + de-dup
- In `_article_detail.html`, remove the duplicate "Open source": keep a single control. Move Mark-read, Save, and Open-source into a compact **icon-only** toolbar **pinned to the top-right of the reader header** (`.detail-header`).
- Remove the `.open-source-link` text link from the meta row (lines 36–40) and the separate `.btn-open-source` text button — represent "Open source" once, as an icon in the toolbar (still `target="_blank" rel="noopener"`).
- Preserve HTMX behavior for read/save: same `hx-post` endpoints, `hx-target="#article-detail"`, `hx-swap="outerHTML"`, and read/saved states. Each icon button keeps an `aria-label` + `title`.

### D. Importance reason → tooltip only
- In `_article_detail.html`, remove the visible `.importance-reason` text (lines 62–64). Fold the reason into the score badge's `title` attribute (e.g. `title="{{ importance_score }}/100 — {{ importance_reason }}"`), so it appears only on hover.

### E. Constrain reader width
- Add CSS so the reader content (`.article-detail` / `.detail-content`) is centered with a comfortable max reading width (in the ~680–760px range) that does not grow on wider windows, while remaining fluid (100% width) below that breakpoint. Squeeze the whole detail view (hero image, header, summary, body) consistently, not just the body text.
- Reuse existing spacing/color tokens; do not introduce new design primitives. Ensure it composes with the existing `.reader-pane` layout and the mobile overlay rules.

### F. Keyboard-shortcuts help overlay
- The app already supports `j` (next), `k` (previous), `v` (open source), `m` (toggle read/unread), and `/` (focus search) — bound in `_article_list.html:19–22` and `app.js` (`handleKey`). There is currently **no** in-UI way to discover them.
- Add a hidden help overlay (modal/panel) listing every shortcut and its action, toggled by pressing `?` (and dismissible with `Escape` or a close button / click-away). Suppress the `?` trigger while a text input/textarea is focused (mirror the existing `_inputFocused()` / input-tag guard).
- Implement with the existing Alpine + hand-rolled CSS approach (e.g. an `x-show` panel in `shell.html` toggled by a flag on `aggregatorApp()`), so it works across the whole app, not just the list pane. The overlay markup should be present in the rendered shell so it is server-side assertable.
- The shortcut list is the single source of truth shown to users; keep it consistent with the actual bindings (j/k/v/m, /, and ? itself).

### G. Reset reader scroll on article load
- When a new article is loaded into the reader, the pane currently retains the previous article's scroll position. `_loadReader(id)` in `app.js` (`app.js:136–142`) swaps `GET /article/{id}` into `#reader-pane` (desktop) but never resets scroll.
- After the swap completes, scroll the reader to the top (e.g. set `#reader-pane`'s `scrollTop = 0`, or the scrolling container's, on `htmx:afterSwap` for that target). Ensure it works for both keyboard navigation (`j`/`k`) and clicking a card, and on the mobile overlay reader as well as the desktop pane.
- Identify the actual scrolling element (the reader pane vs. an inner content wrapper vs. window on mobile) and reset whichever one scrolls, so the top of the new article is always visible.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-web/src/aggregator_web/templates/_sidebar.html` | Add a visible, focusable search affordance at the top that opens the existing search pane |
| `packages/aggregator-web/src/aggregator_web/templates/shell.html` | Add the keyboard-shortcuts help overlay markup; wire `?` toggle + `Escape`/click-away dismiss on `aggregatorApp()` |
| `packages/aggregator-web/src/aggregator_web/static/app.js` | Ensure `focusSearch()` is callable from the new sidebar affordance; add the help-overlay toggle flag/handler to `aggregatorApp()` (guard `?` while inputs focused); reset reader scroll to top after `_loadReader()` swap |
| `packages/aggregator-web/src/aggregator_web/templates/_article_card.html` | Move Mark-read/Save to a top-right icon toolbar; icon-only with aria-label/title |
| `packages/aggregator-web/src/aggregator_web/templates/_article_detail.html` | Single top-right icon toolbar (read/save/open-source); remove duplicate Open source; fold importance reason into badge tooltip |
| `packages/aggregator-web/src/aggregator_web/static/styles.css` | Card + reader top-right toolbar layout/icon styling; `.article-detail`/`.detail-content` centered max-width; remove now-unused `.importance-reason` rule if present |
| `packages/aggregator-web/tests/` | Tests for the changes (see Acceptance Criteria) |

## Acceptance Criteria

- **Search discoverable:** the rendered sidebar (`GET /sidebar`) contains a visible, focusable search affordance with an accessible label (`aria-label` or associated `<label>`) and the wiring that triggers the existing search flow (e.g. an `@click`/handler calling `focusSearch()` or an `hx-get="/search"`). A test asserts the affordance element and its trigger attribute are present in the `/sidebar` HTML. The `/` shortcut and mobile button still work; `/search` and `_search.html` are unchanged. *(Actual pane-loading on click is browser behavior — verified manually, not in the pytest suite.)*
- **Card actions:** `_article_card.html` renders Mark-read and Save as icon-only controls (no visible text label) in a top-right action group. A test asserts: the toolbar markup is present; the read button carries an `aria-label` and the `is-read`-dependent state marker; and `POST /article/{id}/read` re-renders the card with the `is-read` class applied. Existing `hx-post` endpoints, `hx-target="closest article"`, and `hx-swap` are preserved.
- **Reader actions:** `GET /article/{id}` renders exactly **one** "Open source" control (test asserts occurrences of the open-source link/control == 1, not 2), and the read/save/open-source controls are in a single top-right toolbar. Read/save HTMX behavior unchanged (existing interaction tests still pass).
- **Importance reason:** the rendered `GET /article/{id}` HTML contains **no** element with class `importance-reason`, and (when `importance_reason` is set) the reason substring appears inside the score badge's `title` attribute. A test asserts both. *(Hover rendering itself is browser behavior, out of scope for the pytest suite.)*
- **Reader width:** `styles.css` contains a `max-width` rule scoped to `.article-detail` (or `.detail-content`) so the reader is centered and capped. A test asserts the stylesheet contains a `max-width` declaration for that selector. *(Visual centering/capping on wide windows is verified manually.)*
- **Shortcuts overlay:** the rendered shell (`GET /`) contains the help-overlay markup listing the shortcuts (j/k/v/m, /, ?), initially hidden, with a `?`-toggle binding and a dismiss control. A test asserts the overlay markup and each shortcut label are present in the shell HTML. *(Toggle/dismiss interaction is browser behavior, verified manually.)*
- **Reader scroll reset:** `app.js` resets the reader's scroll position to the top after a new article is loaded into `#reader-pane`. A test asserts the scroll-reset logic is wired to the reader load/swap (e.g. the relevant `scrollTop = 0` / scroll-reset call is present in `app.js` and associated with the reader target). *(Actual scroll behavior is browser-verified manually.)*
- All existing `aggregator-web` tests continue to pass; `bash scripts/run-tests.sh` is green.

## Pending Decisions

None — scope is fixed and confined to `aggregator-web`. Search work here is strictly exposing the existing implementation; any new search behavior is out of scope and will be specced separately if needed.
