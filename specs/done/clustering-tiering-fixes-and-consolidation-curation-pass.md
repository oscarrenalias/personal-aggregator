---
name: Clustering tiering fixes and consolidation curation pass
id: spec-2ac41d74
description: "Improve clustering quality: fix tiering signals (diversity rewards singletons; relevance double-counts importance and ignores the profile), add a single-source gate, and add an end-of-run consolidation/curation pass that merges same-story threads and curates a short ranked set per tier. Embeddings recall is a separate companion spec."
dependencies: null
priority: high
complexity: null
status: done
tags:
- clustering
- tiering
- curation
- quality
scope:
  in: null
  out: null
feature_root_id: B-6aadaf04
---
# Clustering tiering fixes and consolidation curation pass

Builds on the merged story-thread clustering (`aggregator-clusterer`). Background:
`docs/AI_News_Aggregator_Story_Clustering.md`. The brief and feed remain untouched.
**Embeddings-based recall is explicitly a separate companion spec** (it needs pgvector
infra); this spec improves tiering quality and adds the end-of-run consolidation/curation
the current incremental design lacks.

## Objective

Make the Threads view a **short, curated, high-relevance set** instead of a long list
dominated by single-article, off-interest "topics." Fix the tiering signals that promote
weak singletons, and add a **consolidation/curation pass** that runs after a full
clustering cycle to (a) merge threads that are really the same story and (b) curate a
small ranked set per tier, gating out low-relevance and single-source noise.

## Problems to Fix

Observed on a live run (e.g. a tabloid "Married at First Sight" item, a lone GitHub repo,
and an "Iran ambassador at World Cup" piece all promoted as standalone read-worthy topics):

1. **`source_diversity` rewards singletons.** It is computed as `unique_sources/total`,
   so a 1-article thread scores `1/1 = 1.0` (maximum diversity). Single-source threads
   should score *low* on diversity (corroboration across sources is the signal).
2. **"Relevance" ignores the interest profile and double-counts importance.** The relevance
   dimension is `avg(article.importance_score)/100`, i.e. essentially the same signal as the
   importance dimension — together ~55% of the composite. Off-profile content (tabloid,
   sports gossip) that happens to carry a moderate importance score gets promoted, and
   profile-relevance never gates the top tiers.
3. **Single-article threads can reach must_know/worth_tracking** with no penalty — there is
   no member-count / source-count gate.
4. **No consolidation or curation step exists.** Scoring/tiering is incremental, per touched
   thread only; nothing re-evaluates the full thread set at the end of a run to merge
   near-duplicate threads or curate a short ranked list. The result is a sprawl of tiers.

## Changes

### 1. Fix tiering signals (`aggregator-clusterer/scoring.py`, `management.py`)

- **Source diversity = corroboration by distinct-source count**, not a ratio. Replace
  `_source_diversity` so a single distinct source → ~0.0 and diversity rises with the number
  of distinct sources, saturating (e.g. `min(1.0, (distinct_sources - 1) / (N - 1))` with a
  configurable saturation `N`, default ~4). One source must not score 1.0.
- **Stop double-counting importance.** The current "relevance" dimension is just
  `avg(importance_score)/100`, duplicating the importance dimension. **Decision: drop the
  separate relevance dimension from the scalar composite and re-normalize the remaining
  dimension weights to sum to 1.0** (so removing relevance does not silently rescale the
  composite). Profile-relevance becomes the job of the **curation-pass relevance gate** (§2b),
  which is the authoritative, profile-aware filter — rather than a weak scalar heuristic at
  scoring time. (`relevance_score` may still be persisted for display, set from the gate's
  judgement, but it no longer feeds the composite.)
- **Single-source / single-article gate.** A thread with `<2` distinct sources (or `<2`
  members) may not be assigned `must_know` and is capped at `worth_tracking` only with a
  high importance bar; otherwise it lands in `deep_read`/`low_noise`. Thresholds and the
  minimum-source requirement are configurable (`CLUSTERER_MIN_SOURCES_FOR_MUST_KNOW`, etc.).

### 2. Consolidation / curation pass (new; `aggregator-clusterer/worker.py` + `consolidate.py`)

Run a global pass **after a full clustering cycle** — i.e. when a cycle finds no remaining
unassigned `ready` articles (the corpus is fully clustered), and also on an explicit
recluster request. Guard with the existing advisory lock.

**(a) Merge near-duplicate threads.** Find candidate thread *pairs* that are likely the same
story — using shared entities/topics + FTS similarity on `representative_title` +
`rolling_summary`, within a time window (this is the no-embeddings recall lever; the
companion embeddings spec will strengthen it). For each candidate pair above a similarity
floor, ask the LLM (same `CLUSTERER_LLM_MODEL`) to confirm "same story?"; if yes, **merge**:
reassign the absorbed thread's memberships, union `source_list`/`known_facts`, keep the
better `representative_title`/`rolling_summary`, append a merge entry to `deltas`, then
delete the absorbed thread. Add a **`merge_threads(session, keep_id, absorb_id)`** helper to
`aggregator-common/management.py` (none exists today). Bound the number of LLM merge checks
per pass (`CLUSTERER_MAX_MERGE_CHECKS`).

**(b) Curate a short ranked set per tier.** After merging, re-score all active threads, then
apply curation:
- **Hard relevance gate** against the interest profile — **default mechanism: an LLM call**
  (same `CLUSTERER_LLM_MODEL`) that, given the interest profile and a thread's title/summary,
  returns relevant/not-relevant (+ a short reason persisted to `relevance_score`/`tier_reason`).
  Threads judged off-interest are forced to `low_noise` regardless of importance (fixes the
  tabloid promotion). **Fail-open fallback:** if the LLM call errors/times out, the gate is
  skipped for that thread (tier left as scored) rather than crashing the pass — so behaviour
  is deterministic and testable by stubbing the gate.
- **Caps per tier** (configurable): keep only the top-N by composite in `must_know`
  (default ~5) and `worth_tracking` (default ~10); demote the overflow to `deep_read`. The
  goal is a handful per top tier, not dozens.
- Apply the §1 single-source gate here too.
Persist updated tiers + `tier_reason` (explaining promotion/curation/merge).

The pass must be **idempotent** (re-running it on an already-curated set is stable) and must
not touch the brief or article feed.

### 3. Config + ops + web

- New `CLUSTERER_*` settings: diversity saturation `N`, `MIN_SOURCES_FOR_MUST_KNOW`, tier
  caps (`MUST_KNOW_MAX`, `WORTH_TRACKING_MAX`), merge similarity floor, `MAX_MERGE_CHECKS`,
  and a relevance-gate toggle/threshold. Document in `.env.example` + CLAUDE.md.
- The existing admin `clusters recluster` and the Threads "re-cluster now" trigger should
  also run the consolidation pass (so quality can be re-evaluated on demand).
- **Threads view shows only recently-active threads.** `list_threads` / the `/threads` route
  filter to threads whose `last_updated` is within the last **7 days**
  (`CLUSTERER_THREAD_VIEW_MAX_AGE_DAYS`, default 7); anything staler is hidden. No UI filter
  or opt-in to see older threads. (Filter by `last_updated` age directly, not by the status
  label, so a stale `active` label can't leak old threads in.)
- **Retention prune.** The consolidation pass **deletes** threads whose `last_updated` is
  older than **30 days** (`CLUSTERER_THREAD_RETENTION_DAYS`, default 30), cascade-removing
  their memberships — mirroring brief retention. (The pruned articles themselves are
  untouched; only the thread grouping is removed.)
- Threads view: ensure curated tiers read as a short list and show a thread's merge/curation
  reason. (No major UI rework — the view already groups by tier.)

### 4. Generic / section-title seeding guard (`aggregator-clusterer`)

Don't let non-story feed entries seed threads. Section/index titles like "Top Stories",
"Home", "Homepage", "Latest", "News" etc. (a configurable list + simple heuristic) should be
classified `irrelevant_or_low_value` (or skipped from thread creation) rather than becoming a
catch-all thread. Observed live: a single-source "Top Stories" entry absorbed 10 unrelated
articles and was promoted to `must_know`.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-clusterer/src/aggregator_clusterer/scoring.py` | Fix diversity, decouple relevance, single-source tier gate |
| `packages/aggregator-clusterer/src/aggregator_clusterer/consolidate.py` | New: merge-near-duplicates + curation pass |
| `packages/aggregator-clusterer/src/aggregator_clusterer/worker.py` | Invoke consolidation after a full cycle / on recluster |
| `packages/aggregator-clusterer/src/aggregator_clusterer/config.py` | New tiering/curation/merge settings |
| `packages/aggregator-common/src/aggregator_common/management.py` | New `merge_threads` helper |
| `packages/aggregator-common/src/aggregator_common/queries.py` | `list_threads`: filter to `last_updated` within view-age window |
| `packages/aggregator-web/src/aggregator_web/app.py` | `/threads` route applies the recency filter |
| `packages/aggregator-clusterer/src/aggregator_clusterer/dedup.py` or `classification.py` | Generic section-title seeding guard |
| `packages/aggregator-clusterer/tests/` | Tests: diversity fix, single-source gate, merge, curation caps, idempotency, relevance gate |
| `packages/aggregator-common/tests/` | `merge_threads` test (membership reassignment, source/known-facts union, absorbed thread removed) |
| `packages/aggregator-web/...` | Minor: tier_reason/merge surfacing if needed |
| `.env.example`, `CLAUDE.md`, `deploy/README.md` | Document new settings + the consolidation pass |

## Acceptance Criteria

- A single-source thread scores **low** (not 1.0) on diversity and **cannot** be tiered
  `must_know`; reaching `worth_tracking` requires meeting the configured importance bar.
- The relevance dimension is no longer identical to importance; off-interest threads (e.g.
  a tabloid item) are gated to `low_noise` by the curation relevance gate even with a
  moderate importance score.
- A consolidation pass runs after a full clustering cycle (and on recluster): it merges
  confirmed same-story thread pairs (memberships reassigned, sources/known-facts unioned,
  absorbed thread deleted, `deltas` records the merge) and curates tiers so `must_know` /
  `worth_tracking` are capped to a small configurable N, overflow demoted.
- `merge_threads` is covered by tests; the consolidation pass is **idempotent** (a second
  run makes no further changes) — covered by a test.
- The Threads view returns only threads with `last_updated` within 7 days; a thread updated
  8+ days ago does not appear (tested via the route/`list_threads`).
- The consolidation pass deletes threads with `last_updated` older than 30 days and their
  memberships (tested); the underlying articles remain.
- A generic section-title entry (e.g. "Top Stories") does not seed/become a promoted thread
  (tested: classified low-value / not created).
- The brief and article feed are unchanged.
- Focused tests pass per touched package; full gate green.

## Pending Decisions

- Resolved: this spec is **tiering + consolidation/curation, no embeddings**. Embeddings for
  candidate/merge recall are a **separate companion spec** (pgvector + embedding model);
  the merge step here is designed to accept an embedding similarity signal later.
- Curation mechanism: start with **deterministic gates + caps + an LLM relevance/merge
  judgement**, rather than a full LLM "pick the best N" selection, to keep cost/latency
  bounded. Revisit if the curated set still isn't tight enough.
- Tunable defaults (set sensible values now, configurable): diversity saturation N, tier
  caps, min-sources-for-must_know, merge similarity floor, max merge checks.
- Trigger granularity: run consolidation when a cycle fully drains unassigned articles;
  confirm this is frequent enough vs. running it every cycle (cost) — default to
  full-drain + on-demand recluster.
- Resolved (retention/lifecycle): Threads view shows only threads updated within **7 days**
  (no UI opt-in for older); consolidation **prunes/deletes** threads not updated in **30
  days**. Add a generic section-title seeding guard.
