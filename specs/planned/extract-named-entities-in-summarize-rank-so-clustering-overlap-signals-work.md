---
name: Extract named entities in summarize-rank so clustering overlap signals work
id: spec-499532a1
description: Rank prompt v3 extracts named entities (shadow-evaluated per ADR-60ce8b01) so clusterer entity overlap works; normalised matching; coverage health check
dependencies: null
priority: high
complexity: null
status: planned
tags:
- llm
- summarize-rank
- clusterer
- entities
scope:
  in: null
  out: null
feature_root_id: B-6f6ab0c5
---
# Extract named entities in summarize-rank so clustering overlap signals work

## Objective

Clustering was designed around shared **entities**: the people, organisations, places, products and events an article is about. Entities are one of three overlap signals used to:
- pick candidate threads for each new article (`candidates.py`);
- pick thread pairs worth an LLM merge check (`consolidate.find_merge_candidates`).

The classify prompt also shows them to the model (`classification.py:245-258`).

Nothing has ever populated `articles.entities`. On prod (2026-11-27), **0 of 13,234 articles** have entities, ever. The original clustering spec assumed summarize-rank would extract them, but the rank prompt only asks for topics.

This spec adds entity extraction to summarize-rank as a new rank prompt variant, `v3`. Per ADR-60ce8b01, the variant is evaluated in shadow before cutover, and then the overlap signals start working as designed.

## Problems to Fix

1. **Merge candidates almost never reach the LLM.**
   - The pair score is `0.40×entity + 0.30×topic + 0.30×FTS` (`consolidate.py:44-46`), so with entities always empty it can't exceed 0.60.
   - Among the 156 active threads updated in the 24 h before 2026-11-27 (12,090 pairs), **no pair** reached the 0.35 floor; the maximum was 0.332.
   - Production made 1–25 merge calls a day. Most duplicate threads were never compared.
   - The stopgap since 2026-11-27 is `CLUSTERER_MERGE_SIMILARITY_FLOOR=0.15` in the Pi `.env`.
2. **Classify candidate selection loses its entity signal.** Classify candidate scoring gives entity overlap a 0.30 weight (`candidates.py` `_ENTITY_W`), and that term is always zero. Every classify prompt says `Entities: (none)`. `CLUSTERER_ENTITY_OVERLAP_THRESHOLD` exists in config but nothing reads it; removing or using it is out of scope.
3. **Topics can't stand in for entities.** They are free-text phrases ("Qatar Grand Prix practice", "McLaren car development"), so an exact phrase rarely repeats across articles. The maximum topic Jaccard between recent threads was 0.43.
4. **Nothing detected the gap.** The metrics review never checked whether these inputs are populated, and the clusterer tests use made-up entities.

## Changes

### 1. Rank prompt `v3` (summarize-rank)

Add a registered variant `v3`, alongside `v1` and `v2`, in `prompt.py`:
- **Prompt text:** `v3` is `v2`'s text, profiled and neutral, with one step added after topic extraction (step 2). The steps after it are renumbered: scoring becomes 4, reason becomes 5 (the calibration block stays immediately before it), and the category step becomes 6. `_build_category_instruction` takes the step number as a parameter (5 for `v1`/`v2`, 6 for `v3`). The new step is:

  ```
  3. List the named entities the article is about, up to 8: specific people, organisations, companies,
     places, products, and named events (e.g. "Jensen Huang", "Nvidia", "Las Vegas Grand Prix", "iPhone 17").
     Use each entity's common full name, without titles or legal suffixes ("Nvidia", not "NVIDIA Corp.";
     "Keir Starmer", not "PM Starmer"). Include only entities central to the article, not passing mentions.
     Use an empty list if there are none.
  ```

  The scoring instructions and calibration text are byte-identical to `v2`. A test asserts that the `v3` system prompt equals `v2` with only this step inserted and the step numbers changed. It runs for profiled and neutral prompts, each with and without enabled categories.
- **`PROMPT_VERSION_V3`:** add `PROMPT_VERSION_V3 = "1.5.0"` in `schema.py`. `get_prompt_version` returns it for `v3`.
- **Variant registries:** add `"v3"` to `PROMPT_VARIANTS["summarize_rank.rank"]` in `experiment.py` and to the summarize-rank config validator.

### 2. Schema and persistence

- **Shared schema (required by the shadow framework).** The rank call sends `response_format=RankResult`, which litellm turns into a strict JSON schema in which every property is required. A prompt-variant shadow arm inherits control's `response_format`: `build_arm_kwargs` swaps only `messages`, and `response_format` can't be overridden in arm `params`. So `RankResult` stays one model shared by all variants. As a result, **after this deploys, production `v2` calls also return an `entities` array.** It isn't persisted under `v2`, but it is an extra output item that could shift `v2` scores slightly. §5 starts with a baseline check for exactly this.
- **Schema:** `RankResult` gains `entities: list[str] = []`. It defaults to empty, so `v1`/`v2` responses still validate. A `model_validator` strips whitespace, drops empty strings, removes case-insensitive duplicates (keeping the first spelling), and truncates to 8.
- **Saving:** `rank.py` persists `article.entities = result.entities` only when the prompt variant in use is `v3`. With `v1`/`v2` it leaves `entities` untouched (NULL), so cutover is a pure config switch.
- **Shadow output:** shadow arms already store the parsed output in `llm_shadow_results.output`. With `prompt: "v3"` that output includes `entities`, which the evaluation in §5 relies on. No change is needed.

### 3. Entity comparison in the clusterer

- Add `normalize_entity(s: str) -> str` to `aggregator_common`, for example in a new `aggregator_common/text_norm.py`. It applies, in order:
  - Unicode NFKC;
  - casefold;
  - whitespace collapse;
  - stripping leading and trailing characters whose `unicodedata.category` starts with `P` (this covers quotes) or that are whitespace;
  - removing a leading `"the "`.

  An entity that normalises to `""` is dropped.
- In `candidates.py` and `consolidate.py`, build entity sets through `normalize_entity`. Topics keep their current `_to_set` behaviour, so topic matching doesn't change.
- **Merge scoring:** `find_merge_candidates` builds each thread's entity set from the **union of its 3 most recent member articles**, not only the latest one. "Most recent" means `ROW_NUMBER() OVER (PARTITION BY thread_id ORDER BY assigned_at DESC, article_id DESC) <= 3`, which fixes the current `max(assigned_at)` join returning several rows on ties. Topics keep using the latest article only, as today. Recent members are a more stable signal than one article; candidate selection for classify already uses the union of members.

### 4. Health check for overlap inputs (metrics-review)

Add a check to `.claude/skills/metrics-review/SKILL.md`, in §3 "standard review" and §4 "what healthy looks like". It reports the share of `ready` articles with `summarized_at > now() - interval '24 hours'` that have non-empty `entities`, and the share with non-empty `topics`. Healthy is at least 95% for topics. For entities it's at least 85%, but only once `SUMMARIZE_RANK_PROMPT_VARIANT=v3` is live; before that, entities are expected to be empty and the check is informational. Include the SQL.

### 5. Shadow evaluation (before cutover; criteria fixed here per ADR-60ce8b01)

Experiment `rank-entities-v3-2026-12`, run for at least 5 days. Operator config, documented in `.env.example`:

```json
{"name":"rank-entities-v3-2026-12","enabled":true,"until":"<experiment start + 14 days, ISO-8601 UTC>","max_daily_cost_usd":1.0,
 "operations":{"summarize_rank.rank":{"sample_rate":1.0,"arms":[
   {"name":"luna-low-v3","model":"gpt-6-luna","prompt":"v3",
    "params":{"reasoning_effort":"low","max_tokens":null,"temperature":null,"max_completion_tokens":2000}}]}}}
```

The control here is production after the gpt-4.1 cutover: gpt-6-luna, `v2`, `low`. The arm differs from control only in the prompt. Under spec-13660fe1 the control kwargs already carry `max_completion_tokens` and `reasoning_effort`. Arm params that set `max_tokens: null` and `temperature: null` are harmless no-ops.

**Step 0 — baseline check for the shared schema (before the experiment).** For 3 days after deploy, compare production `v2` scores with the 3 days before it: the mean `importance_score`, the share at or above 66, and the share at exactly 70 or 75, from `articles` by `summarized_at`. The shift should be within ±2 points of the mean and ±3 percentage points of share at or above 66. A larger shift is reported to the user before going on. It means the schema change itself moved scores, and the experiment's control has already changed.

**Cutover criteria.** Run the report with matching thresholds on both sides (the control side reads `WEB_IMPORTANT_THRESHOLD` from the environment that runs the report, which is 70 by default on a workstation):

```
WEB_IMPORTANT_THRESHOLD=66 aggregator-admin llm-experiment-report --experiment rank-entities-v3-2026-12 --arm-threshold 66 --json
```

The report's own built-in verdict uses looser thresholds and is **not** this spec's gate. The criteria are:
1. **Reliability:** at least 99% of arm calls `ok` (computed as `status.ok / n` for the arm in the JSON), and `parse_failure_rate` at most 0.5%.
2. **Scores unchanged:** `mae` at most 4, `flip_rate` at most 5%, and Spearman rho at least 0.90. The arm and control differ only in the prompt, since both use the same schema.
3. **Coverage:** at least 85% of arm outputs have 1 or more entities, and the median count is between 2 and 6:
   ```sql
   select count(*) filter (where jsonb_array_length(coalesce(output->'entities','[]'))>0)::float/count(*) as coverage,
          percentile_cont(0.5) within group (order by jsonb_array_length(coalesce(output->'entities','[]'))) as median_n
   from llm_shadow_results
   where experiment='rank-entities-v3-2026-12' and arm='luna-low-v3' and status='ok';
   ```
4. **Quality:** a manual review.
   - Sample 30 random arm outputs (`select ref_id, output->'entities' from llm_shadow_results where experiment='rank-entities-v3-2026-12' and arm='luna-low-v3' and status='ok' order by random() limit 30`, joined to `articles` for the title). At most 2 may contain a clearly wrong or non-entity item, such as a topic phrase or an adjective.
   - Consistency: list threads with 3 or more shadowed members by joining `ref_id::bigint` to `thread_memberships.article_id`. For 5 of them, the same entity must be spelled consistently across members.
5. **Clustering effect (informational, not a gate).** `scripts/entity_merge_effect.py`, read-only through `DATABASE_URL`:
   - for active threads updated in the last 48 h, takes entities from the arm outputs of each thread's 3 most recent members;
   - computes the merge pair score with `consolidate`'s weights and `normalize_entity`, the existing topics and the same `ts_rank` FTS;
   - prints how many pairs reach 0.15, 0.25 and 0.35.

   It sets the post-cutover floor (§6).

Record all five results, plus the step 0 baseline, in a "Shadow results" section appended to this spec before cutover. The go/no-go is the user's call on that evidence (ADR-60ce8b01).

### 6. Cutover (config only, after the criteria pass)

1. In the Pi `.env`, set `SUMMARIZE_RANK_PROMPT_VARIANT=v3` and remove `LLM_EXPERIMENT`.
2. Restart, then run `aggregator-admin ops llm-cutover-cleanup --yes`. This empties the shadow rows and clears `thread_merge_verdicts`, since candidate sets change.
3. Merge floor:
   - Keep the stopgap of `0.15` for 7 days, while entity coverage of thread members builds up.
   - Then set `CLUSTERER_MERGE_SIMILARITY_FLOOR` from the §5.5 data and a 7-day check of merge calls a day. The aim is roughly 20–60 merge calls a day, as a guide rather than a gate.
   - Record the chosen value in this spec.
4. No backfill. Existing articles keep `entities` NULL. Threads pick up entities as new articles arrive, and merge scoring uses each thread's 3 most recent members.

## Requirements

### ADDED: article-ranking / Named-entity extraction (rank prompt v3)
When `SUMMARIZE_RANK_PROMPT_VARIANT=v3`, summarize-rank SHALL extract up to 8 named entities per article and persist them to `articles.entities` as a JSON list of strings, with duplicates (case-insensitive) removed. With `v1` or `v2`, summarize-rank MUST NOT write `articles.entities`.

#### Scenario: v3 persists entities
- **WHEN** an article is ranked with variant `v3`, and the model returns entities `["Nvidia", "nvidia", "Jensen Huang", " "]`
- **THEN** `articles.entities` is `["Nvidia", "Jensen Huang"]`

#### Scenario: v2 leaves entities untouched
- **WHEN** an article is ranked with variant `v2`
- **THEN** `articles.entities` stays NULL

#### Scenario: v3 scoring text unchanged
- **WHEN** the `v3` and `v2` system prompts are compared for both profiled and neutral modes
- **THEN** they differ only by the inserted entity step and the renumbered later steps

### ADDED: clustering-overlap / Normalised entity overlap
The clusterer SHALL compare entities after `normalize_entity`, which applies NFKC, casefold, whitespace collapse and surrounding-punctuation removal, and drops a leading "the ". Merge candidate scoring SHALL use the union of entities from each thread's 3 most recent member articles.

#### Scenario: Case and spacing differences still overlap
- **WHEN** thread A's latest members have entity `"NVIDIA"` and thread B's have `" Nvidia "`
- **THEN** their entity overlap counts the shared entity

#### Scenario: Empty after normalisation is dropped
- **WHEN** an entity is `"  ''  "` or `"—"`
- **THEN** `normalize_entity` yields `""` and it is not added to the entity set

#### Scenario: Merge scoring uses recent members
- **WHEN** a thread's latest article has no entities, but one of its previous two members has `["Jensen Huang"]`
- **THEN** that thread's entity set for merge scoring contains `"jensen huang"`

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-summarize-rank/src/aggregator_summarize_rank/prompt.py` | `v3` prompts (profiled + neutral); `build_messages` / `get_prompt_version` handle `v3` |
| `packages/aggregator-summarize-rank/src/aggregator_summarize_rank/schema.py` | `PROMPT_VERSION_V3`; `RankResult.entities` + validator |
| `packages/aggregator-summarize-rank/src/aggregator_summarize_rank/rank.py` | Persist entities only under `v3` |
| `packages/aggregator-summarize-rank/src/aggregator_summarize_rank/config.py` | Accept `v3` |
| `packages/aggregator-common/src/aggregator_common/experiment.py` | `"v3"` in `PROMPT_VARIANTS["summarize_rank.rank"]` |
| `packages/aggregator-common/src/aggregator_common/text_norm.py` | New: `normalize_entity` |
| `packages/aggregator-clusterer/src/aggregator_clusterer/candidates.py` | Entities via `normalize_entity` |
| `packages/aggregator-clusterer/src/aggregator_clusterer/consolidate.py` | Entities via `normalize_entity`; union of 3 most recent members |
| tests in each touched package | Per Requirements and Acceptance Criteria |
| `.claude/skills/metrics-review/SKILL.md` | Entity/topic coverage check + healthy thresholds |
| `scripts/entity_merge_effect.py` | New, read-only: §5.5 pair-score simulation |
| `packages/aggregator-common/src/aggregator_common/models.py` | `Article.entities` annotation `Optional[list]` (type hint only, no migration) |
| `CLAUDE.md`, `.env.example` | `v3` rank variant, entity extraction, experiment config example |

## Acceptance Criteria

1. With `SUMMARIZE_RANK_PROMPT_VARIANT` unset or `v2`, the rank kwargs are equal as dicts to before and the persisted columns are unchanged (`entities` stays NULL). The JSON schema sent to the API now includes `entities`, as §2 intends.
2. `v3` is accepted by the summarize-rank config and by `PROMPT_VARIANTS`, so an `LLM_EXPERIMENT` arm with `"prompt": "v3"` validates. An unknown variant is still rejected.
3. A `RankResult` without `entities` (a `v1`/`v2` response) validates with `entities == []`.
4. Every Requirements scenario has a test.
5. The metrics-review skill documents the coverage SQL and thresholds.
6. `bash scripts/run-tests.sh` passes, with no new warnings.

## Pending Decisions

- **Backfill (decided: no).** Re-ranking old articles would cost LLM calls and could change their scores. Threads pick up entities within days anyway.
- **Merge floor after cutover:** to be set from §5.5 and §6.3 data. The stopgap is 0.15, set 2026-11-27.
- **Classify behaviour change.** Once entities flow, classify candidate sets and the classify prompt input change, while the classify prompt itself doesn't. The cutover should include a check of classify's attach-vs-new-thread rate in the first days after it, against the week before.
