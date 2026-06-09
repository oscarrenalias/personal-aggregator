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
- Feed parsing: `feedparser`. Content extraction: `trafilatura`/readability. LLM: official Anthropic SDK (`claude-*` models).
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
docker-compose.yml              # postgres + the four services
```

Each service package depends on `aggregator-common`, installs only its own deps, exposes a console-script entrypoint (`aggregator-retriever`, etc.), and ships its own `Dockerfile`.

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

## Spec order

Build in dependency order, one spec per component:

1. **foundation** — Postgres schema, article state machine + transitions, claiming/reaper, repo skeleton, config.
2. **retriever**
3. **processor**
4. **summarize-rank**
5. **web / API**
