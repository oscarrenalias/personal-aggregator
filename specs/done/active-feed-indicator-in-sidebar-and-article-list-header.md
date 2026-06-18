---
name: Active feed indicator in sidebar and article list header
id: spec-801ae2c3
description: "Always show which feed/view is active: (A) a clearly-visible active state on every sidebar link via an Alpine nav store (HTMX feed nav doesn't push URL, so path-based highlighting can't work), seeded from the server on load; (B) a human-readable title header at the top of the article-list panel naming the current smart view / category / source. Web-only, additive."
dependencies: null
priority: medium
complexity: null
status: done
tags:
- web
- ux
- navigation
- sidebar
- htmx
- alpine
scope:
  in: null
  out: null
feature_root_id: null
---
# Active feed indicator in sidebar and article list header

## Objective

Make it always obvious **which feed/view the user is currently reading**, via two complementary
cues: (A) a clearly-visible active state on the corresponding sidebar link, and (B) a title header
at the top of the article-list panel naming the current view. Today neither exists for feed views
— only the Threads link highlights — so a user landing mid-scroll has no indication of context.

## Problems to Fix

- **Sidebar:** only the Threads link has an active state (Alpine `is-active` bound to
  `window.location.pathname.startsWith('/threads')` in `_sidebar.html`). The Today link, all Smart
  Views (All/Unread/Saved/Important/Uncategorized), categories, and sources have **no** active
  indicator. Worse, feed links navigate via HTMX (`hx-get`) **without changing the URL**, so the
  `window.location.pathname` technique cannot work for them anyway.
- **Article-list panel:** `_render_feed` passes `articles`, `next_url`, `base_url`, `unread_only`,
  `sort` — but **no human-readable title**. `_article_list.html` renders sort/filter controls with
  nothing naming the current feed, so the panel itself never says what you're looking at.

## Changes

### A. Active state on every sidebar link (Alpine nav store)

Because HTMX feed navigation does not push the URL, track the active view in client state rather
than from the path:

- Add a small Alpine store `nav` with a `current` string (the active view **key**) and a setter.
- Each sidebar link sets `$store.nav.current` to its key on click (`@click`) and binds
  `:class="{ 'is-active': $store.nav.current === '<key>' }"` plus
  `:aria-current="$store.nav.current === '<key>' ? 'page' : undefined"`.
- View keys (stable identifiers, one per link): `today`, `threads`, `smart/all`, `smart/unread`,
  `smart/saved`, `smart/important`, `smart/uncategorized`, `category/<name>`, `source/<id>`.
- **Initial load (authoritative seed):** the shell route (`GET /`, `index` in app.py) renders the
  landing view; it injects the landing view key into the template context and the shell sets the
  initial store value `Alpine.store('nav', { current: '<key>' })`. The **default landing key is
  `smart/all`** (the current landing feed) — not "or whatever," it is fixed so the initial active
  link is deterministic. A fresh load/refresh therefore shows the correct active link before any
  click.
- **Threads:** migrate the existing path-based highlight to the same store mechanism for
  consistency (keep it working on `/threads` deep-loads).
- **Sidebar re-render (concrete mechanism):** the `:class`/`:aria-current` bindings live on the
  sidebar links, which are inside the fragment swapped on the `refreshSidebar` trigger. Alpine does
  not automatically process newly-swapped DOM, so the swapped fragment must be re-initialized:
  bind an `htmx:afterSwap` listener (scoped to the sidebar target) that calls
  `Alpine.initTree(evt.detail.target)`, **or** keep the `:class` bindings on a persistent wrapper
  element *outside* the swapped region that reads `$store.nav.current`. Pick the
  persistent-wrapper approach if the sidebar markup allows it (no re-init needed); otherwise use
  `Alpine.initTree` on swap. The store itself persists across swaps regardless. This must be
  specified in the implementation, not left to "if needed."
- **Styling:** make `is-active` clearly visible (e.g. accent left-border/background + stronger
  text weight), not the current subtle treatment. Must meet contrast in the dark theme.

### B. Title header in the article-list panel

- **Centralized label mapping (single source of truth):** add one dict in app.py mapping each
  smart-view key → display label (`smart/all`→"All", `smart/unread`→"Unread", `smart/saved`→
  "Saved", `smart/important`→"Important", `smart/uncategorized`→"Uncategorized"). The
  category/source routes derive `view_title` from the looked-up category name / source name they
  already load. `_render_feed` gains a `view_title` parameter; every feed route passes it from this
  one mapping (or the looked-up name) rather than five inline string literals.
- `_article_list.html` renders `view_title` as a header above `feed-controls` (the sort/unread
  toggle row). Keep it compact; it should read as the panel's heading.
- **Fragment boundary (explicit):** infinite scroll issues an HX-Request that the route renders as
  bare `_article_card.html` fragments appended via `hx-swap="afterend"` onto the last card —
  these never go through `_article_list.html`. The `view_title` header is rendered in
  `_article_list.html` **outside** the `#article-list` card container (the element cards are
  appended into), so appended pages structurally cannot re-inject the header. The route's
  fragment path (the `if hx_request and cursor:` branch in `_render_feed`) renders cards only and
  must not emit the header.
- Escape `view_title` (category/source names are user-controlled).

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-web/.../templates/_sidebar.html` | nav-store `@click` + `:class`/`:aria-current` on every link; drop path-based threads highlight |
| `packages/aggregator-web/.../templates/shell.html` (or JS init) | register Alpine `nav` store; seed `current` from server context |
| `packages/aggregator-web/.../templates/_article_list.html` | render `view_title` header above `feed-controls`; keep it out of the fragment path |
| `packages/aggregator-web/.../app.py` | pass `view_title` (+ key) from each feed route into `_render_feed`; pass initial view key to the shell |
| `packages/aggregator-web/.../static/*.css` | visible `is-active` style; list-header style |
| `packages/aggregator-web/.../static/*.js` (Alpine) | `nav` store definition if not inline |
| tests | route passes correct `view_title`; header present in full render and absent in fragment render |

## Acceptance Criteria

- Selecting any sidebar entry (Today, each Smart View, a category, a source, Threads) shows a
  clearly-visible active state on exactly that link; switching views moves the highlight.
- The active state is correct after a fresh page load/refresh (seeded from the server), not only
  after a click, and survives a `refreshSidebar` sidebar re-render.
- The article-list panel shows a header naming the current view (smart view label, category name,
  or source name); the name is HTML-escaped.
- Infinite-scroll appended pages do **not** duplicate the header.
- `aria-current="page"` is set on the active link for accessibility.
- Existing web tests pass; new tests cover `view_title` per route and header presence/absence
  across full vs fragment renders.

## Resolved Decisions

- **Default seed key:** `smart/all`, injected by the shell route — deterministic initial active
  link (see Change A).
- **`refreshSidebar` re-init:** persistent-wrapper binding preferred, else `Alpine.initTree` on
  `htmx:afterSwap`; specified, not left open (see Change A).
- **Label mapping:** one dict in app.py is the single source of truth; routes don't inline labels
  (see Change B).
- **Fragment boundary:** header lives in `_article_list.html` outside `#article-list`; the
  card-only fragment path never emits it (see Change B).
- **Today/Threads:** get the sidebar active state only; they don't use `_article_list.html`, so no
  list-panel header change.
- **Store location:** mirror the existing `$store.sidebar` registration pattern already in the
  shell/JS.

## Pending Decisions

- **Icon vs text-only header:** ship text-only; an icon per view kind is optional later polish.
