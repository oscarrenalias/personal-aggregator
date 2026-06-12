---
name: Today intelligent daily brief
id: spec-c79ab535
description: "A 'Today' intelligent daily brief: a new tool-using (agentic) LLM service synthesizes the day's ranked articles into a structured brief — what happened, why it matters to the user (per their interest profile), historical continuity, and the articles to read (internal + external links). New briefs/brief_topics tables, a dedicated aggregator-brief service using a separate BRIEF_LLM_MODEL with DB query tools (extensible to web), a 'Today' web view with manual refresh, an admin command, and deploy wiring. DB-mediated daily generation + on-demand refresh."
dependencies: null
priority: high
complexity: high
status: planned
tags:
- brief
- today
- llm
- agentic
- web
- service
scope:
  in: "New aggregator-brief service (package, Dockerfile, console script, compose+CI wiring); aggregator-common migration + ORM for briefs/brief_topics + claim helpers; aggregator-web 'Today' view + refresh; aggregator-admin brief commands; .env.example + compose + build-images.sh + CI matrix. Reuses litellm, the interest profile, full-text search, and the claim/state pattern."
  out: "No open-web search in v1 (the tool registry is built to allow adding it later, but only DB-backed tools ship now). No changes to the retriever/processor or the article state machine. No push notifications/email. The brief does not modify articles."
feature_root_id: B-4c2b82a7
---
# Today intelligent daily brief

## Objective

Add a **"Today"** view that is an *intelligent daily brief*, not a flat 24h article list. Once per day (and on manual refresh) a new **tool-using LLM service** synthesizes the day's relevant articles into a structured brief:

- **What happened** — a narrative of the day's notable developments.
- **What you should pay attention to** — prioritized against the user's interest profile.
- **What to read** — links to the underlying articles, either to the source site or to the article inside the aggregator.

Each highlighted topic carries **historical continuity** (e.g. "a follow-up to the Iran developments two days ago"). The generating model is given **tools to query the aggregator's own historical article database**, so it decides when it needs background — the tool registry is designed so an open-web tool can be added later without rework.

## Motivation

The aggregator ranks and categorizes articles well, but the user still has to scan a list to understand "what matters today." A synthesized, preference-aware brief with continuity turns the product from a feed reader into a personal analyst. It runs ~once/day, so it can afford a more capable (separate) model.

## Design overview

A new **`aggregator-brief`** daemon generates briefs. Generation is **DB-mediated and claim-based**, consistent with the existing services:

- A brief row moves through statuses **`pending` → `generating` → `ready` / `failed`** (mirror `aggregator_common.state` / `claim.py` conventions: `claimed_by`/`claimed_at`, `SELECT … FOR UPDATE SKIP LOCKED`, a reaper for stale `generating` claims).
- **Daily trigger:** each loop, if no brief exists for the current local-day period and the configured generation hour has passed, the service enqueues a `pending` brief.
- **On-demand refresh:** the web "Refresh" button (and the admin CLI) insert a `pending` brief; the service claims and generates it. The web stays read-only — it only inserts a request row and reads results (DB-mediated, no service-to-service calls).

Generation is an **agentic tool-use loop** via litellm (same library the summarize-rank service uses): the model is seeded with the day's top-ranked articles + the interest profile + recent prior briefs, is given DB-query tools, iterates tool calls to gather continuity, and finally emits the structured brief.

## Data model (aggregator-common)

New Alembic migration chained off the current head (the dev agent must `alembic heads` to find it; do not hardcode). Add two tables + ORM models in `models.py`:

- **`briefs`**: `id` (Identity PK), `status` (text; pending/generating/ready/failed), `claimed_by`/`claimed_at` (nullable, for the claim pattern), `period_start`/`period_end` (timestamptz — the window the brief covers), `generated_at` (nullable), `model` (text, nullable — which LLM produced it), `headline` (text, nullable), `intro` (text, nullable), `error` (text, nullable), `created_at`/`updated_at`.
- **`brief_topics`**: `id`, `brief_id` (FK → briefs, ON DELETE CASCADE), `position` (int, ordering), `headline` (text), `what_happened` (text), `why_it_matters` (text), `historical_context` (text, nullable), `references` (JSONB — list of `{article_id:int|null, title:str, url:str|null, internal:bool}`), `created_at`. **Note:** `references` is a SQL reserved word — quote it in DDL and give the ORM attribute a safe name (e.g. `topic_references`) mapped to the `references` column, or simply name the column `refs` to avoid the footgun.

**Daily idempotency:** enforce "at most one *auto-generated* brief per local-day period" at the schema level, not by loop timing — e.g. an `origin` column (`auto`/`manual`) plus a partial unique index on `(period_start)` where `origin='auto'`, or an atomic conditional insert. This keeps the guarantee correct under parallel workers / a web-refresh racing the daemon.

Add claim helpers (reuse/extend `claim.py` patterns or a small `brief_claim` module): claim the oldest `pending` brief with `FOR UPDATE SKIP LOCKED`, complete (→ ready), fail (→ failed with error), and a reaper returning stale `generating` rows to `pending`.

Retention: keep brief history (needed for continuity). Optionally prune briefs older than `BRIEF_RETENTION_DAYS` (default 30) in the service loop.

## Brief service (new package `aggregator-brief`)

New workspace package mirroring the other services (own `pyproject.toml`, `src/aggregator_brief/`, `Dockerfile`, console script `aggregator-brief = "aggregator_brief.__main__:main"`, depends on `aggregator-common` + `litellm`). Follow the startup convention: `load_env()` → `BriefSettings()` → `configure_logging(settings, stream=sys.stdout)`.

**Loop (`loop.py`):** reap stale claims → daily-trigger check (enqueue pending if today's brief is missing and past the generation hour) → claim a `pending` brief → generate → complete/fail. Poll interval `BRIEF_POLL_INTERVAL_SECONDS`.

**Generation (agentic tool loop):**
1. **Seed context:** select the `ready` articles in `[period_start, period_end)` ranked by `importance_score` desc (and profile relevance), capped at `BRIEF_MAX_CANDIDATE_ARTICLES`; include id, title, summary, categories/topics, published date, source, url. Also include the interest profile text and short summaries of the last few briefs (for continuity).
2. **System prompt:** "You are the user's personal news analyst. Produce a daily brief with an intro and `BRIEF_MAX_TOPICS` (≈3–7) topics. For each topic: what happened; why it matters to *this* user (use their interest profile and de-prioritize their low-priority topics); historical continuity if it follows earlier events; and the articles to read (reference them by id). Use the provided tools to search the historical article database for background/continuity when a topic looks like a follow-up. Be selective — surface what matters, not everything."
3. **Tools (litellm function-calling):** a small extensible registry:
   - `search_articles(query, since?, until?, categories?, limit?)` → full-text (`websearch_to_tsquery`) + optional date/category filter over **all** ready articles (not just today's), returning id/title/summary/published_at/url/categories. This is the historical-lookup tool.
   - `get_article(article_id)` → fuller text/metadata for one article (optional but recommended).
   - `submit_brief(intro, headline, topics[...])` → the **terminal** tool the model calls to emit the final structured brief; calling it ends the loop. (Alternatively, end with provider-native structured output — but a terminal `submit_brief` tool is the cleanest loop exit.)
   Cap the loop at `BRIEF_TOOL_MAX_CALLS` iterations; if exceeded without `submit_brief`, fail gracefully. Define the tool schemas centrally so adding a future `web_search` tool is a one-line registry addition.
4. **Validate `submit_brief` payload:** schema-validate the terminal payload — required topic fields present (`headline`, `what_happened`, `why_it_matters`), topic count clamped to `BRIEF_MAX_TOPICS`. On a validation failure, allow **one** corrective tool turn (feed the error back to the model); if it still fails, mark the brief `failed` with the reason. Do not persist a malformed brief.
5. **Reconcile references against the DB:** each reference the model emits is resolved against known article ids — a matching id becomes `internal:true` (UI links `/article/{id}`); an **unknown/hallucinated id is dropped** (or, if the model supplied a real source `url`, downgraded to an external-only reference). Never persist a dangling internal link.
6. **Persist:** write the `briefs` row (headline/intro/model/generated_at) + `brief_topics` rows with reconciled `references`. Set status `ready`. On any error, set `failed` + `error`, and let the reaper/next request retry.

**Config (`BriefSettings`, `BRIEF_` prefix, subclassing `aggregator_common.config.Settings`):** `BRIEF_LLM_MODEL` (default a capable model, e.g. `gpt-4.1`), the LLM params (`BRIEF_LLM_MAX_OUTPUT_TOKENS`, `BRIEF_LLM_TEMPERATURE`, `BRIEF_LLM_TIMEOUT_SECONDS`), `BRIEF_PERIOD_HOURS` (24), `BRIEF_TIMEZONE` (IANA tz for "today"; default UTC), `BRIEF_GENERATION_HOUR` (local hour to auto-generate, default 6), `BRIEF_MAX_CANDIDATE_ARTICLES` (e.g. 80), `BRIEF_MAX_TOPICS` (e.g. 6), `BRIEF_CONTINUITY_COUNT` (how many recent prior briefs to seed for continuity, e.g. 3), `BRIEF_TOOL_MAX_CALLS` (e.g. 12), `BRIEF_POLL_INTERVAL_SECONDS`, `BRIEF_CLAIM_LEASE_SECONDS`, `BRIEF_RETENTION_DAYS`. The LLM key (`OPENAI_API_KEY`/`ANTHROPIC_API_KEY`) is read from env as today.

## Web UI (aggregator-web)

- Add a **"Today"** entry at the top of the sidebar (above Smart Views).
- `GET /today` → renders the latest `ready` brief into the center pane: headline/intro, then per-topic cards — **What happened**, **Why it matters**, optional **Background** (historical_context), and a **Read** list of reference links (internal `/article/{id}` with `hx-get` into the reader, or external source `url` with `target="_blank" rel="noopener"`). Reuse existing card/typography styles.
- **Refresh:** `POST /today/refresh` inserts a `pending` brief and returns a "Generating…" state; the Today view polls (HTMX `every Ns`, reusing the live-refresh pattern) until a newer `ready` brief appears, then shows it. Disable/skip duplicate requests if one is already pending/generating.
- Empty states: "No brief yet — generating your first one…" (and trigger one) / "Generating…".
- The web service only inserts the request row and reads briefs; it does not call the LLM.

## Admin CLI (aggregator-admin)

Add a `brief` Typer group: `brief generate` (enqueue a pending brief / force refresh), `brief show` (render the latest ready brief, `--json` supported), `brief list` (recent briefs + status). Reachable on the Pi via the `aggregator` wrapper (`sudo /opt/personal-aggregator/aggregator brief generate`).

## Deploy wiring

- Add `brief` to `docker-compose.yml` (dev, build) and `docker-compose.prod.yml` (image, `env_file: .env`, `DATABASE_URL=@postgres`, depends_on postgres+migrate, restart unless-stopped).
- Add `brief` to `scripts/build-images.sh` `SERVICES` and the CI build matrix (it becomes the 6th image `aggregator-brief`).
- Add the `BRIEF_*` vars (incl. `BRIEF_LLM_MODEL`) to `.env.example` with comments.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-common/src/aggregator_common/migrations/versions/*.py` | New migration: `briefs` + `brief_topics` tables (chained off current head) |
| `packages/aggregator-common/src/aggregator_common/models.py` | `Brief` + `BriefTopic` ORM models |
| `packages/aggregator-common/src/aggregator_common/claim.py` (or new `brief_claim.py`) | Claim/complete/fail/reaper for `pending`/`generating` briefs |
| `packages/aggregator-brief/**` | **New service package**: pyproject, `__main__.py`, `config.py` (BriefSettings), `loop.py`, `generate.py` (agentic tool loop), `tools.py` (search_articles/get_article/submit_brief registry), `prompt.py`, `schema.py`, `Dockerfile`, tests |
| `packages/aggregator-web/src/aggregator_web/app.py` | `GET /today`, `POST /today/refresh` routes |
| `packages/aggregator-web/src/aggregator_web/templates/` | `_today.html` (brief render) + sidebar "Today" entry + poll-while-generating |
| `packages/aggregator-web/src/aggregator_web/static/styles.css` | Brief/topic styling |
| `packages/aggregator-admin/src/aggregator_admin/` | `brief` command group (generate/show/list) wired into `main.py` |
| `docker-compose.yml`, `docker-compose.prod.yml`, `scripts/build-images.sh`, `.github/workflows/ci.yml`, `.env.example` | Wire the new `brief` service/image + `BRIEF_*` config |

## Acceptance Criteria

- **Schema:** migration creates `briefs` and `brief_topics` with the columns above; `alembic upgrade head` then `downgrade` round-trips cleanly. ORM models present.
- **Claim/state:** a `pending` brief can be claimed (status → `generating`, `claimed_by`/`claimed_at` set), completed (→ `ready`), or failed (→ `failed` + `error`); the reaper returns a stale `generating` brief to `pending`. Unit-tested with the testcontainers Postgres.
- **Generation (mocked LLM):** with litellm mocked to (a) issue a `search_articles` tool call then (b) call `submit_brief`, `generate()` executes the tool against the DB, persists a `briefs` row + `brief_topics` rows with the 3-part fields and references, sets status `ready`, and records the model. A test asserts the tool loop runs, the historical query is executed, and the structured result is persisted; a `BRIEF_TOOL_MAX_CALLS` overrun fails gracefully. (No real LLM calls in tests.)
- **search_articles tool:** returns ready articles matching a query across the full history (not just the period), honoring date/category filters — tested against seeded data.
- **Reference reconciliation:** when `submit_brief` references an article id that exists, it's persisted as `internal:true`; when it references a **non-existent** id, it is dropped (or downgraded to external if a real url was given) — never persisted as a dangling internal link. Tested with a mocked `submit_brief` containing one valid and one bogus id.
- **Malformed payload:** a `submit_brief` call missing a required topic field (or exceeding `BRIEF_MAX_TOPICS`) triggers one corrective turn; if still invalid, the brief is marked `failed` and nothing partial is persisted. Tested with a mocked malformed payload.
- **Daily idempotency:** two concurrent enqueue attempts for the same auto period result in exactly one `auto` brief for that period (schema-enforced). Tested.
- **Daily + on-demand:** the loop enqueues at most one auto brief per local-day period; inserting a `pending` brief (refresh) causes the next claim cycle to generate it. Tested.
- **Web:** `GET /today` renders the latest ready brief (intro + topics + reference links, internal `/article/{id}` and external `url`); with no brief it shows the generating/empty state. `POST /today/refresh` inserts a `pending` brief and the view polls until ready. The sidebar shows a "Today" entry. Tests assert the rendered structure and that refresh enqueues a brief.
- **Admin:** `aggregator-admin brief generate` enqueues a pending brief; `brief show`/`brief list` render the latest/recent briefs (with `--json`). Tested.
- **Config:** `BRIEF_LLM_MODEL` and the other `BRIEF_*` settings are read with the `BRIEF_` prefix and have the documented defaults.
- **Deploy:** `brief` service present in both compose files and `build-images.sh`/CI matrix (6th image); `.env.example` documents `BRIEF_*`. `bash scripts/run-tests.sh` is green.

## Pending Decisions

- **Open-web tool:** out of scope for v1 (DB tools only), but the tool registry is structured so a `web_search` tool can be added later without reworking the loop.
- **Terminal output mechanism:** prefer a `submit_brief` terminal tool to end the agentic loop; if the chosen model/litellm path makes provider-native structured output cleaner, that is an acceptable equivalent as long as the persisted shape matches `brief_topics`.
- **Timezone for "today":** configurable via `BRIEF_TIMEZONE` (default UTC); the user sets their local tz on the Pi.
