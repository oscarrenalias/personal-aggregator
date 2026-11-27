---
name: "gpt-4.1 cutover: switch production LLM calls to the shadow-validated setups"
id: spec-13660fe1
description: Per-operation model/reasoning settings so production runs the shadow-validated gpt-6-luna/terra setups; cutover clean-up command; eval export from llm_calls
dependencies: null
priority: high
complexity: null
status: planned
tags:
- llm
- cutover
- clusterer
- summarize-rank
- brief
- evals
scope:
  in: null
  out: null
feature_root_id: B-e0dd0e2f
---
# gpt-4.1 cutover: switch production LLM calls to the shadow-validated setups

## Objective

OpenAI retires gpt-4.1 and gpt-4.1-mini on 2027-01-14. Production still runs every summarize-rank, clusterer and brief call on gpt-4.1-mini (Pi `.env` overrides). Three shadow experiments (`gpt41-retirement-2026-10`, `luna-prompts-v2-2026-11`, `luna-prompts-v3-2026-11`) and the offline merge eval (ADR-f8cc0455) have settled the replacement setup for each operation. This spec makes production able to run exactly those setups, then performs the cutover clean-up required by ADR-9687cb01 (as amended — see Pending Decisions).

The cutover itself is a config change on the Pi. This spec builds what the config needs, and the clean-up tooling, and documents the exact `.env` values.

### Target setup (decided)

These are the arm configurations that ran in shadow. Production must reproduce them exactly — same model, prompt variant and parameters.

| Operation | Model | Prompt | `reasoning_effort` | Completion budget | Temperature | Shadow arm it reproduces |
|---|---|---|---|---|---|---|
| `summarize_rank.rank` | `gpt-6-luna` | `v2` | `low` | 2000 | removed | `luna-low-v2` (luna-prompts-v2-2026-11) |
| `clusterer.classify` | `gpt-6-luna` | `v3` | `none` | 512 | 0.0 (kept) | `luna-none-v3` (luna-prompts-v3-2026-11) |
| `clusterer.merge` | `gpt-6.1-terra` | `v4b` | `none` | 128 | 0.0 (kept) | `terra-none-v4b` (luna-prompts-v3-2026-11) + offline eval |
| `clusterer.consolidate_facts` | `gpt-6-luna` | `v1` (only one) | `low` | 2000 | removed | `luna-low` (gpt41-retirement-2026-10) |
| `brief.generate` | `openai/responses/gpt-6-luna` | `v2` | `low` | 8192 | removed | `luna-low-v2` (luna-prompts-v2-2026-11) |

Plus, per draft ADR-1cb48609: `WEB_IMPORTANT_THRESHOLD=66` set together with `SUMMARIZE_RANK_PROMPT_VARIANT=v2`; `CLUSTERER_SURFACE_MIN_GRADE` stays 80.

Parameter rule encoded in every shadow arm's `params` block (`experiment.build_arm_kwargs` only overlays those params and adds `allowed_openai_params`; it does not apply the rule itself): when `reasoning_effort` is set, `max_tokens` is replaced by `max_completion_tokens`, `allowed_openai_params=["reasoning_effort"]` is added for litellm, and `temperature` is removed unless the effort is `none` (the models reject a non-default temperature when reasoning).

## Problems to Fix

1. **No per-operation model.** `CLUSTERER_LLM_MODEL` is used by classify (`classification.py:478`), merge (`worker.py:202`) and fact consolidation (`worker.py:150`). Merge must run on gpt-6.1-terra while classify and facts run on gpt-6-luna.
2. **Production calls cannot send `reasoning_effort`.** No production call path passes it, and all of them send `max_tokens` (and `temperature`). gpt-6-luna/terra reject `max_tokens`, and when reasoning they reject a non-default temperature. Without `reasoning_effort: none`, a 128-token merge budget can be consumed by reasoning, returning empty content — the merge function fails open to "don't merge", so merging would silently stop.
3. **Shared or hard-coded completion budgets.** `CLUSTERER_LLM_MAX_OUTPUT_TOKENS` (512) is shared by classify and facts, but facts needs 2000 when reasoning at `low`. Merge hard-codes `max_tokens: 128` and `temperature: 0.0` (`worker.py:204-205`).
4. **Cutover clean-up has no tooling.** ADR-9687cb01 requires the cutover to empty the shadow results and truncate `thread_merge_verdicts`, because cached "don't merge" verdicts were decided by gpt-4.1-mini. There is no command for it, and `prod-db.sh` is read-only by design.
5. **The offline eval export reads `llm_shadow_results`.** `evals/clusterer_merge/export.sql` builds merge pairs from `llm_shadow_results`, which will be empty after cutover. The production record of every merge call is `llm_calls` (`service='clusterer'`, `operation='merge'`, `ref_id="min:max"`), which exists with or without an experiment.

## Changes

### 1. Shared parameter helper (`aggregator-common`)

Add `aggregator_common/llm_params.py`:

```python
REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high"})

def apply_reasoning_params(kwargs: dict, reasoning_effort: str | None) -> dict:
    """Return a copy of litellm kwargs adjusted for a reasoning model.

    reasoning_effort None -> kwargs returned unchanged (equal dict; current behaviour).
    Otherwise:
      * kwargs["reasoning_effort"] = reasoning_effort
      * "max_tokens" is popped and its value moved to "max_completion_tokens"
        (an explicit "max_completion_tokens" already in kwargs wins)
      * "temperature" is popped unless reasoning_effort == "none"
      * "reasoning_effort" is appended to allowed_openai_params (deduplicated)
    """
```

- Raises `ValueError` for a value outside `REASONING_EFFORTS`.
- `experiment.build_arm_kwargs` keeps its own behaviour. It is not refactored in this spec.
- Interaction with future experiments: shadow arms overlay their `params` onto the transformed control kwargs. When control has a reasoning effort set, future arm configs must use `max_completion_tokens` (not `max_tokens`). An arm that wants `temperature` back (e.g. `reasoning_effort: none` over a `low` control) must set it explicitly. Document this in the CLAUDE.md shadow-framework section.
- Unit tests cover:
  - the `None` passthrough is identical to the input;
  - `none` keeps temperature, while `low` removes it;
  - `max_tokens` → `max_completion_tokens`;
  - an existing `allowed_openai_params` is preserved and deduplicated;
  - an invalid value raises.

### 2. New settings (all optional; defaults keep today's behaviour exactly)

Follow the per-service prefix convention. Validate every `*_REASONING_EFFORT` value against `REASONING_EFFORTS` at startup, using a pydantic validator that fails startup on an invalid value, like the existing `*_PROMPT_VARIANT` validators. An empty string from `.env` is treated as unset.

| Setting | Service | Default | Used by |
|---|---|---|---|
| `LLM_REASONING_EFFORT` | summarize-rank | unset | rank call |
| `CLUSTERER_CLASSIFY_LLM_MODEL` | clusterer | unset → `CLUSTERER_LLM_MODEL` | classify |
| `CLUSTERER_CLASSIFY_LLM_REASONING_EFFORT` | clusterer | unset | classify |
| `CLUSTERER_CLASSIFY_LLM_MAX_OUTPUT_TOKENS` | clusterer | unset → `CLUSTERER_LLM_MAX_OUTPUT_TOKENS` | classify |
| `CLUSTERER_MERGE_LLM_MODEL` | clusterer | unset → `CLUSTERER_LLM_MODEL` | merge |
| `CLUSTERER_MERGE_LLM_REASONING_EFFORT` | clusterer | unset | merge |
| `CLUSTERER_MERGE_LLM_MAX_OUTPUT_TOKENS` | clusterer | `128` (today's hard-coded value) | merge |
| `CLUSTERER_FACTS_LLM_MODEL` | clusterer | unset → `CLUSTERER_LLM_MODEL` | consolidate_facts |
| `CLUSTERER_FACTS_LLM_REASONING_EFFORT` | clusterer | unset | consolidate_facts |
| `CLUSTERER_FACTS_LLM_MAX_OUTPUT_TOKENS` | clusterer | unset → `CLUSTERER_LLM_MAX_OUTPUT_TOKENS` | consolidate_facts |
| `BRIEF_LLM_REASONING_EFFORT` | brief | unset | brief generate (every turn of the tool loop) |

Existing settings stay and keep their meaning: `LLM_MODEL`, `LLM_MAX_OUTPUT_TOKENS`, `LLM_TEMPERATURE`, `CLUSTERER_LLM_MODEL`, `CLUSTERER_LLM_MAX_OUTPUT_TOKENS`, `CLUSTERER_LLM_TEMPERATURE`, `BRIEF_LLM_MODEL`, `BRIEF_LLM_MAX_OUTPUT_TOKENS`, `BRIEF_LLM_TEMPERATURE`. Merge keeps `temperature: 0.0` (not configurable; unchanged).

### 3. Wire the settings into each production call

At each control call site, build `control_kwargs` as today, then substitute the operation's model and budget. Pass the result through `apply_reasoning_params(control_kwargs, <op effort>)` before the call and before `experiment.submit_shadow`, so shadow arms derive from the real control kwargs.

- `summarize-rank/ranker.py` (~line 121): `LLM_REASONING_EFFORT`.
- `clusterer/classification.py` (~line 477): classify model, budget and effort.
- `clusterer/worker.py` `_make_llm_merge_fn` (~line 201): merge model, `CLUSTERER_MERGE_LLM_MAX_OUTPUT_TOKENS` instead of the literal 128, and merge effort.
- `clusterer/worker.py` facts function (~line 149): facts model, budget and effort.
- `brief/generate.py` `_build_llm_kwargs` (~line 115): `BRIEF_LLM_REASONING_EFFORT`. This covers both the normal and the forced-submit turn. Brief shadow arms (`shadow.py`) are not changed.

The telemetry `model` fallbacks that read `settings.clusterer_llm_model` (e.g. `classification.py:499`) must use the per-operation model.

Tests, per call site, with litellm mocked:
- **Defaults:** with all new settings unset, the kwargs are identical to today's. This is a regression guard.
- **Target setup:** with the setup from the Objective table, the kwargs equal the corresponding shadow arm's effective kwargs. Specifically:
  - `model` is the operation's model;
  - `max_tokens` is absent and `max_completion_tokens` equals the table value;
  - `temperature` is present only for `none`;
  - `reasoning_effort` and `allowed_openai_params` are set.
- **Merge independence:** setting `CLUSTERER_MERGE_LLM_MODEL` changes only merge calls, not classify or facts.

### 4. Cutover clean-up command (`aggregator-admin`)

Add `aggregator-admin ops llm-cutover-cleanup` in `ops.py`.

It runs in one transaction:
- `DELETE FROM llm_shadow_results`, keeping the table, its constraint and its index;
- `TRUNCATE thread_merge_verdicts`.

Behaviour:
- **Dry run first:** without `--yes`, it prints the row counts it would remove and exits 0 without writing.
- **Report:** with `--yes`, it prints the counts removed.
- **Logs:** logs go to stderr, like the other admin commands.
- **Safety check:** it refuses to run, exiting non-zero with a message, unless `LLM_EXPERIMENT` in the admin container's environment is unset, empty, or valid JSON with `"enabled": false`. Invalid JSON also counts as a refusal, so an ambiguous config is never treated as off. This prevents a still-running experiment from writing new rows straight away. The check reads the variable directly with `json.loads`; it does not call `experiment.init`.

Tests (testcontainers):
- dry run writes nothing;
- `--yes` empties both tables, and `llm_shadow_results` still exists and accepts an insert;
- refusal when an enabled `LLM_EXPERIMENT` is set, and when it holds invalid JSON;
- runs when `LLM_EXPERIMENT` is unset, empty, or has `"enabled": false`.

### 5. Offline eval export reads `llm_calls`

Rewrite `evals/clusterer_merge/export.sql` so the pair source is `llm_calls`:
- `WHERE service='clusterer' AND operation='merge' AND ref_id IS NOT NULL`
- `GROUP BY ref_id`, with `MIN(created_at)` as `first_seen`
- one pair per `ref_id`, whichever arm made the call

Everything downstream is unchanged:
- the joins to `threads`, `thread_memberships`, `articles` and `sources`;
- the member selection;
- the output JSON shape.

`shadow_verdicts` stays in the output as an empty object `{}`, so `build_dataset.py` and the frozen-dataset format don't change. The query stays read-only.

Also:
- update `evals/tests/test_export_sql.py`: assert that `llm_calls` is the source and that `llm_shadow_results` is not referenced;
- update `evals/README.md`: say that pairs come from `llm_calls`, which the janitor keeps for `JANITOR_LLM_TELEMETRY_RETENTION_DAYS` (30) days, so an export covers about the last month, and that `shadow_verdicts` is empty from now on.

### 6. Docs and examples

- **CLAUDE.md:**
  - add the new settings to the Config table;
  - update `WEB_IMPORTANT_THRESHOLD`, `CLUSTERER_*_PROMPT_VARIANT` and the `SUMMARIZE_RANK_PROMPT_VARIANT`/`BRIEF_PROMPT_VARIANT` rows to describe the production values;
  - replace the shadow framework's "Retention — dropped by the cutover spec" sentence with "emptied at cutover by `aggregator-admin ops llm-cutover-cleanup`; the table is kept for future experiments";
  - in the Operator deploy note, say to revert `JANITOR_ARTICLE_RETENTION_DAYS` to 14 at cutover.
- **`.env.example`:** add a commented "post-cutover production setup" block with the exact values from §7. Remove the stale experiment example, or mark it as an example only.
- **`deploy/README.md`:** add a short "LLM cutover (gpt-4.1 retirement)" section that points to §7's steps.

### 7. Operator cutover procedure (documented in `deploy/README.md`; run by the user on the Pi)

1. Deploy the release that contains this spec: `sudo ./install.sh update`.
2. In `/opt/personal-aggregator/.env`, set these values and remove the `LLM_EXPERIMENT` line:
   ```
   LLM_MODEL=gpt-6-luna
   LLM_REASONING_EFFORT=low
   LLM_MAX_OUTPUT_TOKENS=2000
   SUMMARIZE_RANK_PROMPT_VARIANT=v2
   WEB_IMPORTANT_THRESHOLD=66
   CLUSTERER_LLM_MODEL=gpt-6-luna
   CLUSTERER_CLASSIFY_PROMPT_VARIANT=v3
   CLUSTERER_CLASSIFY_LLM_REASONING_EFFORT=none
   CLUSTERER_CLASSIFY_LLM_MAX_OUTPUT_TOKENS=512
   CLUSTERER_MERGE_PROMPT_VARIANT=v4b
   CLUSTERER_MERGE_LLM_MODEL=gpt-6.1-terra
   CLUSTERER_MERGE_LLM_REASONING_EFFORT=none
   CLUSTERER_MERGE_LLM_MAX_OUTPUT_TOKENS=128
   CLUSTERER_FACTS_LLM_REASONING_EFFORT=low
   CLUSTERER_FACTS_LLM_MAX_OUTPUT_TOKENS=2000
   BRIEF_LLM_MODEL=openai/responses/gpt-6-luna
   BRIEF_LLM_REASONING_EFFORT=low
   BRIEF_LLM_MAX_OUTPUT_TOKENS=8192
   BRIEF_PROMPT_VARIANT=v2
   JANITOR_ARTICLE_RETENTION_DAYS=14
   ```
3. Restart: `sudo systemctl restart aggregator`.
4. Run `aggregator-admin ops llm-cutover-cleanup` in the admin container: first without `--yes` to check the counts, then with `--yes`.
5. Verify, using the `metrics-review` skill, about one hour later and again the next day:
   - `llm_calls` for summarize_rank, clusterer and brief shows only the new models, with 0 errors;
   - merge calls return non-empty content;
   - the next scheduled brief is `ready`.
6. **Rollback:** restore the previous `.env`, which is still on gpt-4.1-mini until 2027-01-14, and restart. The clean-up is not reversed: `thread_merge_verdicts` rebuilds itself as pairs are re-checked, and the deleted shadow rows are not needed. This is acceptable.

## Requirements

### ADDED: llm-call-configuration / Per-operation model and reasoning parameters
Each production LLM operation (`summarize_rank.rank`, `clusterer.classify`, `clusterer.merge`, `clusterer.consolidate_facts`, `brief.generate`) SHALL use its own configurable model, reasoning effort and completion budget. With the new settings unset, it MUST send exactly the kwargs it sends today.

#### Scenario: Merge on a different model than classify
- **WHEN** `CLUSTERER_LLM_MODEL=gpt-6-luna` and `CLUSTERER_MERGE_LLM_MODEL=gpt-6.1-terra` are set
- **THEN** merge calls use `gpt-6.1-terra`
- **AND** classify and consolidate_facts calls use `gpt-6-luna`

#### Scenario: Reasoning parameters applied
- **WHEN** an operation's reasoning effort is set to `low`
- **THEN** its call carries `reasoning_effort="low"`, `max_completion_tokens` and `allowed_openai_params` containing `reasoning_effort`
- **AND** carries neither `max_tokens` nor `temperature`

#### Scenario: Reasoning effort none keeps temperature
- **WHEN** an operation's reasoning effort is set to `none`
- **THEN** its call carries `reasoning_effort="none"`, `max_completion_tokens` and `temperature`
- **AND** carries no `max_tokens`

#### Scenario: Invalid reasoning effort
- **WHEN** `CLUSTERER_MERGE_LLM_REASONING_EFFORT=bogus` is set
- **THEN** clusterer startup fails with a message naming the setting and the valid values

#### Scenario: Defaults unchanged
- **WHEN** none of the new settings are set
- **THEN** every operation's litellm kwargs are identical to the kwargs before this change

### ADDED: llm-cutover / Cutover clean-up command
`aggregator-admin ops llm-cutover-cleanup --yes` SHALL delete all rows from `llm_shadow_results` (keeping the table) and truncate `thread_merge_verdicts` in one transaction. It MUST refuse to run while an enabled `LLM_EXPERIMENT` is configured.

#### Scenario: Dry run
- **WHEN** the command runs without `--yes`
- **THEN** it prints the row counts it would remove and writes nothing

#### Scenario: Experiment still enabled
- **WHEN** `LLM_EXPERIMENT` is set with `"enabled": true`, or holds invalid JSON
- **THEN** the command exits non-zero without writing

### ADDED: offline-evals / Merge-pair export source
The clusterer.merge eval export SHALL read merge pairs from `llm_calls` (`service='clusterer'`, `operation='merge'`) and MUST NOT depend on `llm_shadow_results`.

#### Scenario: Export with no experiment running
- **WHEN** `llm_shadow_results` is empty and `llm_calls` has merge calls in the retention window
- **THEN** the export emits one record per distinct merge `ref_id` whose threads still exist, with `shadow_verdicts` = `{}`

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-common/src/aggregator_common/llm_params.py` | New: `REASONING_EFFORTS`, `apply_reasoning_params` |
| `packages/aggregator-common/tests/test_llm_params.py` | New: helper tests |
| `packages/aggregator-summarize-rank/src/aggregator_summarize_rank/config.py` | `llm_reasoning_effort` + validator |
| `packages/aggregator-summarize-rank/src/aggregator_summarize_rank/ranker.py` | Apply effort to control kwargs |
| `packages/aggregator-clusterer/src/aggregator_clusterer/config.py` | Per-operation model / effort / budget settings + validators + fallback resolution |
| `packages/aggregator-clusterer/src/aggregator_clusterer/classification.py` | Classify uses its own model / budget / effort |
| `packages/aggregator-clusterer/src/aggregator_clusterer/worker.py` | Merge and facts use their own model / budget / effort; drop literal 128 |
| `packages/aggregator-brief/src/aggregator_brief/config.py` | `brief_llm_reasoning_effort` + validator |
| `packages/aggregator-brief/src/aggregator_brief/generate.py` | Apply effort in `_build_llm_kwargs` |
| `packages/aggregator-admin/src/aggregator_admin/ops.py` | `llm-cutover-cleanup` command |
| tests in each touched package | Defaults-unchanged and target-setup kwargs tests; admin command tests |
| `evals/clusterer_merge/export.sql` | Source pairs from `llm_calls` |
| `evals/tests/test_export_sql.py` | Assert new source |
| `evals/README.md` | Export source and retention note |
| `CLAUDE.md`, `.env.example`, `deploy/README.md` | Docs per §6–7 |

## Acceptance Criteria

1. With no new settings set, every production LLM call's kwargs are equal as dicts to before (same keys and values). Tests cover rank, classify, merge, facts and brief.
2. With the §7 values, each operation's kwargs match its shadow arm from the Objective table. Tests assert each one.
3. An invalid `*_REASONING_EFFORT` value fails service startup with a message naming the setting and the valid values.
4. `aggregator-admin ops llm-cutover-cleanup` follows the §4 behaviour, and its tests pass.
5. `export.sql` doesn't reference `llm_shadow_results`, and the evals tests pass.
6. No database migration is added; `llm_shadow_results` keeps its schema.
7. `bash scripts/run-tests.sh` passes, with no new warnings.
8. CLAUDE.md, `.env.example` and `deploy/README.md` document the new settings and the cutover procedure.

## Pending Decisions

- **Keep `llm_shadow_results`, emptied (resolved 2026-11-27, user).** ADR-9687cb01 says the cutover drops the table. The user chose to keep it, empty, so future experiments can reuse the shadow framework without a migration. Recorded in ADR-60ce8b01 (approved 2026-11-27), which supersedes ADR-9687cb01. It also records that this cutover rests on the offline eval plus a reviewed shadow sample.
- **Brief and fact consolidation reasoning level (resolved 2026-11-27, user):** `low` for both.
- **Merge setup (resolved 2026-11-27, user):** `v4b` on gpt-6.1-terra, `reasoning_effort` none. The user chose to cut over before the experiment's `until` date (2026-12-07), based on the offline eval plus 46 shadow pairs with 0 errors.
- **ADR-1cb48609** (attach-by-default classify, threshold 66): approved 2026-11-27 alongside this cutover.
