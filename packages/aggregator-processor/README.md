# aggregator-processor

Cleans and enriches articles that the retriever has persisted, then marks them ready for the summarize-rank service. It is the second stage in the pipeline:

```
retriever → processor → summarize-rank → web
```

The processor claims articles in `pending_processing` status, extracts clean text and metadata, selects a header image, writes a full-text search vector, and transitions each article to `pending_ranking` (success) or `skipped`/`failed_processing` (error paths).

## Role in the pipeline

| Input status | Output status | Trigger |
|---|---|---|
| `pending_processing` | `pending_ranking` | Successful extraction |
| `pending_processing` | `skipped` | Insufficient content after all strategies |
| `pending_processing` | `failed_processing` | Unhandled exception (retried up to `PROCESSOR_MAX_RETRIES` times) |

Stale claims older than `CLAIM_LEASE_SECONDS` are reaped back to `pending_processing` on every poll cycle so a crashed worker never strands an article.

## Configuration

All variables can be set as environment variables or in a `.env` file in the working directory. `PROCESSOR_*` variables are processor-specific; shared variables are defined in `aggregator-common`.

### Shared (`aggregator-common`)

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | *(required)* | PostgreSQL DSN |
| `CLAIM_LEASE_SECONDS` | `600` | Seconds before a stale claim is reaped |
| `LOG_LEVEL` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

### Processor-specific

| Variable | Default | Description |
|---|---|---|
| `PROCESSOR_POLL_INTERVAL_SECONDS` | `5` | Seconds to sleep between poll cycles when the queue is empty |
| `PROCESSOR_MAX_WORKERS` | `4` | Thread-pool size; each thread processes one article concurrently |
| `PROCESSOR_BATCH_SIZE` | `20` | Articles claimed per poll cycle |
| `PROCESSOR_HTTP_TIMEOUT_SECONDS` | `30` | Timeout for full-page HTTP fetches |
| `PROCESSOR_MAX_PAGE_BYTES` | `5242880` | Maximum response body size for a page fetch (5 MiB); larger responses raise `FetchError` |
| `PROCESSOR_USER_AGENT` | `personal-aggregator/0.1 (processor)` | `User-Agent` header sent on page fetches |
| `PROCESSOR_FEED_CONTENT_MIN_CHARS` | `1500` | If the feed entry body is at least this many characters, skip the page fetch entirely |
| `PROCESSOR_MIN_CONTENT_CHARS` | `200` | Articles whose final extracted text is shorter than this (and whose feed candidate is also shorter) are marked `skipped` |
| `PROCESSOR_MAX_RETRIES` | `3` | Maximum retry attempts before an article is permanently `failed_processing` |
| `PROCESSOR_BACKOFF_BASE_SECONDS` | `30` | Base delay for exponential retry backoff: `backoff * 2^(retry_count-1)` seconds |

## Content extraction strategy

The processor uses a feed-first, page-fetch-fallback approach to minimise unnecessary HTTP traffic:

1. **Feed content check** — Inspect the richest feed body available (`content[0].value` → `summary`).
   - If its length is ≥ `PROCESSOR_FEED_CONTENT_MIN_CHARS` (default 1 500 chars), extract directly from the feed HTML without any page fetch.
2. **Page fetch** — If the feed body is too short, fetch the article URL with `httpx` (streaming, size-limited to `PROCESSOR_MAX_PAGE_BYTES`).
   - If the page extraction yields *less* text than the feed candidate, fall back to the feed candidate.
   - If the fetch fails (`FetchError`) but a feed candidate exists, fall back to the feed candidate.
3. **Insufficient content** — If the final extracted text is shorter than `PROCESSOR_MIN_CONTENT_CHARS` *and* the raw feed candidate is also shorter, the article is marked `skipped` with an explanatory `last_error`.

Content is extracted with `trafilatura`. Missing fields (title, author, publish date, excerpt) fall back to values stored in `raw_payload` by the retriever.

## Header image selection

`image.select_header_image()` walks the following priority chain and returns the first valid `http(s)` URL:

1. `og:image` meta tag in page HTML
2. `twitter:image` meta tag in page HTML
3. Feed media: `media:content` / `media:thumbnail` / image enclosure (feedparser normalises these)
4. First `<img src>` found in page HTML
5. First `<img src>` found in feed `content` or `summary` HTML
6. `source.default_image_url` (configured per-source in the `sources` table)
7. `None` (no image)

Steps 1, 2, and 4 are skipped when no page fetch was performed.

## Search vector

After successful extraction, `search.update_search_vector()` writes a weighted `tsvector` to `articles.search_vector`:

- Title — weight **A** (highest)
- Body — weight **B**

The vector is set via a raw SQL `UPDATE` using `to_tsvector('english', ...)` and is indexed by a GIN index for full-text search in the web service.

## Dependencies on aggregator-common

| Symbol | Source | Usage |
|---|---|---|
| `claim_batch` | `aggregator_common.claim` | Atomically claim a batch of `pending_processing` articles with `SELECT … FOR UPDATE SKIP LOCKED` |
| `reap_stale_claims` | `aggregator_common.claim` | Return stale claims to `pending_processing` at the start of every poll cycle |
| `complete` | `aggregator_common.claim` | Transition an article to a terminal/next status after processing |
| `fail` | `aggregator_common.claim` | Record an error, increment `retry_count`, and apply exponential backoff |
| `ArticleStatus` | `aggregator_common.state` | Enum values used when claiming and transitioning articles |
| `Article`, `Source` | `aggregator_common.models` | ORM models for the `articles` and `sources` tables |
| `SessionFactory` | `aggregator_common.db` | SQLAlchemy session factory bound to `DATABASE_URL` |
| `ProcessorSettings` (base) | `aggregator_common.config` | Base pydantic-settings class providing shared config fields |

## Running locally

Start Postgres first (see the repo-root `docker-compose.yml`), then set `DATABASE_URL`:

```bash
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/aggregator
```

**Daemon mode** (polls continuously, handles SIGINT/SIGTERM gracefully):

```bash
uv run aggregator-processor
```

**One-shot mode** (claims one batch, processes it, prints a summary, exits):

```bash
uv run aggregator-processor --once
```

Example one-shot output:

```
run_once: processed=12 failed=0 skipped=1
```

Override settings inline:

```bash
PROCESSOR_BATCH_SIZE=5 PROCESSOR_MAX_WORKERS=2 uv run aggregator-processor --once
```
