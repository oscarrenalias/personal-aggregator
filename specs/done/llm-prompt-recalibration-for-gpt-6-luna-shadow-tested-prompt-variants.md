---
name: LLM prompt recalibration for gpt-6-luna (shadow-tested prompt variants)
id: spec-9eac0646
description: Shadow-testable prompt variants + v2 prompts for classify/merge/rank/brief recalibrated for gpt-6-luna; production stays on v1
dependencies: null
priority: high
complexity: null
status: done
tags:
- llm
- experiment
- prompts
scope:
  in: null
  out: null
feature_root_id: B-ce401902
---
# LLM prompt recalibration for gpt-6-luna (shadow-tested prompt variants)

## Objective

gpt-4.1 / gpt-4.1-mini shut down on 2027-01-14. The `gpt41-retirement-2026-10` shadow experiment (spec-5f4aeb9f / spec-9dda2dee / spec-e8ca2c7d) showed gpt-6-luna is reliable and 3–4× cheaper, but reads three of our prompts differently from gpt-4.1-mini in ways that would visibly change the product. This spec:

1. Adds **prompt variants** to the shadow framework so a changed prompt can be shadow-tested on identical inputs (today arms can only change `model`/`params`; `messages` is a forbidden override).
2. Adds a **`v2` prompt** for `clusterer.classify`, `clusterer.merge`, `summarize_rank.rank` and `brief.generate`, written for gpt-6-luna.
3. Adds per-service settings that select the prompt variant in production (default `v1` = today's prompts, unchanged), so the later cutover is an env change: model + `*_PROMPT_VARIANT=v2`.
4. Adds an outcome-agreement metric for classify to `llm-experiment-report`, because strict label+thread agreement overstates disagreement.

Production behaviour does not change when this ships: every default stays `v1` and the control arm always uses the production prompt.

## Evidence (experiment data 2026-10-20 → 2026-10-27, manual review of disagreement cases)

- **Classify (luna-none, n=1549):** strict assignment agreement 75.5 %, outcome agreement (same thread vs new) 89.9 %. The divergence is lopsided: control-attach → Luna-new 94 cases vs control-new → Luna-attach 14. Luna treats "same thread" as "same development": e.g. *"Xbox's first chief operating officer is out after just a few months"* → `new_thread` although the candidate is *"Xbox leadership changes as COO Helen Chiang departs"*; *"President Stubb heralds 'good day for peace' after Oslo summit"* → `new_thread` against the 32-member Oslo-summit thread; *"Meta Reports Third Quarter 2026 Results"* → `new_thread` against *"Meta raises 2026 capex forecast and projects stronger Q4 revenue"*. In the reverse direction Luna is usually right (Genie 4 duplicate, PHP exploitation warning, Micron strike). Luna emits fewer `new_facts` (2.2 vs 3.5), mostly by not restating the thread headline — desirable.
- **Merge (luna-none, n=50):** agreement 84 %; Luna rejected 6 control merges because the threads covered "different developments" of one story (Lula poll vs Lula working-class analysis; Hamilton praising Antonelli vs Antonelli pole at the same GP). Its 2 arm-only merges were correct (two watchOS 27.2 threads). The current prompt says *"Be conservative: only answer true when both threads are unambiguously about the same event or ongoing story"* — Luna reads "same event".
- **Rank (luna-low, n=515):** ρ 0.81, MAE 9.8, Luna −3.6 lower. gpt-4.1-mini puts 30.7 % of scores at exactly 70 or 75 (20 distinct values); Luna uses ~70 distinct values (4.5 % at 70/75), so it actually follows the prompt's "be selective" instruction. Share ≥ 70: control 33.6 %, Luna 17–21 %. Luna correctly drops startup-funding/launch items the profile deprioritises (*"Blacksmith hits $1 billion valuation"* 75→43) and correctly raises major geopolitics (Serbia, Sudan, Brazil election); it wrongly drops live F1 race-weekend items the profile calls highest priority (*"FP1 times"* 75→68, *"Loss of pace in Q3 … – Verstappen"* 75→68) and some major AI releases (*"Mistral Large 4"* 75→56). Note `WEB_IMPORTANT_THRESHOLD=70` while the prompt's "important" band starts at 66 (`GOOD_TO_KNOW_MAX=65`); the threshold stays at 70 (see Resolved Decisions).
- **Brief (n=3 per arm):** Luna headlines are better (*"Antonelli Takes Austin Pole as Ukraine's Starlink Access Returns"* vs *"… Dominate October 22, 2026"*). Two defects: luna-medium's intro for brief 146451 states the US GP *"produced a Charles Leclerc victory"* (Antonelli won; Leclerc was P2); luna-low's intro leaks process: *"the candidate set otherwise has little high-confidence, timely s…"*.

## Changes

### 1. Prompt variants in the shadow framework (`aggregator_common/experiment.py`)

- `Arm` gains `prompt: str = "v1"`. The arm config accepts an optional `"prompt"` key (non-empty string). It is a top-level arm key, **not** a `params` key — `messages` stays in `_FORBIDDEN_PARAMS`.
- New module constant `PROMPT_VARIANTS: dict[str, frozenset[str]]`, the single registry of variants per operation:
  ```python
  PROMPT_VARIANTS = {
      "summarize_rank.rank":         frozenset({"v1", "v2"}),
      "clusterer.classify":          frozenset({"v1", "v2"}),
      "clusterer.merge":             frozenset({"v1", "v2"}),
      "clusterer.consolidate_facts": frozenset({"v1"}),
      "brief.generate":              frozenset({"v1", "v2"}),
  }
  ```
  `_parse_config` rejects an arm whose `prompt` is not in the registry for its operation (one ERROR log, experiment disabled — same behaviour as other config errors).
- `_KNOWN_ARM` (`experiment.py:33`) gains `"prompt"`.
- `Decision.prompt_variants(control_variant: str = "v1") -> set[str]` returns `{arm.prompt for arm in arms} - {control_variant}` — the variants a call site must build messages for.
- `submit_shadow(decision, control_kwargs, control_outcome, attempt, *, control_variant: str = "v1", variant_messages: dict[str, list] | None = None)`. Single-call sites (rank, classify, merge) build messages for every variant in `decision.prompt_variants(control_variant)` on the caller thread (pure string work) and pass them; `control_variant` is the production variant setting for that operation.
- `build_arm_kwargs(control_kwargs, decision, arm, *, control_variant: str = "v1", variant_messages: dict[str, list] | None = None)` replaces `kw["messages"]` with `variant_messages[arm.prompt]` **only when** `variant_messages is not None and arm.prompt != control_variant`. If that variant is required but absent from `variant_messages`, it raises a new `MissingPromptVariant(variant)` exception.
- `_process_item` moves its `build_arm_kwargs` call inside the existing per-arm `try` (today it sits outside it, `experiment.py:333-349`); `_process_item` calls `build_arm_kwargs(..., control_variant=item["control_variant"], variant_messages=item.get("variant_messages") or {})`, so on the executor path an absent mapping also raises; only direct callers (brief) use the `None` pass-through. `MissingPromptVariant` produces that arm's row with `status="api_error"`, `error="missing prompt variant <v>"` and the control row's `prompt_hash`, while the control row and other arms are still written.
- With the defaults (`control_variant="v1"`, `variant_messages=None`) behaviour is unchanged, so existing callers — including `aggregator_brief/shadow.py:176`, which calls `build_arm_kwargs` on every tool-loop turn — and the tests in `aggregator-common/tests/test_experiment.py` keep working.
- Each row's `prompt_hash` is computed from that row's effective messages (brief: from that arm's seed messages). Row `params` JSON: a **copy** of `arm.params` plus `"prompt": arm.prompt` whenever `arm.prompt != "v1"` or `control_variant != "v1"`; the control row's `params` gets `{"prompt": control_variant}` when `control_variant != "v1"`. `arm.params` itself is never mutated (it is iterated into the litellm kwargs; a stray `prompt` key would be sent to the provider).
- **Brief does not use `variant_messages`.** `shadow._run_arm` keeps calling `build_arm_kwargs` with `variant_messages=None` on every turn, so the growing conversation passes through untouched. Before calling `run_brief_loop`, when `arm.prompt != settings.brief_prompt_variant`, `_run_arm` replaces `arm_messages[0]` (role `system`) with `build_system_prompt(settings, result.interest_profile_text, variant=arm.prompt)`; all other seed messages stay identical to control. `BriefRunResult` gains `interest_profile_text: str = ""`, set by `generate_brief` from the profile it already loads (`generate.py:414-417`) — no re-read, so profile edits between control and shadow cannot cause drift.
- **`clusterer.consolidate_facts` is unchanged** (`worker.py:42-98`): the registry allows only `v1`, so any other `prompt` is rejected at config parse and no `variant_messages` is ever needed.
- The `v1` path must be byte-identical to today: with no `prompt` keys in the config and all `*_PROMPT_VARIANT` unset, every `llm_shadow_results` row and every call's kwargs are unchanged.

### 2. Production variant selection

New settings (per-service prefix convention), all default `"v1"`, validated against `PROMPT_VARIANTS` at startup (invalid → fail fast with a clear error):

| Variable | Service | Applies to |
|---|---|---|
| `SUMMARIZE_RANK_PROMPT_VARIANT` | summarize-rank | `summarize_rank.rank` |
| `CLUSTERER_CLASSIFY_PROMPT_VARIANT` | clusterer | `clusterer.classify` |
| `CLUSTERER_MERGE_PROMPT_VARIANT` | clusterer | `clusterer.merge` |
| `BRIEF_PROMPT_VARIANT` | brief | `brief.generate` |

The control arm always uses the production variant. Shadow arms default to `prompt: "v1"`; an arm whose `prompt` equals the production variant reuses the control messages, any other arm gets its own variant's messages (so after a production switch to `v2`, a `"prompt": "v1"` arm still receives v1).

Rank versioning: `get_prompt_version(variant: str = "v1") -> str` returns `PROMPT_VERSION` (`"1.3.0"`) for `v1` and `PROMPT_VERSION_V2` (`"1.4.0"`) for `v2`; `ranker.rank()` passes `settings.summarize_rank_prompt_variant`, so re-rank tooling can tell them apart. `build_messages(..., variant="v1")` is called once for the control variant and once per variant in `decision.prompt_variants(...)`.

Merge verdict cache: `thread_merge_verdicts` (`consolidate.py:360-391`) has primary key `(keep_id, absorb_id)` with `*_last_updated` validity stamps and no model/prompt recorded, so negative verdicts recorded under v1 would suppress re-evaluation after a switch to v2. This spec does **not** change the cache; the cutover must truncate `thread_merge_verdicts` when it switches `CLUSTERER_MERGE_PROMPT_VARIANT` (recorded in Out of Scope and the cutover criteria). The cache also means the validation experiment only samples pairs that are not cached — accepted; merge volume is judged over the full run.

### 3. `v2` prompts (exact text)

Only the parts shown change; everything else in each prompt (JSON schema, field rules, user message) is identical to `v1`.

#### 3a. `clusterer.classify` v2

`_SYSTEM_PROMPT_V2` = v1's opening line and JSON-field block (unchanged, through the `thread_title` rules), then in this order: the new **"Thread scope:"** section, the new **"Label semantics:"** block (replaces v1's), **"Examples:"**, the **new_facts** paragraph, then v1's trailing thread_id paragraph ("When the article fits a candidate thread… do not invent thread ids.", `classification.py:87-89`) unchanged. It goes through the same `.format(label_values=_LABEL_VALUES)` as v1. New text:

```
Thread scope:
A thread follows one ongoing story over time — the same event, decision, product launch,
dispute, election, conflict front, earnings cycle or race weekend. A new development in
that story (a reaction, quote, follow-up, result, price, ruling, departure, review) belongs
IN that thread. Use related_new_thread only when the article's central event would deserve
its own headline even if the candidate thread did not exist (e.g. a different strike, a
different company's product, a different race). When torn between a same_thread_* label
and related_new_thread, choose the same_thread_* label.

Label semantics:
- new_thread: no candidate thread covers this story
- same_thread_new_fact: same story, and the article adds concrete information not already in the thread (thread_id required)
- same_thread_new_angle: same story, new perspective, analysis, or reaction without new hard facts (thread_id required)
- same_thread_duplicate: same story, nothing beyond what the thread already states (thread_id required)
- same_thread_background_only: same story, background/context only (thread_id required)
- correction_or_clarification: corrects or clarifies prior reporting in the thread (thread_id required)
- related_new_thread: shares context with a candidate but is a separate story by the test above
- irrelevant_or_low_value: off-topic or no value

Examples:
- Thread "Xbox leadership changes as COO departs"; article "Xbox's first COO is out after a few months" → same_thread_duplicate
- Thread "Oslo summit marks progress in Ukraine peace talks"; article "Finnish president hails 'good day for peace' after Oslo summit" → same_thread_new_angle
- Thread "Meta raises capex forecast, projects stronger Q4"; article "Meta reports third-quarter results" → same_thread_new_fact
- Thread "Driver poised to clinch the F1 title"; article "Driver says he may not use the No. 1 next year" → related_new_thread

new_facts: only facts that are not already stated in the thread's title, summary or known
facts. Never restate the thread headline as a fact. An empty list is correct for
duplicates and pure angles.
```

#### 3b. `clusterer.merge` v2 — replace the system message's last sentence

`v1`: *"Be conservative: only answer true when both threads are unambiguously about the same event or ongoing story."*

`v2`:
```
Answer true when both threads follow the same ongoing story, even if each focuses on a
different development or angle of it — e.g. qualifying and the race of the same grand
prix, polls and the result of the same election, the features and the security fixes of
the same software release. Answer false when they only share a domain, a company or a
person but cover separate stories — e.g. two different elections, two different products
from one company, a game's reviews and an unrelated patch, or a reaction piece about a
different event. If unsure, answer false.
```

#### 3c. `summarize_rank.rank` v2 — append to `_SYSTEM_WITH_PROFILE` step 3, after "Consider: …"

```
   Calibration:
   - Use specific values across the whole range; do not default to round numbers such as 70 or 75.
   - When the profile names something as highest priority (for example live race-weekend
     coverage: practice, qualifying, race results, penalties that change results), score
     matching articles 70 or higher unless they are trivial.
   - Flagship releases from leading AI labs (new frontier models, major agentic-coding tools)
     score 70 or higher for a profile with AI as a high-priority interest.
   - Funding rounds, valuations and company launches stay at most good-to-know unless the
     profile explicitly prioritises them, regardless of the subject area.
```
`_SYSTEM_NEUTRAL` v2 appends, after its "Consider: …" line, exactly:
```
   Calibration:
   - Use specific values across the whole range; do not default to round numbers such as 70 or 75.
```

#### 3d. `brief.generate` v2 — append a section to `_SYSTEM_TEMPLATE` before the final "Be selective" paragraph

```
## Accuracy and voice

- State only facts that appear in the provided articles or in tool results. Never infer
  an outcome (a winner, a result, a vote count, a price) that no article states; if
  articles conflict, say so or leave the claim out.
- The intro summarises only topics that appear in the brief, using the same facts.
- Write for the reader. Never mention the candidate set, seed data, tools, your selection
  process or your confidence in the brief text.
```

### 4. Report: classify outcome agreement (`aggregator_admin/llm_experiment.py`)

`_content_clusterer_classify` adds:
- `outcome_agreement`: share of pairs where both sides chose the same outcome. Outcome = `"attach:<thread_id>"` for `same_thread_*` and `correction_or_clarification`; `"drop"` for `irrelevant_or_low_value`; `"new"` for `new_thread` and `related_new_thread`.
- Counts and rates (rate = count / `n_ok_pairs`): `ctrl_attach_arm_new` / `ctrl_attach_arm_new_rate` (control attach, arm new), `ctrl_new_arm_attach` / `ctrl_new_arm_attach_rate`, `both_attach_diff_thread` / `both_attach_diff_thread_rate`.
- `mean_new_facts_ctrl`, `mean_new_facts_arm`.

The classify verdict criterion switches from `min_assignment_agreement: 0.85` to `min_outcome_agreement: 0.92`, with failed-criterion string `outcome_agreement_below_threshold` (replacing `assignment_agreement_below_threshold`); `assignment_agreement` stays in the output for reference. No other verdict changes.

### 5. Validation experiment (operator config, documented in `.env.example`)

After deploy, replace `LLM_EXPERIMENT` with a new experiment comparing v1 and v2 on identical inputs (brief: v2 only, see below):

```json
{"name": "luna-prompts-v2-2026-11", "enabled": true, "until": "2026-11-30T00:00:00Z", "max_daily_cost_usd": 2.0,
 "operations": {
  "summarize_rank.rank": {"sample_rate": 0.3, "arms": [
    {"name": "luna-low-v1", "model": "gpt-6-luna", "params": {"reasoning_effort": "low", "temperature": null, "max_tokens": null, "max_completion_tokens": 2000}},
    {"name": "luna-low-v2", "model": "gpt-6-luna", "prompt": "v2", "params": {"reasoning_effort": "low", "temperature": null, "max_tokens": null, "max_completion_tokens": 2000}}]},
  "clusterer.classify": {"sample_rate": 0.5, "arms": [
    {"name": "luna-none-v1", "model": "gpt-6-luna", "params": {"reasoning_effort": "none", "max_tokens": null, "max_completion_tokens": 512}},
    {"name": "luna-none-v2", "model": "gpt-6-luna", "prompt": "v2", "params": {"reasoning_effort": "none", "max_tokens": null, "max_completion_tokens": 512}}]},
  "clusterer.merge": {"sample_rate": 1.0, "arms": [
    {"name": "luna-none-v1", "model": "gpt-6-luna", "params": {"reasoning_effort": "none", "max_tokens": null, "max_completion_tokens": 128}},
    {"name": "luna-none-v2", "model": "gpt-6-luna", "prompt": "v2", "params": {"reasoning_effort": "none", "max_tokens": null, "max_completion_tokens": 128}}]},
  "brief.generate": {"sample_rate": 1.0, "arms": [
    {"name": "luna-low-v2",    "model": "openai/responses/gpt-6-luna", "prompt": "v2", "params": {"reasoning_effort": "low",    "temperature": null, "max_tokens": null, "max_completion_tokens": 8192}},
    {"name": "luna-medium-v2", "model": "openai/responses/gpt-6-luna", "prompt": "v2", "params": {"reasoning_effort": "medium", "temperature": null, "max_tokens": null, "max_completion_tokens": 8192}}]}
 }}
```
Brief deliberately has no `-v1` Luna arm: three Responses-bridge arms risk the shared 600 s deadline, and v1 Luna briefs already exist from `gpt41-retirement-2026-10`; brief v2 is judged against the production control (gpt-4.1-mini via the Pi's `BRIEF_LLM_MODEL` override) plus those earlier rows. `params` are copied verbatim from the `gpt41-retirement-2026-10` arms in `.env.example`; only `name` and `prompt` are new. Starting this experiment ends `gpt41-retirement-2026-10` (its rows stay in `llm_shadow_results`).

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-common/src/aggregator_common/experiment.py` | `Arm.prompt`, `PROMPT_VARIANTS`, config validation, `variant_messages` in `submit_shadow`/`build_arm_kwargs`, per-arm `prompt_hash`, `params.prompt` |
| `packages/aggregator-summarize-rank/src/aggregator_summarize_rank/prompt.py` | `build_messages(..., variant="v1")`, v2 calibration text, per-variant `get_prompt_version()` |
| `packages/aggregator-summarize-rank/src/aggregator_summarize_rank/schema.py` | `PROMPT_VERSION_V2 = "1.4.0"` |
| `packages/aggregator-summarize-rank/src/aggregator_summarize_rank/config.py`, `ranker.py` | `SUMMARIZE_RANK_PROMPT_VARIANT`; build control + variant messages in `ranker.rank()` (`ranker.py:99-155`); pass `control_variant` + `variant_messages` |
| `packages/aggregator-clusterer/src/aggregator_clusterer/classification.py` | `_SYSTEM_PROMPT_V2`; variant selection; `variant_messages` |
| `packages/aggregator-clusterer/src/aggregator_clusterer/worker.py` | merge v2 system message; variant selection; `variant_messages` |
| `packages/aggregator-clusterer/src/aggregator_clusterer/config.py` | `CLUSTERER_CLASSIFY_PROMPT_VARIANT`, `CLUSTERER_MERGE_PROMPT_VARIANT` |
| `packages/aggregator-brief/src/aggregator_brief/prompt.py` | `build_system_prompt(..., variant="v1")` with v2 section |
| `packages/aggregator-brief/src/aggregator_brief/config.py`, `generate.py`, `shadow.py` | `BRIEF_PROMPT_VARIANT`; production uses it; `BriefRunResult.interest_profile_text`; shadow swaps the system seed per arm, hashes per-arm seeds, and sets control and arm row `params` per §1 |
| `packages/aggregator-admin/src/aggregator_admin/llm_experiment.py` | classify outcome metrics + verdict criterion |
| `packages/aggregator-admin/tests/test_llm_experiment.py` | update `_ok_content` and `test_fail_classify_agreement_below_threshold` to the outcome criterion |
| `.env.example`, `CLAUDE.md` | new settings, `prompt` arm key, validation experiment |

## Acceptance Criteria

1. With `LLM_EXPERIMENT` unset or containing no `prompt` keys and all `*_PROMPT_VARIANT` unset, the full test suite passes; the only existing tests modified are the classify verdict tests in `aggregator-admin/tests/test_llm_experiment.py` (`_ok_content`, `test_fail_classify_agreement_below_threshold` → `outcome_agreement` / `outcome_agreement_below_threshold`); and the messages sent for each operation are byte-identical to before (asserted by a test per operation that compares `v1` messages to a snapshot captured from current code).
2. An arm with `"prompt": "v3"` (unregistered) or `"prompt": ""` disables the experiment with one ERROR log naming the operation and arm index; an arm with `"prompt"` inside `params` is rejected via the existing forbidden-key path.
3. For a sampled item with arms `luna-none-v1` and `luna-none-v2`, `llm_shadow_results` gets three rows; the v2 row's `prompt_hash` differs from control and v1, its `params` contains `"prompt": "v2"`, and the v2 arm's LLM call received the v2 system prompt (asserted via a fake attempt fn).
4. Setting `CLUSTERER_CLASSIFY_PROMPT_VARIANT=v2` makes the production classify call use the v2 system prompt; `=bogus` fails at settings construction with an error naming the variable. Same for the other three variables.
5. `get_prompt_version("v2")` returns `"1.4.0"`, `get_prompt_version()` and `get_prompt_version("v1")` return `"1.3.0"`; with `SUMMARIZE_RANK_PROMPT_VARIANT=v2` the version stored with a ranked article is `"1.4.0"`.
6. Brief shadow: a v2 arm's first message is the v2 system prompt built from `result.interest_profile_text`, messages 2..n equal control's seed messages, and on later turns the arm's conversation (assistant + tool messages) is preserved — asserted with a fake `call_fn` over at least two turns. The v2 row's `prompt_hash` differs from control's.
6a. Production v2 + v1 arm: with `CLUSTERER_CLASSIFY_PROMPT_VARIANT=v2` and an arm `"prompt": "v1"`, the arm call receives the v1 system prompt, its row's `params.prompt == "v1"`, the control row's `params.prompt == "v2"`, and the two `prompt_hash` values differ. Same check for brief with `BRIEF_PROMPT_VARIANT=v2`.
6b. Missing variant: when a single-call site omits a needed variant from `variant_messages`, or omits `variant_messages` entirely, that arm's row is `status="api_error"` with `error="missing prompt variant v2"`, and the control row and other arms are still written.
7. Each new text block of section 3 appears in the corresponding v2 prompt exactly as written (tests hold the blocks as fixtures copied from this spec and assert `block in prompt`; for classify, after `.format`), and none of them appears in the v1 prompt.
8. `llm-experiment-report --json` for classify includes `outcome_agreement`, the three counts and their `_rate` fields, `mean_new_facts_ctrl`, `mean_new_facts_arm`; `irrelevant_or_low_value` is its own `"drop"` outcome; the verdict uses `min_outcome_agreement: 0.92` / `outcome_agreement_below_threshold`. Covered by a report test on synthetic pairs with known counts.

## Cutover criteria (evaluated on the validation experiment, not part of this spec's implementation)

Recorded here so the later cutover spec has fixed targets:
- **Classify v2:** `ctrl_attach_arm_new` rate ≤ 2.5 % (v1: 6.1 %), `ctrl_new_arm_attach` ≤ 2 %, outcome agreement ≥ 92 %.
- **Merge v2:** agreement ≥ 90 %, arm-only merges ≤ 6 % of pairs; manual check of every disagreement. Cutover truncates `thread_merge_verdicts` when switching the merge variant.
- **Rank v2:** ≤ 10 % of scores at exactly 70 or 75; share ≥ 70 between 22 % and 32 %; manual check that no live race-weekend F1 item or flagship AI-lab release in the sample is below 70.
- **Brief v2:** ≥ 7 days of pairs; zero intros mentioning candidates/seed/tools; manual fact-check of every intro against its referenced articles finds no invented outcome.

## Resolved Decisions

1. **Important threshold:** `WEB_IMPORTANT_THRESHOLD` stays at **70** (decided 2026-10-27). The rank cutover criterion is evaluated against 70; no threshold change in this spec or the cutover unless revisited.
2. **Brief arm:** keep both `low` and `medium` in the validation run and pick at cutover. The `none` arm is dropped (no observed advantage, and it is the only Chat Completions brief path).
3. **Facts consolidation** gets no v2 (identical outputs so far); it switches model only at cutover.

## Out of Scope

- The model switch itself and removal of the `v1` prompts (cutover spec).
- Making the merge verdict cache variant/model-aware (cutover truncates `thread_merge_verdicts` instead).
- Dropping `llm_shadow_results` and restoring `JANITOR_ARTICLE_RETENTION_DAYS=14` (cutover spec).
- Prompt changes for podcast (already on gpt-5.6-terra) and the Decisions API (deferred).
