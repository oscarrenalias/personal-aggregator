---
name: "LLM shadow evaluation A: core framework, summarize-rank and clusterer"
id: spec-5f4aeb9f
description: "Shadow-evaluation framework (config, sampling, background executor, llm_shadow_results, telemetry) wired into summarize-rank and clusterer to compare gpt-6-luna against gpt-4.1/gpt-4.1-mini"
dependencies: null
priority: high
complexity: null
status: done
tags:
- llm
- migration
- experiment
- openai
scope:
  in: null
  out: null
feature_root_id: B-0bbdd357
---
# LLM shadow evaluation A: core framework, summarize-rank and clusterer

## Objective

Add a **shadow evaluation** framework to `aggregator-common` and wire it into
summarize-rank and the clusterer. For a sampled share of production LLM calls,
`gpt-6-luna` then runs on the *identical input* as the current model; its output is
recorded for offline comparison and **never applied**.

This is part A of three:

- **A (this spec):** framework, storage, telemetry, summarize-rank + clusterer.
- **B:** brief shadow runs (`spec-9dda2dee`, depends on A).
- **C:** analysis report + `llm-stats` changes (`spec-e8ca2c7d`, depends on A; can land
  during data collection).

None of the three switches production models. The cutover is a follow-up spec written
from the experiment's results.

## Why

OpenAI shuts down `gpt-4.1` and `gpt-4.1-mini` (plus `gpt-4o`/`gpt-4o-mini`) on
**2027-01-14** (notice 2026-10-14; recommended replacement `gpt-6-luna`). summarize-rank
(`gpt-4.1-mini`), the clusterer (`gpt-4.1`) and the brief (`gpt-4.1`) stop working on that
date. Shadowing gives paired, same-input comparisons from real traffic before anything is
switched.

| Milestone | Target |
|---|---|
| A + B deployed to the Pi, experiment on | 2026-10-31 |
| Data collection ends (3 weeks) | 2026-11-21 |
| C merged (report available) | ≤ 2026-11-21 |
| Go/no-go per operation | 2026-11-24 |
| Cutover spec merged | ≤ 2026-12-15 |

## Resolved decisions

1. **Replacement model:** `gpt-6-luna` for every operation. `gpt-5.6-terra` and the
   OpenAI Decisions API are deferred to a possible later round.
2. **Reasoning effort:** decision calls use `none`. Content generation (summaries, titles,
   known facts) tests both `low` and `medium` as separate arms.
3. **Shadow, not live split:** the control model's output is always the one persisted.
4. **Sampling:** `summarize_rank.rank` 30%; clusterer operations 100%.
5. Analysis output is machine-readable (spec C); no human-facing UI.

## Phase 0 results (done 2026-10-20; probe scripts `scripts/llm_luna_probe*.py`, uncommitted)

The probe ran every production call shape against `gpt-6-luna` with litellm **1.88.1**.
It used the services' real prompt builders/parsers on 10 synthetic but realistic articles
and threads, about 115 calls costing a few cents. Findings, all of which this spec has to
handle:

| # | Finding | Consequence |
|---|---|---|
| 1 | litellm 1.88.1 rejects `reasoning_effort` for `gpt-6-luna` **client-side** (`UnsupportedParamsError: openai does not support parameters: ['reasoning_effort']`) | Every arm call that sets `reasoning_effort` must also pass `allowed_openai_params=["reasoning_effort"]`. With it, reasoning reaches the API (`reasoning_tokens` 0 at `none`, > 0 at `low`/`medium`). Upgrading litellm (latest 1.106.0) is out of scope |
| 2 | The API rejects `max_tokens`: *"Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead."* This happens at **every** effort. Every call site passes `max_tokens` | Arms must remove `max_tokens` and set `max_completion_tokens` |
| 3 | `temperature`: any value is accepted at `none`. At `low`/`medium` only the default is accepted (*"'temperature' does not support 0.2 with this model. Only the default (1) value is supported."*) | `none` arms keep the production temperature; `low`/`medium` arms remove it |
| 4 | `response_format=RankResult` parsed 10/10 at none/low/medium. Classify, merge and facts JSON parsed 10/10, 10/10 and 3/3 | No parser changes needed |
| 5 | Observed max completion tokens (incl. reasoning): rank 155/282/297 (none/low/medium), classify 125/176 (none/low), merge 44, facts 259/482 (low/medium). Inputs were shorter than production's (up to 6,000 chars per article), so limits get a wide margin | Caps below: `none` arms keep production caps; reasoning arms get 2,000 |
| 6 | Latency p50 1.3–4.9 s; cost per call about $0.00003 (merge) to $0.00026 (facts/medium) | Shadow cost is small; budget cap below is a safety net |
| 7 | Brief tool calling at `low`/`medium` fails on Chat Completions (*"Function tools with reasoning_effort are not supported for gpt-6-luna in /v1/chat/completions"*). It works via litellm's `openai/responses/gpt-6-luna` bridge, including forced `submit_brief` | Handled in spec B |

## Experiment configuration

`Settings` gets `llm_experiment: str | None = None` (env `LLM_EXPERIMENT`). It is **typed
`str`** and parsed by hand in `aggregator_common/experiment.py`: a JSON-typed pydantic
field would crash startup on invalid JSON. Parsing never raises. If the value is invalid
JSON, has an unknown field at any level, or names an operation key outside the known set
(`summarize_rank.rank`, `clusterer.classify`, `clusterer.merge`,
`clusterer.consolidate_facts`, `brief.generate`), log **one ERROR** and disable the
experiment.

Unset, empty, or `"enabled": false` → no sampling and no shadow work. The production call
path behaves exactly as today.

Config to ship (commented out in `.env.example`, set in the Pi's `.env` at deploy):

```json
{
  "enabled": true,
  "name": "gpt41-retirement-2026-10",
  "until": "2026-11-22T00:00:00Z",
  "max_daily_cost_usd": 2.0,
  "operations": {
    "summarize_rank.rank": {"sample_rate": 0.3, "arms": [
      {"name": "luna-low",    "model": "gpt-6-luna", "params": {"reasoning_effort": "low",    "temperature": null, "max_tokens": null, "max_completion_tokens": 2000}},
      {"name": "luna-medium", "model": "gpt-6-luna", "params": {"reasoning_effort": "medium", "temperature": null, "max_tokens": null, "max_completion_tokens": 2000}}
    ]},
    "clusterer.classify": {"sample_rate": 1.0, "arms": [
      {"name": "luna-none", "model": "gpt-6-luna", "params": {"reasoning_effort": "none", "max_tokens": null, "max_completion_tokens": 512}},
      {"name": "luna-low",  "model": "gpt-6-luna", "params": {"reasoning_effort": "low",  "temperature": null, "max_tokens": null, "max_completion_tokens": 2000}}
    ]},
    "clusterer.merge": {"sample_rate": 1.0, "arms": [
      {"name": "luna-none", "model": "gpt-6-luna", "params": {"reasoning_effort": "none", "max_tokens": null, "max_completion_tokens": 128}}
    ]},
    "clusterer.consolidate_facts": {"sample_rate": 1.0, "arms": [
      {"name": "luna-low",    "model": "gpt-6-luna", "params": {"reasoning_effort": "low",    "temperature": null, "max_tokens": null, "max_completion_tokens": 2000}},
      {"name": "luna-medium", "model": "gpt-6-luna", "params": {"reasoning_effort": "medium", "temperature": null, "max_tokens": null, "max_completion_tokens": 2000}}
    ]}
  }
}
```

(Spec B adds the `brief.generate` entry.)

Semantics:

- **Arm kwargs** = control kwargs, with `model` replaced, then `params` merged over them.
  A `null` value **removes** that key. `messages`, `tools`, `tool_choice`,
  `response_format`, `timeout` and `metadata` cannot be overridden; a `params` entry for
  any of them is a config error. When the effective kwargs contain `reasoning_effort`, the
  helper adds `allowed_openai_params=["reasoning_effort"]` automatically (Phase 0
  finding 1).
- **Arm metadata** = control metadata (`service`, `operation`, `ref_id` kept) plus
  `experiment=<name>` and `arm=<arm name>`. Control metadata of a sampled item gets
  `experiment=<name>` and `arm="control"`.
- **Sampling** is deterministic. An item is sampled iff
  `int(sha256(f"{name}:{key}:{ref_id}").hexdigest()[:8], 16) / 2**32 < sample_rate`.
  Items with `ref_id is None` are **never** sampled. Every arm runs on every sampled item.
- **`until`** (UTC): no new items are sampled after it.
- **`max_daily_cost_usd`**: the shadow cost today is
  `SUM(llm_calls.cost_usd) WHERE experiment=<name> AND arm <> 'control' AND created_at >= <today 00:00 UTC>`.
  It comes from a per-process cache refreshed at most every 60 s. At or over the cap, the
  item is skipped. Overshoot by in-flight jobs and cache lag (a few cents) is acceptable.
- **Order of the decision, made BEFORE the control call:**
  1. Experiment enabled and `key` configured → else counter `not_configured`, no-op.
  2. `ref_id` not None → else `no_ref_id`.
  3. Now ≤ `until` → else `skipped_until`.
  4. Hash sampled → else `not_sampled`.
  5. Budget under cap → else `skipped_budget`.
  6. Only then is the item `sampled`, and control metadata gets tagged.

  An item skipped at any step leaves control metadata untagged, so `llm_calls` never has
  orphan `arm='control'` rows from skips. (A queue-full drop after the control call can
  still leave a tagged control `llm_calls` row with no shadow rows; spec C pairs on
  `llm_shadow_results`, so that's harmless.)

### Public API of `aggregator_common/experiment.py` (spec B uses it too)

```python
def init(settings: Settings) -> None        # parse + validate LLM_EXPERIMENT once; logs the one ERROR on invalid config; idempotent
def shutdown() -> None                      # drop queued items, log final counters; idempotent
def decide(key: str, ref_id: str | None) -> Decision | None   # steps 1-6 above; None = not sampled
# Decision: experiment name, key, ref_id, arms, control_metadata(base_metadata) -> dict
def build_arm_kwargs(control_kwargs: dict, decision: Decision, arm: Arm) -> dict   # override + metadata + allowed_openai_params rules
def prompt_hash(messages: list[dict]) -> str
def write_item(rows: list[ShadowRow], *, truncate_raw: bool = True) -> None
    # one transaction for the control row + all arm rows, ON CONFLICT DO NOTHING; never raises (logs)
def submit_shadow(decision: Decision | None, control_kwargs: dict, control_outcome: CallOutcome,
                  attempt: Callable[[dict], CallOutcome]) -> None   # enqueue onto the executor; no-op if decision is None
```

The function names are binding, since spec B is built against them. Internals are up to
the implementer.

- `Arm` = `name, model, params`.
- `ShadowRow` = the `llm_shadow_results` columns minus `id`/`created_at`.
- For summarize-rank, `CallOutcome.value` is the `RankResult` and `output` is
  `RankResult.model_dump(mode="json")`, taken **before** `_filter_categories` on both
  control and arms, so both sides are compared unfiltered.

**Startup/shutdown wiring:** each service entrypoint calls `experiment.init(settings)`
after `configure_logging` (summarize-rank `__main__.py`/`main.py`, clusterer `__main__.py`).
The existing SIGTERM handlers (`aggregator_summarize_rank/loop.py:46`,
`aggregator_clusterer/worker.py:465`) call `experiment.shutdown()`. With `init` never
called (e.g. in tests), `decide` returns None.

## Shadow execution contract

Each call site factors its existing "completion + parse (+ retries)" logic into one
function. **The same function** then runs the control and every arm, which guarantees an
identical parse and retry policy:

```python
@dataclass
class CallOutcome:
    status: str            # ok | parse_error | api_error | timeout | refusal
    value: Any             # the call site's parsed result (RankResult, ClassificationResult, bool, list[str]) or None
    output: dict | list | None   # JSON-serialisable form of value, for storage
    raw_output: str | None
    attempts: int
    request_ids: list[str]
    error: str | None
    exception: BaseException | None = None   # the original exception, never stored; lets callers re-raise unchanged
    response: Any = None                     # the last litellm response, never stored; lets rank() build its usage dict (ranker.py:79-97)

def attempt(call_kwargs: dict) -> CallOutcome: ...   # per call site; never raises
```

Call-site flow:

1. `decision = experiment.decide(key, ref_id)`. If it isn't None,
   `control_kwargs["metadata"] = decision.control_metadata(control_kwargs["metadata"])`.
2. `outcome = attempt(control_kwargs)`.
3. `experiment.submit_shadow(decision, control_kwargs, outcome, attempt)` returns
   immediately.
4. The call site continues exactly as today from `outcome`. Production return values,
   exception **types and messages**, and side effects are unchanged:
   - summarize-rank re-raises `outcome.exception` unchanged for `api_error`/`timeout`, and
     raises the same `RankError(...)` as today only for `parse_error`;
   - classify returns `_error_result()` in the cases where it does today;
   - merge and facts fail open to `False`/`None` as today.

**Retries:** summarize-rank's "retry once on malformed output" lives *inside* its
`attempt`. The shadow therefore fires once per item, after the control finishes, and each
arm gets its own up-to-2-attempt loop. `attempts` records the count.

**Status mapping** (applies to control and arms alike):

- `refusal`: `choices[0].message.refusal` is non-empty, or `finish_reason == "content_filter"`.
- `timeout`: a litellm/OpenAI timeout exception.
- `api_error`: any other exception from the completion call.
- `parse_error`: the call returned but the call site's parser/validator rejected the
  output on its final attempt.
- `ok`: anything else.

Merge and facts swallow exceptions today. Their `attempt` must classify the outcome
*before* the call site applies its fail-open fallback.

For classify, `parse_error` covers exactly the post-call cases that return
`_error_result()` today: empty content, invalid JSON, a missing or invalid label. An
exception from the completion call is `api_error`/`timeout`. A `thread_id` outside the
presented candidates keeps being coerced to `None`, as today, and counts as `ok`.

**Background executor:**

- One per process. 2 worker threads, bounded queue of 50 **items**. An item is one job
  holding the captured control kwargs, the control outcome, the `attempt` callable and the
  arm list.
- The worker runs each arm in order, then writes the `control` row and all arm rows in
  **one transaction** (`write_item`). An item is therefore recorded completely or not at
  all: a crash, SIGTERM or DB error mid-item leaves no rows. When the queue is full, the
  whole item is dropped and counted (`dropped_queue_full`).
- Per-process counters (the decision counters above, plus `enqueued`,
  `dropped_queue_full`, `written`, `write_failed`) are logged at INFO at most every 10
  minutes and at shutdown.
- `attempt` functions run on worker threads, so they must not touch the caller's DB
  session. Everything they need (e.g. classify's `presented_ids`) is captured in the
  closure at enqueue time.
- Shadow writes use a fresh session per item.
- On SIGTERM, queued items are dropped, not awaited.

**Non-interference (hard requirements):**

- Nothing in `submit_shadow` or the executor raises into the caller.
- The caller does no DB I/O or network I/O on behalf of the shadow, apart from the
  cached budget check.
- Shadow work never writes `articles`, `threads`, `thread_memberships`,
  `thread_merge_verdicts`, `cluster_state` or any other production table. It writes only
  `llm_calls` (through the existing telemetry callback) and `llm_shadow_results`.
- Merge-verdict cache hits make no control call, so they make no shadow either.

## Call-site changes

| Key | Change | `ref_id` |
|---|---|---|
| `summarize_rank.rank` | `rank` (`ranker.py:49`) splits into `attempt` (completion + `_parse_rank_result` + one retry) and the existing caller logic | `str(article_id)` (exists) |
| `clusterer.classify` | The completion + JSON parse + label/`thread_id`/`presented_ids` validation in `classify_article` (`classification.py:197-296`) becomes a reusable `attempt`. `output` stores label, thread_id, confidence, new_facts, reason, thread_title | `str(article.id)` (exists) |
| `clusterer.merge` | `_make_llm_merge_fn` (`worker.py:81`) builds an `attempt`; `output` = `{"same_story": bool, "reason": str}` | **new:** `f"{min(a, b)}:{max(a, b)}"` of the two thread ids (orientation-independent) |
| `clusterer.consolidate_facts` | `_make_llm_facts_fn` (`worker.py:31`): the returned callable's signature becomes `(thread_id: int, old_facts: list[str]) -> list[str] \| None`, and `run_facts_consolidation_pass` / `run_consolidation_pass` (`consolidate.py:463`, `:597`) pass the thread id | **new:** `str(thread_id)` |

The new `ref_id`s are also passed in production `metadata`, so `llm_calls.ref_id` gets
filled for merge and facts calls.

## Storage (one Alembic migration; down_revision = head at implementation time, currently `c6d7e8f9a0b1`)

**`llm_calls`** gets new nullable columns:

- `experiment TEXT`
- `arm TEXT`: `control`, an arm name, or NULL when not sampled
- `reasoning_effort TEXT`: from the call kwargs
- `reasoning_tokens INTEGER`: from `usage.completion_tokens_details.reasoning_tokens`,
  filled for **every** call

`LlmTelemetryLogger` fills them on both success and failure rows:

- `experiment`/`arm` from `litellm_params.metadata`, like `service`;
- `reasoning_effort` from `kwargs["optional_params"]`, falling back to top-level `kwargs`,
  because litellm puts provider params under `optional_params` in callback kwargs;
- `reasoning_tokens` from `usage.completion_tokens_details.reasoning_tokens`, handling
  both object and dict forms like the existing `cached_tokens` code.

Add an index on `(experiment, arm, service, operation, ref_id)`. Spec C joins on it.

**New table `llm_shadow_results`:**

| Column | Type | Notes |
|---|---|---|
| `id` | bigserial PK | |
| `created_at` | timestamptz not null default now() | |
| `experiment`, `service`, `operation`, `ref_id`, `arm`, `model` | text not null | |
| `params` | jsonb not null | effective overrides sent (`{}` for control) |
| `prompt_hash` | text not null | sha256 of `json.dumps(messages, sort_keys=True)`; the same for every arm of an item |
| `status` | text not null | see status mapping |
| `attempts` | int not null | |
| `output` | jsonb null | |
| `raw_output` | text null | truncated to 8 KB unless `write_item(truncate_raw=False)` |
| `error` | text null | truncated to 2 KB |
| `request_ids` | jsonb null | joinable to `llm_calls.request_id` |

- Unique `(experiment, service, operation, ref_id, arm)`. Inserts use
  `ON CONFLICT … DO NOTHING`, so **only the first sampled occurrence of an item is
  recorded**. Re-ranks, reclusters, repeated condensations and re-evaluated merge pairs
  don't add rows. An insert conflict is not an error.
- Index on `(experiment, service, operation)`.
- No janitor retention. The cutover spec drops the table.

## Files to Add/Modify

| File | Change |
|---|---|
| `packages/aggregator-common/src/aggregator_common/experiment.py` | new: config parsing/validation, sampling, budget guard, `CallOutcome`, `submit_shadow`, executor, row writer, counters |
| `packages/aggregator-common/src/aggregator_common/config.py` | `llm_experiment: str \| None` |
| `packages/aggregator-common/src/aggregator_common/models.py` | `LlmCall` columns; `LlmShadowResult` |
| `packages/aggregator-common/src/aggregator_common/llm_telemetry.py` | record experiment/arm/reasoning_effort/reasoning_tokens |
| `packages/aggregator-common/src/aggregator_common/migrations/versions/<new>.py` | migration |
| `packages/aggregator-summarize-rank/src/aggregator_summarize_rank/ranker.py` | `attempt` split + `submit_shadow` |
| `packages/aggregator-clusterer/src/aggregator_clusterer/classification.py` | `attempt` split + `submit_shadow` |
| `packages/aggregator-clusterer/src/aggregator_clusterer/worker.py` | merge/facts `attempt`s, `ref_id`s, facts fn signature |
| `packages/aggregator-clusterer/src/aggregator_clusterer/consolidate.py` | pass `thread_id` to the facts fn |
| `packages/aggregator-summarize-rank/src/aggregator_summarize_rank/{__main__,main,loop}.py`, `packages/aggregator-clusterer/src/aggregator_clusterer/{__main__,worker}.py` | `experiment.init` at startup, `experiment.shutdown` on SIGTERM |
| clusterer tests that build a one-argument facts fn (e.g. `tests/test_facts_consolidation.py`, `tests/test_consolidate.py`) | update to the `(thread_id, old_facts)` signature. This is an allowed edit to existing tests |
| `.env.example`, `CLAUDE.md` | document `LLM_EXPERIMENT`, the new columns/table, and the shadow contract |

## Implementation order (one developer bead each)

1. **Storage + telemetry:** migration, `LlmCall` columns, `LlmShadowResult`, telemetry
   fields (AC 10, 12).
2. **`experiment.py`:** config/validation, `decide`, sampling, budget, `build_arm_kwargs`,
   `prompt_hash`, `write_item`, executor, `submit_shadow`, counters, `init`/`shutdown`
   (AC 5–8, 13, 14). Depends on 1.
3. **summarize-rank integration** + entrypoint wiring (AC 1–4 for rank). Depends on 2.
4. **clusterer classify integration** + clusterer entrypoint wiring (AC 1–3, 9 for
   classify). Depends on 2.
5. **clusterer merge + facts integration**, `ref_id`s, facts signature change in
   `worker.py`/`consolidate.py` and tests (AC 1–3, 9, 11 for merge/facts). Depends on 2.

## Acceptance Criteria

1. With `LLM_EXPERIMENT` unset, all existing tests pass; the only edits allowed are the
   facts-fn signature updates listed above. A test asserts that no items are enqueued and
   no `llm_shadow_results` rows are written for each of the four operations.
2. For each of the four operations (litellm mocked), with an experiment configured: the
   caller's return value, or exception type and message, is identical to the
   unconfigured case for the same mocked response. This includes a summarize-rank API
   error propagating as the original exception, not as `RankError`. Exactly one `control` row and one row per arm are written, all with
   the same `prompt_hash`. Arm kwargs reflect the overrides: `model` replaced, `null`
   removing `max_tokens`/`temperature`, `max_completion_tokens` set, and
   `allowed_openai_params=["reasoning_effort"]` present whenever `reasoning_effort` is set.
3. For each status (`ok`, `parse_error`, `api_error`, `timeout`, `refusal`), a test shows
   the right status on both control and arm rows. A failing arm doesn't change the caller's
   result, and the caller returns before a deliberately slow arm finishes.
4. Summarize-rank: when the control's first attempt is malformed and its second succeeds,
   the control row has `attempts=2`, and exactly one shadow item is enqueued (not two).
5. A full queue drops whole items (no partial rows) without blocking. The budget cap and
   `until` stop new items. Counters reflect each case.
6. Sampling is deterministic for the same `(name, key, ref_id)`. The observed rate over
   10,000 synthetic ids is within ±2 pp of `sample_rate`. `ref_id=None` is never sampled.
7. Re-submitting the same `(experiment, key, ref_id)` writes no extra rows and raises
   nothing.
8. Invalid JSON, an unknown field (top-level, operation-level or arm-level), an unknown
   operation key, or a forbidden `params` key logs exactly one ERROR, and the service
   starts with the experiment disabled.
9. A test shows that a shadow run for classify, merge and facts leaves `threads`,
   `thread_memberships` and `thread_merge_verdicts` unchanged.
10. `llm_calls` rows carry `experiment`/`arm`/`reasoning_effort` when set, and
    `reasoning_tokens` whenever usage reports it. This is verified through litellm's real
    callback path (`litellm.completion(..., mock_response=...)` with the telemetry logger
    registered), not with a hand-built callback kwargs dict.
11. Merge and facts production calls now carry `ref_id` in `llm_calls`.
12. The migration upgrades and downgrades cleanly on testcontainers Postgres.
13. A crash injected between arm calls and `write_item` leaves no rows for that item.
14. A skip at any decision step (`until`, budget, not sampled) leaves the control call's
    metadata untagged.

## Deploy notes (operator, with A + B)

- Set `LLM_EXPERIMENT` (A's JSON plus B's `brief.generate` entry) in
  `/opt/personal-aggregator/.env`.
- **Raise `JANITOR_ARTICLE_RETENTION_DAYS` to 35** for the experiment window. The janitor
  deletes unsaved articles after 14 days, which would delete most early-window articles
  before the report runs and skew spec C's engagement metrics. Restore it in the cutover
  spec.

## Out of scope

- Switching production models, changing `*_LLM_MODEL` defaults, `docker-compose.yml`
  pins or `.env.example` model advice (cutover spec).
- The brief (spec B); the report and `llm-stats` (spec C).
- Upgrading litellm; prompt variants as arms; the Decisions API; non-Luna challengers;
  splitting classify; the podcast service.
