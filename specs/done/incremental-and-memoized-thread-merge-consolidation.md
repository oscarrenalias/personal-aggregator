---
name: Incremental and memoized thread merge consolidation
id: spec-691341da
description: "Make clusterer merge-consolidation LLM cost proportional to new content: (A) scope merge candidates to threads changed since last consolidation, and (B) memoize negative 'don't merge' verdicts so unchanged pairs are never re-sent to the LLM. Eliminates the constant overnight merge floor (was 58% of LLM calls) without changing which merges happen. Additive thread_merge_verdicts table; explicit recluster forces a full sweep."
dependencies: null
priority: medium
complexity: null
status: done
tags:
- clusterer
- cost
- llm
- consolidation
- optimization
- observability
scope:
  in: null
  out: null
feature_root_id: null
---
# Incremental and memoized thread merge consolidation

## Objective

Make the clusterer's merge-consolidation LLM cost **proportional to new content** instead of a
constant per-pass floor. Today every consolidation pass re-evaluates the *entire* active-thread
corpus and spends up to `CLUSTERER_MAX_MERGE_CHECKS` LLM calls on the top-scoring pairs — the
same pairs each pass, since the corpus barely changes between passes. Two changes fix this:
(A) scope merge candidates to threads that actually changed since the last consolidation, and
(B) cache negative ("don't merge") verdicts so an unchanged pair is never re-sent to the LLM.

Observed motivation (Pi, gpt-4.1-mini, 2026-06-16/17): merge was **58% of all LLM calls
(351/607)** and held a flat ~10–30 calls/hour overnight even when only 1–3 articles arrived —
i.e. the LLM was repeatedly re-answering "should A and B merge?" for pairs where neither thread
changed and it had already said no. See `docs/mini-switch-results-2026-06-17.md`.

## Problems to Fix

- **Global, not incremental.** `find_merge_candidates` (consolidate.py) sweeps *all* active
  threads and scores every pair; a single newly-assigned article flips `cluster_state.dirty=true`
  (worker.py `any_article_assigned`) and triggers a full-corpus re-evaluation on the next pass.
- **No memory of past decisions.** A "not the same thread" verdict from `llm_merge_fn` is
  discarded; the next pass asks the LLM the identical question about the identical unchanged pair.
- Net effect: merge cost is a constant floor (≈ passes/hour × `MAX_MERGE_CHECKS`) decoupled from
  how much new content actually arrived — wasteful in tokens and against the OpenAI free-token
  allotment, and it scales poorly as the thread corpus grows.

## Changes

### A. Incremental candidate scoping (consolidate.py)

`find_merge_candidates(session, settings, *, changed_since=None)` only emits a pair when **at
least one** of the two threads changed since the consolidation boundary — i.e.
`thread.last_updated >= changed_since`. Pairs where *both* threads are stale cannot have a new
reason to merge, so they are skipped before any LLM call.

- The caller (`run_consolidation_pass` / `run_merge_pass`, invoked from worker.py) passes
  `changed_since = cluster_state.last_consolidated_at`.
- **Boundary cases:** `changed_since is None` (first ever pass) → treat all threads as changed
  (full sweep, current behaviour). An **explicit recluster** request must force a full sweep
  (`changed_since=None`) so the operator can always trigger complete re-evaluation.
- The composite similarity scoring (entity/topic Jaccard + FTS) and the `MERGE_SIMILARITY_FLOOR`
  ranking are unchanged; only the candidate set is narrowed. A "changed" thread is still scored
  against *all* other active threads (a thread that just changed may now merge with a previously
  stable one), so merge *quality* is preserved — we only drop stale×stale pairs.

### B. Memoized negative verdicts (new `thread_merge_verdicts` table)

Persist each negative `llm_merge_fn` result so an unchanged pair is not re-checked:

- New table `thread_merge_verdicts`: `keep_id` (fk threads, lower id), `absorb_id` (fk threads,
  higher id), `keep_last_updated` (timestamptz snapshot at decision time), `absorb_last_updated`
  (timestamptz snapshot), `decided_at` (timestamptz). PK `(keep_id, absorb_id)`. FKs
  `ON DELETE CASCADE` so verdicts vanish when either thread is deleted/merged away.
  Only **negative** verdicts are stored — a positive verdict merges the pair, so the absorbed
  thread ceases to exist and there is nothing to re-check.
- **Cache-key normalization (invariant):** every cache read and write normalizes the key to
  `keep_id = min(thread_a.id, thread_b.id)`, `absorb_id = max(...)`, **independent** of the
  keep/absorb direction `merge_threads` later uses. `find_merge_candidates` already returns
  `(lo, hi)` in this order; the cache must use the same ordering so a verdict written for a pair
  is always found again regardless of which side `run_merge_pass`/`merge_threads` treats as keep.
  A desynced key would silently defeat the cache, so this is a correctness requirement, not a
  style choice.
- In `run_merge_pass`, before calling `llm_merge_fn(keep, absorb)`: look up the cached verdict;
  **skip the LLM call** if a row exists AND `keep.last_updated <= keep_last_updated` AND
  `absorb.last_updated <= absorb_last_updated` (neither thread changed since the verdict). A
  skipped pair counts toward neither `llm_calls` nor `max_checks` (it's free), so the budget is
  spent on genuinely new comparisons.
- When `llm_merge_fn` returns **false**, upsert a verdict row with the current `last_updated` of
  both threads. When it returns **true** (merge), no verdict row is written (the pair collapses);
  the keep thread's `last_updated` advances on merge, which automatically invalidates any other
  cached verdicts involving it (their `<=` check now fails).
- **Explicit recluster** ignores the cache (and may clear it) so a forced pass re-asks everything.

### C. Retention — cascade-only (v1)

Verdict-row cleanup relies **solely on `ON DELETE CASCADE`**: when the janitor (or a merge)
deletes a thread, its verdict rows are removed automatically. No separate
`purge_orphaned_merge_verdicts` helper in v1 — the table only ever holds rows keyed by two live
threads, so it cannot accumulate orphans. (A future janitor sweep to age out *stale-but-live*
verdicts is possible but explicitly out of scope here; the cache self-invalidates on change, so
unbounded growth is not a concern at this corpus size.)

### D. Observability

`run_consolidation_pass` logs candidate count, LLM checks made, and **checks skipped via cache**
so the cost reduction is visible in logs; optionally surface skip counts where the clusterer
already reports consolidation stats.

### E. Config flags (safe rollout)

Both behaviours are gated behind base-`ClustererSettings` flags, **defaulting on**, so either can
be disabled without a code change for rollback/A-B comparison:

- `CLUSTERER_INCREMENTAL_MERGE` (default **true**) — when false, `find_merge_candidates` ignores
  `changed_since` and does the current global sweep.
- `CLUSTERER_MERGE_VERDICT_CACHE` (default **true**) — when false, `run_merge_pass` neither reads
  nor writes `thread_merge_verdicts` (every candidate is sent to the LLM as today).

With both false, behaviour is byte-for-byte the current global, un-memoized sweep.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-clusterer/.../consolidate.py` | `changed_since` scoping in `find_merge_candidates`; cache lookup/skip + negative-verdict upsert in `run_merge_pass`; log skipped count |
| `packages/aggregator-clusterer/.../worker.py` | pass `last_consolidated_at` as `changed_since`; force full sweep + cache-ignore on explicit recluster |
| `packages/aggregator-common/.../models.py` | `ThreadMergeVerdict` ORM model |
| `packages/aggregator-common/.../migrations/versions/*` | additive `thread_merge_verdicts` migration with `ON DELETE CASCADE` FKs (round-trip with explicit revision targets) |
| `packages/aggregator-clusterer/.../config.py` | `clusterer_incremental_merge`, `clusterer_merge_verdict_cache` flags (default true) |
| tests (clusterer + common) | scoping, cache hit/skip, invalidation on change/merge, recluster bypass, cache-key order-independence, flag-off restores global sweep, migration round-trip + cascade delete |

## Acceptance Criteria

- A consolidation pass triggered by N newly-changed threads issues merge LLM calls only for pairs
  involving a changed thread; stale×stale pairs produce **zero** LLM calls.
- A pair the LLM rejected is **not** re-sent on a later pass while neither thread's `last_updated`
  has advanced; it **is** re-sent once either thread changes (or on explicit recluster).
- Overnight / low-inflow periods drop merge LLM volume toward zero (cost tracks inflow), with no
  change to which merges actually happen vs. the current global sweep on a representative batch.
- Explicit recluster still performs a full re-evaluation (ignores both the `changed_since` filter
  and the verdict cache).
- The verdict cache key is **order-independent**: a verdict written for `(a, b)` is found when the
  pair is later presented as `(b, a)` (normalized to `keep_id=min, absorb_id=max`).
- Disabling `CLUSTERER_INCREMENTAL_MERGE` and `CLUSTERER_MERGE_VERDICT_CACHE` restores the current
  global, un-memoized sweep (clean rollback path).
- `thread_merge_verdicts` rows are removed when a referenced thread is deleted/merged
  (`ON DELETE CASCADE`); additive migration, round-trip clean.
- Existing clusterer tests pass; new tests cover scoping, cache skip/invalidate, recluster bypass.

## Resolved Decisions

- **Scope:** both A (incremental scoping) and B (verdict cache) ship in v1.
- **Retention:** cascade-only (no janitor purge in v1) — see Change C.
- **Cache key:** normalized to `keep_id=min(id), absorb_id=max(id)` on every read/write — see
  Change B invariant.
- **Positive-verdict TTL:** none needed (a merge removes the pair).
- **Config flags:** `CLUSTERER_INCREMENTAL_MERGE` + `CLUSTERER_MERGE_VERDICT_CACHE`, default on —
  see Change E.

## Pending Decisions

- None blocking. (Future, out of scope: aging out stale-but-live verdict rows via the janitor if
  the corpus ever grows enough that the cache table itself needs bounding.)
