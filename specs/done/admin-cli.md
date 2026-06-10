---
name: Admin CLI
id: spec-fd43343f
description: "aggregator-admin Typer CLI to operate the datastore: sources (add/list/enable/disable/set-interval/refresh-now/remove), articles (list/show/search/retry/rerank/interaction/purge), ops (status/stuck/reap/failures). All mutations go through the state machine. Depends on foundation."
dependencies: null
priority: high
complexity: null
status: done
tags:
- admin
- cli
- ops
- depends-foundation
scope:
  in: null
  out: null
feature_root_id: B-a209300c
---
# Admin CLI

## Objective

Implement `aggregator-admin`: a command-line surface for operating directly on the datastore — managing feed sources, inspecting and operating on articles, and pipeline diagnostics/maintenance. It is the human (and scripting) interface to the shared Postgres state, and the means to **bootstrap and operate** the system (e.g. add the first source so the retriever has something to poll).

First-version scope: **`sources`**, **`articles`**, and **`ops`** command groups. The `profile` group (editing the user interest profile) is deferred to when summarize-rank lands.

## Dependencies

Depends only on the **foundation** (`spec-9e974b88`, feature root `B-d585b1bf`): `aggregator_common` models, the `article_status` enum + **state machine** (`can_transition`, stage mapping), and the claim/reaper functions (`reap_stale_claims`). All mutations go through these — the CLI must not invent transitions the state machine disallows.

## Background / Decisions

- **CLI framework:** **Typer** (Click-based, type-hint driven). Console script `aggregator-admin` with subcommand groups.
- **Output:** human-readable tables by default; every read command accepts `--json` for machine-readable output.
- **Safety (uniform rule for all destructive commands — `purge`, `sources remove`):** if `--yes` is passed, proceed; else if stdin is a TTY, prompt for interactive confirmation; else (non-interactive, no `--yes`) exit non-zero without acting. This single rule applies everywhere; per-command text does not restate it.
- **The admin CLI does not fetch feeds, extract, or call the LLM.** `sources refresh-now` only nudges scheduling (`next_check_at`) so the retriever picks the source up on its next tick.
- **Article re-extraction is out of contract for v1.** The foundation state machine has no `ready/processed → pending_processing` transition, so a full re-extract is **not** offered. Admin recovery is limited to the allowed transitions (see `articles retry` / `rerank`).

## Changes

### 1. Package + entrypoint

```
packages/aggregator-admin/
  pyproject.toml                 # deps: typer, rich, aggregator-common; console_script: aggregator-admin
  src/aggregator_admin/
    __init__.py
    main.py                      # Typer app; wires the three sub-apps
    sources.py                   # `sources` group
    articles.py                  # `articles` group
    ops.py                       # `ops` group
    output.py                    # table + --json rendering, confirmation helper
  tests/
```

Each command opens a short-lived session from `aggregator_common.db` and commits per invocation.

### 2. `sources` group

| Command | Behavior |
|---|---|
| `sources add --name <n> --url <u> [--interval <s>] [--priority <p>] [--disabled]` | Insert a source (defaults: interval 3600, priority 0, enabled). Prints the new id. Duplicate `feed_url` → clear error, exit non-zero. |
| `sources list [--enabled/--disabled] [--json]` | Tabular list: id, name, url, enabled, interval, next_check_at, consecutive_failures. |
| `sources show <id> [--json]` | Full source record incl. last_error, etag/last_modified. |
| `sources enable <id>` / `sources disable <id>` | Toggle `enabled`; on enable, reset `consecutive_failures=0` and `next_check_at=now()`. |
| `sources set-interval <id> <seconds>` | Update `refresh_interval_seconds`. |
| `sources refresh-now <id>` | Set `next_check_at = now()` so the retriever fetches it next tick (does not fetch). |
| `sources remove <id> [--force] [--yes]` | Delete the source. If it has articles, the command is refused unless `--force` is given, which cascades the delete to the source's articles. Destructive — subject to the uniform `--yes`/TTY safety rule above. |

### 3. `articles` group

| Command | Behavior |
|---|---|
| `articles list [--status <s>] [--source <id>] [--limit <n>] [--json]` | List with id, source, status, feed/clean title, importance_score, retrieved_at. Default limit 50, newest first. |
| `articles show <id> [--json]` | Full article incl. status, claim fields, last_error, summary/topics/score when present. |
| `articles search <query> [--limit <n>] [--json]` | Full-text search via `search_vector` (only matches processed articles; documented as such). |
| `articles retry <id>` / `articles retry --status <failed_processing\|failed_ranking> [--limit <n>]` | Transition a failed article back to its pending state per the **stage mapping** (`failed_processing→pending_processing`, `failed_ranking→pending_ranking`), clearing `last_error`/`retry_count`/claim. Single-id form rejects an article not in a failed state via `can_transition`. Batch (`--status`) form is **skip-and-continue**: it only acts on rows in that status, prints a summary count of how many were retried, and never aborts mid-batch. |
| `articles rerank <id>` | `ready → pending_ranking` (allowed transition). Rejects non-`ready` articles. |
| `articles mark-read <id>` / `mark-unread` / `save` / `unsave` / `hide` / `unhide` | Set the corresponding interaction flag(s) (`is_read`/`read_at`, `is_saved`, `is_hidden`). |
| `articles purge [--status <s>] [--source <id>] [--before <iso-date>] --yes` | Delete articles matching the filter. Requires at least one filter and `--yes`. Prints count deleted. |

All status transitions go through `aggregator_common` `can_transition` + the claim helpers; an illegal transition is refused with a clear message, not forced.

### 4. `ops` group

| Command | Behavior |
|---|---|
| `ops status [--json]` | Counts by `status`; counts of in-flight (`claimed_at IS NOT NULL`), failed, and ready-to-display; source counts (enabled/disabled). |
| `ops stuck [--lease-seconds <s>] [--json]` | List rows whose `claimed_at` is older than the lease (stale claims): id, status, claimed_by, claimed_at. `--lease-seconds` defaults to the foundation's `CLAIM_LEASE_SECONDS` setting. |
| `ops reap [--lease-seconds <s>]` | Run `aggregator_common.reap_stale_claims` and report the number released. `--lease-seconds` defaults to the foundation's `CLAIM_LEASE_SECONDS` setting. |
| `ops failures [--stage processing\|ranking] [--limit <n>] [--json]` | List `failed_*` articles with `last_error`, `retry_count`, source. |

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-admin/pyproject.toml` | package, deps (typer, rich, aggregator-common), `aggregator-admin` console script |
| `src/aggregator_admin/main.py` | Typer app wiring the three groups |
| `src/aggregator_admin/sources.py` | sources commands |
| `src/aggregator_admin/articles.py` | articles commands |
| `src/aggregator_admin/ops.py` | ops commands |
| `src/aggregator_admin/output.py` | table/JSON rendering + confirmation helper |
| `packages/aggregator-admin/tests/**` | command tests (testcontainers + Typer `CliRunner`, in-process) |

## Acceptance Criteria

- `sources add --name X --url U` inserts an enabled source with defaults; `sources list` shows it; adding the same `--url` again exits non-zero with a duplicate message.
- `sources enable/disable` flips `enabled`; enable also resets `consecutive_failures` to 0 and `next_check_at` to now.
- `sources set-interval` updates `refresh_interval_seconds`; `sources refresh-now` sets `next_check_at <= now()`.
- `sources remove` on a source with articles is refused without `--force`; with `--force --yes` it deletes the source and its articles.
- `articles list --status pending_processing --json` returns valid JSON filtered to that status; `--limit` is honored.
- `articles retry` moves a `failed_processing` article to `pending_processing` and a `failed_ranking` article to `pending_ranking`, clearing claim/error/retry; it refuses an article that is not in a failed state.
- `articles rerank` moves a `ready` article to `pending_ranking` and refuses a non-`ready` article.
- `articles purge` with no filter is refused; with a filter and `--yes` it deletes matching rows and prints the count.
- `ops status` counts match the database; `ops reap` releases stale claims via `reap_stale_claims` and reports the count; `ops failures --stage ranking` lists only `failed_ranking` rows.
- Destructive commands (`purge`, `sources remove`) refuse to proceed without `--yes`.
- Bad input (unknown id, illegal transition, missing required filter) exits non-zero with a clear, actionable message.
- Full suite green via `uv run pytest` using testcontainers and Typer's `CliRunner` in-process (no external services beyond the ephemeral Postgres).

## Pending Decisions

- `profile` command group (view/edit interest profile) is deferred to the summarize-rank milestone.
- Whether to support full article re-extraction depends on adding a `ready/failed → pending_processing` transition to the foundation state machine; deferred (not in v1).
- `rich` is the assumed table renderer; a plain-text fallback is acceptable if `rich` is undesirable.
