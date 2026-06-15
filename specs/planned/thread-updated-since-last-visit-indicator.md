---
name: Thread updated-since-last-visit indicator
id: spec-3917b7ba
description: "Thread-list dot indicating a thread has new content since the user last opened it. Approach A: new additive threads.last_viewed_at column, stamped on the web GET /threads/{id} detail view (NOT on MCP get_thread); ThreadResult.has_updates = last_viewed_at IS NULL OR last_updated > last_viewed_at; binary dot (no count) in the thread list, cleared on open. Reuses sidebar dot styling."
dependencies: null
priority: medium
complexity: null
status: planned
tags:
- web
- threads
- ux
scope:
  in: null
  out: null
feature_root_id: B-3575779d
---
# Thread updated-since-last-visit indicator

## Objective

Make it easy to see, in the **thread list**, which threads have gained new content **since the
user last opened them**. Show a single, binary **dot** (no count) on such threads, cleared once
the thread is opened. This is the "have I seen what's new here?" signal that's currently
missing — relative time ("3h ago") tells you *when* a thread last changed, not whether *you've*
seen it.

## Problems to Fix

- A thread quietly accumulates new member articles over time; nothing in the list tells the
  reader which threads have updates they haven't seen yet, so re-checking threads is guesswork.

## Changes

### 1. Track when the user last viewed each thread (server-side)

Add an additive nullable column `last_viewed_at TIMESTAMP WITH TIME ZONE` to the `threads`
table (Alembic migration + `Thread` ORM field). Server-side (not `localStorage`) because the UI
is used across devices over Tailscale and the signal should be consistent everywhere.

Set it when the user **opens a thread's detail view**: in the web `GET /threads/{thread_id}`
route, stamp `last_viewed_at = now()` (a small write-on-read, idempotent and harmless — like a
"seen" marker). Add a `management.mark_thread_viewed(session, thread_id)` helper as the single
write path.

**Important — only the human web view marks a thread seen.** The MCP `get_thread` tool must
**not** set `last_viewed_at` (an agent inspecting a thread isn't the user reading it).

### 2. Derive the "has updates" signal

Add `has_updates: bool` to `ThreadResult`, computed in `list_threads`:
`has_updates = (last_viewed_at IS NULL) OR (last_updated > last_viewed_at)`.
- A **never-viewed** thread → `has_updates = true` (it's all new).
- After opening, `last_viewed_at = now() >= last_updated`, so the dot clears; if a new member
  later bumps `last_updated`, it goes true again.
- Cheap: both are columns on `Thread`, no per-member query / no N+1. (`last_updated` already
  advances when a member is added.)

### 3. Render the dot

In the thread list (`_thread_list.html` / `_thread_card.html`), show a single **binary dot** —
**no count** — when `has_updates` is true, reusing the sidebar's dot styling/`.sidebar-dot`
language for visual consistency. No dot when false. Place it unobtrusively near the thread
title / metadata row.

### 4. Clearing behaviour

Opening the thread sets `last_viewed_at`, so the dot disappears on the **next thread-list
render**. (Optional polish: have the detail route emit an `HX-Trigger` to refresh the thread
list so the dot clears immediately — see Pending Decisions.)

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-common/src/aggregator_common/models.py` | Add `last_viewed_at` to `Thread` |
| `packages/aggregator-common/src/aggregator_common/migrations/versions/*` | New additive migration: `threads.last_viewed_at` |
| `packages/aggregator-common/src/aggregator_common/queries.py` | Add `has_updates` to `ThreadResult`; compute in `list_threads` |
| `packages/aggregator-common/src/aggregator_common/management.py` | `mark_thread_viewed(session, thread_id)` helper |
| `packages/aggregator-web/src/aggregator_web/app.py` | `GET /threads/{id}` stamps `last_viewed_at` via the helper |
| `packages/aggregator-web/src/aggregator_web/templates/_thread_list.html` (+ `_thread_card.html`) | Binary dot when `has_updates` |
| `packages/aggregator-web/src/aggregator_web/static/styles.css` | Dot styling (reuse sidebar dot) |
| `packages/aggregator-common/tests/`, `packages/aggregator-web/tests/` | Migration round-trip, `has_updates` logic, mark-viewed-on-open, dot rendering, MCP-does-not-mark |

## Acceptance Criteria

- New additive `threads.last_viewed_at` column; migration applies and downgrades cleanly
  (round-trip test uses **explicit revision targets**, not `head`/`-1`, so later migrations
  don't break it).
- Opening a thread via the web `GET /threads/{id}` sets `last_viewed_at = now()`; MCP
  `get_thread` does **not**.
- `ThreadResult.has_updates` = `last_viewed_at IS NULL OR last_updated > last_viewed_at`, with
  no N+1.
- The thread list shows a single binary dot (no number) on threads with `has_updates`; opening a
  thread clears its dot (by the next list render); a never-viewed thread shows the dot.
- Focused `aggregator-common` + `aggregator-web` tests pass; full gate green.

## Pending Decisions

- **Signal basis**: `last_updated > last_viewed_at` (proposed — cheap, single column). Note
  `last_updated` may also advance on a summary-only refresh with no new article; that still
  counts as "the thread changed", which is acceptable. If we want strictly "new *article* since
  visit", switch to comparing against the newest non-suppressed member's `assigned_at`.
- **Immediate clear**: clear the dot on the next natural list render (proposed) vs. emit an
  `HX-Trigger: refreshThreadList` from the detail route so it clears instantly. Lean to the
  simple version first.
- **Write-on-GET**: stamping `last_viewed_at` inside the detail `GET` is the simplest path
  (idempotent). Alternative is a dedicated `POST /threads/{id}/seen` fired by the detail view —
  more "correct" REST but more moving parts; not worth it here.
- **MCP exposure**: `has_updates` flows through MCP `list_threads`/`get_thread` via `asdict`
  automatically — harmless; no MCP work needed.
