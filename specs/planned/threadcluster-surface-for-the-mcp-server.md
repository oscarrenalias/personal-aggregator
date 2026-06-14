---
name: Thread/cluster surface for the MCP server
id: spec-d5e593e3
description: "Expose the story-thread (clustering) surface over the aggregator-mcp server: list_threads + get_thread tools, a thread://{id} resource, a recluster ops tool, and a whats_developing prompt — all reusing existing aggregator_common.queries/management helpers. Also widens ThreadResult to carry top_grade + surfaced."
dependencies: null
priority: medium
complexity: null
status: planned
tags:
- mcp
- clustering
- threads
- agent-interface
scope:
  in: null
  out: null
feature_root_id: B-b9e002d3
---
# Thread/cluster surface for the MCP server

## Objective

Expose the story-thread (clustering) surface over the `aggregator-mcp` server so an agent
can do over MCP what the Threads web view already does: list the surfaced threads, read a
single thread (its rolling summary, what-changed deltas, and member articles across
sources), and trigger a recluster. Today the MCP server exposes articles, profile, sources,
categories, brief, and ops — but **nothing** about threads (zero `thread`/`cluster`
references in the package). This closes that gap and gives agents a topic-level view of the
news, not just a flat article list.

## Problems to Fix

- The clustering subsystem (built and stabilized after the MCP server was first written) is
  invisible to agents. An agent integration can list articles but cannot ask "what stories
  are developing", inspect a thread's sources/what-changed, or kick a recluster — even
  though the web UI and shared `aggregator_common.queries` already provide all of this.
- `ThreadResult` (the shared projection) omits the field that actually drives surfacing in
  the single-grade model — `top_grade` — and still carries vestigial pre-single-grade fields
  (`tier`, `tier_reason`, `relevance_score`, `novelty_score`, `diversity_score`,
  `time_sensitivity_score`, `confidence`, `novelty_label`). An MCP consumer would see noise
  scores but not the one grade that matters.

## Changes

All new MCP entrypoints reuse the **existing** shared helpers in
`aggregator_common.queries` and `aggregator_common.management` — no new query logic in the
MCP package — mirroring how the article/brief/ops tools already wrap `queries`/`ops`.

### 1. Expose `top_grade` (and `surfaced`) on `ThreadResult`

In `aggregator-common/queries.py`, add `top_grade: Optional[int]` and `surfaced: bool` to the
`ThreadResult` dataclass and populate them in `_to_thread_result` from `thread.top_grade` /
`thread.surfaced`. **Only these two fields are added.** The other fields referenced in the
Acceptance Criteria — `representative_title`, `rolling_summary`, `deltas`, `source_list`,
`source_count`, `member_count` — already exist on `ThreadResult` today and are simply
serialized through to MCP by `asdict`. This is the single field set that the single-grade model surfaces on; the
web templates already read `top_grade` off the ORM, so this only widens the dataclass. The
vestigial fields stay for now (removing them is a separate cleanup — see Pending Decisions)
but they are documented as deprecated in the tool docstrings so agents key off `top_grade`.

### 2. `list_threads` tool

```python
@mcp.tool()
def list_threads(sort: str = "importance", status: Optional[str] = None,
                 limit: int = _settings.mcp_default_limit) -> list:
```
Wraps `queries.list_threads(session, sort=..., status=..., limit=min(limit, max_limit))`.
`sort` accepts `"importance"` (default, `top_grade` desc) or `"recent"` (`last_updated`
desc) — same two modes the web view uses; an invalid value falls back to `"importance"`
(matching `_normalize_thread_sort`). Returns the same surfaced + 7-day-window list the web
Threads view shows (the recency/surfaced filter lives in `queries.list_threads`, so MCP and
web stay consistent automatically). Returns `[asdict(r) for r in results]`.

### 3. `get_thread` tool (thread + members in one payload)

```python
@mcp.tool()
def get_thread(thread_id: int) -> dict:
```
Returns `{"thread": asdict(ThreadResult), "members": [asdict(ThreadMemberResult), ...]}` by
calling `queries.get_thread` + `queries.get_thread_members`. Members carry per-article
`clean_title`, `url`, `source_name`, `classification_label`, `new_facts`, `reason`, and
`suppressed` — enough for an agent to render "what changed" and "also covered by" without a
second round trip. Returns `{"error": "not_found", "detail": ...}` when the id is unknown
(mirrors the `NotFoundError` handling other tools use; do not raise).

### 4. `thread://{id}` resource

```python
@mcp.resource("thread://{id}")
def thread_resource(id: str) -> dict:
```
Read-only snapshot of one thread (same payload as `get_thread`), mirroring the existing
`article://{id}` resource — so a thread can be attached as MCP context by URI.

### 5. `recluster` ops tool

```python
@mcp.tool()
def recluster() -> dict:
```
Wraps `management.enqueue_recluster(session)` (the same call behind the web
`POST /threads/recluster`), which flags `cluster_state` dirty so the next clusterer poll runs
a consolidation pass (bypassing the 10-minute throttle floor, per existing recluster
semantics). Returns `{"status": "enqueued"}`. This is the thread-side counterpart to the
existing `rerank` ops tool.

### 6. `whats_developing` prompt

A thread-oriented counterpart to the existing `whats_latest` prompt: a short prompt that
steers the agent to call `list_threads` and summarize the developing stories (top grade +
multi-source) rather than the flat article feed.

### 7. Docs

Update the `aggregator-mcp` bullet in `CLAUDE.md` (and any MCP README under the package) to
list the new thread tools/resource/prompt alongside the article/source/brief/ops surface.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-common/src/aggregator_common/queries.py` | Add `top_grade` + `surfaced` to `ThreadResult`; populate in `_to_thread_result` |
| `packages/aggregator-mcp/src/aggregator_mcp/server.py` | Add `list_threads`, `get_thread` tools; `thread://{id}` resource; `recluster` tool; `whats_developing` prompt |
| `packages/aggregator-mcp/tests/test_tools.py` (and/or `test_server.py`) | Tests for the new tools/resource/prompt |
| `packages/aggregator-common/tests/` | Assert `ThreadResult` now carries `top_grade`/`surfaced` (extend existing thread query tests) |
| `CLAUDE.md` | Document the new MCP thread surface |

## Acceptance Criteria

- `list_threads` over MCP returns the same set the web `/threads` view shows (surfaced,
  updated within 7 days), honoring `sort=importance|recent`, `status`, and `limit` (clamped
  to `mcp_max_limit`); each entry includes `top_grade`, `surfaced`, `source_count`,
  `member_count`, `representative_title`, `rolling_summary`, `deltas`, `source_list`.
- `get_thread(id)` returns the thread plus its members (with `clean_title`, `url`,
  `source_name`, `new_facts`, `suppressed`); an unknown id returns an `{"error":
  "not_found"}` dict, not an exception.
- `thread://{id}` resolves to the same thread payload.
- The `whats_developing` prompt is registered and resolves; its returned text steers the
  agent to call `list_threads` and summarize developing stories (asserted by a test that the
  prompt resolves and references the thread surface).
- `recluster()` enqueues a recluster (flags `cluster_state` dirty) and returns
  `{"status": "enqueued"}`; verified by asserting the dirty flag / enqueue side effect.
- `ThreadResult` exposes `top_grade` and `surfaced`; web rendering is unaffected
  (regression-safe — fields are additive).
- Focused `aggregator-mcp` + `aggregator-common` tests pass; full gate green. No schema
  change.

## Pending Decisions

- **Combined vs split member fetch:** `get_thread` returns thread + members in one payload
  (proposed) rather than a separate `get_thread_members` tool, since agents almost always
  want both and it saves a round trip. Revisit if payloads get large.
- **Vestigial `ThreadResult` fields:** `tier`, `relevance_score`, `novelty_score`, etc. are
  left in place and marked deprecated in docstrings; fully removing them (and the
  corresponding ORM columns) is out of scope here — a separate cleanup once nothing reads
  them.
- **Mutations:** scope is read + recluster only. No "merge/split/hide thread" or
  "reassign membership" mutation tools in this pass (the clusterer owns membership; manual
  surgery is a later, riskier surface). Out of scope.
- **Pipeline status:** adding thread counts to the `pipeline_status` ops snapshot /
  `status://pipeline` resource is a nice-to-have, deferred unless cheap to fold in.
