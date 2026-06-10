---
name: Containerization and Versioning
id: spec-2c473de7
description: "Containerize the headless backend: per-service arm64 Dockerfiles (retriever/processor/summarize-rank/admin), aggregator-migrate one-shot entrypoint, single SemVer from git tags (APP_VERSION build-arg + OCI labels), docker-compose.prod.yml (postgres+migrate+3 daemons), .dockerignore, build script/Makefile. web excluded (stub). Local build+run; CI/GHCR/Pi is the release spec."
dependencies: null
priority: high
complexity: null
status: draft
tags:
- packaging
- docker
- versioning
- deploy
- depends-foundation
scope:
  in: null
  out: null
feature_root_id: null
---
# Containerization and Versioning

## Objective

Make the working backend services buildable as **arm64 Docker images** and runnable as a single-host **production compose stack**, with one **SemVer version** stamped from git. This is the "headless" runtime: `postgres → migrate → retriever → processor → summarize-rank`. No registry/CI here (that is the release spec) — images build locally on Apple Silicon and the stack runs the full pipeline in containers.

## Dependencies / context

- Built + merged services: `retriever`, `processor`, `summarize-rank` (long-running daemons) and `admin` (CLI). **`web` is still a stub → excluded** from the runtime stack and gets no Dockerfile yet (a stub would crash-loop under a restart policy; add it when the web service is built).
- `aggregator-common` owns the Alembic migrations (`src/aggregator_common/migrations/`).
- `uv.lock` exists; each package exposes a console-script entrypoint.

## Background / Decisions

Decided (see the deployment discussion); implement to them.

- **Architecture: `linux/arm64` only** (Pi 4/5, 64-bit OS), built natively on the Apple-Silicon dev machine via `docker buildx` (OrbStack).
- **Versioning: single repo SemVer from git tags.** A build computes `APP_VERSION` from `git describe --tags --always --dirty` (e.g. `v0.1.0`, or `v0.1.0-3-gabc123` / short-sha when untagged). Passed as a Docker `--build-arg`, embedded as `ENV APP_VERSION` + OCI image labels. Default `dev` when not supplied.
- **Base image:** `python:3.13-slim`; **uv** for dependency install; **non-root** runtime user.
- **Build context = repo root** (uv workspace); a `.dockerignore` trims it.
- **Postgres:** official `postgres:16` image (not built).
- **Migrations run as a one-shot step**, not inside service startup.

## Changes

### 1. Migration entrypoint (`aggregator-common`)

Add a console entrypoint **`aggregator-migrate`** to `aggregator-common` that programmatically runs Alembic `upgrade head` against `DATABASE_URL`, using the **packaged** migrations directory (build an Alembic `Config` in code with `script_location` pointing at `aggregator_common/migrations`, `sqlalchemy.url` from `Settings`). This avoids needing `alembic.ini` on disk in the image and gives the compose stack a clean migrate step. Calls `load_env()` first.

### 2. Per-service Dockerfiles

Create `packages/aggregator-<svc>/Dockerfile` for **retriever, processor, summarize-rank, admin** (not web). Multi-stage, consistent across services:
- **builder stage:** `python:3.13-slim` + uv; copy `pyproject.toml`, `uv.lock`, and `packages/`; `uv sync --frozen --no-dev --package aggregator-<svc>` into a venv (installs that package + `aggregator-common`).
- **runtime stage:** `python:3.13-slim`; copy the venv; create + run as a non-root user; `ARG APP_VERSION` → `ENV APP_VERSION` + `LABEL org.opencontainers.image.{version,revision,source,title}`; `ENTRYPOINT ["aggregator-<svc>"]`.
  - The daemon services run their loop by default; `admin` image's entrypoint is `aggregator-admin` (used via `docker compose run --rm admin …`).

### 3. `.dockerignore`

Trim the build context: exclude `.git`, `.takt`, `.venv`, `**/__pycache__`, `**/.pytest_cache`, `**/.ruff_cache`, `**/tests`, `specs`, `.agents`, `.claude`, `templates`, `.env`, `*.md`. Keep `pyproject.toml`, `uv.lock`, `packages/*/src`, package `pyproject.toml`s.

### 4. Version helper

Add `aggregator_common.version()` returning `os.environ.get("APP_VERSION", "dev")`. Each daemon logs its version once at startup (one `logger.info("<service> starting, version=%s", version())` line, after `configure_logging`).

### 5. `docker-compose.prod.yml` (headless stack)

Services reference images by `${IMAGE_PREFIX:-personal-aggregator}/<svc>:${APP_VERSION:-dev}` (local build uses the default prefix/tag; the Pi sets `IMAGE_PREFIX=ghcr.io/<owner>/personal-aggregator` + a version — handled in the release spec). No `build:` sections — images come from the build script.

- **postgres** — `postgres:16`, env `POSTGRES_USER/PASSWORD/DB` from `.env`, named volume `pgdata`, `healthcheck` (`pg_isready`), `restart: unless-stopped`.
- **migrate** — a service image with `entrypoint: ["aggregator-migrate"]`, `depends_on: postgres (service_healthy)`, `restart: "no"`.
- **retriever / processor / summarize-rank** — each its image, env from `.env`, `depends_on: { postgres: service_healthy, migrate: service_completed_successfully }`, `restart: unless-stopped`.
- In-compose `DATABASE_URL=postgresql://aggregator:aggregator@postgres:5432/aggregator` (host = the `postgres` service name). **Single source of truth:** the credentials in `DATABASE_URL` must match `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`; document this in `.env.example` so the two cannot silently diverge (both default to `aggregator`).
- (web intentionally absent.)

### 6. Build script + Makefile

- `scripts/build-images.sh`: compute `VERSION=$(git describe --tags --always --dirty)`; for each of the 4 services, `docker buildx build --platform linux/arm64 --build-arg APP_VERSION=$VERSION -t ${IMAGE_PREFIX:-personal-aggregator}/<svc>:$VERSION -t ${IMAGE_PREFIX:-personal-aggregator}/<svc>:dev -f packages/aggregator-<svc>/Dockerfile --load .`.
- `Makefile` targets: `build` (run the script), `up` (`docker compose -f docker-compose.prod.yml up -d`), `down`, `logs`, `version` (print the git-derived version).

### 7. Config docs

Update `.env.example` with a "production / compose" note: in-compose `DATABASE_URL` uses host `postgres`; list the prod-required vars (`POSTGRES_*`, `DATABASE_URL`, `OPENAI_API_KEY`, `LLM_MODEL`). Update `CLAUDE.md` Containers section with the build/run commands and the version scheme.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-common/src/aggregator_common/migrate.py` | programmatic `alembic upgrade head` |
| `packages/aggregator-common/src/aggregator_common/version.py` (or `__init__`) | `version()` helper |
| `packages/aggregator-common/pyproject.toml` | `aggregator-migrate` console script |
| `packages/aggregator-{retriever,processor,summarize-rank}/…/loop.py` (or entrypoint) | log version at startup |
| `packages/aggregator-{retriever,processor,summarize-rank,admin}/Dockerfile` | new per-service images |
| `.dockerignore` | new |
| `docker-compose.prod.yml` | new headless stack |
| `scripts/build-images.sh`, `Makefile` | build/run helpers |
| `.env.example`, `CLAUDE.md` | prod config + version docs |
| `packages/aggregator-common/tests/test_migrate.py` | migrate-entrypoint + version-helper tests |

## Acceptance Criteria

- `aggregator-migrate` runs `alembic upgrade head` programmatically against `DATABASE_URL` and creates the full schema; running it twice is a no-op (idempotent) — verified via testcontainers.
- `aggregator_common.version()` returns `APP_VERSION` from the environment, defaulting to `dev` — unit tested.
- Each of the 4 Dockerfiles builds successfully with `docker buildx build --platform linux/arm64 --build-arg APP_VERSION=v9.9.9 …`, and the resulting image has `ENV APP_VERSION=v9.9.9` and an `org.opencontainers.image.version=v9.9.9` label. *(Operator-validated by real build — see Validation; not run by the pytest gate.)*
- `scripts/build-images.sh` builds and tags all 4 images with the git-derived version and a `dev` tag.
- `docker compose -f docker-compose.prod.yml config` is valid, and `… up -d` starts postgres, runs `migrate` to completion, then starts the 3 daemons with `restart: unless-stopped`; `web` is absent.
- The full `uv run` pytest suite stays green (new tests: migrate entrypoint, version helper).

### Validation (operator, post-merge — not the pytest gate)

The pytest merge gate cannot build images or run compose. After merge, validate on the Apple-Silicon host: `scripts/build-images.sh` builds the 4 arm64 images; `docker compose -f docker-compose.prod.yml up -d` brings up postgres + migrate + the 3 daemons; confirm the pipeline runs in-containers (retriever inserts, processor/summarize-rank advance articles) against a throwaway volume. Also confirm each built image's default user is **non-root** (`docker inspect --format '{{.Config.User}}'` is set, not empty/root).

## Pending Decisions

- `web` is excluded until its service is built; then it gets a Dockerfile and a compose entry (web port binding, healthcheck).
- arm64-only for now (no amd64/multi-arch); revisit if other targets appear.
- Postgres backup/restore is out of scope here.
- GHCR image references, multi-arch publish, systemd unit, and the Pi install script are the **release spec** (Spec B), which depends on this.
- **Continuous delivery requirement (for Spec B):** every push to `main` must produce a working, pullable release — Spec B's CI publishes `:edge` + `:main-<shortsha>` on each `main` push (main is always green via the `takt merge` gate), and `:vX.Y.Z` + `:latest` on `v*` tags. The image-tag parameterization here (`${IMAGE_PREFIX}/<svc>:${APP_VERSION}`) already supports both. Requires `main` to be pushed to `origin` (github.com/oscarrenalias/personal-aggregator); GHCR path `ghcr.io/oscarrenalias/personal-aggregator/<svc>`.
