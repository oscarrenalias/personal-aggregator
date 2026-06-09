---
name: Retriever Service
id: spec-ee787452
description: "aggregator-retriever service: long-running ThreadPoolExecutor loop that polls due RSS/Atom sources with conditional requests, dedupes entries (resolves dedup_key construction), and persists new articles as pending_processing. Single-instance. Depends on foundation."
dependencies: null
priority: high
complexity: null
status: planned
tags:
- retriever
- service
- rss
- depends-foundation
scope:
  in: null
  out: null
feature_root_id: B-3037f6ce
---
# Retriever Service

## Objective

Implement the `aggregator-retriever` service: a long-running process that polls enabled RSS/Atom sources on their per-source schedule, fetches feeds efficiently (conditional requests), deduplicates entries, and persists newly discovered articles as `pending_processing` for the processor to pick up.

This spec also resolves the `dedup_key` construction deferred by the foundation spec.

## Dependencies

Depends on the **foundation** (`spec-9e974b88`, feature root `B-d585b1bf`): `aggregator_common` models (`sources`, `articles`), the `article_status` enum + state machine, and the `UNIQUE (source_id, dedup_key)` constraint. Do not redefine any of these — import them.

**`sources` columns this service reads/writes** (the integration checklist against the foundation model): reads `id`, `name`, `feed_url`, `enabled`, `refresh_interval_seconds`, `priority`, `next_check_at`, `etag`, `last_modified`; writes `last_checked_at`, `next_check_at`, `etag`, `last_modified`, `consecutive_failures`, `last_error`, `enabled`. If any name differs in the foundation model, the foundation model wins — adjust here, do not add columns.

## Background / Decisions

Already decided (see `CLAUDE.md` and this spec); implement to them.

- **Concurrency:** a bounded `ThreadPoolExecutor` of synchronous workers. Sync `httpx` + `feedparser` + sync SQLAlchemy throughout — no asyncio.
- **Instances:** **single retriever process**. It parallelizes *feeds* via the thread pool. No cross-process source claiming; in-flight sources are tracked in memory so the loop never dispatches the same source twice concurrently.
- **The retriever does NOT claim articles and does NOT run the reaper** — it only inserts. (Per the foundation reaper contract.)
- **The retriever does not** fetch full article pages, clean text, summarize, or rank. It persists raw feed entries only (SPEC §Retriever Rules).

## Changes

### 1. Package + entrypoint

Replace the retriever stub with the real implementation.

```
packages/aggregator-retriever/
  pyproject.toml                 # add deps: httpx, feedparser; depends on aggregator-common
  src/aggregator_retriever/
    __init__.py
    config.py                    # retriever-specific settings
    http.py                      # conditional fetch + size cap
    normalize.py                 # normalize_url, dedup_key, raw_payload serialization
    parse.py                     # feedparser → normalized entries
    persist.py                   # insert articles, update source metadata + backoff
    loop.py                      # scheduler loop, thread pool, signal handling
    __main__.py                  # console entrypoint -> loop.run()
  tests/
```

Console script `aggregator-retriever` runs `loop.run()`.

### 2. Configuration (`config.py`)

Retriever-specific settings from env (separate from `aggregator_common.Settings`, which it composes for `DATABASE_URL`):

| Var | Default | Meaning |
|---|---|---|
| `RETRIEVER_POLL_INTERVAL_SECONDS` | 60 | loop wake cadence |
| `RETRIEVER_MAX_WORKERS` | 8 | thread pool size |
| `RETRIEVER_HTTP_TIMEOUT_SECONDS` | 30 | per-request timeout |
| `RETRIEVER_MAX_FEED_BYTES` | 10_485_760 | response size cap (10 MiB) |
| `RETRIEVER_USER_AGENT` | `personal-aggregator/0.1 (+retriever)` | request UA |
| `RETRIEVER_MAX_SOURCE_FAILURES` | 20 | auto-disable threshold |
| `RETRIEVER_BACKOFF_BASE_SECONDS` | 60 | backoff base |
| `RETRIEVER_BACKOFF_CAP_SECONDS` | 21_600 | backoff cap (6 h) |

### 3. Scheduler loop (`loop.py`)

- On start: install SIGINT/SIGTERM handlers for graceful shutdown.
- Each tick (every `RETRIEVER_POLL_INTERVAL_SECONDS`):
  1. Select **due** sources: `enabled = true AND (next_check_at IS NULL OR next_check_at <= now())`, excluding any `source_id` currently in the in-memory in-flight set. Order by `priority DESC, next_check_at NULLS FIRST`.
  2. For each due source, add its id to the in-flight set and submit `process_source(source_id)` to the pool.
  3. On completion (success or failure), remove the id from the in-flight set.
- Graceful shutdown: stop scheduling new sources, let in-flight tasks finish, close the pool and DB, exit 0.
- Each `process_source` task uses **its own DB session/transaction** so failures are isolated per source.

### 4. Fetch with conditional requests (`http.py`)

`fetch(source) -> FetchResult` using a sync `httpx.Client`:

- Headers: `User-Agent`, `Accept-Encoding: gzip, deflate`, plus `If-None-Match: <source.etag>` and `If-Modified-Since: <source.last_modified>` when present.
- Timeout = `RETRIEVER_HTTP_TIMEOUT_SECONDS`; follow redirects with a cap of **5**.
- Stream the body and abort if it exceeds `RETRIEVER_MAX_FEED_BYTES`, enforced by **counting streamed bytes** (do not trust `Content-Length`, which may be absent or wrong); on exceed, raise a typed error → failure path.
- Outcomes:
  - **304 Not Modified** → `FetchResult(not_modified=True)`; no parse, counts as success.
  - **2xx** → `FetchResult(body=..., etag=resp ETag, last_modified=resp Last-Modified)`.
  - non-2xx/3xx, timeout, connection error, oversize → raise typed `FetchError` (message captured for `last_error`).
- **Validator handling:** store and resend `etag` and `last_modified` as the **raw header strings, verbatim** — never parse or reformat them. Reformatting an HTTP-date breaks `304` matching against origin servers.

### 5. Parse & normalize (`parse.py`, `normalize.py`)

- Parse with `feedparser.parse(body)`. Handle RSS 2.0 and Atom (feedparser covers both).
- For each entry produce a normalized record:
  - `dedup_key` — see below.
  - `feed_title`, `feed_url` (entry link), `feed_summary`, `feed_published_at` (from `published`/`updated`; `None` if unparseable).
  - `raw_payload` — the entry as a JSON-safe dict. `feedparser` entries contain `time.struct_time` and bytes; a serializer converts time structs to ISO-8601 strings and drops/normalizes non-serializable values. The full entry is preserved for reprocessing/debug.
- Entries with **no derivable `dedup_key`** (no id, no link, no title) are skipped and logged; they do not fail the feed.

#### `normalize_url(url)`

Deterministic, idempotent:
1. lowercase scheme + host; drop default ports (80/443).
2. drop the URL fragment.
3. remove tracking query params: `utm_*`, `fbclid`, `gclid`, `mc_eid`, `igshid` (denylist, extensible).
4. sort remaining query params by key.
5. strip a trailing `/` from the path except when the path is just `/`.

#### `dedup_key` precedence

First non-empty of:
1. feed entry `id`/GUID (used verbatim),
2. `normalize_url(entry.link)`,
3. `sha256(f"{source_id}\n{title}\n{published_iso}")` hex digest.

### 6. Persist (`persist.py`)

- Insert new articles with `INSERT ... ON CONFLICT (source_id, dedup_key) DO NOTHING`, setting `status = pending_processing`, `retrieved_at = now()`, `claimed_at = NULL`, `retry_count = 0`, plus the feed_* fields and `raw_payload`. Use the rowcount to report how many were genuinely new.
- **Source metadata update** (single transaction per source):
  - **Success** (incl. 304): `last_checked_at = now()`, `next_check_at = now() + refresh_interval_seconds`, `consecutive_failures = 0`, `last_error = NULL`; store `etag` / `last_modified` from the 2xx response (leave unchanged on 304).
  - **Failure:** `consecutive_failures += 1`, `last_error = <message>`, `next_check_at = now() + backoff(consecutive_failures)` where `backoff(n) = min(CAP, BASE * 2^(n-1))` then multiplied by a **uniform ±10% jitter factor** (i.e. `delay * uniform(0.9, 1.1)`, still capped at CAP); if `consecutive_failures >= RETRIEVER_MAX_SOURCE_FAILURES` set `enabled = false`.

### 7. Failure isolation

- Per-source: each `process_source` runs in its own thread + transaction; one source failing never affects another in the same tick.
- Per-entry: a malformed entry is skipped without aborting the rest of the feed.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-retriever/pyproject.toml` | real deps (httpx, feedparser, aggregator-common), console entrypoint |
| `src/aggregator_retriever/config.py` | retriever settings |
| `src/aggregator_retriever/http.py` | conditional fetch + size cap + typed errors |
| `src/aggregator_retriever/normalize.py` | `normalize_url`, `dedup_key`, payload serializer |
| `src/aggregator_retriever/parse.py` | feedparser → normalized entries |
| `src/aggregator_retriever/persist.py` | article insert (ON CONFLICT) + source metadata/backoff |
| `src/aggregator_retriever/loop.py` | scheduler loop, thread pool, in-flight set, signals |
| `src/aggregator_retriever/__main__.py` | entrypoint |
| `packages/aggregator-retriever/tests/**` | unit + DB tests (testcontainers + stubbed HTTP) |

## Acceptance Criteria

- Running one poll cycle against a source whose feed is served via a **stubbed httpx transport** (RSS fixture) inserts one `pending_processing` article per entry, each with a non-null `dedup_key`.
- Re-running the same cycle inserts **0** new articles (dedup via `ON CONFLICT`).
- A `304` response inserts nothing and still advances `last_checked_at`/`next_check_at`.
- `ETag`/`Last-Modified` from a `200` are stored on the source and sent as `If-None-Match`/`If-Modified-Since` on the next fetch.
- `normalize_url` is idempotent and strips `utm_*`/fragments/default-ports/trailing-slash per an enumerated case table.
- `dedup_key` precedence falls through id → normalized link → sha256 hash, with a test per branch.
- A fetch failure increments `consecutive_failures`, records `last_error`, and sets `next_check_at` within the expected jittered window `[BASE*2^(n-1)*0.9, BASE*2^(n-1)*1.1]` capped at `CAP`; the source is disabled once `consecutive_failures` reaches `RETRIEVER_MAX_SOURCE_FAILURES`.
- A malformed entry inside an otherwise-valid feed is skipped; the feed's other entries still persist.
- Both an RSS 2.0 fixture and an Atom fixture parse and persist correctly.
- Per-source isolation: in a cycle with one failing and one healthy source, the healthy source's articles are inserted.
- `raw_payload` round-trips to JSON (no `struct_time`/bytes leakage) and retains the source feed entry.
- SIGTERM stops scheduling and exits 0 after in-flight fetches drain.
- Full suite green via `uv run pytest` (testcontainers Postgres + stubbed HTTP; no network access).

## Pending Decisions

- Tracking-param denylist starts at `utm_*`, `fbclid`, `gclid`, `mc_eid`, `igshid` and can grow.
- Backoff constants (`BASE=60s`, `CAP=6h`) are starting values, tunable later.
- Whether to honor a feed's own `ttl`/`sy:updatePeriod` hints for scheduling is **out of scope** for v1; per-source `refresh_interval_seconds` governs cadence.
