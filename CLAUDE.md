# personal-aggregator

Personal RSS reader and news aggregator. Periodically retrieves articles from configured RSS/Atom feeds, cleans them, uses an LLM to summarize and rank them against the user's interests, and serves the result through a web UI. See `SPEC.md` for the full product spec.

## Architecture

Four services communicating **only through shared Postgres state** (no synchronous service-to-service calls). Articles move through a durable state machine; each service finds its pending work by claiming rows.

```
sources → retriever → processor → summarize-rank → web UI
```

- **retriever** — polls feeds, persists raw articles, marks them pending processing.
- **processor** — cleans/extracts article content, header image, search index; marks ready for ranking.
- **summarize-rank** — LLM summary, topics, importance score + reason (the only service that calls the LLM).
- **web** — FastAPI read/UI surface + reader operations (mark read, save, search). Also the future seam for an MCP/agent interface.

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
  aggregator-web/
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
  migrations/      # Alembic environment; versions/ holds migration scripts
```

## Containers & tests

- **Runtime:** OrbStack provides the Docker engine. Note it does **not** create `/var/run/docker.sock`; its socket is `~/.orbstack/run/docker.sock`. The `docker` CLI finds it via the active context, but headless test runs must resolve it explicitly (see below).
- **Dev:** Postgres via `docker-compose`; the app and takt workers run on the **host** via `uv run` (not inside containers).
- **Tests:** `pytest` with **testcontainers** — each test session spins up an ephemeral Postgres on a random port. This isolates concurrent takt workers running in parallel worktrees; never assume a shared/fixed test database. The test harness resolves the Docker socket in this order: `DOCKER_HOST` env → `/var/run/docker.sock` → `~/.orbstack/run/docker.sock`, setting `DOCKER_HOST` for testcontainers when it falls through. This makes `pytest`/`takt merge` work for every worker without per-worker env setup.
- **Deploy:** per-service Dockerfiles + compose. No devcontainers.

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
