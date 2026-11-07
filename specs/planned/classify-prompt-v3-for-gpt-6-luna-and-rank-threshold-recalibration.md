---
name: Classify prompt v3 for gpt-6-luna and rank threshold recalibration
id: spec-1b31bd43
description: Classify v3 prompt (attach-by-default) + shadow validation vs v2; arm-specific importance threshold (66) in llm-experiment-report
dependencies: null
priority: high
complexity: null
status: planned
tags:
- llm
- experiment
- prompts
scope:
  in: null
  out: null
feature_root_id: B-f5e61cce
---
# Classify prompt v3 for gpt-6-luna and rank threshold recalibration

## Objective

The `luna-prompts-v2-2026-11` validation experiment (spec-9eac0646, running since 2026-10-30) passed **merge v2** and **brief v2** but not **classify v2** or **rank v2**. This spec:

1. Adds a **`v3` classify prompt** that makes attaching to an ongoing thread the default, plus a follow-up validation experiment comparing v2 and v3 on identical inputs.
2. Adds an **arm-specific importance threshold** to `llm-experiment-report`, so rank can be evaluated against the recalibrated threshold (**66**) Luna will run with after cutover.
3. Makes every "important" view honour `WEB_IMPORTANT_THRESHOLD`. Today the API's `GET /api/v1/articles?view=important` and the MCP `list_articles` tool / `feed://important` resource ignore it and use a hardcoded 70, so changing the threshold at cutover would only affect the web UI.

Production behaviour does not change: `CLUSTERER_CLASSIFY_PROMPT_VARIANT` stays `v1`, and `WEB_IMPORTANT_THRESHOLD` stays `70` until the cutover spec.

## Evidence (luna-prompts-v2-2026-11, 2026-10-30 → 2026-11-07)

- **Classify (n=1071):** v2 barely moved from v1. Control-attach → Luna-new is 6.3 % for v2 vs 7.0 % for v1 (target ≤ 2.5 %); outcome agreement is 88.2 % vs 88.3 % (target ≥ 92 %). The v2 "reason" strings still argue by development: *"a development not covered by the candidate threads"*, *"none of the candidate threads covers this specific attack"*. In a random sample of 12 v2 splits, these were clear errors:
  - *"Putin's territorial demands dim ceasefire prospects"* split from the Ukraine peace-talks thread.
  - *"Verstappen and engineer joke about the clutch in FP2"* split from the Brazilian GP practice thread.
  - *"George Russell takes engine penalty at Brazilian GP"* split from the thread on his 2026 season and the race weekend.
  - *"The Elder Scrolls timeline has stagnated"* (commentary) split from the Elder Scrolls 6 reveal thread.
  - *"Bomb threats hit multiple European flights"* split from *"Wizz Air flight diverted after bomb threat"*.

  Others were defensible: Meta *Muse Spaces* vs *Muse Voice* (different product), and a Fitness+ feature vs an iOS 27.3 release thread. The v2 "Thread scope" text itself encourages splitting: *"e.g. a different strike, a different company's product, a different race"*. Luna applies "a different strike" even when the thread is about the strike campaign.
- **Rank (n=671):** v2 removed threshold clumping (0.4 % of scores at exactly 70/75, vs control 31 %). Ordering agrees with control (ρ 0.82), but Luna's scale is lower. The share of scores ≥ 70 is 13.6 % for v2 vs 34.4 % for control.

  | Luna v2 threshold | 60 | 65 | **66** | 70 |
  |---|---|---|---|---|
  | Share at or above | 28.4 % | 23.2 % | **22.8 %** | 13.6 % |

  At the top of the scale the two models already agree: ≥ 80 is 0.8 % for both, so `CLUSTERER_SURFACE_MIN_GRADE=80` does not need to change. Race results and practice sessions score 71–82 under v2; the items lost below 70 are mostly F1 off-track news and AI business news, which the profile ranks lower.

## Changes

### 1. Classify `v3` prompt (`aggregator_clusterer/classification.py`)

- Register `"v3"` for classify in `PROMPT_VARIANTS` (`aggregator_common/experiment.py`): `"clusterer.classify": frozenset({"v1", "v2", "v3"})`. No other operation changes.
- Add `_SYSTEM_PROMPT_V3` (exact text below, formatted with `label_values=_LABEL_VALUES` like v1/v2).
- `_build_system_prompt(variant)` returns `_SYSTEM_PROMPT_V3` for `"v3"`. Its silent fall-through to v1 for an unknown variant is replaced by `ValueError(f"unknown classify prompt variant {variant!r}")`; the registry and the settings validator already prevent unknown values from reaching it.
- `ClustererSettings.clusterer_classify_prompt_variant` needs no code change; its validator reads the registry, so `v3` becomes valid automatically.
- The shadow call site (`classification.py`, `variant_messages` loop) already builds messages for each variant in `decision.prompt_variants(control_variant)` and needs no change.

`_SYSTEM_PROMPT_V3` (exact text):

```
You are a news thread classifier. Given an article and a numbered list of candidate threads, decide how to classify the article.

Respond with a JSON object only — no markdown, no commentary. Required fields:
- "label": one of [{label_values}]
- "thread_id": the integer thread_id of the best-matching candidate thread (must be one of the listed thread_ids), or null if the article starts a new thread
- "confidence": a float between 0.0 and 1.0
- "new_facts": a list of strings (may be empty) — concrete new facts the article adds to the thread
- "reason": a brief explanation of the classification
- "thread_title": a concise, complete-phrase headline (≤80 characters, never ending mid-word), neutral and objective.
  new_thread or related_new_thread: synthesize a title from the article — do not copy the headline verbatim.
  same_thread_new_fact or correction_or_clarification: refresh the title to reflect the latest development.
  All other labels: set to null.
  Avoid clickbait, sensationalist framing, and exclamation marks.

How to decide:
1. Read each candidate thread's title and summary as its scope. Threads are deliberately broad:
   a thread about peace talks covers every proposal, demand, meeting and reaction in those talks;
   a thread about a race weekend covers practice, qualifying, the race, penalties and driver
   comments from that weekend; a thread about a product reveal covers later reporting, reviews
   and commentary about that product.
2. Ask: would a reader following this thread expect this article to appear in it?
   If yes, attach it with a same_thread_* label, even when the article reports a development,
   incident, quote or detail that the thread does not mention yet. A new development is the
   normal reason an article joins a thread, never a reason to leave it.
3. Only split when the article's main subject is different: a different company or product,
   a different race weekend, a different election, a different conflict, or a single incident
   unrelated to a thread that is itself about one specific incident.
   Use related_new_thread when it shares context with a candidate; new_thread when no candidate is close.
4. If you are unsure between a same_thread_* label and a split, attach.

Label semantics:
- new_thread: no candidate thread covers this subject
- same_thread_new_fact: within the thread's scope, and adds concrete information not already in the thread (thread_id required)
- same_thread_new_angle: within the thread's scope; new perspective, analysis, commentary or reaction without new hard facts (thread_id required)
- same_thread_duplicate: within the thread's scope; nothing beyond what the thread already states (thread_id required)
- same_thread_background_only: within the thread's scope; background or context only (thread_id required)
- correction_or_clarification: corrects or clarifies prior reporting in the thread (thread_id required)
- related_new_thread: shares context with a candidate but has a different main subject (step 3)
- irrelevant_or_low_value: off-topic or no value

Examples:
- Thread "Ukraine peace talks: Europe sceptical of US plan"; article "Putin's territorial demands dim ceasefire hopes" → same_thread_new_fact
- Thread "Brazilian GP practice: Verstappen tops FP2"; article "Verstappen jokes with engineer about the clutch in FP2" → same_thread_new_angle
- Thread "Bethesda reveals The Elder Scrolls 6 setting"; article "Opinion: the Elder Scrolls timeline has stagnated" → same_thread_new_angle
- Thread "Wizz Air flight diverted after bomb threat"; article "Bomb threats hit several European flights" → same_thread_new_fact
- Thread "Xbox leadership changes as COO departs"; article "Xbox's first COO is out after a few months" → same_thread_duplicate
- Thread "Meta adds Muse Voice video chat"; article "Meta launches Muse Spaces for group collaboration" → related_new_thread (different product)
- Thread "Mexican GP: Verstappen wins, Antonelli takes the title"; article "Brazilian GP qualifying: Hamilton on pole" → related_new_thread (different race weekend)

new_facts: only facts that are not already stated in the thread's title, summary or known
facts. Never restate the thread headline as a fact. An empty list is correct for
duplicates and pure angles.

When the article fits a candidate thread, set thread_id to that thread's integer id (from the listed candidates).
Set thread_id to null when label is new_thread or related_new_thread.
Only use a thread_id from the presented candidate list — do not invent thread ids.
```

### 2. Arm-specific importance threshold in the report (`aggregator_admin/llm_experiment.py`)

- New config key `ARM_IMPORTANT_THRESHOLD`, read from the environment by `_read_config` (`llm_experiment.py:331-342`). `_read_config` coerces with `type(default)(raw)`, which cannot work for a `None` default, so add the key with special handling: parse with `int(raw)`; unset, empty or non-integer values give `None` (a non-integer value also logs one WARNING).
- New CLI option `--arm-threshold <int>` on `llm-experiment-report`. Resolution order: CLI option, then `ARM_IMPORTANT_THRESHOLD` env, then `WEB_IMPORTANT_THRESHOLD`. The CLI command writes the **resolved int** into `config["ARM_IMPORTANT_THRESHOLD"]` before calling `_build_report`, so the report JSON `config` block always shows the effective value (e.g. 70 when nothing is set). Without the option or env var, output is unchanged apart from that extra config key.
- Rank uses the control threshold (`WEB_IMPORTANT_THRESHOLD`) for the control score and the arm threshold for the arm score in both places:
  - the flip test in `_content_summarize_rank`;
  - the disagreement test in the rank branch of the disagreement function (`llm_experiment.py:107-116`).
- `_content_summarize_rank` adds five keys:
  - `share_ge_threshold_ctrl`: share of control scores ≥ the control threshold;
  - `share_ge_threshold_arm`: share of arm scores ≥ the arm threshold;
  - `share_round_70_75_ctrl` and `share_round_70_75_arm`: share of scores exactly 70 or 75;
  - `arm_threshold`: the effective arm threshold.

  All rates are rounded to 4 decimals like `flip_rate`. The new keys are JSON only; the Rich table output (`_render_table`) is unchanged.
- No change to verdict thresholds (`CRITERIA_THRESHOLDS`). MAE still compares raw scores and is expected to fail for a model with a shifted scale; the rank cutover decision uses the criteria below.

### 3. Honour `WEB_IMPORTANT_THRESHOLD` in the API and MCP

Today only the web app (`aggregator_web/app.py:414, 422, 536`) and the API sources/categories endpoints (`aggregator_api/routes/misc.py:72, 77`) pass the setting. Fix the remaining callers that fall back to `queries._DEFAULT_IMPORTANT_THRESHOLD = 70`:

- `aggregator_api/routes/articles.py` `list_articles` (`:37`): pass `important_threshold=_settings.web_important_threshold`. `articles.py` has no settings instance yet; add a module-level `_settings = ApiSettings()` exactly like `misc.py:25`.
- `aggregator_mcp/config.py` `McpSettings`: add `web_important_threshold: int = Field(70, description="Minimum importance_score for the important view (mirrors WEB_IMPORTANT_THRESHOLD)")`. Pydantic-settings maps it to the `WEB_IMPORTANT_THRESHOLD` env var.
- `aggregator_mcp/server.py`: pass `important_threshold=_settings.web_important_threshold` (the module-level `McpSettings()` at `server.py:17`) in the `list_articles` tool (`:49`) and in the `feed://{view}` resource (`:174`). Don't construct a second `McpSettings`.

The defaults stay 70 (`queries._DEFAULT_IMPORTANT_THRESHOLD`, `aggregator_web/reader.py:12`), so behaviour is unchanged until the env var is set.

### 4. Validation experiment (operator config, documented in `.env.example`)

Replace the commented `# LLM_EXPERIMENT={"name":"luna-prompts-v2-2026-11",...}` line in `.env.example` (currently line 283) with the line below. It stays **commented out**, prefixed `# LLM_EXPERIMENT=`, like today. Update the explanatory comments above it accordingly. Rank and brief are not sampled: rank is evaluated with `--arm-threshold 66` on the existing v2 data, and brief v2 has already passed. Merge keeps a single v2 arm to build more evidence at negligible cost (~$0.02/month).

```
LLM_EXPERIMENT={"name":"luna-prompts-v3-2026-11","enabled":true,"until":"2026-12-07T00:00:00Z","max_daily_cost_usd":1.0,"operations":{"clusterer.classify":{"sample_rate":0.5,"arms":[{"name":"luna-none-v2","model":"gpt-6-luna","prompt":"v2","params":{"reasoning_effort":"none","max_tokens":null,"max_completion_tokens":512}},{"name":"luna-none-v3","model":"gpt-6-luna","prompt":"v3","params":{"reasoning_effort":"none","max_tokens":null,"max_completion_tokens":512}}]},"clusterer.merge":{"sample_rate":1.0,"arms":[{"name":"luna-none-v2","model":"gpt-6-luna","prompt":"v2","params":{"reasoning_effort":"none","max_tokens":null,"max_completion_tokens":128}}]}}}
```

The `.env.example` comment must state that this experiment supersedes `luna-prompts-v2-2026-11`, whose rows stay in `llm_shadow_results` for the rank and brief evaluation.

## Files to Modify

- `packages/aggregator-common/src/aggregator_common/experiment.py`: register classify `v3`.
- `packages/aggregator-clusterer/src/aggregator_clusterer/classification.py`: `_SYSTEM_PROMPT_V3`, `_build_system_prompt`.
- `packages/aggregator-clusterer/tests/test_classification.py`: v3 tests.
- `packages/aggregator-common/tests/test_experiment.py`: registry and config tests.
- `packages/aggregator-admin/src/aggregator_admin/llm_experiment.py`: arm threshold, new rank metrics, CLI option.
- `packages/aggregator-admin/tests/test_llm_experiment.py`: arm threshold tests.
- `packages/aggregator-api/src/aggregator_api/routes/articles.py`: pass the threshold.
- `packages/aggregator-api/tests/`: important-view threshold test.
- `packages/aggregator-mcp/src/aggregator_mcp/config.py`, `server.py`: setting + pass-through.
- `packages/aggregator-mcp/tests/`: important-view threshold test.
- `.env.example`: experiment block.
- `CLAUDE.md`:
  - admin bullet: document `--arm-threshold` and the `ARM_IMPORTANT_THRESHOLD` env fallback;
  - `LLM shadow evaluation framework` section: note that classify has a `v3` variant;
  - config table: change the `WEB_IMPORTANT_THRESHOLD` description to say it applies to the web UI, API and MCP.

  `ARM_IMPORTANT_THRESHOLD` is an admin-report setting, not a service setting, so it does not go in the config table.

## Acceptance Criteria

1. `PROMPT_VARIANTS["clusterer.classify"] == frozenset({"v1", "v2", "v3"})`; other operations are unchanged.
2. An `LLM_EXPERIMENT` with a classify arm `"prompt": "v3"` parses. The same arm on `clusterer.merge` disables the experiment with one ERROR log.
3. `ClustererSettings(clusterer_classify_prompt_variant="v3")` validates; `"v4"` raises a validation error.
4. `_build_system_prompt("v3")` returns text that contains the "How to decide:" section and all seven examples verbatim, and differs from v1 and v2. `_build_system_prompt("v1")` and `("v2")` are byte-identical to before this change. `_build_system_prompt("bogus")` raises `ValueError`.
5. With production variant `v1` and arms `v2` and `v3`, one sampled classify item writes three rows with three distinct `prompt_hash` values, and the arm rows' `params.prompt` are `"v2"` and `"v3"`.
6. `llm-experiment-report` without `--arm-threshold` and without `ARM_IMPORTANT_THRESHOLD` produces the same `flip_rate` and disagreement samples as before on the existing fixtures. Tests cover this.
7. With `--arm-threshold 66`, a pair (control 70, arm 66) is not a flip and not a threshold disagreement. A pair (control 70, arm 65) is both. `share_ge_threshold_arm` counts scores ≥ 66.
8. `share_ge_threshold_ctrl`, `share_ge_threshold_arm`, `share_round_70_75_ctrl`, `share_round_70_75_arm` and `arm_threshold` appear in the rank `content` block of the JSON output.
9. Threshold resolution:
   - With `ARM_IMPORTANT_THRESHOLD=66` in the environment and no CLI option, `content.arm_threshold == 66` and `config.ARM_IMPORTANT_THRESHOLD == 66`.
   - With env `66` and `--arm-threshold 60`, both are 60.
   - With neither, both equal `WEB_IMPORTANT_THRESHOLD`.
   - With `ARM_IMPORTANT_THRESHOLD=abc`, the value falls back to `WEB_IMPORTANT_THRESHOLD`.
10. With `WEB_IMPORTANT_THRESHOLD=66` in the settings, an article scored 67 is returned by `GET /api/v1/articles?view=important` and by the MCP `list_articles(view="important")` tool. With the default 70, the same article is not returned by either. Both settings instances are built at import time, so the tests patch the attribute (`monkeypatch.setattr(<module>._settings, "web_important_threshold", 66)` on `aggregator_api.routes.articles` and `aggregator_mcp.server`) rather than the env var.
11. A test reads `.env.example`, finds the single line starting with `# LLM_EXPERIMENT=`, strips that prefix, and checks that `_parse_config` returns a config named `luna-prompts-v3-2026-11` with classify arms `v2` and `v3`.
12. The full test suite passes.

## Cutover criteria (evaluated after ≥ 7 days of `luna-prompts-v3-2026-11`; not part of this implementation)

- **Classify v3:** control-attach → Luna-new ≤ 2.5 %, control-new → Luna-attach ≤ 2 %, outcome agreement ≥ 92 %. If v3 improves on v2 but misses a target, a manual review of 30 random disagreements decides: cut over if Luna is judged correct in at least half of them. gpt-4.1-mini is not ground truth.
- **Rank v2 at threshold 66** (`--arm-threshold 66` on `luna-prompts-v2-2026-11`): `share_ge_threshold_arm` between 20 % and 32 %; `share_round_70_75_arm` ≤ 10 %; manual check that no race result, qualifying or practice classification item is below 66.
- **Merge v2:** remains ≥ 90 % agreement on the new data.

At cutover the operator sets `WEB_IMPORTANT_THRESHOLD=66` together with the model switch and `SUMMARIZE_RANK_PROMPT_VARIANT=v2`. After §3 of this spec, the web UI, API and MCP all honour it. Articles ranked by gpt-4.1-mini before the switch stay on the old scale until they age out, about 14 days with retention restored.

## Resolved Decisions

1. **Threshold 66, not 70 and not 65** (2026-11-07). The user approved recalibrating to ~65; 66 is used because it is where the rank prompt's own "important" band starts (`GOOD_TO_KNOW_MAX + 1`), and its share (22.8 %) is within 0.4 pp of 65. Supersedes spec-9eac0646 Resolved Decision 1.
2. **No rank v3 prompt.** The v2 ordering is sound (ρ 0.82); the gap is scale, which a threshold fixes more predictably than more anchor text.
3. **Merge v2 and brief v2 are accepted** for cutover on the existing data.
4. **`CLUSTERER_SURFACE_MIN_GRADE` stays 80**: ≥ 80 share is 0.8 % for both control and Luna v2.

## Out of Scope

- The cutover itself: model switch, setting variants and threshold, truncating `thread_merge_verdicts`, dropping `llm_shadow_results`, restoring `JANITOR_ARTICLE_RETENTION_DAYS=14`.
- Rescoring existing articles onto Luna's scale.
- Removing v1/v2 classify prompts (done at cutover cleanup).
- Changes to merge, brief, rank prompts or facts consolidation.
