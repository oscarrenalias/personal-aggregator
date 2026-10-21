---
name: "LLM shadow evaluation C: experiment report and llm-stats"
id: spec-e8ca2c7d
description: "llm-experiment-report admin command (metrics, verdicts, JSONL samples) and shadow-aware llm-stats; depends on spec-5f4aeb9f"
dependencies: null
priority: high
complexity: null
status: planned
tags:
- llm
- migration
- experiment
- admin
scope:
  in: null
  out: null
feature_root_id: B-7341e1e9
---
# LLM shadow evaluation C: experiment report and llm-stats

## Objective

Add an admin command that turns the shadow experiment's data (`llm_shadow_results` +
`llm_calls`, from specs A `spec-5f4aeb9f` and B `spec-9dda2dee`) into machine-readable
metrics, a per-arm `pass`/`fail`/`review` verdict, and side-by-side sample dumps. An agent
uses these to decide each operation's `gpt-6-luna` cutover. Also keep production cost
readable in `llm-stats` now that shadow calls share `llm_calls`.

**Depends on spec A** (schema). It doesn't need to be deployed before the experiment
starts, but must be merged by **2026-11-21**, when data collection ends.

## Command

```
uv run aggregator-admin llm-experiment-report --experiment <name>
    [--since <ISO date>] [--until <ISO date>] [--json] [--samples <dir>]
```

- `--json` prints the full metrics object (schema below) to stdout. Without it, print a
  compact Rich table per operation (arm × headline metrics × verdict).
- `--samples <dir>` writes one JSONL file per operation (`<service>.<operation>.jsonl`).
  Each line is one item: `{"ref_id", "prompt_hash", "control": {...}, "arms": {"<arm>": {...}}, "disagreements": [...]}`,
  where each side has `status`, `output` and `raw_output`. Contents:
  - up to **200 disagreeing items**, ordered by the control row's `created_at` desc;
  - plus **50 agreeing items**, drawn with `random.Random(SAMPLE_SEED)` (constant
    `SAMPLE_SEED = 20261021`) from the agreeing pool sorted by `ref_id`, so runs over the
    same data give identical files;
  - for operations with no disagreement rule (see below), the random-sample sizes listed
    there instead.
- Read-only. Runs through the existing admin DB connection, so the metrics-review tunnel
  works unchanged.
- No new dependencies (admin depends only on `aggregator-common`, `typer`, `rich`).
  Spearman, ROC AUC and Jaccard are implemented directly, rank-based with averaged ties.

### Config inputs

The report runs on the workstation through the tunnel, so it can't see the Pi's `.env`.
These values come from `os.environ` with the documented defaults, can be overridden by
CLI options, and are echoed under `"config"` in the JSON:

| Name | Default |
|---|---|
| `WEB_IMPORTANT_THRESHOLD` | 70 |
| `WEB_AUTO_READ_SECONDS` | 5 |
| `BRIEF_TOOL_MAX_CALLS` | 12 |
| `CLUSTERER_MAX_KNOWN_FACTS` | 40 |
| `CLUSTERER_KNOWN_FACTS_KEEP_RECENT` | 20 |
| `CLUSTERER_FACTS_CONDENSED_TARGET_RATIO` | 0.6 |

Don't import service packages.

### Pairing and joins

- An **item** is `(experiment, service, operation, ref_id)`. A **pair** for an arm is an
  item with both a `control` row and that arm's row in `llm_shadow_results`. Unpaired
  rows are excluded everywhere and counted under `unpaired`.
- Content metrics use pairs where both sides are `status='ok'`. Reliability metrics use
  all pairs.
- **`llm_calls` per item and side** = rows matching
  `(experiment, arm, service, operation, ref_id)` (spec A's index). `request_ids` is
  diagnostics only. Items where no `llm_calls` row matches are excluded from the
  cost/latency/token means and counted as `no_telemetry`.

## Disagreement definitions (used for `--samples` and agreement rates)

| Operation | An arm *disagrees* with control when |
|---|---|
| `summarize_rank.rank` | the importance scores fall on different sides of `WEB_IMPORTANT_THRESHOLD` (`score >= threshold` = important), **or** \|Δ importance_score\| ≥ 15, **or** the category sets differ (case-insensitive) |
| `clusterer.classify` | the assignment differs: a different `label`, or an *attaching* label with a different `thread_id`. Attaching labels: `same_thread_new_fact`, `same_thread_new_angle`, `same_thread_duplicate`, `same_thread_background_only`, `correction_or_clarification`. For `new_thread`, `related_new_thread` and `irrelevant_or_low_value`, only the label is compared |
| `clusterer.merge` | `same_story` differs |
| `clusterer.consolidate_facts` | no rule; `--samples` holds 100 random pairs (same seeded method) |
| `brief.generate` | no rule; `--samples` holds all pairs |

Status mismatches (one side `ok`, the other not) always count as disagreements.

## Metrics (per operation × arm, against control)

**Reliability and cost** (every operation, for the arm and for control):

- n items, n per status
- error rate (`api_error`+`timeout`), parse-failure rate, refusal rate
- `finish_reason='length'` rate: the share of items with any matching `llm_calls` row
  with `finish_reason='length'`
- latency: **per item** = sum of `latency_ms` over the item's calls, reported as p50/p95
  over items; also per call p50/p95
- mean prompt / completion / reasoning tokens per item
- mean cost per item (sum of the item's `cost_usd`)
- **projected monthly cost at 100% traffic** = mean cost per item × items per day × 30.
  Items per day = the count of distinct `(UTC date, ref_id)` among **all** `llm_calls`
  for that service/operation in `[since, until)`, sampled or not, ÷ the number of UTC days
  in `[since, until)`. For `brief.generate`, only `ref_id`s of `origin='auto'` briefs
  count, which comes to about 1/day. Control uses the same formula with its own
  mean cost per item, so the two are directly comparable.

**Content** (pairs where both sides are `ok`):

| Operation | Metrics |
|---|---|
| `summarize_rank.rank` | importance_score Spearman ρ, MAE, mean signed difference (arm − control); threshold flip rate; topics mean Jaccard (case-insensitive); categories exact-match rate and mean Jaccard; summary length ratio (arm/control chars, median). **Engagement**, over pairs whose control row `created_at` is ≥ 48 h before `--until` (or now): `auc_saved_arm` and `auc_saved_control`, each the ROC AUC of that side's importance_score for predicting `articles.is_saved` on the same set; likewise `auc_read_arm`/`auc_read_control` for `is_read`; plus `n` and positives count |
| `clusterer.classify` | label agreement; assignment agreement; label confusion matrix; mean control/arm confidence when agreeing vs disagreeing; mean `len(new_facts)` difference; `thread_title` present rate on `new_thread`/`related_new_thread` |
| `clusterer.merge` | agreement; 2×2 confusion; arm merge rate vs control merge rate; `arm_only_merges` = pairs the arm merges and control doesn't, as a share of pairs |
| `clusterer.consolidate_facts` | target = `max(1, int(MAX_KNOWN_FACTS × TARGET_RATIO) − KEEP_RECENT)` (matches `worker.py:37-38`); mean output bullet count; over-target rate (`count > target`); mean bullet length (chars) |
| `brief.generate` | `ok` rate (spec B only stores schema-valid payloads as `ok`); mean `output.turns`; `forced_submit` rate and `corrective_turn` rate (from `output`, written by spec B); mean topic count; `linked_ref_valid_share` = share of `topics[].references[]` with non-null `article_id` that exist in `articles`; `unlinked_ref_share` = share with null `article_id` |

Engagement metrics exclude pairs whose article no longer exists in `articles`; the
janitor may have deleted it. The excluded count is reported as `n_article_missing`.
`linked_ref_valid_share` is evaluated at report time, which the output notes. Spec A's
deploy notes raise `JANITOR_ARTICLE_RETENTION_DAYS` to 35 for the window to keep this
small.

The engagement block includes two caveat fields:
`"exposure_bias": "ranking shown to the user came from control scores"`, and, when
`WEB_AUTO_READ_SECONDS > 0`, `"auto_read_inflation": "is_read is inflated by auto-read; prefer is_saved"`.

## Verdicts

Applied per arm, in this order:

1. Fewer than 30 **pairs** in any status (5 for brief) → `insufficient_data`.
2. Any "all" (reliability) criterion fails → `fail`. A failing arm can't hide behind
   missing data.
3. Any operation-specific (content) criterion fails → `fail`. If fewer than 30 (5 for
   brief) both-`ok` pairs exist, content criteria are skipped, `insufficient_ok_pairs`
   is added to `failed_criteria`, and the verdict is `fail`.
4. The operation is marked review → `review`.
5. Otherwise → `pass`.

`failed_criteria` lists every criterion that failed.

| Operation | Criteria |
|---|---|
| all | (error + parse-failure rate) ≤ control's + 1 pp; length-truncation rate ≤ 1% |
| `summarize_rank.rank` | Spearman ρ ≥ 0.80; MAE ≤ 8; threshold flip rate ≤ 10%; `is_saved` AUC ≥ control's − 0.03 (skipped if fewer than 10 saved articles) |
| `clusterer.classify` | assignment agreement ≥ 85% |
| `clusterer.merge` | agreement ≥ 90%; `arm_only_merges` ≤ 5% |
| `clusterer.consolidate_facts`, `brief.generate` | `review` (decided from samples), unless an "all" criterion fails |

The criteria thresholds live in one constant dict in the module, so a later round can
tune them. Control is a reference, not ground truth; the verdict feeds the cutover
decision together with the sample review.

## JSON schema (top level)

```json
{"experiment": "...", "window": {"since": "...", "until": "..."}, "generated_at": "...",
 "config": {"WEB_IMPORTANT_THRESHOLD": 70, "...": "..."},
 "operations": {"summarize_rank.rank": {"control": {"reliability": {...}, "cost": {...}},
   "arms": {"luna-low": {"pairs": 0, "unpaired": 0, "no_telemetry": 0,
                        "reliability": {...}, "cost": {...}, "content": {...},
                        "verdict": "pass|fail|review|insufficient_data", "failed_criteria": [...]}}}}}
```

## `llm-stats` change

- `aggregator_common.queries.llm_stats` (`queries.py:928`) gets
  `include_shadow: bool = False`. The default excludes rows where
  `arm IS NOT NULL AND arm <> 'control'`, so production numbers stay as they are today.
- This default applies to every caller, including MCP's `status://llm` resource and
  `llm_stats` tool (`aggregator_mcp/server.py:674,689`). Agents see production-only cost
  during the experiment without any MCP code change.
- `aggregator-admin llm-stats` gains `--include-shadow`, which passes `True` and adds a
  per-`experiment`/`arm` breakdown table.
- `llm-stats` gets a new `--json` flag (it has none today), emitting the same data
  including the breakdown when `--include-shadow` is set.

## Files to Add/Modify

| File | Change |
|---|---|
| `packages/aggregator-admin/src/aggregator_admin/llm_experiment.py` | new: report |
| `packages/aggregator-admin/src/aggregator_admin/main.py` | register command; `llm-stats` flags |
| `packages/aggregator-admin/src/aggregator_admin/llm_stats.py` | `--include-shadow`, `--json`, breakdown |
| `packages/aggregator-common/src/aggregator_common/queries.py` | `llm_stats(include_shadow=False)` + breakdown query |
| `.claude/skills/metrics-review/SKILL.md` | section: running the report against prod through the tunnel; how to read the verdicts |
| `CLAUDE.md` | document the command |

## Implementation order (one developer bead each)

1. **`llm-stats`:** shared query `include_shadow`, admin `--include-shadow`/`--json`
   (AC 6).
2. **Loading + pairing + reliability/cost metrics**, with the join and projected cost
   (AC 1 for those metrics, AC 5, 7).
3. **Content metrics + stats helpers** (Spearman, AUC, Jaccard) for all operations
   (AC 1 for content). Depends on 2.
4. **Verdicts + disagreement rules + output** (Rich table, `--json`, `--samples`)
   (AC 2–4). Depends on 3.
5. **Docs:** metrics-review SKILL.md, CLAUDE.md.

## Acceptance Criteria

1. On a seeded testcontainers DB with known pairs, `--json` returns every metric listed
   above with values matching hand-computed expectations (including Spearman, MAE, flip
   rate, AUC, confusion matrices and projected cost).
2. Verdict logic: seeded fixtures produce `pass`, `fail` (with the failing criterion
   named in `failed_criteria`), `review` and `insufficient_data` as specified.
3. Disagreement rules: a fixture for each rule in the table (including status
   mismatches) is classified as disagreeing, and agreeing fixtures aren't.
4. `--samples` writes one JSONL file per operation present, capped at 200 disagreeing +
   50 agreeing items, and the agreeing sample is the same across runs.
5. Unpaired items (an arm row without a control row, or the reverse) are excluded from
   all content metrics.
6. `llm-stats` totals, and `queries.llm_stats` as called by MCP, on a fixture with shadow
   rows equal the totals without them. `--include-shadow` adds them with a per-arm
   breakdown. `--json` emits valid JSON in both modes.
7. The command is read-only. A test runs it inside a `SET TRANSACTION READ ONLY` session
   without error.

## Out of scope

- Any automated cutover action.
- Web/API/TUI views of results.
- LLM-as-judge quality scoring (the reviewing agent does qualitative review from
  `--samples`).
