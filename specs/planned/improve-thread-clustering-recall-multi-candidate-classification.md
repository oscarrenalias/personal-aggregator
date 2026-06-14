---
name: "Improve thread clustering recall: multi-candidate classification"
id: spec-fb32a002
description: "Raise thread-clustering recall so same-story articles stop fragmenting into separate single-article threads. Primary fix: show the LLM classifier the top-N candidate threads (not just #1) and let it pick the thread_id; supporting fix: score candidate overlap against the thread aggregate, not just the most-recent member. Diagnosed from Pi v0.1.26 (Anthropic Fable/Mythos splits). HOLD until MCP threads surface lands for data verification."
dependencies: null
priority: medium
complexity: null
status: planned
tags:
- clusterer
- clustering
- recall
- llm
scope:
  in: null
  out: null
feature_root_id: B-96f2fafe
---
# Improve thread clustering recall: multi-candidate classification

## Objective

Raise clustering **recall** so articles about the same (or closely developing) story land in
one thread instead of fragmenting into multiple single-article threads on near-identical
topics. Observed on the Pi (v0.1.26): a healthy multi-source "Anthropic Fable/Mythos" cluster
sits next to standalone single-article clusters covering very similar topics that should have
joined it.

## Problems to Fix

Root causes, found in the clusterer code:

1. **The classifier only ever sees the single best candidate thread.** `worker.py`
   classifies via `classify_article`, which takes `candidates[0]` and
   `_build_user_message` shows the LLM **only that one thread**
   (`classification.py:177`, `:121`). The model answers "does it belong to *this* thread?"
   — never "which of these?" If the correct thread isn't ranked #1 by the composite score,
   the article cannot attach to it and is labeled `new_thread`/`related_new_thread`, spawning
   a fresh single-article cluster. **This is the primary cause.**
2. **Candidate ranking compares against only the thread's most-recent member.**
   `candidates.py:126` computes entity/topic overlap against `thread_members[0]` (the latest
   article), not the thread as a whole. As a thread grows, its representative entity/topic set
   narrows to the last article, so a genuinely-related new article can score low and fail to
   rank #1 — feeding directly into problem 1.
3. **(Secondary) Thread→thread merge is conservative and rarely fires.** Once two same-topic
   single-article threads exist, consolidation merges them only if composite similarity ≥
   `clusterer_merge_similarity_floor` (0.35), within `clusterer_max_merge_checks` (20), while
   throttled (dirty + ≥10 min), **and** the LLM merge-decider — prompted to say true only when
   threads are "unambiguously about the same event" (`worker.py:46`) — agrees. "Related but
   distinct" never merges.

## Changes

### 1. Multi-candidate classification (primary)

In `classification.py`, change `classify_article` to present the **top N candidate threads**
(not just `candidates[0]`) to the LLM and let it return the chosen `thread_id` (one of the
presented ids) or `null` for a new thread:

- `_build_user_message`: render a numbered list of up to N candidate threads, each with its
  `thread_id`, `representative_title`, `rolling_summary`, and `known_facts` (cap summary/fact
  length to bound tokens).
- `_SYSTEM_PROMPT`: instruct the model to pick the `thread_id` of the best-matching listed
  thread when the article belongs to one, else `null`. Keep the existing label set and the
  `thread_id`-nulling rule for `new_thread`/`related_new_thread`.
- Validation: accept `thread_id` only if it is one of the presented candidate ids (else treat
  as new). Keep the fail-open `_error_result()` path.
- New config `clusterer_max_classifier_candidates` (`CLUSTERER_MAX_CLASSIFIER_CANDIDATES`,
  default **5**), clamped to the candidate list length. `get_candidates` already returns up to
  `clusterer_max_candidate_threads`; slice to the smaller of the two.

### 2. Score candidate overlap against the thread aggregate (supporting)

In `candidates.py`, compute `entity_overlap`/`topic_overlap` against the **union of all
member articles'** entities/topics for the thread (or a bounded recent window), not just
`thread_members[0]`. This makes a related new article more likely to surface the right thread
into the top-N. Keep the existing weights and FTS path. Bound cost by reusing the already
bulk-loaded `members_by_thread`.

### 3. (Optional, gated by verification) Loosen the merge

Only if step 1+2 prove insufficient against real data: soften the merge LLM wording from
"unambiguously the same event" to "the same story or a closely related ongoing development",
and/or lower `clusterer_merge_similarity_floor`. Left **out of the default scope** to avoid a
precision regression (over-merging distinct stories); revisit with evidence.

### 4. Docs

Document `CLUSTERER_MAX_CLASSIFIER_CANDIDATES` in `.env.example` and the CLAUDE.md config
table; update the clusterer architecture note to say the classifier now considers multiple
candidate threads.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-clusterer/src/aggregator_clusterer/classification.py` | Multi-candidate prompt + thread_id validation against presented ids |
| `packages/aggregator-clusterer/src/aggregator_clusterer/candidates.py` | Overlap vs thread aggregate (union of members), not most-recent member |
| `packages/aggregator-clusterer/src/aggregator_clusterer/config.py` | Add `clusterer_max_classifier_candidates` (default 5) |
| `packages/aggregator-clusterer/tests/` | Tests: top-N rendering, picks a non-#1 candidate, invalid thread_id → new, aggregate-overlap ranking |
| `.env.example`, `CLAUDE.md` | Document the new setting + classifier behavior |

## Acceptance Criteria

- The classifier receives up to `clusterer_max_classifier_candidates` threads and can attach
  an article to a candidate that was **not** ranked #1 (covered by a test where the correct
  thread is candidate #2/#3).
- A returned `thread_id` not among the presented candidates is rejected and treated as a new
  thread (no attaching to arbitrary ids).
- Candidate entity/topic overlap reflects the thread aggregate, not just its latest member
  (unit test: a thread whose latest member drifted still ranks high for a same-story article).
- Fail-open behavior preserved (LLM/JSON errors → `new_thread`, no crash).
- No schema change. Focused `aggregator-clusterer` tests pass; full gate green.

## Pending Decisions

- **Verify before building**: confirm via the MCP `get_thread` surface (or DB) that the Pi
  splits are `related_new_thread` / failed-candidate-ranking cases (problem 1/2) rather than
  something else, and pick `N` from what we see.
- **N default**: start at 5; trade-off is prompt tokens vs recall. Revisit after observation.
- **Merge loosening (change 3)**: deliberately out of default scope; only pursue with
  evidence that 1+2 are insufficient, to protect precision.
- **`related_new_thread`**: keep the label (it's correct for genuinely distinct stories); the
  fix is about not *misrouting* same-story articles, not removing the spin-off option.
