---
name: "Not-interested: dismiss/hide a thread"
id: spec-4ff84968
description: "Add a 'Not interested' control that hides a thread from the Threads view (overriding the mechanical surfacing rule). Additive threads.dismissed column; list_threads excludes dismissed (persists across reclustering); web dismiss/restore routes + buttons in detail/list views with a 'Show dismissed' filter; dismiss_thread MCP tool. Reversible. Down-weighting the topic at grading time is a separate later spec."
dependencies: null
priority: medium
complexity: null
status: planned
tags:
- threads
- clustering
- web
- mcp
- ux
scope:
  in: null
  out: null
feature_root_id: B-f8dd0029
---
# Not-interested: dismiss/hide a thread

## Objective

Give the reader a **"Not interested"** control on a thread that hides it from the Threads
view. Surfacing today is purely mechanical (`surfaced = top_grade ≥ MIN_GRADE OR
distinct_sources ≥ MIN_SOURCES OR member_count ≥ MIN_MEMBERS`), so a well-formed but
personally-irrelevant thread (e.g. a 3-article "NBA champion" cluster) surfaces on critical
mass regardless of the reader's interest. This adds a human override: dismiss the thread and
it stops appearing. Reversible.

Out of scope (a separate, deeper feature): turning dismissals into a negative-interest signal
that down-weights the *topic* at grading time in `summarize-rank`. This spec only hides the
specific thread; it does not change how articles are graded or how future threads form.

## Problems to Fix

- No way for reader preference to override the deterministic surfacing rule. An irrelevant
  thread that meets critical mass cannot be removed from the Threads view.

## Changes

### 1. Schema — `dismissed` flag on threads

Add an additive boolean column `dismissed` to the `threads` table
(`Boolean, nullable=False, server_default="false"`), with a new Alembic migration chained
from the current head. Dismissal is **orthogonal** to the lifecycle `status`
(active/dormant/archived) and to `surfaced` — a thread can be active+surfaced yet dismissed.
Add the column to the `Thread` ORM model.

### 2. Query — exclude dismissed from the list, keep it fetchable

- `aggregator_common.queries.list_threads`: add `Thread.dismissed == False` to the filters so
  dismissed threads drop out of the Threads view. (The clusterer recomputes `surfaced` on
  touch but never touches `dismissed`, so a dismissal **persists across reclustering** — call
  this out in the docstring.)
- `get_thread` / `get_thread_members`: unchanged — still return a dismissed thread by id so
  the detail page and the restore action work.
- Add `dismissed: bool` to the `ThreadResult` dataclass (populate in `_to_thread_result`) so
  the UI and MCP can show/act on the state.
- New `list_threads(..., include_dismissed: bool = False)` parameter (or a dedicated
  `list_dismissed_threads`) so a "Show dismissed" view can list them for restore.

### 3. Mutation helper

Add `aggregator_common.management.set_thread_dismissed(session, thread_id, dismissed: bool)`
— idempotent, returns the updated state (or a not-found signal). Single source of truth for
web + MCP.

### 4. Web — button + routes

- `POST /threads/{id}/dismiss` and `POST /threads/{id}/restore` (or one toggle route) calling
  `set_thread_dismissed`. Return an HTMX response that removes the row / navigates back to the
  list (`HX-Trigger` to refresh the thread list), mirroring the existing recluster route
  pattern.
- **Detail view** (`_thread_detail.html`): a "Not interested" button in the thread toolbar;
  on dismiss, return to the Threads list (the thread is now hidden).
- **List view** (`_thread_list.html`): a small per-row "Not interested" affordance.
- **Restore path**: a lightweight "Show dismissed" toggle/filter on the Threads index
  (`?show_dismissed=1`) that lists dismissed threads with a **Restore** button. Keeps the
  feature reversible without a separate page.

### 5. MCP — dismiss/restore tools

Building on the just-merged MCP threads surface: add `dismiss_thread(thread_id,
dismissed: bool = True)` (wraps `set_thread_dismissed`), and have `list_threads`/`get_thread`
expose the `dismissed` field. Lets an agent hide/restore threads too.

### 6. Docs

Update `CLAUDE.md` (clusterer/web sections) and any thread docs to describe the dismiss
behavior and that it persists across reclustering.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-common/src/aggregator_common/models.py` | Add `dismissed` column to `Thread` |
| `packages/aggregator-common/src/aggregator_common/migrations/versions/*` | New additive migration: `threads.dismissed` |
| `packages/aggregator-common/src/aggregator_common/queries.py` | Filter dismissed in `list_threads`; add `dismissed` to `ThreadResult`; `include_dismissed` path |
| `packages/aggregator-common/src/aggregator_common/management.py` | `set_thread_dismissed` helper |
| `packages/aggregator-web/src/aggregator_web/app.py` | `dismiss`/`restore` routes; `show_dismissed` on the index |
| `packages/aggregator-web/src/aggregator_web/templates/_thread_detail.html` | "Not interested" button |
| `packages/aggregator-web/src/aggregator_web/templates/_thread_list.html` | per-row dismiss + restore (dismissed view) |
| `packages/aggregator-mcp/src/aggregator_mcp/server.py` | `dismiss_thread` tool; surface `dismissed` |
| `packages/aggregator-common/tests/`, `packages/aggregator-web/tests/`, `packages/aggregator-mcp/tests/` | Tests |
| `.env.example`/`CLAUDE.md` | Docs |

## Acceptance Criteria

- Dismissing a thread removes it from the default Threads view (web + MCP `list_threads`);
  restoring brings it back.
- A dismissed thread is still reachable by id (`get_thread`) and is shown under "Show
  dismissed" with a Restore action.
- `dismissed` persists across a clusterer recompute/consolidation cycle (a touched, re-scored
  thread that was dismissed stays hidden) — covered by a test.
- `set_thread_dismissed` is idempotent; dismissing twice is a no-op; unknown id is handled
  gracefully (not-found, no crash) in both web (404) and MCP (`{"error": "not_found"}`).
- Migration applies and rolls back cleanly (additive column, round-trip tested).
- Focused tests pass across the three packages; full gate green.

## Pending Decisions

- **Toggle vs two routes**: a single `/threads/{id}/dismiss` accepting a desired state, or
  separate `dismiss`/`restore` — implementer's choice; keep it consistent with existing route
  conventions.
- **Restore discoverability**: a "Show dismissed" filter on the index is the proposed minimal
  reversible path (vs a dedicated page or an undo toast). Revisit if it feels hidden.
- **Down-weighting the topic** (negative-interest signal into the ranker) is explicitly a
  separate later spec — this one only hides the specific thread.
- **Bulk/auto behavior**: no "dismiss all from source" or auto-dismiss heuristics in this pass.
