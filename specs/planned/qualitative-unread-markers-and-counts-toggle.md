---
name: Qualitative unread markers and counts toggle
id: spec-64f334b4
description: "Replace numeric unread-count badges in the sidebar with qualitative markers (dots for smart views/sources, freshness phrases for categories) behind a WEB_SHOW_UNREAD_COUNTS toggle that defaults to markers (counts off). Implements GitHub issue #3."
dependencies: null
priority: medium
complexity: null
status: planned
tags:
- web
- sidebar
- ux
- issue-3
scope:
  in: null
  out: null
feature_root_id: B-44bc3395
---
# Qualitative unread markers and counts toggle

Implements GitHub issue #3. Replaces numeric unread-count badges in the sidebar with
subtle qualitative markers (dots + category freshness phrases), behind a config toggle
that defaults to markers (counts off). Aligns the sidebar with the product's "reduce
noise, not chase inbox-zero" direction.

## Objective

Stop the sidebar showing big unread numbers ("Technology 48"), which encourage
count-chasing. Instead show **whether there's anything worth opening** and **how fresh /
notable** it is, with no numbers by default. A numeric mode remains available via config
for anyone who wants the old behavior.

## Problems to Fix

- Numeric unread badges everywhere (smart views, categories, sources) drive inbox-zero
  anxiety and don't convey *importance* — "48" tells you nothing about whether any of it
  matters to you.

## Changes

### 1. Config toggle (default = markers, counts OFF)

Add to `WebSettings` (`aggregator-web/config.py`): `web_show_unread_counts: bool = False`
(`WEB_SHOW_UNREAD_COUNTS`, default **false**). When `true`, the sidebar renders the
existing numeric badges unchanged (backward compatible). When `false` (default), it renders
the qualitative markers below. Document in `.env.example` + CLAUDE.md.

### 2. Marker model (per smart-view, category, source)

Extend the sidebar data (the `get_sidebar_counts` path in `feeds.py`) so each entry carries,
in addition to the existing unread count:
- `has_new: bool` — there is ≥1 unread (ready, not hidden) article. (Reuse the existing
  unread count > 0.)
- `has_priority: bool` — there is ≥1 unread article with `importance_score >=
  web_important_threshold` (high-priority / notable). Compute with the same query shape as
  the unread count, plus the importance filter.
- For **categories** additionally `last_activity: datetime | None` — `max(retrieved_at)`
  over that category's ready articles — used to derive a freshness phrase.

Keep it efficient (one grouped query per dimension, mirroring the current source GROUP BY;
no N+1). **Decision: extend the existing `SidebarCounts`** with these fields (one structure
threaded through the route → template), rather than a parallel structure.

### 3. Rendering — smart views & sources: a dot

In `_sidebar.html`, when counts are OFF, replace the `unread-badge` with a small dot:
- **empty** (`has_new == false`): no marker (muted row). On the **Unread** smart view
  specifically, when `has_new` is false, show a small muted **"All caught up"** label (in
  scope, tested).
- **has new** (`has_new && !has_priority`): a subtle dot (`•`, low-emphasis color).
- **high-priority new** (`has_priority`): a stronger/accent dot (same glyph, accent color).
No number is ever shown in marker mode.

### 4. Rendering — categories: a freshness phrase

For categories (marker mode), show a short right-aligned phrase instead of a dot/number,
derived deterministically:
- `has_priority` → **"New notable stories"**
- else `last_activity` today → **"Updated today"**
- else `last_activity` yesterday → **"Updated yesterday"**
- else (older / none / no unread) → **"Quiet"**
(Phrasing fixed in a small helper; "today"/"yesterday" are determined by comparing
`last_activity` to the current **UTC calendar day** — no timezone setting involved.)

### 5. Styling

Add `.sidebar-dot` (subtle + `.is-priority` accent variant) and `.sidebar-freshness`
(muted small text) classes in `styles.css`. The existing `.unread-badge` styling stays for
the counts-on mode.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-web/src/aggregator_web/config.py` | Add `web_show_unread_counts` (default False) |
| `packages/aggregator-web/src/aggregator_web/feeds.py` | Extend sidebar data with `has_new`/`has_priority` (+ category `last_activity`); efficient queries |
| `packages/aggregator-web/src/aggregator_web/app.py` | `/sidebar` passes markers + the toggle to the template |
| `packages/aggregator-web/src/aggregator_web/templates/_sidebar.html` | Conditional: numeric badge (toggle on) vs dot / freshness phrase (toggle off) |
| `packages/aggregator-web/src/aggregator_web/static/styles.css` | `.sidebar-dot` (+ priority variant), `.sidebar-freshness` |
| `.env.example`, `CLAUDE.md` | Document `WEB_SHOW_UNREAD_COUNTS` (default false) |
| `packages/aggregator-web/tests/` | Sidebar renders dots/phrases by default; numeric only when toggle on; has_priority/has_new/freshness logic |

## Acceptance Criteria

- Default (no env set): the sidebar shows **no numbers** — smart views & sources show a dot
  only when they have unread (subtle), a stronger dot when they have high-priority unread,
  and nothing when empty; categories show "New notable stories / Updated today / Updated
  yesterday / Quiet".
- With `WEB_SHOW_UNREAD_COUNTS=true`: the sidebar renders the existing numeric badges
  unchanged (regression-safe).
- `has_priority` is driven by unread articles with `importance_score >=
  web_important_threshold`; `has_new` by any unread; category freshness by `max(retrieved_at)`
  (+ priority). Covered by tests (a category with a high-priority unread shows "New notable
  stories"; one with only old read articles shows "Quiet"; etc.).
- Sidebar counting stays efficient (no N+1 beyond today's query count).
- Focused aggregator-web tests pass; full gate green. No schema change.

## Pending Decisions

- Default is **counts off** (markers), per the issue. Numeric mode is opt-in via env.
- "Today/yesterday" computed against the UTC calendar day for simplicity (revisit if a
  user-timezone notion is wanted later — would reuse a TZ setting).
- "All caught up" empty-state line: in scope, shown on the Unread smart view only.
- Out of scope: per-visit "new since you last looked" tracking (this uses unread state, not
  last-seen); changing the article-card/feed UI (sidebar only).
