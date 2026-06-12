---
name: Story thread clustering and prioritization
id: spec-7b39b44f
description: "Foundational story clustering: a new aggregator-clusterer worker groups ready articles into prioritized 'threads' (no embeddings; FTS+entities+topics+time+LLM), with a Threads web view to inspect cluster quality. Brief and feed unchanged; topic memory and brief integration deferred."
dependencies: null
priority: high
complexity: null
status: planned
tags:
- clustering
- threads
- pipeline
- web
scope:
  in: null
  out: null
feature_root_id: B-8c98aa16
---
# Story thread clustering and prioritization

Background/product direction: `docs/AI_News_Aggregator_Story_Clustering.md`. This spec
implements the foundational clustering layer only (the doc's MVP 1 + a first cut of MVP 3
"what changed"). Topic memory (doc MVP 4) and brief integration (doc MVP 2) are explicitly
deferred to later specs.

Naming: an event-level cluster of articles is a **thread** (user-facing "Threads"); the
persistent longer-running subject that will later group threads is a **topic** — NOT built
here. Code/data uses `thread`.

## Objective

Move the product beyond an endless list of individual articles by grouping related `ready`
articles into **threads** — sets of articles about the same concrete development — and
prioritizing them so the user sees "the few things worth knowing" instead of hundreds of
items. This is built as a **standalone, inspectable capability**: a new clusterer worker
plus a "Threads" web view to evaluate cluster quality on real feeds. The existing daily
brief and article feed are intentionally left unchanged; brief integration comes later,
only once thread quality is validated.

## Problems to Fix

- The app still presents articles one-by-one; duplicates and rewrites of the same story
  are not collapsed, so the user carries the cognitive load of de-duplicating mentally.
- There is no notion of "this is the same story as yesterday, here's only what changed."
- Nothing distinguishes a genuinely new development from the 18th syndicated rewrite.

## Changes

### 1. New `aggregator-clusterer` worker service

A new package + console-script + Dockerfile + claim-based daemon, modelled on
`aggregator-brief`/`aggregator-summarize-rank` (load_env → Settings → configure_logging →
poll loop; SIGINT/SIGTERM clean stop). It runs **after** summarize-rank and operates on
`ready` articles, reusing their existing `summary`, `topics`, `entities`, and
`importance_score` (no re-derivation). Wire it into both compose files, the build matrix,
and CI like the brief service.

Cadence: batch poll on an interval (configurable). Hybrid-ready but batch to start —
each cycle clusters recent unassigned `ready` articles and refreshes affected threads.

**Article lifecycle (do not perturb the existing state machine):** the clusterer does NOT
change `articles.status` (articles stay `ready`) and does NOT use the shared
`articles.claimed_by/claimed_at` columns (those belong to processor/summarize-rank and
their reaper — reusing them would cause contention). The **thread-membership row is the
single source of truth**: an article is "unassigned" iff it has no membership row;
assigning it creates one (the idempotency key — re-running a cycle never double-assigns).
A cycle selects `ready` articles within the candidate window that lack a membership. Guard
against two concurrent cycles with a Postgres advisory lock (or a single-instance
assumption) rather than per-article claims.

### 2. Candidate retrieval (NO embeddings for MVP)

For each unassigned `ready` article, gather candidate existing threads / recent articles
using signals we already have:
- publication-time window (configurable, e.g. 24–72h fast / up to N days slow),
- shared extracted **entities** and **topics** overlap,
- Postgres full-text (`tsvector`) similarity on title/summary,
- canonical-URL / title near-duplicate checks.
Embeddings/pgvector are explicitly out of scope; add later only if recall is poor. Store
the candidate signals/scores considered, for debuggability.

### 3. LLM classification (final decision)

For borderline candidates, call the LLM (litellm, its own `CLUSTERER_LLM_MODEL`, default a
cheap model) with structured output to classify the article against the best candidate
thread. Label set (from the doc):
`new_thread`, `same_thread_new_fact`, `same_thread_new_angle`, `same_thread_duplicate`,
`same_thread_background_only`, `correction_or_clarification`, `related_new_thread`,
`irrelevant_or_low_value`. Output includes `thread_id` (or null for new), `confidence`,
`new_facts[]`, and a short `reason`. Clear duplicates by canonical URL can short-circuit
without an LLM call to save cost.

### 4. Thread entity + "known facts" + novelty

Each thread carries: representative title, rolling summary, a compact **known-facts** list,
member article ids (with per-member classification label), first-seen / last-updated
timestamps, status (`active` / `dormant` / `archived` by age), source list + a source
**diversity** measure, a **confidence** in the grouping, and the latest novelty label.
When an article adds a `same_thread_new_fact`/`correction`, append to known-facts and record
a "what changed" delta + bump last-updated. Duplicates/background-only are recorded as
members but flagged suppressed (kept as evidence, not surfaced).

### 5. Thread prioritization (explainable)

Score each thread on multiple, stored dimensions — **relevance** (vs the interest profile +
categories), **novelty**, **importance/impact** (roll up member importance), **source
diversity**, **confidence**, **time sensitivity** — and derive a priority **tier**:
`must_know`, `worth_tracking`, `deep_read`, `low_noise`. Persist a human-readable `reason`
("New regulatory development affecting a topic you follow"). No black-box single score
without explanation.

### 6. "Threads" web view (inspection)

A new top-level sidebar entry **Threads**, alongside Today + the feeds (nothing demoted),
using the existing list→reader master-detail pattern:
- List pane: thread cards grouped by tier (Must know / Worth tracking / Deep reads /
  Low-noise), each showing representative title, tier, source count, last-updated, member
  count, and the priority `reason`.
- Reader pane (thread detail): summary, **what changed** (latest delta), known facts,
  member articles (with their labels + a clearly separated "suppressed duplicates" group),
  source comparison, the why-grouped confidence/explanation, and links into the existing
  article reader for any member.
- Include a small **"suppressed today"** summary (e.g. "18 duplicates, 6 rewrites collapsed")
  to demonstrate noise reduction.

### 7. Inspection & control surface

- Admin CLI `clusters` group for debugging/validation: `list` (by tier/recency), `show`
  (one thread + members + signals), and `recluster` (manual re-cluster of a recent window).
- A manual **"re-cluster now"** trigger reachable from the Threads view (enqueue a cycle),
  mirroring the brief's refresh, so quality can be re-evaluated on demand.
- **Re-cluster scope (this iteration):** a re-cluster re-runs candidate retrieval +
  classification for **unassigned** articles in the window and refreshes the scores/tiers/
  summaries of touched threads. It does **not** split or merge existing threads, nor move
  already-assigned articles between threads — that is deferred. It is idempotent w.r.t.
  already-assigned articles.
- (MCP exposure of threads is deferred to the later brief-integration spec.)

### 8. Config

New `CLUSTERER_`-prefixed settings (subclassing the shared `Settings`): poll interval,
candidate time windows, similarity/entity-overlap thresholds, max candidates, LLM model +
output tokens + temperature + timeout, claim lease, dormant/archive age thresholds,
tier thresholds. Document in `.env.example` and CLAUDE.md.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-clusterer/**` | New package: worker loop, candidate retrieval, LLM classification, thread upsert + scoring, config, Dockerfile, console script, tests |
| `packages/aggregator-common/src/aggregator_common/models.py` | New `Thread` (+ membership) ORM models |
| `packages/aggregator-common/src/aggregator_common/migrations/versions/*` | New migration for thread tables (down_revision = current head) |
| `packages/aggregator-common/src/aggregator_common/queries.py` | Thread read helpers (list by tier, get thread + members + signals) |
| `packages/aggregator-common/src/aggregator_common/management.py` | `enqueue_recluster` (+ any thread mutation helpers) shared by admin + web |
| `packages/aggregator-web/src/aggregator_web/app.py` | `/threads`, `/thread/{id}`, `/threads/recluster` routes |
| `packages/aggregator-web/src/aggregator_web/templates/` | `_thread_list.html`, `_thread_card.html`, `_thread_detail.html`; sidebar "Threads" entry |
| `packages/aggregator-web/src/aggregator_web/static/{app.js,styles.css}` | `threadList` component (reuse reader master-detail), thread/tier styling |
| `packages/aggregator-admin/src/aggregator_admin/clusters.py` | New `clusters` command group (list/show/recluster) |
| `docker-compose.yml`, `docker-compose.prod.yml`, `scripts/build-images.sh`, `.github/workflows/ci.yml`, `.env.example`, `CLAUDE.md`, `deploy/README.md` | Wire + document the new service |
| `packages/*/tests/` | Worker classification/scoring tests, web route/template tests, admin CLI tests |

## Acceptance Criteria

- A new `aggregator-clusterer` service runs after summarize-rank, claims recent `ready`
  articles in batches, and assigns each to a new or existing **thread** using
  entity/topic/FTS/time-window candidate retrieval + LLM classification (no embeddings).
- Threads persist representative title, rolling summary, known-facts, members (with
  per-member label), first-seen/last-updated, source diversity, confidence, novelty, and a
  status that ages active→dormant→archived.
- Each thread is scored on the named explainable dimensions and assigned a tier
  (must_know / worth_tracking / deep_read / low_noise) with a stored human-readable reason.
- Obvious duplicates (canonical URL) are collapsed without an LLM call; near-duplicates and
  rewrites are classified and flagged suppressed (retained as evidence, not surfaced).
- The **Threads** web view lists threads grouped by tier and opens a thread detail showing
  summary, what-changed, known facts, members, suppressed duplicates, sources, and the
  why-grouped explanation; a "suppressed today" summary is shown. The existing feed, Today,
  and reader are unchanged.
- A manual re-cluster can be triggered from the Threads view and via
  `aggregator-admin clusters recluster`; `clusters list`/`show` aid inspection.
- Thread membership is the source of truth: an article is assigned to at most one primary
  thread, the clusterer does not touch `articles.status`/`claimed_*`, and re-running a
  cycle is idempotent for already-assigned articles (no duplicate memberships).
- The daily **brief is NOT modified** by this spec (no behaviour change to brief output).
- New migration applies/reverses cleanly; new service wired into both compose files, build
  script, and CI; `CLUSTERER_*` config documented.
- Focused tests pass per touched package; full gate green.

Validation note: **cluster quality is assessed manually** this iteration — via the Threads
view and `clusters show` — there is no automated precision/recall threshold (deferred until
signals/thresholds are tuned on real feeds). Tests assert mechanics (assignment,
idempotency, tiering, dedup-without-LLM, routes), not semantic clustering accuracy.

## Pending Decisions

- Resolved: foundation-first; **no brief integration** in this spec; **no embeddings**
  (FTS + entities/topics + time window → LLM); new dedicated `aggregator-clusterer` worker
  (batch); user-facing name **Threads** (persistent **Topics** layer deferred).
- Out of scope (later specs): brief consuming threads (MVP 2); **topic memory** —
  persistent topics, timelines, standing summaries, "what changed this week", follow/ignore
  (MVP 4); the feedback loop (more/less like this); the Ask/query view; embeddings/pgvector;
  demoting the article feed from the default view.
- To tune empirically during build (sensible defaults now, configurable): similarity /
  entity-overlap thresholds, candidate time windows, and tier cutoffs.
- Migration chaining: the new migration's `down_revision` must chain from whatever Alembic
  head exists at merge time (the takt merge/rebase step revalidates), to avoid a stale head.
