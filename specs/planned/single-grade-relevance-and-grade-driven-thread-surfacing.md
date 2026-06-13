---
name: Single-grade relevance and grade-driven thread surfacing
id: spec-899062cd
description: Collapse three relevance judgments into one discriminating 5-level grade in summarize-rank; clusterer keeps LLM clustering but drops the composite tiering/relevance-gate/caps; threads surface by a deterministic grade + critical-mass rule; un-surfaced 1-article threads kept-but-hidden and pruned at 30 days.
dependencies: null
priority: high
complexity: null
status: planned
tags:
- clustering
- summarize-rank
- relevance
- simplification
scope:
  in: null
  out: null
feature_root_id: B-ab90b81a
---
# Single-grade relevance and grade-driven thread surfacing

Supersedes the tiering/curation approach from the just-merged clustering work. The clusterer
still **clusters** (LLM same-story grouping) — that is unchanged — but its **second ranking
layer is removed**. Background: `docs/AI_News_Aggregator_Story_Clustering.md`.

## Objective

Collapse three overlapping relevance judgments (summarize-rank `importance_score`, the
clusterer composite tier score, and the consolidation relevance-gate) into **one grade,
produced once** by summarize-rank. The clusterer keeps grouping articles into threads but
no longer re-ranks them; the Threads view shows only threads that **surface** by a
deterministic rule based on member grades + cluster shape. Un-surfaced (e.g. 1-article)
threads are kept in the DB (so later articles can still join them) but hidden, and pruned
after 30 days.

## Problems to Fix

- **Too many rankings.** Relevance is judged three times (summarize-rank, clusterer
  composite, consolidation gate). It should be graded once.
- **The single grade isn't discriminating.** summarize-rank's rubric scores "directly
  related to a stated interest" as 61–80, so — because feeds are curated to interests —
  71% of articles score ≥50 (avg 58; 222 in the 70–80 band). Topical match is being
  conflated with importance.
- **Threads sprawl.** Because tiering is driven by that inflated importance + freshness, the
  `worth_tracking` tier filled with ~294 threads — "worth tracking" became a synonym for
  "All." Capping it is treating the symptom.

## Changes

### 1. One discriminating grade — `summarize-rank`

Recalibrate the importance rubric to a 5-level, profile-anchored scale where **topical match
is the baseline, not a high score**, and high scores are reserved for genuine significance:

```
noise        (0-20):   off-interest / deprioritized / pure noise
on-topic     (21-45):  on a followed interest but ROUTINE — incremental updates, minor
                       releases, opinion, repetitive coverage. MOST on-topic news is here.
good-to-know (46-65):  a genuine, noteworthy development on a followed interest
important    (66-85):  a significant development the user would want to know about
must-know    (86-100): major, high-impact, central to top interests
```

Add explicit instruction: "Most articles, even on-topic, are routine and belong below 45.
Be selective — on a typical day only a handful are 'important' or 'must-know'." Keep the
0–100 `importance_score` column (so the article "Important" view and the band→label mapping
keep working; **no article schema change**). Bump `PROMPT_VERSION`. Define a shared band→label
helper (e.g. `aggregator_common` grade bands) used by both summarize-rank and the clusterer.

This is the **only** relevance ranking in the system.

### 2. Clusterer: keep clustering, REMOVE the second ranking, ADD grade-driven surfacing

- **Keep:** candidate retrieval + the LLM same-story classification (grouping), thread
  upsert, the merge pass (dedupe), aging/prune. The diversity/distinct-source computation
  stays (now feeds the surfacing rule, not a score).
- **Remove:** the composite scoring blend (`scoring.py` relevance/novelty/importance/
  diversity/confidence/time weights), the 4-tier thresholds, and the consolidation
  **relevance-gate** LLM call and the per-tier **caps**. (The grade + surfacing rule replace
  them; no per-thread relevance re-judgment, no caps as a mechanism.)
- **Add a deterministic `surfaced` determination** per thread, computed from member grades +
  cluster shape — no LLM, no composite. Let `top_grade = max(member importance_score)` and
  `distinct_sources`, `member_count` from the thread's members. Then:
  - `surfaced = (top_grade ≥ CLUSTERER_SURFACE_MIN_GRADE) OR (distinct_sources ≥ CLUSTERER_SURFACE_MIN_SOURCES) OR (member_count ≥ CLUSTERER_SURFACE_MIN_MEMBERS)`
  - Defaults: `MIN_GRADE = 66` (the "important" band floor), `MIN_SOURCES = 2`, `MIN_MEMBERS = 3`.
  - So a single must-know/important article surfaces as a 1-article thread; a burst of
    "good-to-know"/"on-topic" articles on one story surfaces via critical mass; a lone
    on-topic article does **not** surface (kept hidden in the feed/DB).
- Store the result so the view filters cheaply: add `threads.surfaced BOOLEAN NOT NULL
  DEFAULT false` and `threads.top_grade INTEGER NULL` (the single max-member-importance field
  used for ordering — not two separate fields). Recompute both whenever a thread is touched
  during clustering and in the consolidation pass. (Threads-table migration only — no article
  changes.) **Backfill:** the migration sets `surfaced=false`/`top_grade=null` for existing
  rows; the validation re-cluster (§5) recomputes them, so no thread is left undefined in
  practice — and an un-recomputed thread simply stays hidden (safe default).

### 3. Threads view — one list of surfaced threads

The `/threads` route + `list_threads` filter to **`surfaced = true` AND within the 7-day
window**, ordered by `top_grade` desc, then `last_updated` desc. One bucket (no tier sections).
Un-surfaced threads are never shown but remain in the DB; the 30-day retention prune removes
any thread (surfaced or not) not updated in 30 days.

### 4. Config + cleanup

- Add `CLUSTERER_SURFACE_MIN_SOURCES` (2), `CLUSTERER_SURFACE_MIN_MEMBERS` (3),
  `CLUSTERER_SURFACE_MIN_GRADE` (66).
- Remove/deprecate the now-unused tiering settings: `CLUSTERER_WEIGHT_*`,
  `CLUSTERER_TIER_*_THRESHOLD`, `CLUSTERER_MUST_KNOW_MAX`, `CLUSTERER_WORTH_TRACKING_MAX`,
  `CLUSTERER_MIN_SOURCES/MEMBERS_FOR_MUST_KNOW`, `CLUSTERER_RELEVANCE_GATE_ENABLED`. Update
  `.env.example` + CLAUDE.md.
- Keep the consolidation throttle (10-min floor) and merge/prune from the prior bead.

### 5. Validation (ops, after merge)

Re-rank the backlog (`articles rerank --all`, one-time ≈600 summarize-rank calls) so grades
spread under the new rubric, then re-cluster, and confirm the surfaced thread list is short
and on-target.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-summarize-rank/src/aggregator_summarize_rank/prompt.py` | Recalibrated 5-level importance rubric; bump PROMPT_VERSION |
| `packages/aggregator-common/src/aggregator_common/grades.py` (new) | Shared band→label helper (noise/on-topic/good-to-know/important/must-know) |
| `packages/aggregator-common/src/aggregator_common/models.py` | Add `threads.surfaced` (+ `top_grade`/max-importance) |
| `packages/aggregator-common/.../migrations/versions/*` | Migration: add surfaced/top_grade to threads |
| `packages/aggregator-clusterer/src/aggregator_clusterer/scoring.py` | Replace composite tiering with the deterministic surfaced computation |
| `packages/aggregator-clusterer/src/aggregator_clusterer/consolidate.py` | Remove relevance-gate + caps; recompute `surfaced`; keep merge/prune |
| `packages/aggregator-clusterer/src/aggregator_clusterer/config.py` | Add surface settings; drop tiering/weight/cap/gate settings |
| `packages/aggregator-common/src/aggregator_common/queries.py` | `list_threads` filters surfaced=true + recency; order by grade |
| `packages/aggregator-web/.../app.py` + templates | Threads view = one surfaced list (drop tier sections) |
| `.env.example`, `CLAUDE.md`, `deploy/README.md` | Update config + describe the single-grade + surfacing model |
| tests across the touched packages | Rubric band mapping, surfacing rule, view filter, migration up/down |

## Acceptance Criteria

- summarize-rank uses the 5-level rubric; on a re-ranked backlog the importance distribution
  is **bottom-heavy** — target **<30% of articles at ≥46** (vs the current 71% ≥50), with
  "important"/"must-know" (≥66) a small minority. (Objective post-re-rank sanity check; the
  automated tests assert the band→label mapping, not model output.)
- The clusterer still creates threads via the LLM same-story classifier; **no composite
  score, tier, relevance-gate call, or cap remains** in the clustering/consolidation path.
- A thread's `surfaced` flag follows the rule (≥1 important/must-know member OR
  ≥2 sources / ≥3 members); a single must-know article surfaces; a lone on-topic article does
  not. Covered by tests.
- The Threads view shows only surfaced threads within 7 days, one list ordered by grade;
  un-surfaced threads are absent from the view but present in the DB and pruned at 30 days.
- Migration applies/reverses cleanly; brief and article feed unchanged; full gate green.

## Pending Decisions

- Resolved (design): one grade (summarize-rank, 5-level) + clustering + deterministic
  grade/critical-mass surfacing; remove composite tiers, relevance gate, and caps. 1-article
  threads kept-but-hidden, pruned at 30 days. Critical mass = ≥3 articles OR ≥2 sources.
  One bucket. Grade as bands over `importance_score`.
- Open/defer: embeddings recall (oblique same-story misses) and topic memory (the "iOS 27"
  umbrella) remain separate future specs, unaffected by this simplification.
- To sanity-check after re-rank: whether `MIN_GRADE=66` / critical-mass defaults yield a
  satisfyingly short surfaced list; tune if needed (config only).
