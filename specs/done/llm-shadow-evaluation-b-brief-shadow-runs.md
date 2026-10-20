---
name: "LLM shadow evaluation B: brief shadow runs"
id: spec-9dda2dee
description: Post-completion shadow runs of the full brief tool loop with gpt-6-luna arms (Responses bridge for low/medium); depends on spec-5f4aeb9f
dependencies: null
priority: high
complexity: null
status: done
tags:
- llm
- migration
- experiment
- openai
- brief
scope:
  in: null
  out: null
feature_root_id: B-63b4373b
---
# LLM shadow evaluation B: brief shadow runs

## Objective

Extend the shadow evaluation from spec A (`spec-5f4aeb9f`) to `brief.generate`. After each
daily brief completes, re-run the **full brief agent loop** with each `gpt-6-luna` arm on
the same seed input, and record each arm's submitted brief payload in
`llm_shadow_results`. Nothing is written to `briefs`.

**Depends on spec A** (`experiment.py`, `CallOutcome`, `llm_shadow_results`, telemetry
columns). It must be deployed with A by **2026-10-31** so the brief gets the full 3 weeks
of daily samples (about 21 pairs).

## Why it differs from the other operations

`brief.generate` isn't one completion. It's a multi-turn tool loop (`generate_brief`,
`aggregator_brief/generate.py:120`): the model calls `search_articles`/`get_article`, then
`submit_brief`, with a forced `submit_brief` on the last turn and one corrective turn on
schema failure. A shadow can't replay one request. It has to run the loop itself, with
read-only tool access.

Phase 0 finding 7 (see spec A): on Chat Completions, `gpt-6-luna` supports function tools
**only with `reasoning_effort="none"`**. At `low`/`medium` the API returns *"Function tools
with reasoning_effort are not supported for gpt-6-luna in /v1/chat/completions. To use
function tools, use /v1/responses or set reasoning_effort to 'none'."*

Routing through litellm's Responses bridge, `model="openai/responses/gpt-6-luna"`, works
with the existing `litellm.completion` loop unchanged. The probe verified full loops at
`low` and `medium` (2–3 turns, `BriefSubmitSchema`-valid, 6 topics) and the forced
`submit_brief` tool choice at both. `temperature` must be removed at `low`/`medium`, and
`max_tokens` replaced by `max_completion_tokens` at every effort (findings 2–3).

## Configuration

Add to the `LLM_EXPERIMENT` operations:

```json
"brief.generate": {"sample_rate": 1.0, "arms": [
  {"name": "luna-none",   "model": "gpt-6-luna",                  "params": {"reasoning_effort": "none",                          "max_tokens": null, "max_completion_tokens": 8192}},
  {"name": "luna-low",    "model": "openai/responses/gpt-6-luna", "params": {"reasoning_effort": "low",    "temperature": null, "max_tokens": null, "max_completion_tokens": 8192}},
  {"name": "luna-medium", "model": "openai/responses/gpt-6-luna", "params": {"reasoning_effort": "medium", "temperature": null, "max_tokens": null, "max_completion_tokens": 8192}}
]}
```

`ref_id` = `str(brief.id)`. Spec A's decision rules (`experiment.decide`) apply
unchanged. **Only `origin='auto'` (scheduled) briefs are sampled.** Manual briefs (admin
or MCP `refresh_brief`) are never shadowed, which keeps the design at one pair per day and
the cost predictable.

`luna-none` deliberately gets 8192 output tokens, twice the control's 4096: reasoning
arms need the headroom, and all arms use the same cap for comparability.

## Spec A API used

Only spec A's public API: `experiment.init`, `decide`, `build_arm_kwargs`, `prompt_hash`,
`write_item(..., truncate_raw=False)`, `shutdown`, `CallOutcome`/status values. B doesn't
use A's background executor, and doesn't reach into its internals.

## Changes

1. **Extract the loop** in `generate.py`:

   ```python
   @dataclass
   class BriefRunResult:
       seed_messages: list[dict]       # system + seed user messages, before any tool turns
       payload: BriefSubmitSchema
       turns: int                      # LLM calls made, including a corrective turn
       forced_submit: bool             # the submitting turn used the forced submit_brief tool_choice
       corrective_turn: bool
       request_ids: list[str]

   class BriefLoopError(GenerationError):   # carries partial results for shadow rows
       turns: int; request_ids: list[str]; last_submit_args: str | None; status: str  # parse_error | refusal | timeout

   def run_brief_loop(session, messages, call_fn: Callable[[list[dict], bool], Any],
                      settings, *, deadline: float | None = None,
                      clock: Callable[[], float] = time.monotonic) -> BriefRunResult
   ```

   - `call_fn(messages, force_submit)` returns a litellm response. The loop owns
     tool dispatch, `submit_brief` extraction, `BriefSubmitSchema` validation and the
     corrective turn, exactly as today.
   - `BriefLoopError` reuses today's exact `GenerationError` message texts, so existing
     `match=` tests keep passing.
   - When `deadline` is set and `clock() >= deadline` before a turn, the loop raises
     `BriefLoopError(status="timeout")` with message `"deadline"`.
   - Factor `_build_llm_kwargs(messages, settings, *, force_submit, ref_id, extra_metadata=None) -> dict`
     out of `_call_llm`. `_call_llm` calls `litellm.completion(**_build_llm_kwargs(...))`,
     and the arm `call_fn` uses the same builder before applying `build_arm_kwargs`.
   - `generate_brief(session, brief, settings, *, extra_metadata: dict | None = None)` now
     **returns `BriefRunResult`**. Existing callers ignore the return value, and its
     persistence behaviour is unchanged.
   - The control `call_fn` is `lambda m, f: _call_llm(m, settings, force_submit=f, ref_id=...)`,
     calling the module-level `_call_llm` at call time, so the ~10 existing
     `patch("aggregator_brief.generate._call_llm", ...)` tests keep intercepting.
   - `_call_llm` gains an optional `extra_metadata` keyword, merged into `metadata`. It is
     passed **only when** `extra_metadata` is not None, so existing fakes with the old
     signature keep working whenever the experiment is unset.
   - Existing brief tests pass unchanged.
2. **Sampling decision before the control run** (`loop.py`, before `generate_brief` at
   `loop.py:111`): `decision = experiment.decide("brief.generate", str(brief.id))` when
   `brief.origin == "auto"`, else None. When sampled, pass
   `extra_metadata=decision.control_metadata({})` so the control's `llm_calls` rows are
   tagged `arm='control'`.
3. **Shadow runner** in a new `aggregator_brief/shadow.py`, invoked from `loop.py`
   **after `session.commit()` (`loop.py:122`) and after the generation session is
   closed**. It sits in its own try/except that can never reach the `fail_brief` path,
   and runs only when `decision` is not None and generation succeeded.
   - **Control row:** `status='ok'`, `attempts=turns`, `output`, `prompt_hash` =
     `prompt_hash(result.seed_messages)`, `raw_output` = the submitted `submit_brief`
     arguments JSON.
   - **Arms**, in config order, each run `run_brief_loop` on the **same
     `result.seed_messages`** (deep-copied, never rebuilt):
     - `call_fn` builds kwargs equal to the control's `_call_llm` kwargs for that turn
       (`tools`, per-turn `tool_choice`, `timeout`, metadata) and applies
       `build_arm_kwargs` per turn.
     - Tool dispatch uses **one fresh session per arm**, with
       `SET TRANSACTION READ ONLY` issued first and rolled back and closed at the end.
   - **Row `output`** (control and arms):
     `{"payload": <payload.model_dump(mode="json")>, "turns": int, "forced_submit": bool, "corrective_turn": bool}`.
   - **Row write:** all rows of the item go in one `write_item(rows, truncate_raw=False)`
     call at the end, so brief payloads are never truncated.
   - **Partial items are accepted for `brief.generate`** (an exception to A's
     all-or-nothing rule): arms that ran are written, and arms skipped by the deadline get
     no row.
4. **Arm status mapping:**

   | Situation | status |
   |---|---|
   | valid payload submitted | `ok` |
   | litellm/OpenAI timeout exception on any turn | `timeout` |
   | any other completion exception | `api_error` |
   | `message.refusal` non-empty or `finish_reason == "content_filter"` on any turn | `refusal` |
   | stop without a tool call, loop exhausted without `submit_brief`, or payload invalid after the corrective turn | `parse_error` |
   | deadline hit mid-loop | `timeout` (error `"deadline"`) |

   Rows for failed arms still carry `attempts`, `request_ids` and
   `raw_output = last_submit_args` from `BriefLoopError` or the caught exception.
5. **Deadline:** start + 600 s for all arms together, checked before each arm and before
   each turn, through the injectable `clock`. Arms not started get no row and are counted
   and logged as `skipped_deadline`. The runner also checks the loop's `stop_event`
   between arms and stops on SIGTERM. To make that possible, `_run_one_iteration`
   (`loop.py:72`) gains an optional `stop_event: threading.Event | None = None`, passed
   from `run()`. A manual brief queued meanwhile waits at most this
   long, which is acceptable.
6. `run()` in `loop.py` calls `experiment.init(settings)` at startup and
   `experiment.shutdown()` on SIGTERM.
7. Shadow runs never write `briefs` or any table other than `llm_calls` (through
   telemetry) and `llm_shadow_results`.

## Files to Add/Modify

| File | Change |
|---|---|
| `packages/aggregator-brief/src/aggregator_brief/generate.py` | `run_brief_loop`, `BriefRunResult`, `BriefLoopError`, `generate_brief` return value, `_call_llm(extra_metadata=)` |
| `packages/aggregator-brief/src/aggregator_brief/shadow.py` | new: shadow runner |
| `packages/aggregator-brief/src/aggregator_brief/loop.py` | decision before generation; runner after commit; init/shutdown |
| `packages/aggregator-brief/tests/test_shadow.py` | new tests |
| `CLAUDE.md`, `.env.example` | add the `brief.generate` entry and notes on the Responses bridge |

## Implementation order (one developer bead each)

1. **Loop extraction** in `generate.py` (AC 1). No experiment code yet.
2. **Shadow runner + `loop.py` wiring** (AC 2–6, 8). Depends on 1 and on spec A's
   `experiment.py`.
3. **Docs** (`CLAUDE.md`, `.env.example`).

## Acceptance Criteria

1. With `LLM_EXPERIMENT` unset, all existing brief tests pass **unchanged** (no edits),
   and no `llm_shadow_results` rows are written.
2. With the experiment configured and `_call_llm`/litellm mocked (control and all three
   arms reach `submit_brief`), one `control` row plus three arm rows are written. All
   share the same `prompt_hash`, each `output.payload` is `BriefSubmitSchema`-valid, and
   `raw_output` is not truncated for payloads over 8 KB.
3. Arm calls receive the overridden kwargs per turn: the `openai/responses/gpt-6-luna`
   model for low/medium; no `temperature` for low/medium; `max_completion_tokens` and no
   `max_tokens` for all; `allowed_openai_params=["reasoning_effort"]`; metadata with
   `experiment` and `arm=<name>`. Control calls carry `arm='control'` when sampled.
4. Each row of the status table is produced by a matching mocked scenario. The completed
   brief row is unchanged afterwards, and `fail_brief` is never called.
5. The shadow run writes nothing to `briefs`, and an attempted write through its
   tool-dispatch session fails as read-only.
6. With an injected clock, a slow arm that hits the deadline mid-loop gets status
   `timeout`, and later arms get no row and are counted as `skipped_deadline`.
7. **Deploy check (operator, after Pi deploy):** the first day's shadow brief leaves
   `llm_calls` rows for all three arms with non-null `cost_usd`, confirming that the
   telemetry callback fires on the Responses bridge. If it doesn't, file a defect bead.
8. A brief with `origin='manual'` is never shadowed.

## Out of scope

- Changing the production brief model or moving production briefs to the Responses API
  (cutover spec).
- Any UI for viewing shadow briefs.
- Comparing brief quality (spec C).
