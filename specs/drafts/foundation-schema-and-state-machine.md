---
name: Foundation - Schema and State Machine
id: spec-9e974b88
description: "Shared foundation: uv monorepo skeleton, aggregator-common package, Postgres schema (single wide articles table), article state machine, claim/reaper mechanism, and testcontainers harness. Contract for all four services."
dependencies: null
priority: high
complexity: null
status: draft
tags:
- foundation
- schema
- database
- infra
scope:
  in: null
  out: null
feature_root_id: null
---
# Foundation - Schema and State Machine

## Objective

Establish the shared foundation that all four services (retriever, processor, summarize-rank, web) build on: the uv monorepo skeleton, the `aggregator-common` package, the Postgres schema, the article **state machine**, the **work-claiming + reaper** mechanism, and the test harness.

Because the services integrate **only through Postgres state** (no synchronous calls), the schema and the allowed state transitions defined here are the API between services. This spec is the contract; later component specs depend on it and must not redefine it.

No service business logic is implemented here — only the shared substrate plus its tests.

## Background / Decisions

These decisions are already made (see `CLAUDE.md`). Implement to them; do not re-litigate.

- **Stack:** Python + uv workspace, Postgres, SQLAlchemy 2.x (typed/`Mapped`), Alembic migrations.
- **Layout:** package-per-service monorepo. This spec creates `aggregator-common` and the empty workspace; service packages are stubbed only.
- **Schema shape:** a **single wide `articles` table** — raw, processed, LLM, and interaction fields as nullable columns on one row. No split per-stage tables.
- **In-flight modeling:** **claim-based**. `status` never has a `processing`/`ranking` value; a row is in-flight when `claimed_at IS NOT NULL`. A reaper releases stale claims.
- **IDs:** `bigint` generated-always identity PK. Dedup is a separate concern (`dedup_key`, see below) — identity ≠ dedup.
- **Tests:** pytest + testcontainers (ephemeral Postgres per session). No shared/fixed test DB.

## Changes

### 1. Workspace skeleton

Create the uv workspace and package layout:

```
pyproject.toml                         # [tool.uv.workspace] members = ["packages/*"]
packages/
  aggregator-common/
    pyproject.toml                     # name = "aggregator-common"
    src/aggregator_common/
      __init__.py
      config.py
      db.py
      models.py
      state.py
      claim.py
      migrations/                      # Alembic env + versions
    tests/
  aggregator-retriever/                # stub: pyproject + src/aggregator_retriever/__init__.py + console entrypoint that exits 0
  aggregator-processor/                # stub
  aggregator-summarize-rank/           # stub
  aggregator-web/                      # stub
docker-compose.yml                     # postgres service for local dev
```

Service packages are **stubs only**: a valid `pyproject.toml` depending on `aggregator-common`, a console-script entrypoint (`aggregator-retriever`, etc.) that prints a banner and exits 0. Their real behavior comes in later specs.

### 2. Configuration (`config.py`)

A `Settings` object loaded from environment variables (12-factor), at minimum:

- `DATABASE_URL` — Postgres DSN.
- `CLAIM_LEASE_SECONDS` — default 600. Reaper releases claims older than this.
- `LOG_LEVEL` — default `INFO`.

LLM keys and service-specific settings are **out of scope** here (belong to summarize-rank). Loader must fail fast with a clear error if `DATABASE_URL` is missing.

### 3. Schema (`models.py` + initial Alembic migration)

Three tables. Use SQLAlchemy 2.x typed models; the Alembic migration is the source of truth for DDL.

#### `sources`

| Column | Type | Notes |
|---|---|---|
| `id` | bigint PK identity | |
| `name` | text not null | |
| `feed_url` | text not null unique | |
| `enabled` | boolean not null default true | |
| `refresh_interval_seconds` | int not null default 3600 | per-source frequency |
| `priority` | int not null default 0 | optional weighting |
| `last_checked_at` | timestamptz null | |
| `next_check_at` | timestamptz null | retriever scheduling key |
| `etag` | text null | conditional-request cache |
| `last_modified` | text null | conditional-request cache |
| `consecutive_failures` | int not null default 0 | |
| `last_error` | text null | |
| `default_image_url` | text null | source-level fallback image |
| `created_at` / `updated_at` | timestamptz not null | |

#### `articles` (single wide table)

Identity / dedup:

| Column | Type | Notes |
|---|---|---|
| `id` | bigint PK identity | surrogate identity |
| `source_id` | bigint not null FK→sources(id) | |
| `dedup_key` | text not null | normalized identity; `UNIQUE (source_id, dedup_key)` |
| `status` | `article_status` enum not null | see state machine |

Claim / failure (per the claim model — one claim at a time, since a row is only ever worked by one stage given its status):

| Column | Type | Notes |
|---|---|---|
| `claimed_by` | text null | worker id |
| `claimed_at` | timestamptz null | non-null = in-flight |
| `retry_count` | int not null default 0 | reset on stage transition |
| `next_retry_at` | timestamptz null | backoff gate |
| `last_error` | text null | |

Raw (retriever):

| Column | Type | Notes |
|---|---|---|
| `raw_payload` | jsonb not null | full parsed feed entry, for reprocessing/debug |
| `feed_title` | text null | |
| `feed_url` | text null | article link from feed |
| `feed_summary` | text null | |
| `feed_published_at` | timestamptz null | |
| `retrieved_at` | timestamptz not null | |

Processed (processor):

| Column | Type | Notes |
|---|---|---|
| `clean_title` | text null | |
| `clean_text` | text null | extracted readable text |
| `excerpt` | text null | |
| `author` | text null | |
| `published_at` | timestamptz null | normalized |
| `header_image_url` | text null | |
| `word_count` | int null | |
| `language` | text null | |
| `processed_at` | timestamptz null | |
| `search_vector` | tsvector null | normal nullable column written **explicitly by the processor** (not generated/trigger-maintained), so the processor controls field weighting; GIN-indexed in the initial migration |

LLM (summarize-rank):

| Column | Type | Notes |
|---|---|---|
| `summary` | text null | |
| `topics` | jsonb null | array of strings |
| `entities` | jsonb null | optional |
| `importance_score` | int null | 0–100 |
| `importance_reason` | text null | |
| `llm_meta` | jsonb null | model, tokens, prompt version |
| `summarized_at` | timestamptz null | |

Interaction (web):

| Column | Type | Notes |
|---|---|---|
| `is_read` | boolean not null default false | |
| `read_at` | timestamptz null | |
| `is_saved` | boolean not null default false | |
| `is_hidden` | boolean not null default false | |

Timestamps: `created_at` / `updated_at` not null. `updated_at` is maintained uniformly across `sources`, `articles`, and `interest_profile` by a Postgres `BEFORE UPDATE` trigger created in the initial migration (single shared trigger function), so all four services get consistent behavior without app-layer coordination.

**Indexes:**
- `UNIQUE (source_id, dedup_key)`
- partial index on `(status, next_retry_at)` `WHERE claimed_at IS NULL` — the claim hot path.
- index on `claimed_at` — reaper scan.
- GIN on `search_vector`.
- index on `(importance_score DESC)` and `(feed_published_at DESC)` for UI sorting.

#### `interest_profile` (singleton config)

| Column | Type | Notes |
|---|---|---|
| `id` | bool PK default true, `CHECK (id)` | enforces single row |
| `profile_text` | text not null default '' | freeform user interests |
| `updated_at` | timestamptz not null | |

### 4. State machine (`state.py`)

`article_status` enum values (Postgres enum type):

- `pending_processing` — retriever inserted it; awaiting processor.
- `pending_ranking` — processor finished; awaiting summarize-rank.
- `ready` — summarize-rank finished; displayable in full.
- `failed_processing` — processing exhausted retries.
- `failed_ranking` — ranking exhausted retries.
- `skipped` — deliberately not processed/ranked (e.g. too short, unsupported).

There is **no** `processing` or `ranking` status — in-flight is `claimed_at IS NOT NULL`.

**Entry point:** a row enters the machine when the retriever inserts it as `pending_processing` with `claimed_at IS NULL`, `retry_count = 0`. This is the only insert-time status; every other status is reached via a transition below.

**Stage ↔ status mapping** (drives `claimable_status_for`, success target, and `fail` target — define as data, not narrative):

| Stage | Claimable status | Success target | Failure target |
|---|---|---|---|
| `processor` | `pending_processing` | `pending_ranking` | `failed_processing` |
| `summarize_rank` | `pending_ranking` | `ready` | `failed_ranking` |

Allowed transitions (define as data + a `can_transition(from, to)` guard; reject anything else):

```
pending_processing → pending_ranking        (processor success)
pending_processing → failed_processing       (processor, retries exhausted)
pending_processing → skipped                 (processor, deliberate skip)
pending_ranking    → ready                    (summarize-rank success)
pending_ranking    → failed_ranking           (summarize-rank, retries exhausted)
pending_ranking    → skipped                  (summarize-rank, deliberate skip)
failed_processing  → pending_processing       (manual/explicit reprocess)
failed_ranking     → pending_ranking          (manual/explicit re-rank)
ready              → pending_ranking           (explicit re-rank request)
```

Each `pending_*` status has an associated "claim status" — the value a worker filters on. Expose helpers: `claimable_status_for(stage)` and the set of in-flight detection.

### 5. Work-claiming + reaper (`claim.py`)

Provide reusable functions used by every worker service:

- `claim_batch(session, status, worker_id, limit, now) -> list[Article]`:
  ```sql
  UPDATE articles SET claimed_by = :worker, claimed_at = :now
  WHERE id IN (
    SELECT id FROM articles
    WHERE status = :status
      AND claimed_at IS NULL
      AND (next_retry_at IS NULL OR next_retry_at <= :now)
    ORDER BY id
    FOR UPDATE SKIP LOCKED
    LIMIT :limit
  )
  RETURNING *;
  ```
- `complete(session, article, new_status)`: validates the transition via `state.can_transition`, sets `status`, clears `claimed_by`/`claimed_at`, resets `retry_count`/`next_retry_at`/`last_error`.
- `fail(session, article, error, max_retries, backoff)`: increments `retry_count`, records `last_error`, clears the claim; if `retry_count < max_retries` sets `next_retry_at` (exponential backoff) and leaves status unchanged (re-claimable); else transitions to the stage's `failed_*` status.
- `reap_stale_claims(session, lease_seconds, now) -> int`: clears `claimed_by`/`claimed_at` for rows where `claimed_at < now - lease` (returns them to claimable). Returns count.

All claim/complete/fail operations are single transactions.

**Reaper invocation contract:** foundation provides `reap_stale_claims` as a function only — it does **not** run a scheduler. Each worker service is expected to call it on its own periodic loop using `CLAIM_LEASE_SECONDS` as the lease. (The retriever, which only inserts, need not run it.) Calling it more than once concurrently is safe — it is idempotent and races resolve via row locks.

### 6. Docker + test harness

- `docker-compose.yml` with a `postgres` service (named volume, exposed port) for local manual running.
- pytest fixtures in `aggregator-common/tests/conftest.py` using **testcontainers**: spin up an ephemeral Postgres per test session, run Alembic `upgrade head` against it, yield a session factory. Random port — safe under parallel takt worktrees.
- **Docker socket resolution:** the runtime is OrbStack, which does not create `/var/run/docker.sock`. The conftest must resolve the socket *before* testcontainers initializes, in this order and set `DOCKER_HOST` if it falls through: (1) existing `DOCKER_HOST` env — leave as-is; (2) `/var/run/docker.sock` if it exists; (3) `~/.orbstack/run/docker.sock` (use `Path.home()`, not a hardcoded user path). If none resolve, fail with a clear message naming OrbStack. This must work headless so `pytest`/`takt merge` pass for every worker without manual env setup.
- Acceptance: the suite runs green via `uv run pytest` with no `DOCKER_HOST` exported and no `/var/run/docker.sock` present (OrbStack-only environment).

## Files to Modify

| File | Change |
|---|---|
| `pyproject.toml` | uv workspace root, members = packages/* |
| `packages/aggregator-common/pyproject.toml` | common package + deps (sqlalchemy, alembic, psycopg, pydantic-settings) |
| `packages/aggregator-common/src/aggregator_common/config.py` | env-driven Settings |
| `packages/aggregator-common/src/aggregator_common/db.py` | engine/session factory |
| `packages/aggregator-common/src/aggregator_common/models.py` | sources, articles, interest_profile models |
| `packages/aggregator-common/src/aggregator_common/state.py` | enum, transition table, guards |
| `packages/aggregator-common/src/aggregator_common/claim.py` | claim_batch, complete, fail, reap_stale_claims |
| `packages/aggregator-common/src/aggregator_common/migrations/**` | Alembic env + initial migration |
| `packages/aggregator-common/tests/conftest.py` | testcontainers Postgres fixtures |
| `packages/aggregator-common/tests/test_state.py` | transition guard tests |
| `packages/aggregator-common/tests/test_claim.py` | claim/complete/fail/reaper tests |
| `packages/aggregator-{retriever,processor,summarize-rank,web}/**` | stub packages + entrypoints |
| `docker-compose.yml` | postgres dev service |

## Acceptance Criteria

- `uv sync` installs the whole workspace; `aggregator-common` is importable from every service package.
- Each service stub exposes its console entrypoint (`uv run aggregator-retriever` etc.) and exits 0.
- `alembic upgrade head` against a fresh Postgres creates `sources`, `articles`, `interest_profile`, the `article_status` enum, and all listed indexes; `alembic downgrade base` reverses it.
- `state.can_transition` returns true for exactly the transitions listed and false for all others (test enumerates the full matrix).
- `claim_batch` under two concurrent sessions never returns the same row twice (verified with a `FOR UPDATE SKIP LOCKED` concurrency test).
- `fail` re-queues a row with backoff until `retry_count == max_retries`, then moves it to the correct `failed_*` status **per the stage mapping** — a `processor` exhaustion lands in `failed_processing`, a `summarize_rank` exhaustion lands in `failed_ranking` (both asserted).
- A freshly retriever-inserted row has status `pending_processing`, `claimed_at IS NULL`, `retry_count = 0`, and is immediately claimable by the processor stage.
- `reap_stale_claims` releases a row whose `claimed_at` is older than the lease and ignores fresh claims.
- A row cannot be claimed while `next_retry_at` is in the future.
- The full pytest suite passes via testcontainers with no externally running Postgres.

## Pending Decisions

- **`dedup_key` construction** is defined in the retriever spec (candidates: feed GUID → article URL → normalized URL → source+title+date hash). Foundation only enforces the `UNIQUE (source_id, dedup_key)` constraint and stores whatever the retriever computes.
- **Backoff curve** (base/cap for `next_retry_at`) — start simple (e.g. `min(cap, base * 2^retry_count)`); exact constants can be tuned later.
