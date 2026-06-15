---
name: Dedicated janitor service for data retention
id: spec-44ab62a3
description: "New aggregator-janitor service: a daily, advisory-lock-guarded daemon that is the single owner of data retention. Adds article retention (14d; not saved; not in a live thread; read-status irrelevant) and takes over the thread prune (from clusterer) and brief prune (from brief). Retention logic extracted to shared aggregator-common helpers; clusterer/brief stop deleting. Adds the 8th service to compose + CI. No schema change."
dependencies: null
priority: medium
complexity: null
status: planned
tags:
- janitor
- retention
- maintenance
- new-service
- db
scope:
  in: null
  out: null
feature_root_id: B-967c6d04
---
# Dedicated janitor service for data retention

## Objective

Introduce a new, independently-deployable **`aggregator-janitor`** service whose single
responsibility is **data retention**: once a day it purges expired articles, threads, and
briefs. It becomes the **single owner of all hard-delete/retention logic**, taking over the
prune responsibilities currently embedded in the clusterer and the brief service.

Rationale: retention/maintenance is a distinct concern from clustering and brief generation.
Today the clusterer hard-deletes expired threads inside its consolidation pass and the brief
service prunes old briefs — purging is scattered across feature daemons. Consolidating it into
a dedicated daily janitor gives clean separation of concerns, one place to reason about data
lifecycle, and a natural home for the new **article** retention (which doesn't exist today, so
the `articles` table grows unbounded — ~1000/week).

## Problems to Fix

- `articles` is never purged → unbounded table + `tsvector`/GIN index growth.
- Retention logic is **scattered**: thread prune lives in `aggregator-clusterer`
  (`consolidate.run_retention_prune`), brief prune lives in `aggregator-brief`. No single owner.
- Purging is entangled with feature work (a clustering cycle also deletes data), making it
  harder to reason about and tune independently.

## Changes

### 1. New package `aggregator-janitor`

A new uv-workspace package mirroring the other services:
- `pyproject.toml` (depends on `aggregator-common`), console-script entrypoint
  (`aggregator-janitor`), `__main__.py`, `config.py`, `Dockerfile`.
- A **daily scheduler daemon** modeled on `aggregator-brief`'s scheduler: a poll loop that runs
  the retention sweep once per day at a configurable hour/timezone, guarded by a **Postgres
  advisory lock** (distinct key from the clusterer's) so concurrent/duplicate instances never
  double-run. Calls `load_env()` + `configure_logging(stream=sys.stdout)` per the daemon
  convention.
- The daemon stays thin: it just invokes the shared retention helpers and logs the counts.

### 2. Shared retention helpers (single source of truth) in `aggregator-common`

Add pure, unit-testable helpers (e.g. in a new `aggregator_common/retention.py`), each
returning a deleted-count and run in one transaction:
- `purge_expired_articles(session, retention_days)` — **NEW**. Delete articles where
  `retrieved_at < now() - retention_days` **AND** `is_saved = false` **AND** not a member of any
  `active`/`dormant` thread. Read status is **not** a factor (old unread + un-threaded articles
  are purged too). FK ordering: delete the eligible articles' `thread_memberships` rows first
  (they point only at archived/expired threads), then the articles.
- `purge_expired_threads(session, retention_days)` — **MOVED** from
  `aggregator-clusterer/consolidate.run_retention_prune` (same logic: delete threads whose
  `last_updated` is older than the window; memberships removed with them).
- `purge_expired_briefs(session, retention_days)` — **MOVED** from the brief service's prune.

### 3. Remove the prune sites from clusterer and brief (no double ownership)

- `aggregator-clusterer/consolidate.py`: remove the `run_retention_prune` call from
  `run_consolidation_pass` (and the function, or have it delegate to the shared helper but stop
  calling it from the clusterer). The clusterer keeps clustering, scoring, merge, and the
  dormant→archived **status** transitions — only the hard-`DELETE` of expired threads moves to
  the janitor.
- `aggregator-brief`: remove its brief-pruning step; the janitor owns it.
- Net: clustering and brief generation no longer delete anything.

### 4. Config

`JanitorSettings(aggregator_common.config.Settings)` with `JANITOR_` prefix:
- `JANITOR_ARTICLE_RETENTION_DAYS` (default **14**)
- `JANITOR_THREAD_RETENTION_DAYS` (default 30 — preserve current behavior)
- `JANITOR_BRIEF_RETENTION_DAYS` (default 30 — preserve current behavior)
- `JANITOR_RUN_HOUR` (default e.g. 4), `JANITOR_TIMEZONE` (default `UTC`),
  `JANITOR_POLL_INTERVAL_SECONDS` (default 3600), `JANITOR_CLAIM_LEASE_SECONDS` / advisory-lock key.

(The existing `CLUSTERER_THREAD_RETENTION_DAYS` / `BRIEF_RETENTION_DAYS` settings become unused
once their prune sites move; document the migration of these knobs to the `JANITOR_` names.)

### 5. Deployment / CI plumbing

- Add `janitor` to `docker-compose.yml` (dev, built from source) and `docker-compose.prod.yml`
  (pulls `aggregator-janitor` image; `env_file: .env`, `DATABASE_URL` to the `postgres` service,
  `depends_on` postgres healthy + migrate complete, `restart: unless-stopped`).
- Add `aggregator-janitor` to `scripts/build-images.sh` and the CI build matrix (7 → 8 images,
  GHCR path `ghcr.io/oscarrenalias/personal-aggregator/aggregator-janitor`).
- Update `CLAUDE.md` (architecture diagram + service list, monorepo layout, config table,
  release pipeline service list) and `deploy/README.md` as needed.

### 6. Tests

- `aggregator-common/tests`: the three retention helpers — eligibility rules (age/saved/
  live-thread for articles; `last_updated` for threads; age for briefs), FK ordering (no
  constraint violation), counts.
- `aggregator-janitor/tests`: the daily-schedule gate (runs once per day at the configured
  hour), advisory-lock guard, and that a run invokes all three purges and reports totals.
- Regression: clusterer no longer deletes threads in its consolidation pass; brief no longer
  prunes.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-janitor/**` (new) | New service: pyproject, entrypoint, `__main__`, `config.py`, daily-scheduler daemon, `Dockerfile`, tests |
| `packages/aggregator-common/src/aggregator_common/retention.py` (new) | `purge_expired_articles` / `purge_expired_threads` / `purge_expired_briefs` shared helpers |
| `packages/aggregator-clusterer/src/aggregator_clusterer/consolidate.py` | Remove the thread retention-prune call from the consolidation pass (logic moves to the shared helper / janitor) |
| `packages/aggregator-brief/**` | Remove the brief-prune step |
| `docker-compose.yml`, `docker-compose.prod.yml` | Add the `janitor` service |
| `scripts/build-images.sh` + CI workflow | Build/push the `aggregator-janitor` image (8th service) |
| `CLAUDE.md`, `deploy/README.md`, `.env.example` | Document the janitor, its `JANITOR_*` config, and the moved retention knobs |
| workspace root `pyproject.toml` | Register the new workspace package |

## Acceptance Criteria

- A new `aggregator-janitor` service runs once per day (configurable hour/timezone, advisory-lock
  guarded) and, in one run, purges expired **articles** (14d; not saved; not in a live thread —
  read status irrelevant), expired **threads** (30d), and expired **briefs** (30d), logging the
  deleted counts. No FK violations (memberships removed before articles).
- **Saved** articles and articles in **active/dormant** threads are never deleted; articles
  newer than the window are never deleted.
- The clusterer's consolidation pass **no longer deletes threads**, and the brief service **no
  longer prunes briefs** — the janitor is the sole owner of retention. Existing thread/brief
  retention windows are preserved (just relocated and renamed to `JANITOR_*`).
- **All retention windows are configurable**: `JANITOR_ARTICLE_RETENTION_DAYS`,
  `JANITOR_THREAD_RETENTION_DAYS`, and `JANITOR_BRIEF_RETENTION_DAYS` (plus the schedule:
  `JANITOR_RUN_HOUR`/`JANITOR_TIMEZONE`/`JANITOR_POLL_INTERVAL_SECONDS`) are read from env/`.env`
  and take effect on restart with no code change or image rebuild. The eligibility *invariants*
  (keep saved articles; keep articles in a live thread) are intentionally NOT configurable.
- The janitor image builds and publishes in CI alongside the other services and starts under
  both dev and prod compose.
- Focused `aggregator-common`, `aggregator-janitor`, `aggregator-clusterer`, and
  `aggregator-brief` tests pass; full gate green. **No schema change.**

## Pending Decisions

- **Article window**: 14 days (decided). Thread/brief windows stay 30 days (preserve current
  behavior) unless we want to revisit.
- **Scheduling approach**: reuse the brief's daily-scheduler pattern (poll + run-once-per-day at
  a configured hour) vs. a simpler "sleep 24h" loop. Lean toward the brief pattern for
  consistency and resilience across restarts.
- **Knob migration**: `CLUSTERER_THREAD_RETENTION_DAYS` and `BRIEF_RETENTION_DAYS` become
  `JANITOR_*`. Decide whether to keep the old env names as aliases for one release or cut over
  cleanly (proposed: cut over + document, since this is pre-1.0 and self-hosted).
- **Manual trigger**: optionally expose a one-shot mode (`aggregator-janitor --once`) and/or an
  admin/MCP "run retention now" — nice-to-have, not required for v1.
- **Count-based cap**: age-based only for now; a max-row cap could be added later.
