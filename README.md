# personal-aggregator

A personal RSS reader and news aggregator. It periodically retrieves articles from
configured RSS/Atom feeds, cleans them, uses an LLM to summarize and rank them against
your stated interests, and serves the result through a fast, Feedly-style web UI.

It runs as a small set of services that share a Postgres database. Articles flow through
a durable state machine; each service claims pending work and hands it on:

```
sources → retriever → processor → summarize-rank → web UI
```

- **retriever** — polls feeds, stores raw articles.
- **processor** — cleans/extracts content, header image, search index.
- **summarize-rank** — LLM summary, topics, categories, importance score (the only service that calls an LLM).
- **web** — FastAPI + HTMX + Alpine.js reader UI (smart views, category/topic feeds, search, mark read/save, `j`/`k`/`v`/`m`/`n` shortcuts). Meant to be exposed privately over Tailscale.
- **admin** — Rich CLI for managing feeds, categories, the interest profile, and operations.

## Repository structure

A `uv` workspace, one package per service. Each package has its own dependencies,
console-script entrypoint, and `Dockerfile`.

```
packages/
  aggregator-common/         # shared: SQLAlchemy models, DB, config, state machine, migrations
  aggregator-retriever/      # feed polling
  aggregator-processor/      # content cleaning/extraction
  aggregator-summarize-rank/ # LLM summary + ranking + categorization
  aggregator-web/            # FastAPI + HTMX web UI / PWA
  aggregator-admin/          # Rich CLI
docker-compose.yml           # local dev stack (builds images from source)
docker-compose.prod.yml      # production stack (pulls released images from GHCR)
```

The shared contract lives in `aggregator-common`: the database schema and the allowed
article state transitions are the API between services.

## Local development

Requires [`uv`](https://docs.astral.sh/uv/) and a Docker engine (Docker Desktop / OrbStack).

### 1. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set your LLM key (only `summarize-rank` needs it):

```
OPENAI_API_KEY=sk-...
# DATABASE_URL is already set for the local stack; LLM_MODEL defaults to gpt-4.1-mini
```

### 2. Start the stack

The dev `docker-compose.yml` builds all images from source and runs the whole pipeline
(Postgres → migrations → retriever → processor → summarize-rank → web):

```bash
docker compose up -d --build
```

The web UI is then available at **http://127.0.0.1:8000**.

```bash
docker compose logs -f web        # tail a service
docker compose down               # stop (your data persists in the postgres_data volume)
```

> To run a single service on the host instead (e.g. for fast iteration on the web UI):
> `uv run --all-packages python -m aggregator_web`. Always run Python via `uv run`.

### 3. Configure what you read

The **admin CLI** runs on the host against the same database. Run any command with
`--help` to see all options.

**Set your interests** (drives ranking and importance scores):

```bash
uv run --all-packages aggregator-admin profile set "Indie games, AI research, distributed systems, Formula 1."
# or from a file:
uv run --all-packages aggregator-admin profile set --file interests.txt
uv run --all-packages aggregator-admin profile show
```

**Add feed sources:**

```bash
uv run --all-packages aggregator-admin sources add -n "BBC News" -u "http://feeds.bbci.co.uk/news/rss.xml"
uv run --all-packages aggregator-admin sources add -n "Eurogamer" -u "https://www.eurogamer.net/feed"
uv run --all-packages aggregator-admin sources list
uv run --all-packages aggregator-admin sources refresh-now <id>   # fetch on next poll cycle
```

**Import sources from an OPML file:**

```bash
uv run --all-packages aggregator-admin sources import-opml subscriptions.opml
# dry-run: preview what would be added without touching the database
uv run --all-packages aggregator-admin sources import-opml subscriptions.opml --dry-run
# import disabled, with a custom refresh interval, and capture the result as JSON
uv run --all-packages aggregator-admin sources import-opml feedly-export.opml --disabled --interval 7200 --json
```

Flags: `--dry-run` (preview only), `--interval <seconds>` (default 3600), `--disabled` (import in disabled state), `--json` (machine-readable output).

Nested OPML folders are flattened. Duplicate URLs — both within the file and against existing sources — are skipped automatically.

**Export sources to OPML:**

```bash
# print to stdout
uv run --all-packages aggregator-admin sources export-opml
# write to a file
uv run --all-packages aggregator-admin sources export-opml backup.opml
```

All sources are exported as OPML 2.0, sorted alphabetically by name. The optional positional argument is the output file path; omit it to write to stdout.

**Manage categories** (the controlled set the LLM classifies articles into):

```bash
uv run --all-packages aggregator-admin categories list
uv run --all-packages aggregator-admin categories add "Cloud" --description "Cloud platforms and infra"
```

**Inspect the pipeline:**

```bash
uv run --all-packages aggregator-admin articles list
uv run --all-packages aggregator-admin ops --help     # diagnostics and maintenance
```

Once sources and a profile are set, the services pick up the work automatically: articles
are retrieved, cleaned, summarized, ranked, and appear in the web UI within a poll cycle.

## Tests

```bash
bash scripts/run-tests.sh
```

Tests use `pytest` with [testcontainers](https://testcontainers.com/) — each run spins up
an ephemeral Postgres, so no shared test database is needed.

## Deployment

Production runs the same services from released images on a Raspberry Pi over Tailscale.
See [`deploy/README.md`](deploy/README.md) for full install/update/rollback instructions.
