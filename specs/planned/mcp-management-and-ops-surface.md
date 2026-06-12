---
name: MCP management and ops surface
id: spec-a8dc03b5
description: "Expand the MCP server so agents can manage profile, sources, and categories (incl. deletes) and run pipeline diagnostics + remediation; DB logic extracted into shared aggregator-common helpers reused by the admin CLI."
dependencies: null
priority: high
complexity: null
status: planned
tags: []
scope:
  in: null
  out: null
feature_root_id: B-f46d86c3
---
# MCP management and ops surface

## Objective

Extend the `aggregator-mcp` server so an agent (e.g. openclaw) can not only read and
do light article writes, but **manage** the instance and **troubleshoot** it: edit the
interest profile, manage sources and categories (including deletes), and run pipeline
diagnostics plus remediation actions (release stuck claims, retry failures, re-rank).

The single source of truth for these operations is the admin CLI's existing DB logic.
To avoid two divergent implementations, that logic is **extracted into shared
`aggregator-common` helpers**; both the admin CLI and the MCP server call the same
functions. No new auth — the tailnet/host remains the trust boundary, consistent with
the existing MCP surface.

## Problems to Fix

- The MCP surface is read + light article writes only (`mark_read`, `save`, etc.). An
  agent can't change the profile, manage sources/categories, or fix a stuck pipeline —
  all of which currently require shelling into the admin CLI on the host.
- The relevant DB logic lives **inside** the admin CLI command functions (coupled to
  typer/Rich I/O), so it cannot be reused by MCP without duplication.

## Changes

### 1. Extract shared management + ops helpers into `aggregator-common`

Move the **DB logic** (not the Rich/typer presentation) out of the admin command
functions into shared, importable helpers, and refactor the admin CLI to call them so
there is exactly one implementation. Suggested home: a new
`aggregator_common/management.py` (profile/source/category mutations) and
`aggregator_common/ops.py` (pipeline diagnostics + remediation); reuse the existing
`claim.reap_stale_claims` and `state.can_transition` rather than reimplementing.

**Conventions (decided):**
- Helpers are imported by submodule path (`from aggregator_common.management import …`,
  `from aggregator_common.ops import …`); they are **not** re-exported from
  `aggregator_common/__init__.py`. CLI and MCP both use this same import style.
- Define two shared exception types in `aggregator-common` (e.g.
  `aggregator_common.errors.NotFoundError` and `ConflictError`). Helpers raise
  `NotFoundError` for a missing id and `ConflictError` for a unique-violation (e.g.
  duplicate source feed_url / category name). **Both** the admin CLI and the MCP tools
  catch these and surface a clear message (the CLI as a Rich error/non-zero exit, MCP as
  an error payload), so error behaviour is shared, not just the happy path.

New shared helpers (signatures illustrative):

- Profile: `set_interest_profile(session, text) -> dict`
- Sources: `add_source(session, name, feed_url, *, refresh_interval_seconds=None, priority=None, enabled=True) -> dict`,
  `enable_source(session, source_id)`, `disable_source(session, source_id)`,
  `set_source_interval(session, source_id, seconds)`, `refresh_source_now(session, source_id)`,
  `remove_source(session, source_id) -> dict` (**cascade-deletes the source's articles**; return counts).
- Categories: `add_category(session, name, *, description=None, sort_order=None, enabled=True)`,
  `rename_category(session, category_id, new_name)`, `set_category_description(session, category_id, description)`,
  `set_category_order(session, category_id, sort_order)`, `enable_category(session, category_id)`,
  `disable_category(session, category_id)`, `remove_category(session, category_id) -> dict` (**permanent**).
- Ops (diagnostics, read): `pipeline_status(session) -> dict` (article counts by status, in-flight
  count, enabled/disabled source counts), `list_stuck(session, lease_seconds) -> list`,
  `list_failures(session, *, stage=None, limit=50) -> list`.
- Ops (remediation, write): `reap_stale_claims(session, lease_seconds) -> dict` — reaps
  **both** stale article claims (reuse `claim.reap_stale_claims`) **and** stale brief
  claims (reuse `brief_claim.reap_stale_brief_claims`), returning per-kind counts;
  `retry_failed(session, *, stage=None, article_id=None) -> dict`
  (failed_processing→pending_processing, failed_ranking→pending_ranking; reset claim/retry/error),
  `rerank(session, *, article_id=None, all_ready=False, failed_only=False) -> dict`
  (→pending_ranking via `state.can_transition`).

The admin CLI commands (`profile`, `sources`, `categories`, `ops`, `articles
retry/rerank`) must be refactored to call these helpers; their behaviour (and tests)
must remain unchanged.

### 2. Add MCP tools wrapping the shared helpers

In `aggregator-mcp/server.py`, add `@mcp.tool()` wrappers (same `get_session()` +
helper-call pattern as existing tools), returning JSON-friendly dicts/lists:

- Profile: `set_interest_profile(text)`
- Sources: `add_source`, `enable_source`, `disable_source`, `set_source_interval`,
  `refresh_source_now`, `remove_source`
- Categories: `add_category`, `rename_category`, `set_category_description`,
  `set_category_order`, `enable_category`, `disable_category`, `remove_category`
- Ops: `pipeline_status`, `list_stuck`, `list_failures`, `reap_stale_claims`,
  `retry_failed`, `rerank`

Each tool's docstring is the agent-facing description; explicitly note in the docstrings
of `remove_source` (deletes the source's articles too) and `remove_category` that they
are destructive/irreversible. Add an optional `status://pipeline` resource returning
`pipeline_status` for quick health reads, and a `troubleshoot` prompt that guides the
agent to check `pipeline_status` → `list_stuck`/`list_failures` → `reap_stale_claims`/
`retry_failed`.

### 3. Docs

Update `deploy/README.md` (MCP section) and `CLAUDE.md` MCP surface listing to enumerate
the new management/ops tools, calling out the destructive ones.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-common/src/aggregator_common/management.py` | New: profile/source/category mutation helpers |
| `packages/aggregator-common/src/aggregator_common/ops.py` | New: pipeline diagnostics + remediation helpers (reaps article + brief claims) |
| `packages/aggregator-common/src/aggregator_common/errors.py` | New: shared `NotFoundError` / `ConflictError` raised by helpers |
| `packages/aggregator-admin/src/aggregator_admin/profile.py` | Refactor to call shared helpers (behaviour unchanged) |
| `packages/aggregator-admin/src/aggregator_admin/sources.py` | Refactor to call shared helpers |
| `packages/aggregator-admin/src/aggregator_admin/categories.py` | Refactor to call shared helpers |
| `packages/aggregator-admin/src/aggregator_admin/ops.py` | Refactor to call shared helpers |
| `packages/aggregator-admin/src/aggregator_admin/articles.py` | Refactor retry/rerank to call shared helpers |
| `packages/aggregator-mcp/src/aggregator_mcp/server.py` | Add management + ops tools, `status://pipeline` resource, `troubleshoot` prompt |
| `packages/aggregator-common/tests/` | Tests for new shared helpers (incl. cascade delete, transition validation) |
| `packages/aggregator-mcp/tests/` | Tests: new tools registered + callable against a seeded DB |
| `packages/aggregator-admin/tests/` | Keep CLI tests green after refactor |
| `deploy/README.md`, `CLAUDE.md` | Document new MCP surface |

## Acceptance Criteria

- Shared helpers exist in `aggregator-common` and are the **only** implementation; the
  admin CLI calls them and all existing admin tests still pass (no behaviour change).
- MCP exposes the new tools: profile `set_interest_profile`; sources `add_source`,
  `enable_source`, `disable_source`, `set_source_interval`, `refresh_source_now`,
  `remove_source`; categories `add_category`, `rename_category`,
  `set_category_description`, `set_category_order`, `enable_category`,
  `disable_category`, `remove_category`; ops `pipeline_status`, `list_stuck`,
  `list_failures`, `reap_stale_claims`, `retry_failed`, `rerank`.
- `remove_source` cascade-deletes the source's articles and reports counts;
  `remove_category` permanently deletes; both have docstrings flagging this.
- `retry_failed` and `rerank` only perform allowed status transitions
  (`state.can_transition`), resetting claim/retry/error fields, and are covered by tests.
- `pipeline_status`/`list_stuck`/`list_failures` return the same data the admin `ops`
  commands report.
- `reap_stale_claims` releases both stale article and stale brief claims and reports
  per-kind counts (covered by a test).
- Helpers raise shared `NotFoundError`/`ConflictError`; both the CLI and MCP surface them
  (a test asserts e.g. adding a duplicate source feed_url raises `ConflictError` and the
  MCP tool returns an error payload rather than a 500).
- `status://pipeline` resource and `troubleshoot` prompt are registered.
- Tools are registered and callable against a seeded DB (aggregator-mcp tests); new
  shared helpers are unit-tested (aggregator-common tests); docs updated.
- Full test gate (all packages) passes.

## Pending Decisions

- Resolved: full surface including destructive deletes (`remove_source`,
  `remove_category`) and ops remediation (`reap`, `retry_failed`, `rerank`) — approved.
  No auth (tailnet trust boundary).
- Out of scope: bulk `purge` articles over MCP, and OPML import/export over MCP
  (file-based; stays CLI-only).
