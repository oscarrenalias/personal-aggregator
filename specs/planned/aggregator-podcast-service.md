---
name: aggregator-podcast service
id: spec-podcast-01
description: "Graduates the podcast generation prototype into a first-class aggregator service: scheduled daemon, Postgres-backed episode tracking, shared volume for MP3 files, API endpoints, web UI player, and janitor retention."
dependencies: null
priority: high
complexity: large
status: planned
tags:
- podcast
- new-service
- api
- web
- janitor
scope:
  in: null
  out: null
feature_root_id: null
---

# aggregator-podcast service

## Context and motivation

A daily podcast script generator prototype was built and validated in `scripts/podcast_generate.py` and `scripts/podcast_tts.py`. It uses a three-phase LLM pipeline (story selection → segment writing → intro/transitions/outro) with OpenAI TTS to produce a 10–15 minute news briefing MP3. The prototype is production-quality and has been signed off for productisation.

This spec graduates it into the standard aggregator service pattern: a scheduled daemon, Postgres-backed state, shared Docker volume for audio files, REST API, and web UI player. It follows `aggregator-brief` as the reference implementation.

## New service: aggregator-podcast

### Package layout

```
packages/aggregator-podcast/
├── Dockerfile
├── pyproject.toml                   # deps: aggregator-common, litellm>=1, openai>=2
├── src/aggregator_podcast/
│   ├── __init__.py
│   ├── __main__.py                  # load_env → Settings → loop.run() / run_once(); --once flag
│   ├── config.py                    # PodcastSettings(Settings)
│   ├── loop.py                      # poll cycle: reap → enqueue → claim → generate → complete/fail
│   ├── generate.py                  # ported from scripts/podcast_generate.py
│   └── tts.py                       # ported from scripts/podcast_tts.py
└── tests/
    ├── conftest.py
    └── test_podcast_loop.py
```

### Config (PodcastSettings extends aggregator_common.config.Settings)

| Env var | Default | Description |
|---|---|---|
| `PODCAST_GENERATION_HOUR` | `7` | Local hour to auto-enqueue (0-23) |
| `PODCAST_TIMEZONE` | `UTC` | Timezone for hour comparison |
| `PODCAST_POLL_INTERVAL_SECONDS` | `60` | Daemon poll cadence |
| `PODCAST_CLAIM_LEASE_SECONDS` | `900` | Claim lease timeout |
| `PODCAST_AUDIO_DIR` | `/data/podcasts` | Directory where MP3s are written |
| `PODCAST_CANDIDATE_WINDOW_HOURS` | `36` | Thread history window for story selection |
| `PODCAST_LLM_MODEL` | `gpt-5.6-terra` | Script generation LLM |
| `PODCAST_LLM_MAX_TOKENS` | `4096` | Max output tokens per LLM call |
| `PODCAST_TTS_MODEL` | `gpt-4o-mini-tts` | TTS model |
| `PODCAST_TTS_VOICE` | `marin` | TTS voice |
| `PODCAST_TTS_MAX_CHARS_PER_CHUNK` | `1000` | Max chars per TTS API call (auto-split at sentence boundaries above this) |
| `PODCAST_CONTINUITY_COUNT` | `2` | Number of prior ready episodes fed to selection LLM for deduplication/continuity (0 = disabled) |

### Scheduling loop (loop.py)

Follows `aggregator-brief/loop.py` exactly:
1. Reap stale claims (`reap_stale_podcast_claims` from `aggregator_common.podcast_claim`).
2. Auto-enqueue: if current local hour >= `PODCAST_GENERATION_HOUR` and no auto episode exists for today, `INSERT INTO podcast_episodes ... ON CONFLICT DO NOTHING` (partial unique index on `(date) WHERE origin='auto'`).
3. Claim: `SELECT ... FOR UPDATE SKIP LOCKED` on `status='pending'`.
4. Generate: call `generate.generate_podcast(episode, settings, session)`.
5. Complete/fail.

### Generation (generate.py)

Port of `scripts/podcast_generate.py` with these changes:
- Uses `PodcastSettings` instead of argparse constants.
- Uses the passed DB session instead of creating its own engine.
- **Continuity**: before Phase 1, queries the last `PODCAST_CONTINUITY_COUNT` ready episodes, extracts `{date, episode_theme, story segments: [{thread_id, headline, topic_category}]}` from their `script_json`, and injects this as a "Recent episodes" block into the Phase 1 (selection) system prompt. The prompt instructs the LLM to: (a) skip threads already covered unless significant new developments exist, (b) when re-covering, explicitly reference prior coverage ("as we covered yesterday…").
- Returns: `script_json` dict and `audio_path` string.

### TTS (tts.py)

Port of `scripts/podcast_tts.py` with these changes:
- Accepts `PodcastSettings` for model/voice/chunk-size config.
- Takes the `script_json` dict and `audio_dir` path as arguments.
- Returns: `(audio_path: str, audio_size_bytes: int)`.

### Dockerfile

Copy of `packages/aggregator-brief/Dockerfile` with `aggregator-brief` → `aggregator-podcast`.

## aggregator-common changes

### New model: PodcastEpisode (models.py)

Table `podcast_episodes`:
- `id` BigInteger GENERATED ALWAYS AS IDENTITY PK
- `date` Date NOT NULL — calendar date of the episode
- `origin` Text NOT NULL DEFAULT 'auto' — `auto` | `manual`
- `status` Text NOT NULL DEFAULT 'pending' — `pending` | `generating` | `ready` | `failed`
- `episode_theme` Text
- `script_json` JSONB — full script dict (null until generation completes)
- `audio_path` Text — absolute path to MP3 on the shared volume
- `audio_size_bytes` BigInteger
- `duration_seconds` Integer
- `llm_model` Text
- `tts_model` Text
- `tts_voice` Text
- `error` Text
- `claimed_by` Text
- `claimed_at` TIMESTAMP WITH TIME ZONE
- `generated_at` TIMESTAMP WITH TIME ZONE
- `created_at` TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
- `updated_at` TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now() (set_updated_at trigger)

### New migration

File: `<hex8>_podcast_episodes.py` (down_revision = `f8a9b0c1d2e3`).
- Creates the `podcast_episodes` table.
- Creates partial unique index: `podcast_episodes_date_auto` ON `podcast_episodes(date) WHERE origin='auto'`.
- Attaches the existing `set_updated_at()` trigger to `updated_at`.
- Downgrade drops table, index, and trigger.

### New claim helpers (podcast_claim.py)

Mirrors `brief_claim.py`:
- `claim_podcast(session, worker_id, now)` — SELECT FOR UPDATE SKIP LOCKED, sets status='generating'.
- `complete_podcast(session, episode_id, script_json, audio_path, audio_size_bytes, duration_seconds, llm_model, tts_model, tts_voice)` — sets status='ready', generated_at=now().
- `fail_podcast(session, episode_id, error)` — sets status='failed'.
- `reap_stale_podcast_claims(session, lease_seconds, now)` — resets generating rows older than lease.

### New read helpers (queries.py)

- `get_latest_podcast_episode(session)` → `Optional[PodcastEpisode]` — most recent `status='ready'` by `date DESC`.
- `get_podcast_episode_by_date(session, date: date)` → `Optional[PodcastEpisode]`.
- `list_podcast_episodes(session, limit, cursor)` → `(list[PodcastEpisode], next_cursor)` — keyset cursor on `(date DESC, id DESC)`, filters `status='ready'`.
- `get_recent_podcast_episodes(session, count)` → `list[PodcastEpisode]` — for continuity; returns last N ready episodes, `script_json` column included.

### Retention (retention.py)

New function `purge_expired_podcast_episodes(session, retention_days)`:
1. Find rows where `date < today - retention_days`.
2. For each row, if `audio_path` is set and the file exists on disk, delete it.
3. Bulk-delete the rows.
4. Return count.

## Janitor changes

**`aggregator-janitor/src/aggregator_janitor/config.py`**: add `janitor_podcast_retention_days: int = 7`.

**`aggregator-janitor/src/aggregator_janitor/janitor.py`**: import `purge_expired_podcast_episodes` from `aggregator_common.retention`; add call in `_run_retention`; log count.

## aggregator-api changes

New file: `packages/aggregator-api/src/aggregator_api/routes/podcasts.py`

```
router = APIRouter(prefix="/podcasts", tags=["podcasts"])

GET  /podcasts              → PaginatedResponse[PodcastEpisodeResponse]
GET  /podcasts/latest       → PodcastEpisodeResponse  (404 if none ready)
GET  /podcasts/{date}       → PodcastEpisodeResponse  (date: YYYY-MM-DD string; 404 if not found)
GET  /podcasts/{id}/audio   → FileResponse(audio_path, media_type="audio/mpeg")  (404 if no file)
GET  /podcasts/{id}/script  → JSONResponse(episode.script_json)  (404 if no script)
```

`PodcastEpisodeResponse` Pydantic model: `id`, `date`, `episode_theme`, `status`, `duration_seconds`, `audio_url` (constructed as `/api/v1/podcasts/{id}/audio`), `segment_count` (len of story segments in script_json), `llm_model`, `tts_model`, `tts_voice`, `generated_at`, `created_at`.

FileResponse supports HTTP range requests natively in Starlette/FastAPI — required for audio seek.

Register in `aggregator-api/src/aggregator_api/app.py`:
```python
from aggregator_api.routes.podcasts import router as podcasts_router
app.include_router(podcasts_router)
```

## aggregator-web changes

**`packages/aggregator-web/src/aggregator_web/app.py`**:
- Add `GET /podcasts` route: fetch latest episode + list via queries, render `podcasts.html`.
- Set `nav_key = "podcasts"` for Alpine sidebar highlight.

**New template `packages/aggregator-web/src/aggregator_web/templates/podcasts.html`**:
- Extends `shell.html`.
- If episode available: show episode date/theme, HTML5 `<audio controls preload="metadata" src="/api/v1/podcasts/{id}/audio">` player, episode duration, list of story headlines.
- If no episode: "No podcast episodes available yet" message.
- Episode archive list: date, theme, duration, play link for each of the available episodes.

**Sidebar** (`shell.html` or sidebar template): add Podcasts nav link with `data-nav-key="podcasts"`, positioned near "Today".

## Docker changes

**`docker-compose.yml`** (dev):
- Add named volume `podcasts_data`.
- Add `podcast` service (build from `packages/aggregator-podcast/Dockerfile`, `env_file: .env`, `DATABASE_URL`, `PODCAST_LLM_MODEL: gpt-4.1-mini` for dev cost control, volume `podcasts_data:/data/podcasts`, depends_on postgres+migrate).
- Add `podcasts_data:/data/podcasts:ro` volume mount to `web` service.

**`docker-compose.prod.yml`** (prod):
- Same volume and service definitions, but use image reference pattern instead of build.

**Root `pyproject.toml`**: add `"packages/aggregator-podcast"` to `[tool.uv.workspace] members`.

## CLAUDE.md update

Add `aggregator-podcast` to the services section and document its config vars alongside the others.

## Acceptance criteria

1. `uv run alembic upgrade head` applies migration cleanly; `podcast_episodes` table exists with the partial unique index.
2. `uv run --all-packages python -m aggregator_podcast --once` completes successfully: `podcast_episodes` row has `status='ready'`, `audio_path` points to an existing MP3 file, `script_json` is populated.
3. `GET /api/v1/podcasts/latest` returns a `PodcastEpisodeResponse` with correct `audio_url`.
4. `GET /api/v1/podcasts/{id}/audio` streams the file; `curl --range 0-1023` returns a `206 Partial Content` response.
5. `GET /podcasts` renders the player page; `<audio>` element is present with the correct `src`.
6. Running janitor with `JANITOR_PODCAST_RETENTION_DAYS=0` deletes the episode row and the MP3 file from disk.
7. Running the loop a second time for the same day does not create a duplicate episode (ON CONFLICT DO NOTHING works).
8. With `PODCAST_CONTINUITY_COUNT=2` and two prior episodes in the DB, the selection prompt includes the prior episode headlines (verify via log or debug output).
9. `bash scripts/run-tests.sh` passes green.
