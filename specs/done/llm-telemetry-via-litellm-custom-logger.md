---
name: LLM telemetry via LiteLLM custom logger
id: spec-5452e3b1
description: "LLM telemetry: a LiteLLM CustomLogger persists one row per completion to a new llm_calls Postgres table (service/operation, model, prompt/completion/cached tokens, cost, latency, finish_reason, tool calls, ref_id), enabling per-service cost/prompt-size/tool-use monitoring. Async + fail-safe (never breaks the LLM path); prompts not stored unless flagged. Admin llm-stats + MCP status://llm surfaces; janitor retention; optional env-gated Langfuse callback. Additive migration."
dependencies: null
priority: medium
complexity: null
status: done
tags:
- observability
- llm
- telemetry
- litellm
- cost
scope:
  in: null
  out: null
feature_root_id: B-3c53947f
---
# LLM telemetry via LiteLLM custom logger

## Objective

Capture per-call LLM telemetry — **prompt size, cost, latency, failure mode, and tool usage**,
attributed **per service/operation** — by registering a LiteLLM `CustomLogger` that persists one
row per `litellm.completion()` call to a new `llm_calls` Postgres table. This gives a private,
always-on, queryable basis for monitoring the size/cost/effectiveness of prompts and tool calls
(the per-service cost split the daily OpenAI cost export cannot provide), with negligible
overhead and no data leaving the host. Provide an optional Langfuse callback (env-gated) as an
on-ramp to a richer trace UI later, without changing this design.

## Problems to Fix

- No visibility into LLM usage by service/operation: the OpenAI cost export is a daily total
  with no model/service breakdown, so we can't see "clusterer vs brief vs summarize-rank" cost,
  prompt-size trends, truncated outputs, or how the brief uses its tools.
- Validating model changes (e.g. gpt-4.1 → gpt-4.1-mini) is currently guesswork on the billing
  dashboard; we want first-party numbers.

## Changes

### 1. `llm_calls` table (+ migration, ORM)

New table written once per completion. Columns:
`id` (pk), `created_at` (timestamptz, default now), `service` (text — clusterer/brief/
summarize_rank/…), `operation` (text — classify/merge/generate/rank/…), `model` (text),
`prompt_tokens` (int), `completion_tokens` (int), `total_tokens` (int), `cached_tokens` (int),
`cost_usd` (numeric), `latency_ms` (int), `status` (text — success/error), `error_type`
(text, nullable — e.g. timeout/rate_limit/api_error), `finish_reason` (text, nullable —
stop/length/tool_calls), `num_tool_calls` (int default 0), `tool_names` (jsonb, nullable),
`ref_id` (text, nullable — article/thread/brief id for joining to outcomes), `request_id`
(text, nullable — provider/litellm id), `prompt_preview` (text, nullable — flag-gated),
`prompt_hash` (text, nullable). Indexes: `(created_at)`, `(service, created_at)`. ORM model in
`models.py`; additive migration (round-trip test uses **explicit revision targets**).

### 2. The `CustomLogger` + setup hook (`aggregator-common`)

`aggregator_common/llm_telemetry.py`:
- A `LlmTelemetryLogger(litellm.integrations.custom_logger.CustomLogger)` implementing
  **`async_log_success_event`** and **`async_log_failure_event`** (async = off the request
  path). From `kwargs`/`response_obj`/`start_time`/`end_time` it extracts: model; `usage`
  (prompt/completion/total + cached tokens); cost via `litellm.completion_cost(response_obj)`
  (fallback 0 on error); latency; `finish_reason`; `tool_calls` (name list + count) from
  `choices[0].message.tool_calls`; `request_id`; and `service`/`operation`/`ref_id` from
  `kwargs["litellm_params"]["metadata"]`. Failure events record `status='error'` +
  `error_type` (classified from the exception). Writes one row via a dedicated short-lived
  session (never the caller's). **Must never raise into the LLM path** — wrap the whole handler
  in try/except and log-and-drop on any error.
- `setup_llm_telemetry(settings)` — idempotently registers the logger on `litellm.callbacks`
  (only when `LLM_TELEMETRY_ENABLED`), and, when Langfuse env keys are present, also appends
  `"langfuse"` to `litellm.success_callback`. Called once at each service entrypoint, after
  `load_env()`/`Settings()` (alongside `configure_logging`).

### 3. Per-call metadata tagging (call sites)

At each `litellm.completion(...)` call, pass `metadata={"service": ..., "operation": ...,
"ref_id": ...}`:
- `aggregator-clusterer`: `classification.classify_article` (`service=clusterer, operation=classify, ref_id=article.id`) and the merge decider in `worker._make_llm_merge_fn` (`operation=merge`).
- `aggregator-brief`: brief generation (`service=brief, operation=generate, ref_id=brief.id`).
- `aggregator-summarize-rank`: the ranker (`service=summarize_rank, operation=rank, ref_id=article.id`).
- Each service calls `setup_llm_telemetry(settings)` in its `__main__`/entrypoint.

### 4. Config (shared, base `Settings`)

- `LLM_TELEMETRY_ENABLED` (default **true**)
- `LLM_TELEMETRY_CAPTURE_PROMPTS` (default **false** — when true, store truncated
  `prompt_preview` + `prompt_hash`; off by default for privacy/size)
- Optional Langfuse: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` (when all
  present → enable the Langfuse callback; off by default). Document in `.env.example`/CLAUDE.md.

### 5. Query surface

- **Admin CLI** `aggregator llm-stats [--days N]` — Rich table: per `service`×`model` →
  request count, total/avg cost, avg & p95 prompt_tokens, avg completion_tokens,
  truncated (`finish_reason='length'`) count, error count/%, and (for brief) avg/max tool calls
  + cap hits. Plus a shared `queries.llm_stats(session, days)` helper backing it.
- **MCP** resource `status://llm` (and/or a `llm_stats` tool) returning the same summary so an
  agent can inspect prompt size/cost/tool usage.

### 6. Retention (janitor)

Add `purge_expired_llm_calls(session, retention_days)` to `aggregator-common/retention.py` and
call it from the janitor sweep; new `JANITOR_LLM_TELEMETRY_RETENTION_DAYS` (default **30**) so
`llm_calls` doesn't grow unbounded.

### 7. Tests

- `aggregator-common`: logger writes a correct row from a mocked success event (tokens/cost/
  tool_calls/metadata) and a failure event (status/error_type); never raises on malformed
  input; migration round-trip; `llm_stats` aggregation; `purge_expired_llm_calls`.
- call sites: each passes the expected `metadata`.
- admin: `llm-stats` renders. mcp: `status://llm` returns the summary.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-common/.../models.py` | `LlmCall` ORM model |
| `packages/aggregator-common/.../migrations/versions/*` | additive `llm_calls` migration |
| `packages/aggregator-common/.../llm_telemetry.py` (new) | `LlmTelemetryLogger` + `setup_llm_telemetry` |
| `packages/aggregator-common/.../config.py` | telemetry + Langfuse settings |
| `packages/aggregator-common/.../queries.py` | `llm_stats` aggregation helper |
| `packages/aggregator-common/.../retention.py` | `purge_expired_llm_calls` |
| `packages/aggregator-clusterer/.../classification.py`, `worker.py`, `__main__.py` | metadata + setup |
| `packages/aggregator-brief/.../*` | metadata + setup |
| `packages/aggregator-summarize-rank/.../*` | metadata + setup |
| `packages/aggregator-janitor/.../*` | call `purge_expired_llm_calls` in the sweep |
| `packages/aggregator-admin/.../*` | `llm-stats` command |
| `packages/aggregator-mcp/.../server.py` | `status://llm` resource / `llm_stats` tool |
| `.env.example`, `CLAUDE.md`, tests | docs + coverage |

## Acceptance Criteria

- Every `litellm.completion()` across clusterer/brief/summarize-rank writes one `llm_calls` row
  with service, operation, model, token counts (incl. cached), cost_usd, latency_ms,
  finish_reason, and tool-call name/count; failures write `status='error'` + `error_type`.
- The logger is async and **cannot break or slow an LLM call** — any logging error is swallowed
  (a forced exception in the handler does not propagate; covered by a test).
- `aggregator llm-stats --days 7` shows the per-service/model cost + prompt-size + tool-usage
  breakdown; MCP `status://llm` returns the same.
- Prompts are **not** persisted unless `LLM_TELEMETRY_CAPTURE_PROMPTS=true`.
- The janitor prunes `llm_calls` older than `JANITOR_LLM_TELEMETRY_RETENTION_DAYS`.
- Langfuse callback activates only when its env keys are set; default install sends nothing
  off-box.
- Additive migration, round-trip clean. Focused tests pass across packages; full gate green.

## Pending Decisions

- **Downstream parse outcomes**: the logger captures *call-level* status (litellm API success/
  failure, `finish_reason='length'` for truncation) — but a *successful* call whose JSON our
  code then fails to parse (e.g. clusterer `classification_error`) is a domain outcome already
  tracked in `thread_memberships.reason`. v1 keeps these separate; a later enhancement could
  correlate via `request_id` or have call sites stamp an outcome. (Noted so "json_parse rate"
  for clustering is read from `thread_memberships`, not `llm_calls`, for now.)
- **Cost source of truth**: `litellm.completion_cost` uses LiteLLM's price map (list prices) —
  good for relative/trend analysis and per-service split, but won't reflect the OpenAI
  free-allotment/incentivized-tier effect. Treat `cost_usd` as list-price attribution; the
  billing dashboard remains the actual-$ source.
- **Write path**: one INSERT per call via a dedicated session (proposed). If volume ever grows,
  batch/async-queue — not needed at ~1k calls/day.
- **MCP exposure**: read-only summary only (no raw prompts over MCP).

## Implementation notes (from spec review)

- **Async-loop safety (critical):** `async_log_success_event` runs on LiteLLM's event loop. The
  DB write uses the **sync** SQLAlchemy session factory, so it MUST run off the loop —
  `await asyncio.to_thread(_write_row, ...)` (or an executor) — so a slow/failing DB write can
  never block or stall LiteLLM's loop. Session **acquisition, INSERT, and commit** are all inside
  the swallow boundary (one broad try/except that logs-and-drops). A test should force an
  exception in the write path and assert the completion still succeeds and nothing propagates.
- **LiteLLM registration surfaces:** the `CustomLogger` *instance* → `litellm.callbacks`; the
  string `"langfuse"` → `litellm.success_callback`. Don't conflate the two.
- **Defensive field extraction:** `cost` (`litellm.completion_cost`), `cached_tokens`
  (`usage.prompt_tokens_details.cached_tokens`), `finish_reason`, and `tool_calls` locations vary
  by provider/LiteLLM version — extract each in its own try/except with safe defaults (0/None);
  a missing field must never drop the row.
- **Failure-event metadata:** read `kwargs["litellm_params"]["metadata"]` on
  `async_log_failure_event` too; default `service='unknown'` if absent so error rows keep
  attribution (the per-service error-rate criterion depends on it).
- **Retention owner:** `aggregator-janitor` + `aggregator_common/retention.py` already exist — add
  `purge_expired_llm_calls` there; do not create a new service.
