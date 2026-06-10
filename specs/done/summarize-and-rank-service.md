---
name: Summarize and Rank Service
id: spec-5d47fa68
description: "aggregator-summarize-rank service: claims pending_ranking, calls an LLM via litellm (provider-agnostic, default gpt-4.1-mini, env-configurable) with provider-native structured output to produce summary/topics/importance score+reason against the interest profile; minimal I/O for cost; transitions to ready/failed_ranking/skipped. Daemon + thread pool + --once. Depends on foundation."
dependencies: null
priority: high
complexity: null
status: done
tags:
- summarize-rank
- service
- llm
- litellm
- depends-foundation
scope:
  in: null
  out: null
feature_root_id: B-925acd8d
---
# Summarize and Rank Service

## Objective

Implement the `aggregator-summarize-rank` service: a long-running worker that claims `pending_ranking` articles, calls an LLM to produce a concise **summary**, **key topics**, an **importance score (0–100)**, and a short **importance reason** scored against the user's interest profile, persists those, and transitions each article `pending_ranking → ready` (or `failed_ranking` / `skipped`).

This is the **only** service that calls an LLM. It is provider-agnostic (OpenAI now, Claude later) via a single configurable model.

## Dependencies

Depends on the **foundation** (`spec-9e974b88`): `aggregator_common` models (`articles`, `interest_profile`), the `article_status` enum + state machine, and the claim/reaper helpers (`claim_batch`, `complete`, `fail`, `reap_stale_claims`). Reuses the daemon/claim pattern established by the processor (`spec-09a0a914`).

**`articles` fields read:** `clean_title` (fallback `feed_title`), `clean_text` (fallback `excerpt`/`feed_summary`), `source_id`. **Fields written:** `summary`, `topics` (jsonb array), `importance_score` (int 0–100), `importance_reason`, `llm_meta` (jsonb), `summarized_at`, plus status/claim/error via the claim helpers. **`interest_profile.profile_text`** is read each cycle (cached briefly).

## Background / Decisions

Already decided (see `CLAUDE.md` and these answers); implement to them.

- **LLM access via `litellm`** — a single `litellm.completion(...)` call. The model string selects the provider (`gpt-4.1-mini` → OpenAI; `claude-…`/`anthropic/…` → Anthropic later). litellm reads `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` from the environment.
- **Model is env-configurable** via `LLM_MODEL` (default `gpt-4.1-mini`).
- **Provider-native structured output** — pass a Pydantic `RankResult` as litellm `response_format`, which maps to OpenAI JSON-schema structured outputs / Anthropic tool use, guaranteeing parseable JSON.
- **Minimize cost** — send only the minimum needed (title + source name + truncated cleaned text + interest profile), cap output tokens, low temperature.
- **Execution:** daemon claiming `pending_ranking` batches via `claim_batch`, `ThreadPoolExecutor`, runs `reap_stale_claims` each cycle, supports `--once`. Mirrors the processor.

## Changes

### 1. Package + entrypoint

```
packages/aggregator-summarize-rank/
  pyproject.toml                 # deps: litellm, pydantic, aggregator-common
  src/aggregator_summarize_rank/
    __init__.py
    config.py                    # settings (LLM + worker)
    schema.py                    # Pydantic RankResult (the structured-output contract)
    prompt.py                    # builds the bounded LLM input from article + interest profile
    ranker.py                    # litellm call + response parsing/validation -> RankResult
    rank.py                      # per-article pipeline (build input -> rank -> persist -> transition)
    loop.py                      # claim/reap daemon, thread pool, signals; run() + run_once()
    __main__.py                  # entrypoint (default daemon; --once)
  tests/
```

Console script `aggregator-summarize-rank`: no args = daemon; `--once` = one batch then exit. Calls `configure_logging(settings, stream=sys.stdout)` at startup (per the logging convention).

### 2. Configuration (`config.py`)

Subclass `aggregator_common` Settings; add:

| Var | Default | Meaning |
|---|---|---|
| `LLM_MODEL` | `gpt-4.1-mini` | litellm model string (provider implied) |
| `OPENAI_API_KEY` | *(env)* | read by litellm; required for OpenAI models |
| `ANTHROPIC_API_KEY` | *(env)* | read by litellm; required for Claude models |
| `LLM_MAX_INPUT_CHARS` | 6000 | cleaned-text truncation budget sent to the model |
| `LLM_MAX_OUTPUT_TOKENS` | 400 | cap on completion size |
| `LLM_TEMPERATURE` | 0.2 | low, for stable scoring |
| `LLM_TIMEOUT_SECONDS` | 60 | per-call timeout |
| `SUMMARIZE_RANK_POLL_INTERVAL_SECONDS` | 5 | idle loop cadence |
| `SUMMARIZE_RANK_MAX_WORKERS` | 4 | thread pool size (keep modest re: rate limits) |
| `SUMMARIZE_RANK_BATCH_SIZE` | 10 | `claim_batch` limit per cycle |
| `SUMMARIZE_RANK_MAX_RETRIES` | 3 | retries before `failed_ranking` |
| `SUMMARIZE_RANK_BACKOFF_BASE_SECONDS` | 30 | base for exponential backoff |
| `SUMMARIZE_RANK_MIN_CONTENT_CHARS` | 200 | below this ⇒ `skipped` |

`OPENAI_API_KEY`/`ANTHROPIC_API_KEY` are added to `.env.example` (commented).

### 3. Structured-output contract (`schema.py`)

Pydantic `RankResult`:
- `summary: str` — 1–3 sentences (instruct ≤ ~60 words).
- `topics: list[str]` — key topics, **max 5**.
- `importance_score: int` — 0–100 (validated in range).
- `importance_reason: str` — one short sentence.

This model is passed as litellm `response_format`. On a provider that returns malformed/odd output, validate with Pydantic; clamp `importance_score` into 0–100. **Topics are truncated to the first 5** (clamp-style, not a validation failure). `prompt_version` is bumped whenever the system instruction or score bands change, so persisted `llm_meta` reliably records which prompt produced a given score.

### 4. Prompt (`prompt.py`)

Build a **bounded** input:
- System instruction: summarize concisely, extract ≤5 topics, and score importance 0–100 **for this specific user** using the interest profile, with score bands per SPEC (`0–30` low, `31–60` maybe useful, `61–80` relevant, `81–100` important); consider interest match, source relevance, novelty, practical usefulness; keep the reason to one sentence.
- User content: `source name`, `clean_title`, the interest profile text (truncated to a sane cap), and `clean_text` truncated to `LLM_MAX_INPUT_CHARS` (fallback to `excerpt`/`feed_summary`). Nothing else — no raw HTML, no full payload.
- A `prompt_version` constant is recorded in `llm_meta`.

### 5. Ranker (`ranker.py`)

- `rank(article_input, interest_profile, settings) -> RankResult`: one `litellm.completion(model=settings.llm_model, messages=[...], response_format=RankResult, max_tokens=…, temperature=…, timeout=…)`.
- Parse/validate into `RankResult` (clamp score). On malformed output, retry once in-call; if still invalid, raise a typed `RankError`.
- Capture usage for `llm_meta`: model, prompt/completion tokens, and cost via `litellm.completion_cost` when available, plus `prompt_version`.

### 6. Per-article pipeline (`rank.py`)

- Build input from the claimed article; if usable text < `SUMMARIZE_RANK_MIN_CONTENT_CHARS` (after fallbacks) → `complete(session, article, skipped)` with a reason.
- Else call the ranker; on success set `summary`, `topics`, `importance_score`, `importance_reason`, `llm_meta`, `summarized_at` and `complete(session, article, ready)`.
- On `RankError` / API error (rate limit, timeout) → `fail(session, article, error, max_retries=SUMMARIZE_RANK_MAX_RETRIES, backoff=SUMMARIZE_RANK_BACKOFF_BASE_SECONDS)`; records `last_error` + `next_retry_at` each attempt; lands in `failed_ranking` after exhaustion.

### 7. Claim/reap loop (`loop.py`)

Same shape as the processor: `worker_id = f"summarize-rank-{hostname}-{pid}"`; each cycle `reap_stale_claims` then `claim_batch(status=pending_ranking, limit=SUMMARIZE_RANK_BATCH_SIZE)`; process via the pool; sleep when idle. `run_once` = one reap + one batch + summary line, exit 0. Per-article own session/transaction; SIGINT/SIGTERM graceful drain.

### 8. Interest profile

Read `interest_profile.profile_text` once per cycle (it is the singleton row). If empty, fall back to a neutral instruction: score on **general newsworthiness/usefulness to a general reader**, applying the same band thresholds (`0–30`…`81–100`) but interpreting "relevance" as relevance-to-a-general-reader rather than interest match. Note neutral mode in `llm_meta`.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-summarize-rank/pyproject.toml` | deps (litellm, pydantic, aggregator-common), console entrypoint |
| `src/aggregator_summarize_rank/config.py` | LLM + worker settings |
| `src/aggregator_summarize_rank/schema.py` | `RankResult` Pydantic model |
| `src/aggregator_summarize_rank/prompt.py` | bounded prompt builder + `prompt_version` |
| `src/aggregator_summarize_rank/ranker.py` | litellm call + validation + usage capture |
| `src/aggregator_summarize_rank/rank.py` | per-article pipeline + transitions |
| `src/aggregator_summarize_rank/loop.py` | claim/reap daemon, thread pool, signals, run_once |
| `src/aggregator_summarize_rank/__main__.py` | entrypoint (default daemon; `--once`) |
| `.env.example` | add `LLM_MODEL`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` (commented) |
| `packages/aggregator-summarize-rank/tests/**` | unit + DB tests (testcontainers + **mocked litellm**, no live API) |

## Acceptance Criteria

- A claimed `pending_ranking` article is transitioned to `ready` with `summary`, `topics` (≤5), `importance_score` (0–100), `importance_reason`, `llm_meta`, and `summarized_at` set — with the LLM call **mocked** (no network/API in tests).
- The litellm call is invoked with the configured `LLM_MODEL`, `response_format=RankResult`, `max_tokens=LLM_MAX_OUTPUT_TOKENS`, and the bounded prompt; the cleaned text in the prompt is truncated to `LLM_MAX_INPUT_CHARS` (asserted via the captured request).
- `RankResult` validation clamps an out-of-range score into 0–100 and **truncates topics to the first 5** (excess topics are not a validation failure); a genuinely malformed/unparseable model response triggers one in-call retry, then a `RankError`.
- A `RankError` / simulated API error moves the article to `failed_ranking` after `SUMMARIZE_RANK_MAX_RETRIES` (with `last_error` + `next_retry_at` set between attempts).
- An article whose usable text < `SUMMARIZE_RANK_MIN_CONTENT_CHARS` is transitioned to `skipped` (not sent to the LLM).
- The prompt incorporates `interest_profile.profile_text`; with an empty profile it still ranks (neutral mode) and notes it in `llm_meta`.
- `llm_meta` records model, token usage, and `prompt_version`.
- Provider-agnostic: switching `LLM_MODEL` to a Claude string routes through litellm with no code change (verified by asserting the model passed to a mocked litellm, not by a live call).
- Claim safety (two workers never double-rank), reaper releases stale `pending_ranking` claims, `--once` exits 0, SIGTERM drains and exits 0, per-article isolation holds.
- Full suite green via `uv run pytest` (testcontainers Postgres + mocked litellm; no live LLM calls, no network).

## Pending Decisions

- **Exact default model id** (`gpt-4.1-mini`) is env-configurable; verify against current OpenAI offerings at deploy time and adjust `.env` if the id differs.
- Token/char budgets (`LLM_MAX_INPUT_CHARS=6000`, `LLM_MAX_OUTPUT_TOKENS=400`) and `LLM_TEMPERATURE=0.2` are starting values, tunable via env.
- No response caching / batching across articles in v1 (each article = one call); revisit if cost requires it.
- Re-ranking already-`ready` articles (e.g., after an interest-profile change) is handled by the admin `articles rerank` command (`ready → pending_ranking`), not by this service automatically.
