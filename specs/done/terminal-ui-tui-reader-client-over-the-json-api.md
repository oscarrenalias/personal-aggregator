---
name: Terminal UI (TUI) reader client over the JSON API
id: spec-41c4d8de
description: "A terminal reader client (new aggregator-tui package) built on Textual that consumes the JSON API (/api/v1, spec-0a1c22b3) over HTTP — dogfooding the API. Mirrors the web UI's 3-panel layout (nav/list/reader) and keyboard model (j/k/v/n/m/s///?). Read + non-destructive writes (mark read/save, thread dismiss). Depends on the JSON API spec. No auth (API has none yet); no DB access (pure HTTP client). Framework choice (Textual) flagged for confirmation."
dependencies: null
priority: medium
complexity: null
status: done
tags:
- tui
- textual
- client
- ux
- api
- reader
scope:
  in: null
  out: null
feature_root_id: null
---
# Terminal UI (TUI) reader client over the JSON API

> **Dependency:** this consumes the JSON API from `json-api-for-mobile-and-tui-clients-no-auth`
> (spec-0a1c22b3). It can be built once those read + non-destructive-write endpoints exist.

> **UI framework = [Textual](https://textual.textualize.io/) (confirmed by the user).** The
> interactive TUI framework by the Rich authors (built on Rich, which is already in the stack).
> Adds `textual` as a dependency of the new `aggregator-tui` package only.

## Objective

Provide a **terminal reader client** for the aggregator so the news can be read from a terminal,
and to **dogfood the JSON API** (spec-0a1c22b3) by building a real app on it. The TUI talks only
to the `/api/v1` HTTP API (never the DB directly), mirrors the web UI's **3-panel layout**
(nav sidebar · article list · reader pane), and reuses the web UI's **keyboard model**
(`j`/`k`/`v`/`n`/`m`/`/`/`?`) so muscle memory carries over.

## Problems to Fix

- There is no way to read the aggregator from a terminal; the only client is the HTML web UI.
- The new JSON API needs a real consumer to validate it is sufficient/ergonomic for an app — a TUI
  is a fast, low-cost way to test-drive it end to end.

## Changes

### 1. New `aggregator-tui` package

- New workspace package `packages/aggregator-tui/` with a console-script entrypoint
  (`aggregator-tui`). Depends on `textual` and `httpx`. **Does not depend on `aggregator-common`
  or the DB** — it is a pure HTTP client of `/api/v1`, which keeps it honest as an API consumer.
- Config: API base URL from `AGGREGATOR_API_URL` env / `--api-url` flag (default
  `http://localhost:8000/api/v1`; point it at the Pi/Tailscale URL to read live data).
- **API-client seam (testability):** all HTTP access goes through one internal `ApiClient` class
  wrapping `httpx.AsyncClient`, with one typed method per endpoint (`list_articles`,
  `search_articles`, `get_article`, `list_threads`, `get_thread`, `get_thread_members`,
  `get_brief_today`, `list_sources`, `list_categories`, `mark_read`/`mark_unread`/`save`/`unsave`,
  `dismiss_thread`/`restore_thread`). **Tests stub `ApiClient`** (not httpx) with fixtures whose
  JSON shapes match the API response models defined in spec-0a1c22b3 (the source of truth). This
  decouples the TUI's test suite from a live API.

### 2. Three-panel Textual layout

- A Textual `App` with a horizontal 3-pane layout mirroring the web UI:
  - **Left — nav sidebar:** Today, Threads, Smart Views (All/Unread/Saved/Important/Uncategorized),
    Categories, Sources (from `GET /categories`, `GET /sources`).
  - **Centre — list pane:** the selected view's articles (or threads/brief), with a header naming
    the view (consistent with the web's list-pane header).
  - **Right — reader pane:** the selected article's title, source, date, summary, topics, body
    (from `GET /articles/{id}`); for threads, the thread summary + members.
- Responsive (concrete): below **100 columns**, show a **single focused pane** (default = the
  **list** pane); `Enter`/`o` switches focus to the reader, `Esc`/Back returns to the list, and a
  key (e.g. `Tab`) cycles to the sidebar. At ≥100 columns show all three panes. Re-evaluate on
  Textual's `Resize` event so mid-session terminal resizing switches between the two layouts. (The
  100-column breakpoint is provisional — tunable during implementation against a real terminal.)

### 3. Keyboard model (mirror the web UI)

Reuse the web shortcuts (defined in `shell.html`):

| Key | Action |
|---|---|
| `j` / `k` | next / previous item in the list |
| `Enter` / `o` | open selected article into the reader pane |
| `v` | open the article's source URL in the system browser (`webbrowser.open`) |
| `m` | toggle read / unread (selected) |
| `n` | mark read and advance to next |
| `s` | save / unsave (bookmark) toggle |
| `/` | focus search (calls `GET /articles/search`) |
| `g` / `G` | jump to top / bottom of list |
| `Tab` / arrows | move focus between panes |
| `?` | keyboard-shortcuts help overlay |
| `q` | quit |
| (threads view) `d` | dismiss / restore the selected thread |

### 4. API usage (read + non-destructive writes)

- **Reads:** `GET /articles` (view/category/source/unread filters + cursor pagination — load more
  when scrolling to the end), `GET /articles/search`, `GET /articles/{id}`, `GET /threads`,
  `GET /threads/{id}` + `/members`, `GET /brief/today`, `GET /sources`, `GET /categories`.
- **Writes (non-destructive, the ones the API exposes):** `POST /articles/{id}/read|unread|save|
  unsave`, `POST /threads/{id}/dismiss|restore`. Update the on-screen state optimistically after a
  2xx; surface a transient error line on failure.
- Use `httpx.AsyncClient` (Textual is async). One shared client; sensible timeouts; show a clear
  message if the API is unreachable rather than crashing.
- **Search results** paginate via the same `next_cursor` mechanism as the feed list; an **empty**
  result set shows an "No results" placeholder in the list pane; a **search/API error** shows the
  same transient error line as other failed calls (no crash).

### 5. Out of scope (this phase)

- **Auth** — the API has none yet; the TUI just hits it over the perimeter. (When the API gains
  auth, the TUI will need to pass the token/credential — future.)
- **Destructive/admin actions** — no source/category management, no brief refresh (the API doesn't
  expose them).
- **Offline cache / sync**, theming/config beyond the API URL, and packaging/distribution beyond
  the console entrypoint.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-tui/` (new) | new package: Textual `App`, 3-pane widgets, API client (httpx), keybindings, entrypoint; `pyproject.toml` (workspace member; deps `textual`, `httpx`) |
| `pyproject.toml` (root) | add `aggregator-tui` to the workspace members if needed |
| `packages/aggregator-tui/tests/` | tests via Textual's `App.run_test()` pilot against a mocked/stubbed API client: navigation keys move selection, `Enter` loads the reader, `m`/`n`/`s` issue the right API calls, search filters the list, unreachable-API shows an error |
| `CLAUDE.md` | document the `aggregator-tui` package, how to run it, and the `AGGREGATOR_API_URL` setting |

## Acceptance Criteria

**Verifiable with the pilot harness + stubbed `ApiClient` (no live API needed — these gate the
merge):**
- `j`/`k` move selection, `Enter`/`o` open the reader, `v` opens the source in the browser, `m`
  toggles read, `n` marks-read-and-advances, `s` toggles save, `/` searches, `?` shows help, `q`
  quits — matching the web UI's keys.
- Reader-state actions (`m`/`n`/`s`, thread `d`) invoke the corresponding `ApiClient` write methods
  (read/unread/save/unsave, dismiss/restore) and reflect the new state in the UI.
- List + search pagination request further pages via `next_cursor`; empty search shows a "No
  results" placeholder.
- A stubbed `ApiClient` that raises (unreachable) shows a clear error message, not a crash.
- The TUI imports **only** its own code + `textual`/`httpx` (no `aggregator-common`/DB import);
  API base URL is configurable.
- Below 100 columns the app shows a single focused pane (default list); at ≥100 columns, three.
- Full gate green using the stubbed client.

**Smoke check requiring a live `/api/v1` (manual, post-API-merge — does NOT gate this feature's
merge):**
- Pointed at a running API, the sidebar lists real views, the list shows real articles, and the
  reader renders a selected article — end-to-end over HTTP.

Because the test suite runs entirely against the stubbed `ApiClient`, this feature's gate can go
green and merge **independently** of the live API; the JSON API (spec-0a1c22b3) is nonetheless a
runtime prerequisite to actually use the TUI, and will be merged first in practice.

## Resolved Decisions

- **Framework = Textual** — confirmed by the user.
- **`v` key** = open the article's source URL in the system browser — matches the web UI's `v`
  ("Open source in new tab"); parity preserved.
- **Thread `d` key** = dismiss/restore the selected thread in the threads view (toggles).
- **Live-refresh** = manual `r` to refresh the current view in v1; periodic auto-refresh deferred.
- **Test seam** = stub the internal `ApiClient`; gate runs without a live API (see Acceptance
  Criteria + §1).
- **Responsive breakpoint** = 100 columns (provisional, tunable) (see §2).

## Pending Decisions

- None blocking. (Breakpoint value and exact key choices are tunable during implementation.)
