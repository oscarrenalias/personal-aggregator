---
name: Processor Service
id: spec-09a0a914
description: "aggregator-processor service: claims pending_processing articles, extracts clean content via trafilatura (feed-first with page-fetch fallback), selects header image, populates weighted search_vector, transitions to pending_ranking/failed_processing/skipped. Daemon + thread pool + --once. Depends on foundation."
dependencies: null
priority: high
complexity: null
status: draft
tags:
- processor
- service
- extraction
- depends-foundation
scope:
  in: null
  out: null
feature_root_id: null
---
# Processor Service

## Objective

Implement the `aggregator-processor` service: a long-running worker that claims `pending_processing` articles, derives clean, display-ready content (readable text, title, author, publication date, header image, word count, language), populates the full-text `search_vector`, and transitions each article `pending_processing → pending_ranking` (or `failed_processing` / `skipped`).

This spec resolves two decisions the foundation/retriever deferred: the **full-page-fetch-vs-feed-content** strategy and the **header-image selection** priority.

## Dependencies

Depends on the **foundation** (`spec-9e974b88`): `aggregator_common` models (`articles`, `sources`), the `article_status` enum + state machine, and the claim/reaper helpers (`claim_batch`, `complete`, `fail`, `reap_stale_claims`). Reuse them — do not reimplement claiming or transitions.

**`articles` fields read** (populated by the retriever): `raw_payload`, `feed_title`, `feed_url`, `feed_summary`, `feed_published_at`, `source_id`. **Fields written** (the processed columns): `clean_title`, `clean_text`, `excerpt`, `author`, `published_at`, `header_image_url`, `word_count`, `language`, `search_vector`, `processed_at`, plus status/claim/error via the claim helpers. **`sources` field read:** `default_image_url`.

## Background / Decisions

Already decided (see `CLAUDE.md`); implement to them.

- **Content source — feed-first with page-fetch fallback:** if the feed already carries substantial content (≥ `PROCESSOR_FEED_CONTENT_MIN_CHARS` of text), extract from that and do **not** fetch the page. Otherwise fetch the article page and extract. If the page fetch or extraction fails but feed content is usable, still process using the feed content (SPEC §Processor: tolerate partial extraction). Only when nothing usable remains is the article failed/skipped.
- **Extraction:** `trafilatura` for main text + metadata (title, author, date, language).
- **Execution:** long-running daemon claiming batches via `claim_batch` (`FOR UPDATE SKIP LOCKED`), processing with a `ThreadPoolExecutor`; runs `reap_stale_claims` each cycle; supports a `--once` mode. Claim-based, so multiple instances are safe.
- **Header image:** store the remote URL only — no download/caching (SPEC §Header Image).
- **search_vector:** written explicitly by this service with field weighting (foundation contract).
- **The processor does NOT call the LLM and does NOT rank.**

## Changes

### 1. Package + entrypoint

Replace the processor stub with the implementation.

```
packages/aggregator-processor/
  pyproject.toml                 # deps: httpx, trafilatura, aggregator-common
  src/aggregator_processor/
    __init__.py
    config.py                    # processor settings
    fetch.py                     # httpx page fetch + size cap (mirror retriever http.py)
    extract.py                   # trafilatura text + metadata extraction
    image.py                     # header-image priority selection
    search.py                    # weighted search_vector update
    process.py                   # per-article pipeline (content source → extract → fields → transition)
    loop.py                      # claim/reap daemon loop, thread pool, signals; run() + run_once()
    __main__.py                  # entrypoint: argparse (default daemon; --once)
  tests/
```

Console script `aggregator-processor`: no args = daemon; `--once` = claim+process one batch and exit.

### 2. Configuration (`config.py`)

Subclass `aggregator_common` Settings (for `DATABASE_URL`, `CLAIM_LEASE_SECONDS`); add:

| Var | Default | Meaning |
|---|---|---|
| `PROCESSOR_POLL_INTERVAL_SECONDS` | 5 | loop wake cadence when idle |
| `PROCESSOR_MAX_WORKERS` | 4 | thread pool size |
| `PROCESSOR_BATCH_SIZE` | 20 | `claim_batch` limit per cycle |
| `PROCESSOR_HTTP_TIMEOUT_SECONDS` | 30 | page fetch timeout |
| `PROCESSOR_MAX_PAGE_BYTES` | 5_242_880 | page response size cap (5 MiB) |
| `PROCESSOR_USER_AGENT` | `personal-aggregator/0.1 (processor)` | request UA |
| `PROCESSOR_FEED_CONTENT_MIN_CHARS` | 1500 | feed content ≥ this (plain-text len) ⇒ skip page fetch |
| `PROCESSOR_MIN_CONTENT_CHARS` | 200 | final cleaned text < this ⇒ `skipped` |
| `PROCESSOR_MAX_RETRIES` | 3 | retries before `failed_processing` |
| `PROCESSOR_BACKOFF_BASE_SECONDS` | 30 | base for exponential backoff |

### 3. Claim/reap loop (`loop.py`)

- `worker_id` = `f"processor-{hostname}-{pid}"`.
- Each cycle: `reap_stale_claims(session, CLAIM_LEASE_SECONDS, now)`, then `claim_batch(session, status=pending_processing, worker_id, limit=PROCESSOR_BATCH_SIZE, now)`; submit each claimed article id to the pool running `process_article(id, settings)`. If a cycle claims zero, sleep `PROCESSOR_POLL_INTERVAL_SECONDS`.
- `run_once`: one reap + one `claim_batch` + process, print a summary (processed / failed / skipped counts), exit 0.
- Each `process_article` opens its **own session/transaction**; failures are isolated per article. Install SIGINT/SIGTERM handlers; on shutdown stop claiming, drain in-flight, exit 0.

### 4. Content source selection (`process.py`)

1. Derive **candidate feed content**: the richest feed-provided HTML — prefer `raw_payload` `content`/`content:encoded` if present, else `feed_summary`. Measure its plain-text length (HTML stripped).
2. If that length ≥ `PROCESSOR_FEED_CONTENT_MIN_CHARS`: extract from the feed content; **no page fetch**.
3. Else: fetch the article page (`feed_url`) via `fetch.py` and extract from the page HTML.
   - On fetch failure, oversize, or extraction yielding less text than the feed candidate: **fall back** to extracting the feed content.
4. If, after the above, the cleaned text length < `PROCESSOR_MIN_CONTENT_CHARS` and no usable feed text exists: transition to `skipped` (too short/unsupported), recording the reason.

### 5. Fetch (`fetch.py`)

Mirror the retriever's `http.py`: sync `httpx.Client`, `PROCESSOR_USER_AGENT`, timeout, redirect cap 5, stream with a byte-counted cap of `PROCESSOR_MAX_PAGE_BYTES`. Non-2xx/timeout/connection/oversize raise a typed `FetchError`. (No conditional requests here — articles are fetched at most once.)

### 6. Extraction (`extract.py`)

Using `trafilatura` on the chosen HTML:
- `clean_text` — extracted main text (plain text). 
- `clean_title` — extracted title, else `feed_title`.
- `author` — extracted author, else author from `raw_payload`, else None.
- `published_at` — extracted date, else `feed_published_at`, else None.
- `language` — trafilatura-detected language, else None.
- `excerpt` — first ~300 chars of `clean_text` (trimmed at a word boundary), else `feed_summary`.
- `word_count` — word count of `clean_text`.

### 7. Header image (`image.py`)

Select the first available, store the URL only (no download):
1. Open Graph `og:image` — from page HTML (only when the page was fetched).
2. Twitter card `twitter:image` — from page HTML.
3. Feed media image — `raw_payload` `media:content` / `media:thumbnail` / image enclosure.
4. First suitable `<img>` in the chosen content.
5. Source-level `sources.default_image_url`.
6. None.

When the feed-content path is taken (no page fetch), steps 1–2 are unavailable and selection naturally starts at step 3 — that is acceptable degradation.

### 8. search_vector (`search.py`)

Populate `search_vector` with a weighted `tsvector` written explicitly by the processor:
`setweight(to_tsvector('english', clean_title), 'A') || setweight(to_tsvector('english', coalesce(clean_text,'')), 'B')`.
Use the `'english'` configuration for v1. Compute it as part of the success transition.

### 9. Transition

- **Success** → `complete(session, article, pending_ranking)` after setting all processed fields + `processed_at` + `search_vector`. `complete` validates the transition via `can_transition` and clears the claim/retry/error.
- **Failure** (fetch/extraction error, nothing usable) → `fail(session, article, error, max_retries=PROCESSOR_MAX_RETRIES, backoff=PROCESSOR_BACKOFF_BASE_SECONDS)`. On every attempt (transient retries included) `fail` records the error message in `last_error` and sets `next_retry_at`; the row stays `pending_processing` until retries are exhausted, then lands in `failed_processing` per the stage mapping.
- **Skip** (succeeded but content below `PROCESSOR_MIN_CONTENT_CHARS`, or unsupported content type) → `complete(session, article, skipped)` with the reason recorded in `last_error`.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-processor/pyproject.toml` | deps (httpx, trafilatura, aggregator-common), console entrypoint |
| `src/aggregator_processor/config.py` | processor settings |
| `src/aggregator_processor/fetch.py` | page fetch + size cap + typed errors |
| `src/aggregator_processor/extract.py` | trafilatura text + metadata |
| `src/aggregator_processor/image.py` | header-image priority selection |
| `src/aggregator_processor/search.py` | weighted `search_vector` update |
| `src/aggregator_processor/process.py` | per-article pipeline + content-source decision |
| `src/aggregator_processor/loop.py` | claim/reap daemon, thread pool, signals, run_once |
| `src/aggregator_processor/__main__.py` | entrypoint (default daemon; `--once`) |
| `packages/aggregator-processor/tests/**` | unit + DB tests (testcontainers + stubbed HTTP) |

## Acceptance Criteria

- A claimed `pending_processing` article is transitioned to `pending_ranking` with `clean_title`, `clean_text`, `excerpt`, `word_count`, `processed_at`, and `search_vector` set.
- **Feed-first:** an article whose feed content ≥ `PROCESSOR_FEED_CONTENT_MIN_CHARS` is processed with **no HTTP fetch** (verified via a stubbed transport / fetch spy that records zero calls).
- **Thin feed:** an article with short feed content triggers a page fetch + trafilatura extraction (stubbed page HTML), and the extracted text is stored.
- **Graceful fallback:** when the page fetch raises but feed content is usable, the article is still processed to `pending_ranking` using feed content (not failed).
- **Failure:** repeated fetch failure with no usable feed content moves the article to `failed_processing` after `PROCESSOR_MAX_RETRIES`; between attempts the row stays `pending_processing` with `last_error` set to the error message and `next_retry_at` advanced by backoff.
- **Skip:** a successfully fetched but too-short article (< `PROCESSOR_MIN_CONTENT_CHARS`, no usable feed text) is transitioned to `skipped` with a reason.
- **Header image priority:** og:image is chosen when present; with og/twitter absent it falls through to feed media, then first `<img>`, then `sources.default_image_url`, then None — asserted per step.
- **search_vector:** populated and weighted; a `to_tsquery('english', <title term>)` match finds the article, and a title-term match ranks above a body-only match.
- `word_count` is correct and `language` is None when undetectable.
- **Excerpt:** `excerpt` is the first ~300 chars of `clean_text` trimmed at a word boundary; when `clean_text` is empty it falls back to `feed_summary`.
- **Metadata fallback:** when trafilatura yields no author/date, `author` falls back to the `raw_payload` author then None, and `published_at` falls back to `feed_published_at` then None (mirrors the `language` check).
- **Claim safety:** two concurrent workers never process the same article (foundation `SKIP LOCKED`); a batch claim marks rows in-flight.
- **Reaper:** a stale in-flight `pending_processing` claim is released and becomes re-claimable.
- `--once` processes one batch and exits 0; daemon SIGTERM drains in-flight work and exits 0.
- **Per-article isolation:** one article raising during extraction does not prevent others in the same batch from completing.
- Full suite green via `uv run pytest` (testcontainers Postgres + stubbed HTTP; no network).

## Pending Decisions

- `search_vector` uses the `'english'` text-search config for v1; per-article language-aware configs are deferred.
- `robots.txt` is **not** consulted — the processor fetches a single, already-known article URL (not crawling); revisit if it ever broadens.
- Image download/caching is deferred (store remote URL only).
- Threshold constants (`FEED_CONTENT_MIN_CHARS=1500`, `MIN_CONTENT_CHARS=200`) are starting values, tunable later.
