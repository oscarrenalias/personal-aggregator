# aggregator-summarize-rank

Summarizes and ranks articles that the processor has cleaned, then marks them ready for the web UI. It is the third stage in the pipeline:

```
retriever → processor → summarize-rank → web
```

The service claims articles in `pending_ranking` status, sends each article to an LLM (via **litellm**) with the user's interest profile, and stores the resulting summary, topics list, importance score, and reason. Each article is then transitioned to `ready` (success) or `skipped`/`failed_ranking` (error paths).

## Role in the pipeline

| Input status | Output status | Trigger |
|---|---|---|
| `pending_ranking` | `ready` | Successful LLM ranking |
| `pending_ranking` | `skipped` | Article content shorter than `SUMMARIZE_RANK_MIN_CONTENT_CHARS` |
| `pending_ranking` | `failed_ranking` | LLM error (retried up to `SUMMARIZE_RANK_MAX_RETRIES` times) |

Stale claims older than `CLAIM_LEASE_SECONDS` are reaped back to `pending_ranking` on every poll cycle so a crashed worker never strands an article.

## LLM output

The LLM returns a structured JSON object validated against `RankResult`:

| Field | Type | Description |
|---|---|---|
| `summary` | `str` | Short summary of the article |
| `topics` | `list[str]` | Up to 5 topic labels |
| `importance_score` | `int` | Relevance score 0–100 relative to the user's interest profile |
| `importance_reason` | `str` | One-sentence explanation of the score |

These fields are written to the corresponding `articles` columns (`summary`, `topics`, `importance_score`, `importance_reason`) along with LLM usage metadata in `llm_meta`.

## Configuration

All variables can be set as environment variables or in a `.env` file in the working directory. `SUMMARIZE_RANK_*` variables are service-specific; shared variables are defined in `aggregator-common`.

### Shared (`aggregator-common`)

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | *(required)* | PostgreSQL DSN |
| `CLAIM_LEASE_SECONDS` | `600` | Seconds before a stale claim is reaped |
| `LOG_LEVEL` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

### Service-specific

| Variable | Default | Description |
|---|---|---|
| `LLM_MODEL` | `claude-sonnet-4-6` | litellm model identifier (e.g. `claude-sonnet-4-6`, `gpt-4o`) |
| `OPENAI_API_KEY` | *(empty)* | OpenAI API key (required if using an OpenAI model) |
| `ANTHROPIC_API_KEY` | *(empty)* | Anthropic API key (required if using a Claude model) |
| `LLM_MAX_INPUT_CHARS` | `32000` | Maximum characters of article content sent to the LLM |
| `LLM_MAX_OUTPUT_TOKENS` | `1024` | Maximum tokens in the LLM response |
| `LLM_TEMPERATURE` | `0.3` | LLM sampling temperature |
| `LLM_TIMEOUT_SECONDS` | `60` | LLM API call timeout in seconds |
| `SUMMARIZE_RANK_POLL_INTERVAL_SECONDS` | `5` | Seconds to sleep between poll cycles when the queue is empty |
| `SUMMARIZE_RANK_MAX_WORKERS` | `4` | Thread-pool size; each thread processes one article concurrently |
| `SUMMARIZE_RANK_BATCH_SIZE` | `10` | Articles claimed per poll cycle |
| `SUMMARIZE_RANK_MAX_RETRIES` | `3` | Maximum retry attempts before an article is permanently `failed_ranking` |
| `SUMMARIZE_RANK_BACKOFF_BASE_SECONDS` | `30` | Base delay for exponential retry backoff: `backoff * 2^(retry_count-1)` seconds |
| `SUMMARIZE_RANK_MIN_CONTENT_CHARS` | `200` | Minimum content length; articles shorter than this are marked `skipped` without calling the LLM |

## Running locally

Start Postgres first (see the repo-root `docker-compose.yml`), then set `DATABASE_URL` and the appropriate LLM API key:

```bash
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/aggregator
export ANTHROPIC_API_KEY=sk-ant-...
```

**Daemon mode** (polls continuously, handles SIGINT/SIGTERM gracefully):

```bash
uv run aggregator-summarize-rank
```

**One-shot mode** (claims one batch, processes it, prints a summary, exits):

```bash
uv run aggregator-summarize-rank --once
```

Example one-shot output:

```
run_once: ranked=8 failed=0 skipped=1
```

Override settings inline:

```bash
SUMMARIZE_RANK_BATCH_SIZE=5 LLM_MODEL=gpt-4o uv run aggregator-summarize-rank --once
```

## Dependencies on aggregator-common

| Symbol | Source | Usage |
|---|---|---|
| `claim_batch` | `aggregator_common.claim` | Atomically claim a batch of `pending_ranking` articles with `SELECT … FOR UPDATE SKIP LOCKED` |
| `reap_stale_claims` | `aggregator_common.claim` | Return stale claims to `pending_ranking` at the start of every poll cycle |
| `complete` | `aggregator_common.claim` | Transition an article to `ready` or `skipped` after processing |
| `fail` | `aggregator_common.claim` | Record an error, increment `retry_count`, and apply exponential backoff |
| `ArticleStatus` | `aggregator_common.state` | Enum values used when claiming and transitioning articles |
| `Article`, `Source`, `InterestProfile` | `aggregator_common.models` | ORM models for the pipeline tables |
| `SessionFactory` | `aggregator_common.db` | SQLAlchemy session factory bound to `DATABASE_URL` |
| `SummarizeRankSettings` (base) | `aggregator_common.config` | Base pydantic-settings class providing shared config fields |

## Testing

Tests use **testcontainers** to spin up an ephemeral Postgres on a random port, so no shared database is assumed. LLM calls are **mocked** — `litellm.completion` is patched at the `litellm` module level. No live LLM API key is required to run the test suite.

```bash
uv run pytest packages/aggregator-summarize-rank/
```
