# aggregator-retriever

Polls RSS/Atom feeds on a configurable schedule, persists raw articles to Postgres, and marks them `pending_processing` for the next stage in the pipeline.

## Running

```bash
uv run aggregator-retriever
```

Reads configuration from environment variables or a `.env` file in the working directory. Requires `DATABASE_URL` to be set. Runs until `SIGINT` or `SIGTERM`.

## Configuration

All variables are optional except `DATABASE_URL` (inherited from `aggregator-common`).

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | *(required)* | PostgreSQL DSN |
| `RETRIEVER_POLL_INTERVAL_SECONDS` | `60` | Seconds between feed-poll cycles |
| `RETRIEVER_MAX_WORKERS` | `8` | Maximum concurrent feed-fetch workers |
| `RETRIEVER_HTTP_TIMEOUT_SECONDS` | `30` | Per-request HTTP timeout in seconds |
| `RETRIEVER_MAX_FEED_BYTES` | `10485760` | Maximum feed response size in bytes (10 MiB) |
| `RETRIEVER_USER_AGENT` | `personal-aggregator/0.1 (feed retriever)` | User-Agent sent with feed requests |
| `RETRIEVER_MAX_SOURCE_FAILURES` | `20` | Consecutive failures before a source is auto-disabled |
| `RETRIEVER_BACKOFF_BASE_SECONDS` | `60` | Base delay for per-source exponential backoff |
| `RETRIEVER_BACKOFF_CAP_SECONDS` | `21600` | Maximum per-source backoff delay in seconds (6 h) |
| `CLAIM_LEASE_SECONDS` | `600` | Work-claim lease duration (inherited from common) |
| `LOG_LEVEL` | `INFO` | Log level |

## Module layout

```
src/aggregator_retriever/
  config.py     Settings — extends aggregator-common Settings with retriever-specific vars above.
  http.py       HTTP fetching via httpx. Sends conditional GET (ETag / If-Modified-Since),
                streams response with size cap, returns FetchResult or raises FetchError.
  normalize.py  URL normalization (strips utm_*, fbclid, default ports, trailing slash) and
                dedup_key computation. Also serializes feedparser entries to JSON-safe dicts.
  parse.py      Wraps feedparser. Returns a list of NormalizedEntry dataclasses, one per feed entry.
                Entries with no derivable dedup_key are logged and skipped.
  persist.py    Article upsert (INSERT … ON CONFLICT DO NOTHING) and source metadata updates
                (success resets failures; failure increments counter, schedules backoff, and
                disables the source when the failure threshold is reached).
  loop.py       Main polling loop. Uses ThreadPoolExecutor to fetch sources concurrently.
                Tracks in-flight source IDs to avoid double-scheduling. Handles SIGINT/SIGTERM
                gracefully by draining in-flight tasks before exiting.
```

## dedup_key precedence

Each feed entry is assigned a stable dedup key to prevent duplicate articles. The key is derived in order:

1. **`entry.id`** — if feedparser provides a non-empty `id` field, use it as-is.
2. **Normalized link** — if no `id`, normalize `entry.link` (lowercase scheme/host, strip default port, sort and strip tracking query params, strip trailing slash) and use the result.
3. **SHA-256 hash** — if neither `id` nor `link` is present, compute `sha256("{source_id}\n{title}\n{published}")`.
4. **Skip** — if none of the above yield a value, the entry is logged at WARNING and dropped.

## Backoff formula

When a feed fetch fails, the source's `next_check_at` is pushed forward by:

```
delay = min(cap, base × 2^(n-1)) × jitter
```

where:
- `n` = `consecutive_failures` after incrementing
- `base` = `RETRIEVER_BACKOFF_BASE_SECONDS` (default 60 s)
- `cap` = `RETRIEVER_BACKOFF_CAP_SECONDS` (default 21 600 s / 6 h)
- `jitter` = uniform random in `[0.9, 1.1]`

When `consecutive_failures` reaches `RETRIEVER_MAX_SOURCE_FAILURES` (default 20), the source is set `enabled = false` and stops being polled.

## Adding a source for local testing

Insert a row directly into the `sources` table. Only `name` and `feed_url` are required; everything else has sensible defaults.

```sql
INSERT INTO sources (name, feed_url)
VALUES ('Example Blog', 'https://example.com/feed.xml');
```

Optional columns:

| Column | Default | Description |
|---|---|---|
| `enabled` | `true` | Set to `false` to pause polling without deleting the row |
| `refresh_interval_seconds` | `3600` | How often to re-poll this source (seconds) |
| `priority` | `0` | Higher priority sources are scheduled first each cycle |
| `default_image_url` | `NULL` | Fallback image used by the web UI when the article has none |

The retriever picks up new sources on the next poll cycle (within `RETRIEVER_POLL_INTERVAL_SECONDS`).
