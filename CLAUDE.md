# personal-aggregator

Personal RSS reader and news aggregator. Periodically retrieves articles from configured RSS/Atom feeds, cleans them, uses an LLM to summarize and rank them against the user's interests, and serves the result through a web UI. See `SPEC.md` for the full product spec.

## Architecture

Nine services communicating **only through shared Postgres state** (no synchronous service-to-service calls). Articles move through a durable state machine; each service finds its pending work by claiming rows.

```
sources → retriever → processor → summarize-rank → clusterer → web UI
                                                              → brief
                                                              → aggregator-mcp (agent interface)
                                                              → janitor (data retention)
```

- **retriever** — polls feeds, persists raw articles, marks them pending processing.
- **processor** — cleans/extracts article content, header image, search index; marks ready for ranking.
- **summarize-rank** — LLM summary, topics, importance score + reason (the only service that calls the LLM).
- **clusterer** — groups `ready` articles into threads using entity/topic overlap and LLM classification; scores and tiers threads; runs after summarize-rank. Uses a Postgres advisory lock (`pg_try_advisory_lock`) to prevent concurrent instances from racing on the same batch. Thread-membership assignment is idempotent: `assign_article_to_thread` is a no-op if the article already has a `ThreadMembership` row. The LLM classifier receives up to `CLUSTERER_MAX_CLASSIFIER_CANDIDATES` candidate threads (ranked by overlap score) rather than a single best candidate, giving it broader context for accurate thread assignment. The `dismissed` column on `Thread` is orthogonal to the lifecycle status; the clusterer never writes it, so dismissal persists across recomputation and consolidation cycles.
- **web** — FastAPI + HTMX + Alpine.js read/UI surface + reader operations (mark read, save, search). Served as a PWA; exposed privately over Tailscale (binds `127.0.0.1` by default). Thread dismiss/restore is handled by `POST /threads/{thread_id}/dismiss` and `POST /threads/{thread_id}/restore`; both return an `HX-Trigger: refreshThreadList` header so HTMX reloads the thread list in-place. `GET /threads` accepts a `show_dismissed=true` query param to include dismissed threads in the listing (default: excluded). `GET /threads/{thread_id}` stamps `Thread.last_viewed_at = now(UTC)` via `mark_thread_viewed` on every successful page render, which drives the `has_updates` indicator in the thread list. **`GET /threads/{thread_id}` never emits `HX-Trigger: refreshThreadList`** — opening a thread does not trigger a full list refresh (which caused a visible flash); the updated-since-visit dot on the card is cleared client-side for just the opened entry instead.
- **brief** — scheduled LLM service that reads ranked articles, generates a structured daily brief (headline + topics + summaries), and persists it to Postgres. Runs once per day at a configurable hour; the web service serves the brief on the Today view.
- **aggregator-mcp** — FastMCP server exposing the aggregator over the MCP/Streamable HTTP interface for agent integration. Provides tools for searching, listing, and mutating articles; thread tools (`list_threads`, `get_thread`, `dismiss_thread`) — `list_threads` and `get_thread` both expose the `dismissed` and `has_updates` fields on each thread result, and `dismiss_thread(thread_id, dismissed=True/False)` dismisses or restores a thread; **`get_thread` intentionally does not stamp `last_viewed_at`** — agent reads are passive and must not reset the unread indicator that the web UI relies on; source and category management tools (add/enable/disable/remove — `remove_source` and `remove_category` are destructive); ops/diagnostic tools (`pipeline_status`, `list_stuck`, `list_failures`, `reap_stale_claims`, `retry_failed`, `rerank`, `recluster`); brief tools (`get_daily_brief`, `refresh_brief`); resources including `article://{id}`, `feed://{view}`, `thread://{id}`, `brief://today`, `status://pipeline` for a quick health snapshot, and `status://llm` for per-service LLM usage stats; and prompts including `troubleshoot` for step-by-step pipeline diagnosis and `whats_developing` for surfacing in-progress story threads.
- **janitor** — scheduled data-retention daemon that runs once per day at `JANITOR_RUN_HOUR` (default 04:00 in `JANITOR_TIMEZONE`). Deletes expired articles (`JANITOR_ARTICLE_RETENTION_DAYS`; unsaved, not in a live thread), archived threads (`JANITOR_THREAD_RETENTION_DAYS`), completed briefs (`JANITOR_BRIEF_RETENTION_DAYS`), and LLM telemetry rows (`JANITOR_LLM_TELEMETRY_RETENTION_DAYS`). Uses a distinct Postgres advisory lock so it never races the clusterer. Replaces the per-service retention logic previously in clusterer and brief.
- **admin** — Rich CLI for feed management and operational tasks. `llm-stats [--days N]` shows per-service LLM cost, token, and error breakdown.

The shared contract lives in `aggregator-common`: SQLAlchemy models, DB access, config, and the **article state machine**. Because services integrate only through the DB, the schema and allowed state transitions are the API — treat them as such.

### Work-claiming pattern

Every worker claims work with `SELECT … FOR UPDATE SKIP LOCKED`, setting `status` + `claimed_by` + `claimed_at`. A reaper returns rows whose claim is older than the lease timeout to their pending state, so a crashed worker never strands an article.

## Stack

- **Python + uv** (workspace). Always run via `uv run`.
- **Postgres** for all persistence; full-text search via `tsvector` + GIN.
- **FastAPI** for the web service.
- Feed parsing: `feedparser`. Content extraction: `trafilatura`/readability. LLM: `litellm` abstraction layer (defaults to `gpt-4.1-mini`; any litellm-supported model works, configurable via `LLM_MODEL`).
- **Separate, independently deployable services** — each its own package, entrypoint, and Dockerfile.

## Monorepo layout

uv workspace, one package per service:

```
pyproject.toml                  # workspace root
packages/
  aggregator-common/            # shared: models, db, config, state machine
  aggregator-retriever/
  aggregator-processor/
  aggregator-summarize-rank/
  aggregator-clusterer/         # thread clustering and scoring daemon
  aggregator-web/               # FastAPI + HTMX + Alpine.js PWA web UI
  aggregator-brief/             # scheduled daily brief generator
  aggregator-mcp/               # FastMCP server — MCP/Streamable HTTP agent interface
  aggregator-janitor/           # scheduled data-retention daemon
  aggregator-admin/
docker-compose.yml              # postgres only (app service definitions added per service spec)
```

Each service package depends on `aggregator-common`, installs only its own deps, exposes a console-script entrypoint (`aggregator-retriever`, etc.), and ships its own `Dockerfile`.

**`aggregator-common` module layout:**

```
src/aggregator_common/
  models.py        # SQLAlchemy ORM: Article, Source, InterestProfile
  state.py         # ArticleStatus enum + allowed transition table
  claim.py         # claim_batch / complete / fail / reap_stale_claims
  db.py            # engine + session factory
  config.py        # pydantic-settings Settings (reads DATABASE_URL from env/.env)
  env.py           # load_env() — loads .env into os.environ at startup
  logging_setup.py # configure_logging() — shared log setup for all services
  queries.py       # shared read + mutation helpers (list/search/get articles, mark read/saved, threads); list_threads derives has_updates = last_viewed_at is None or last_updated > last_viewed_at in a single batch — no N+1
  management.py    # write helpers: mark_thread_viewed (stamps last_viewed_at), set_thread_dismissed, create/update/merge threads, source/category mutations
  retention.py     # purge_expired_articles / purge_expired_threads / purge_expired_briefs / purge_expired_llm_calls — called by janitor
  llm_telemetry.py # setup_llm_telemetry() — LiteLLM custom logger persisting LlmCall rows; optional Langfuse callback activates when all three LANGFUSE_* env vars are set
  migrations/      # Alembic environment; versions/ holds migration scripts
```

## Containers & tests

- **Runtime:** OrbStack provides the Docker engine. Note it does **not** create `/var/run/docker.sock`; its socket is `~/.orbstack/run/docker.sock`. The `docker` CLI finds it via the active context, but headless test runs must resolve it explicitly (see below).
- **Dev:** the root `docker-compose.yml` runs the **full stack built from source** (`docker compose up -d --build`): `postgres → migrate → retriever → processor → summarize-rank → clusterer → web → brief → janitor`. It keeps the `postgres_data` volume and the host `5432` port so data persists and host tooling (admin CLI, `uv run`) can reach it; the web container binds `127.0.0.1:8000` with `WEB_HOST=0.0.0.0`. This is the dev counterpart to `docker-compose.prod.yml` (which **pulls** released GHCR images instead of building). For fast iteration on a single service, run it on the **host** via `uv run` (e.g. `uv run --all-packages python -m aggregator_web`); takt workers always run on the host.
- **Tests:** `pytest` with **testcontainers** — each test session spins up an ephemeral Postgres on a random port. This isolates concurrent takt workers running in parallel worktrees; never assume a shared/fixed test database. The test harness resolves the Docker socket in this order: `DOCKER_HOST` env → `/var/run/docker.sock` → `~/.orbstack/run/docker.sock`, setting `DOCKER_HOST` for testcontainers when it falls through. This makes `pytest`/`takt merge` work for every worker without per-worker env setup.
- **Deploy:** per-service Dockerfiles + compose. No devcontainers.

### Production compose (headless backend)

The full production stack (`postgres → migrate → retriever → processor → summarize-rank → clusterer → web → brief → janitor`) is managed via `Makefile` targets that wrap `docker-compose.prod.yml`:

| Command | Effect |
|---|---|
| `make build` | Builds all eight arm64 service images (calls `scripts/build-images.sh`) |
| `make up` | `docker compose -f docker-compose.prod.yml up -d` |
| `make down` | `docker compose -f docker-compose.prod.yml down` |
| `make logs` | `docker compose -f docker-compose.prod.yml logs -f` |
| `make version` | Print the current git-derived version |

**Version scheme:** `APP_VERSION` is set by CI using `uv version --short` after bumping the workspace version with `uv version --bump <type>`. The resulting value (e.g. `v0.1.0`) is passed as a Docker `--build-arg APP_VERSION`, then baked into the image as `ENV AGGREGATOR_VERSION` (a **dedicated** name, distinct from the deploy-time `APP_VERSION` image-tag selector so that `env_file: .env` — which carries the deploy `APP_VERSION=<tag>` pin — cannot shadow the build version at runtime). `aggregator_common.version()` reads `AGGREGATOR_VERSION`, defaulting to `dev` when absent. It is surfaced at runtime via the web service's `/healthz` endpoint and logged at each daemon's startup.

## takt orchestration

This is a takt repo. Work is broken into **beads** executed by worker agents in git worktrees. **Read the `takt` skill before any takt action.** Non-negotiable rules:

- Always prefix takt/python with `uv run`.
- Manage specs only via the spec-management skill's `spec.py` (`.claude/skills/skill-spec-management/spec.py`) — never `mv` spec files or hand-edit their frontmatter.
- Specs live in `specs/drafts/ → planned/ → done/`. A spec is the planning input: `uv run takt plan --write <spec>` turns it into beads.
- Use `uv run takt merge <id>`, never `git merge`. Let the scheduler resolve merge-conflict beads — do not resolve them manually.
- Never manually mark a developer bead `done`; it must go through the scheduler to trigger followup test/docs/review beads.

## Foundation implementation notes

### Database schema (Alembic)

- Initial migration revision ID: **`a1b2c3d4e5f6`** (`packages/aggregator-common/src/aggregator_common/migrations/versions/a1b2c3d4e5f6_initial_schema.py`)
- Run migrations from the `packages/aggregator-common/` directory: `uv run alembic upgrade head`
- `DATABASE_URL` is injected at runtime by Alembic's `env.py` via `Settings`; `alembic.ini` leaves `sqlalchemy.url` blank.
- Alembic's `env.py` calls `load_env()` before constructing `Settings`, so the root `.env` is auto-loaded — no need to export `DATABASE_URL` separately.

### Tables

Three tables: `sources`, `articles`, `interest_profile`.

`interest_profile` is a singleton row enforced by a boolean PK (`id` always `true`) plus a CHECK constraint. It holds the free-text user interest profile used by the summarize-rank service.

### Article state machine

Six statuses and nine allowed transitions:

```
                  ┌──────────────────────────────────┐
                  │           reaper                  │
pending_processing ──processor success──► pending_ranking ──summarize-rank success──► ready
      │                                       │                                         │
      │ processor fail                        │ summarize-rank fail                    │ web re-rank
      ▼                                       ▼                                         ▼
failed_processing ◄──reaper retry──     failed_ranking ◄──reaper retry──      pending_ranking
      │ processor skip                        │ summarize-rank skip
      ▼                                       ▼
   skipped                                 skipped
```

Transitions are enforced in `state.py` via `_ALLOWED_TRANSITIONS` (a frozen set of `(from, to)` pairs). `claim.py` always calls `can_transition()` before writing a new status.

### Claim lease and retry backoff

- **Lease timeout:** `claim_lease_seconds` — default **600 s** (10 min). Configurable via env var / `.env`.
- **Retry backoff:** exponential — `backoff * 2^(retry_count-1)` seconds. Both `backoff` (base delay in seconds) and `max_retries` are caller-supplied parameters to `claim.fail()`; each service sets its own policy.
- **Reaper:** `reap_stale_claims()` clears `claimed_by`/`claimed_at` on any row whose `claimed_at` is older than `lease_seconds`. The row stays in its current pending status and becomes re-claimable on the next worker poll.

### Config

At process startup, every service calls `aggregator_common.load_env()` (python-dotenv, `override=False`) **before** constructing `Settings` or calling `configure_logging`. `load_env()` uses `find_dotenv(usecwd=True)` to walk up the directory tree from the current working directory, so it finds the repo-root `.env` regardless of where the process was launched. Because `override=False`, a variable already present in `os.environ` always wins over the `.env` value. After `load_env()`, both pydantic-settings and any third-party library that reads `os.environ` directly (e.g. litellm) see the same config. Adding a new variable requires only editing `.env` — no code change needed.

`Settings` (pydantic-settings) also reads from environment variables and a `.env` file in the working directory (belt-and-suspenders, harmless):

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | *(required)* | PostgreSQL DSN |
| `CLAIM_LEASE_SECONDS` | `600` | Work-claim lease duration (seconds) |
| `LOG_LEVEL` | `INFO` | Log level for all services |
| `LLM_TELEMETRY_ENABLED` | `true` | Persist one `llm_calls` row per LLM completion for cost/usage monitoring |
| `LLM_TELEMETRY_CAPTURE_PROMPTS` | `false` | Also persist prompt preview/hash (privacy-sensitive; off by default) |
| `LANGFUSE_PUBLIC_KEY` | *(unset)* | Langfuse public key; Langfuse callback activates only when all three `LANGFUSE_*` vars are set |
| `LANGFUSE_SECRET_KEY` | *(unset)* | Langfuse secret key |
| `LANGFUSE_HOST` | *(unset)* | Langfuse host URL (defaults to Langfuse cloud when omitted) |
| `WEB_HOST` | `127.0.0.1` | Host to bind the web server (never `0.0.0.0` in production) |
| `WEB_PORT` | `8000` | Port to bind the web server |
| `WEB_PAGE_SIZE` | `50` | Number of articles per page in feed lists |
| `WEB_IMPORTANT_THRESHOLD` | `70` | Minimum `importance_score` for the Important smart view |
| `WEB_SHOW_UNREAD_COUNTS` | `false` | Show numeric unread counts in sidebar (`true`) or qualitative dot markers only (`false`) |
| `BRIEF_LLM_MODEL` | `gpt-4.1` | LLM model used for brief generation |
| `BRIEF_LLM_MAX_OUTPUT_TOKENS` | `4096` | Maximum output tokens for LLM calls |
| `BRIEF_LLM_TEMPERATURE` | `0.3` | LLM sampling temperature |
| `BRIEF_LLM_TIMEOUT_SECONDS` | `120` | LLM call timeout in seconds |
| `BRIEF_PERIOD_HOURS` | `24` | Hours of article history to include in each brief |
| `BRIEF_TIMEZONE` | `UTC` | Timezone for scheduling brief generation |
| `BRIEF_GENERATION_HOUR` | `6` | Hour of day (in `BRIEF_TIMEZONE`) to generate the brief |
| `BRIEF_MAX_CANDIDATE_ARTICLES` | `80` | Maximum articles fed to the LLM as candidates |
| `BRIEF_MAX_TOPICS` | `6` | Maximum topic sections in the generated brief |
| `BRIEF_CONTINUITY_COUNT` | `3` | Number of previous briefs included for continuity context |
| `BRIEF_TOOL_MAX_CALLS` | `12` | Maximum LLM tool calls per brief generation run |
| `BRIEF_POLL_INTERVAL_SECONDS` | `60` | Seconds between scheduler poll cycles |
| `BRIEF_CLAIM_LEASE_SECONDS` | `600` | Work-claim lease duration for brief jobs in seconds |
| `CLUSTERER_POLL_INTERVAL_SECONDS` | `60` | Seconds between clusterer poll cycles |
| `CLUSTERER_CANDIDATE_WINDOW_HOURS_FAST` | `48` | Hours of history for fast-path candidate selection |
| `CLUSTERER_CANDIDATE_WINDOW_DAYS_SLOW` | `7` | Days of history for slow-path candidate selection |
| `CLUSTERER_MAX_CANDIDATE_THREADS` | `10` | Maximum candidate threads evaluated per article per run |
| `CLUSTERER_ENTITY_OVERLAP_THRESHOLD` | `0.2` | Minimum entity overlap ratio to consider articles related |
| `CLUSTERER_TOPIC_OVERLAP_THRESHOLD` | `0.2` | Minimum topic overlap ratio to consider articles related |
| `CLUSTERER_FTS_SIMILARITY_THRESHOLD` | `0.1` | Minimum FTS similarity score to consider articles related |
| `CLUSTERER_LLM_MODEL` | `gpt-4.1` | LLM model for cluster classification reasoning |
| `CLUSTERER_LLM_MAX_OUTPUT_TOKENS` | `512` | Maximum output tokens for LLM calls |
| `CLUSTERER_LLM_TEMPERATURE` | `0.0` | LLM sampling temperature (0 = deterministic) |
| `CLUSTERER_LLM_TIMEOUT_SECONDS` | `30` | LLM call timeout in seconds |
| `CLUSTERER_CLAIM_LEASE_SECONDS` | `600` | Work-claim lease duration for clusterer jobs in seconds |
| `CLUSTERER_DORMANT_AGE_DAYS` | `7` | Days of inactivity before a thread is considered dormant |
| `CLUSTERER_ARCHIVE_AGE_DAYS` | `30` | Days of dormancy before a thread is archived |
| `CLUSTERER_BATCH_SIZE` | `20` | Maximum articles processed per clustering cycle |
| `CLUSTERER_TITLE_JACCARD_THRESHOLD` | `0.7` | Minimum token Jaccard similarity for near-duplicate title detection |
| `CLUSTERER_SURFACE_MIN_GRADE` | `80` | Minimum grade (0–100) for a single-source thread to surface on its own (multi-source clusters surface via critical mass regardless) |
| `CLUSTERER_SURFACE_MIN_SOURCES` | `2` | Minimum distinct source count required to surface a thread |
| `CLUSTERER_SURFACE_MIN_MEMBERS` | `3` | Minimum article member count required to surface a thread |
| `CLUSTERER_MERGE_SIMILARITY_FLOOR` | `0.35` | Minimum composite similarity score required to consider merging two threads |
| `CLUSTERER_MAX_MERGE_CHECKS` | `20` | Maximum candidate thread pairs checked for merging per consolidation cycle |
| `CLUSTERER_THREAD_VIEW_MAX_AGE_DAYS` | `7` | Maximum age in days for threads shown in the default thread view |
| `CLUSTERER_SECTION_TITLE_BLOCKLIST` | `["top stories","home",…]` | JSON array of RSS section/category titles too generic to use as thread titles |
| `CLUSTERER_CONSOLIDATION_MIN_INTERVAL_MINUTES` | `10` | Minimum minutes between consolidation passes; explicit recluster bypasses this floor |
| `CLUSTERER_MAX_CLASSIFIER_CANDIDATES` | `5` | Maximum candidate threads passed to the LLM classifier per article |
| `JANITOR_ARTICLE_RETENTION_DAYS` | `14` | Days to retain articles before purging (unsaved, not in a live thread) |
| `JANITOR_THREAD_RETENTION_DAYS` | `30` | Days to retain archived threads before permanent deletion (replaces `CLUSTERER_THREAD_RETENTION_DAYS`) |
| `JANITOR_BRIEF_RETENTION_DAYS` | `30` | Days to retain completed briefs before pruning (replaces `BRIEF_RETENTION_DAYS`) |
| `JANITOR_LLM_TELEMETRY_RETENTION_DAYS` | `30` | Days to retain LLM call telemetry rows before purging |
| `JANITOR_RUN_HOUR` | `4` | Hour of day (in `JANITOR_TIMEZONE`) to run the retention sweep |
| `JANITOR_TIMEZONE` | `UTC` | Timezone for scheduling the daily retention sweep |
| `JANITOR_POLL_INTERVAL_SECONDS` | `3600` | Seconds between scheduler poll cycles |

**Per-service config convention:** Each service subclasses `aggregator_common.config.Settings` and adds its own fields using a `<SERVICE>_` prefix (e.g., `PROCESSOR_BATCH_SIZE`, `RETRIEVER_POLL_INTERVAL_SECONDS`). Shared fields live in the base class; service-specific fields never bleed into other services' namespaces.

## Logging

Centralized, env-driven logging configured via `aggregator_common.logging_setup.configure_logging(settings, *, stream)`:

- **Level** comes from `settings.log_level` (the `LOG_LEVEL` env var; default `INFO`). Accepts any standard name (DEBUG/INFO/WARNING/ERROR/CRITICAL), case-insensitive.
- **Format:** plain text — `%(asctime)s %(levelname)s %(name)s %(message)s`. No JSON, no file handlers (container runtime captures stdout/stderr).
- **Idempotent:** calling it twice replaces the previously installed handler rather than stacking a duplicate.
- **Stream convention:**
  - Daemon services (retriever, processor, summarize-rank, web): `stream=sys.stdout` — `docker logs` captures stdout by default.
  - Admin CLI: `stream=sys.stderr` — keeps stdout clean for Rich tables and `--json` output.
- **Convention:** every new service entrypoint must call `load_env()` first (before `Settings()` and before `configure_logging`), then call `configure_logging(settings, stream=sys.stdout)` (or `sys.stderr` for admin-style CLIs) after constructing `Settings`.

## Spec order

Build in dependency order, one spec per component:

1. **foundation** — Postgres schema, article state machine + transitions, claiming/reaper, repo skeleton, config.
2. **retriever**
3. **processor**
4. **summarize-rank**
5. **web / API**

## Release pipeline

Versioning and image publishing run entirely in CI — do not bump the version manually.

**Versioning flow:**

- Every push to `main` that touches `packages/**`, `pyproject.toml`, Dockerfiles, `docker-compose.prod.yml`, or `scripts/**` triggers a `patch` bump automatically.
- A `minor` or `major` bump is triggered via **`workflow_dispatch`** with the `bump` input set to `minor` or `major`.
- CI runs `uv version --bump <type>`, commits the updated `pyproject.toml` back to `main` with the message `chore: bump version to vX.Y.Z [skip ci]`, then reads the new version with `uv version --short`.
- The root `pyproject.toml` `[project].version` field is therefore CI-managed. Do not edit it by hand.

**GHCR image paths:**

Images are pushed to `ghcr.io/oscarrenalias/personal-aggregator/aggregator-<service>` for each service (`retriever`, `processor`, `summarize-rank`, `clusterer`, `admin`, `web`, `brief`, `janitor`). Three tags are applied on every successful build:

| Tag | When to use |
|---|---|
| `:vX.Y.Z` | Pin to a specific release |
| `:latest` | Track the most recent release |
| `:main-<short-sha>` | Pin to a specific commit without a version tag |

**GitHub Release:**

After images are pushed, the `publish` job creates a GitHub Release named `vX.Y.Z` and attaches `docker-compose.prod.yml`, `deploy/aggregator.service`, `deploy/install.sh`, and `.env.example` as release assets — these are the files needed for a Pi install.

## Pi deployment

Full instructions live in [`deploy/README.md`](deploy/README.md). Summary:

**Install (first time):**

1. Download the release assets from the latest GitHub Release: `install.sh`, `docker-compose.prod.yml`, `aggregator.service`, `.env.example`.
2. Run `sudo ./install.sh install` — this creates `/opt/personal-aggregator/`, copies the compose file and `.env`, installs the `aggregator` systemd unit, and starts the service.
3. Edit `/opt/personal-aggregator/.env` to set `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`) and `DATABASE_URL`, then `sudo systemctl restart aggregator`.

Use `--check` for a dry-run preview: `sudo ./install.sh --check install`.

**Update:**

```bash
sudo ./install.sh update
```

Pulls the latest images and restarts the stack. Run from the directory containing the release assets so the latest `docker-compose.prod.yml` is picked up.

**Rollback:**

Pin `APP_VERSION` in `/opt/personal-aggregator/.env` to the desired tag (e.g. `APP_VERSION=v0.1.3`), then run `sudo ./install.sh update`. Set `APP_VERSION=latest` to return to tracking the most recent release.

**Service management:**

The systemd unit (`aggregator.service`) manages the whole Compose stack. Standard `systemctl` commands apply: `enable`, `start`, `stop`, `status`, `restart`. Logs: `docker compose -f docker-compose.prod.yml logs -f` for container output, or `journalctl -u aggregator -f` for service lifecycle events.
