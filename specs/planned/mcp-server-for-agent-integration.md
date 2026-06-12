---
name: MCP server for agent integration
id: spec-b5d86383
description: "A dedicated aggregator-mcp service exposing the aggregator over the Model Context Protocol (official Python mcp SDK / FastMCP, Streamable HTTP transport) so external agents (e.g. openclaw) can query and act on it. Read + write tools (search, list, get article, interest profile, categories/sources, mark read/unread, save/unsave), resources (article/feed/profile URIs), and a whats_latest prompt. Tailscale-only (no app auth). New service package + shared aggregator-common query helpers + deploy wiring. No dependency on other features — the daily-brief tools/resource/prompt are deferred to a follow-up once the Today feature ships."
dependencies: null
priority: high
complexity: high
status: planned
tags:
- mcp
- agent
- service
- integration
scope:
  in: "New aggregator-mcp service (package, Dockerfile, console script, compose+CI+build-script wiring) running a FastMCP server over Streamable HTTP; shared read/mutation query helpers in aggregator-common reused by the MCP tools; .env.example entries. Reuses existing search/feed query logic and the article state fields."
  out: No app-level auth (Tailscale boundary only). No new aggregator capabilities beyond exposing what exists. No OAuth. No changes to the web UI. The daily-brief tools/resource/prompt are deferred to a follow-up increment — excluded here so there is no dependency on the Today-brief feature.
feature_root_id: B-3631d87d
---
# MCP server for agent integration

## Objective

Expose the aggregator over the **Model Context Protocol (MCP)** so the user's existing agents (openclaw, work agents) can drive it — "give me the daily brief every morning", "what's the latest on `<topic>`?" — without building any chat/agent UX into the app itself. A new dedicated **`aggregator-mcp`** service runs an MCP server (official Python **`mcp` SDK / FastMCP**, **Streamable HTTP** transport) exposing **tools**, **resources**, and **prompts** backed by the shared Postgres state. Reachable privately over Tailscale; **no app-level auth** (the tailnet is the boundary).

## Motivation

The aggregator already ranks, categorizes, and summarizes articles. The user runs capable agents elsewhere and wants those agents to consume the aggregator rather than re-implementing an assistant inside it. MCP is the standard integration seam for exactly this. CLAUDE.md already designates the read surface as "the future seam for an MCP/agent interface."

## Design overview

- New workspace package **`aggregator-mcp`** (own `pyproject.toml`, `src/aggregator_mcp/`, `Dockerfile`, console script `aggregator-mcp = "aggregator_mcp.__main__:main"`), depending on `aggregator-common` and the official **`mcp`** Python SDK (FastMCP).
- The server uses **Streamable HTTP transport** (remote — agents connect over the network), binding inside the container on a configurable host/port (mirror the web service: bind `0.0.0.0` in-container, publish via compose). Exposed privately over **Tailscale**; **no authentication** in v1 (per decision — tailnet is the trust boundary).
- It is a long-running daemon. Follow the startup convention: `load_env()` → `McpSettings()` → `configure_logging(settings, stream=sys.stdout)` → run the FastMCP app.
- **Reuse, don't duplicate, query logic.** The tools need the same reads/mutations the web service already performs (full-text search, ready-article listing by view/category/source, get-by-id, mark read/save, list categories/sources, read interest profile). Factor the shared read/mutation helpers into **`aggregator-common`** (e.g. a `queries.py`/`operations.py`) and have the MCP tools call them. Mirror the existing implementations (aggregator-web's `/search` `websearch_to_tsquery` query and `feeds.py` filters). The web service may migrate to these shared helpers opportunistically but is not required to in this spec.
- **The dev agent must consult the official MCP Python SDK docs** (modelcontextprotocol.io / the `mcp` package) for the exact FastMCP decorators (`@mcp.tool`, `@mcp.resource`, `@mcp.prompt`) and the Streamable HTTP run/mount API — do not guess the SDK surface.

## MCP surface

### Tools (agent-invoked)
- `search_articles(query: str, limit: int = 20, since: str | None = None, category: str | None = None, source_id: int | None = None)` → ranked full-text matches (id, title, summary, url, published_at, importance_score, categories). Backs "what's the latest on `<topic>`?".
- `list_articles(view: str = "unread", category: str | None = None, source_id: int | None = None, unread_only: bool = False, limit: int = 20)` → ranked list for a smart view (`all`/`unread`/`important`/`saved`/`uncategorized`/`today`) or a category/source. Returns the same article fields as search.
- `get_article(article_id: int)` → full article: clean_title, summary, clean_text (or excerpt), url, importance_score + reason, categories, topics, is_read, is_saved.
- `get_interest_profile()` → the user's free-text interest profile, so the agent can frame relevance and prioritization. *(Added per request — pairs with the `profile://interests` resource.)*
- `list_categories()` / `list_sources()` → the controlled category set and configured feed sources (names + ids), so the agent knows valid filter values.
- **Write actions:** `mark_read(article_id)` / `mark_unread(article_id)`, `save_article(article_id)` / `unsave_article(article_id)`. Each returns the updated state. These let the agent act on the user's behalf ("mark these read", "save that one").

> **Deferred to a follow-up (after the Today-brief feature ships):** `get_daily_brief()`, `refresh_brief()`, the `brief://today` resource, and the `daily_brief` prompt. They are intentionally **excluded from this spec** so MCP has **no dependency on the Today feature** and can build in parallel. A small follow-up increment will add them once the `briefs` schema + enqueue helpers exist.

### Resources (read-only, URI-addressed)
- `article://{id}` → one article.
- `feed://{view}` → a feed listing (smart view / `category/<name>` / `source/<id>`).
- `profile://interests` → the interest profile.

### Prompts (user-invokable templates)
- `whats_latest(topic: str)` → instructs the agent to `search_articles` for `{topic}` and summarize with links.

## Config (`McpSettings`, `MCP_` prefix, subclassing `aggregator_common.config.Settings`)
`MCP_HOST` (in-container bind, default `0.0.0.0`), `MCP_PORT` (default e.g. 8765), `MCP_PATH` (default `/mcp`), `MCP_DEFAULT_LIMIT`, `MCP_MAX_LIMIT`. `DATABASE_URL` as usual. No auth settings in v1.

## Deploy wiring
- Add `mcp` to `docker-compose.yml` (dev, build) and `docker-compose.prod.yml` (image, `env_file: .env`, `DATABASE_URL=@postgres`, depends_on postgres+migrate, publish the MCP port via an `MCP_BIND`/`MCP_PORT` mapping mirroring the web service, restart unless-stopped).
- Add `mcp` to `scripts/build-images.sh` `SERVICES` and the CI build matrix (it becomes the 7th image `aggregator-mcp`).
- Add `MCP_*` vars to `.env.example`. Document in `deploy/README.md`: the endpoint is reached over Tailscale at `http://<pi>:<MCP_PORT><MCP_PATH>`; recommend Tailscale Serve for HTTPS over the tailnet; note there is no app auth (tailnet-only).

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-common/src/aggregator_common/queries.py` (new) | Shared read/mutation helpers: search, list-by-view/category/source, get-by-id, mark read/unread, save/unsave, list categories/sources, read interest profile |
| `packages/aggregator-mcp/**` | **New service**: pyproject (+ `mcp` dep), `__main__.py`, `config.py` (McpSettings), `server.py` (FastMCP app: tools/resources/prompts), `Dockerfile`, tests |
| `docker-compose.yml`, `docker-compose.prod.yml`, `scripts/build-images.sh`, `.github/workflows/ci.yml`, `.env.example`, `deploy/README.md` | Wire the new `mcp` service/image + `MCP_*` config + Tailscale access docs |

## Acceptance Criteria

- **Service scaffold:** `aggregator-mcp` package builds, exposes the `aggregator-mcp` console script, starts a FastMCP Streamable HTTP server bound per `MCP_HOST`/`MCP_PORT`/`MCP_PATH`, and logs via `configure_logging`.
- **Tools registered:** a test asserts the registered names match this exact canonical set —
  - tools: `search_articles`, `list_articles`, `get_article`, `get_interest_profile`, `list_categories`, `list_sources`, `mark_read`, `mark_unread`, `save_article`, `unsave_article`;
  - resources: `article://{id}`, `feed://{view}`, `profile://interests`;
  - prompts: `whats_latest`.
- **Query/mutation helpers (unit-tested against testcontainers Postgres):**
  - `search_articles` returns ready articles matching a query (websearch_to_tsquery), honoring limit/category/source/since.
  - `list_articles` returns ready articles for each view + category/source + unread filter, ranked as the web feeds are.
  - `get_article` returns the full article by id; unknown id is handled gracefully (clear error, not a crash).
  - `mark_read`/`mark_unread` and `save_article`/`unsave_article` flip `is_read`/`is_saved` and return updated state.
  - `get_interest_profile` returns the profile text (and a clear empty state when unset).
  - `list_categories`/`list_sources` return the enabled set.
- **Tools are thin wrappers:** each MCP tool delegates to the shared helper and returns JSON-serializable results — tested by invoking the tool callables directly against the DB (no live agent/LLM needed).
- **Deploy:** `mcp` service present in both compose files + `build-images.sh`/CI matrix (7th image); `.env.example` documents `MCP_*`; `deploy/README` documents Tailscale access. `bash scripts/run-tests.sh` is green.
- *(Manual/integration, not in the pytest suite: an MCP client (e.g. openclaw or `mcp` dev tools) connects over Streamable HTTP, lists the tools/resources/prompts, runs `search_articles` and `list_articles`, fetches an `article://{id}` resource, and invokes the `whats_latest` prompt.)*

## Pending Decisions

- **Auth:** none in v1 (Tailscale-only, per decision). If the endpoint is ever exposed beyond the tailnet, add a bearer token or MCP OAuth then.
- **No Today dependency (decoupled).** The brief-related surface (`get_daily_brief`, `refresh_brief`, `brief://today`, `daily_brief` prompt) is **deferred to a follow-up increment** and excluded here, so this spec has **zero dependency on the Today-brief feature (spec-c79ab535)** and can be planned/built in parallel with it. Once Today has shipped, a small follow-up adds the brief tools/resource/prompt (registering them + wiring to the `briefs` schema and enqueue helper).
- **Web/MCP query convergence:** shared helpers live in `aggregator-common`; migrating aggregator-web to use them is optional cleanup, out of scope here.
