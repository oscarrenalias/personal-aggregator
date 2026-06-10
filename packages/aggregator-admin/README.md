# aggregator-admin

Command-line interface for operating the personal-aggregator datastore. It provides three command groups — `sources`, `articles`, and `ops` — that give direct read/write access to the shared Postgres state used by the retriever, processor, and summarize-rank pipeline services.

The admin CLI is a maintenance tool and does **not** replace any pipeline service. It reads and writes the same rows that the pipeline workers do, so it respects the article state machine and all allowed transitions.

## Installation

The package lives in the uv workspace. No separate install step is needed when working in the repo:

```
uv run aggregator-admin <command>
```

`DATABASE_URL` must be set in the environment or a `.env` file in the working directory before running any command.

## Conventions

### `--json`

Every read command (`list`, `show`, `search`, `status`, `stuck`, `failures`) accepts `--json`. When set, the command emits a JSON array (or object for `ops status`) to stdout instead of the default Rich table. Use this for scripting or piping to `jq`.

### `--yes` / TTY confirmation

Destructive commands (`sources remove`, `articles purge`) require confirmation before acting:

- **Interactive session (TTY):** the command prompts `[y/N]`; anything other than `y`/`yes` aborts.
- **Non-interactive session (no TTY):** pass `--yes` (or `-y`) to proceed without a prompt. Without `--yes` the command exits with a non-zero code and prints an error.

Scripts must always pass `--yes` to suppress the interactive prompt.

## Command groups

### `sources` — manage feed sources

```
uv run aggregator-admin sources <subcommand>
```

| Subcommand | Arguments | Options | Description |
|---|---|---|---|
| `list` | | `--enabled` / `--disabled`, `--json` | List all sources. Filter to enabled or disabled only. |
| `show` | `SOURCE_ID` | `--json` | Show full details for a source, including etag, last_modified, and last_error. |
| `add` | | `--name/-n` (req.), `--url/-u` (req.), `--interval` (default 3600 s), `--priority/-p` (default 0), `--disabled` | Add a new feed source. Prints the new source ID on success. Returns an error if the URL already exists. |
| `enable` | `SOURCE_ID` | | Enable a source, clear its failure counter, and schedule it for immediate check. |
| `disable` | `SOURCE_ID` | | Disable a source (the retriever will skip it on its next poll). |
| `set-interval` | `SOURCE_ID SECONDS` | | Update the refresh interval for a source. |
| `remove` | `SOURCE_ID` | `--force`, `--yes/-y` | Delete a source. Refused when the source has associated articles unless `--force` is given; `--force` cascade-deletes all articles first. Requires confirmation. |
| `refresh-now` | `SOURCE_ID` | | Set `next_check_at` to now so the retriever picks the source up on its next poll cycle. |

### `articles` — inspect and operate on articles

```
uv run aggregator-admin articles <subcommand>
```

| Subcommand | Arguments | Options | Description |
|---|---|---|---|
| `list` | | `--status`, `--source SOURCE_ID`, `--limit` (default 50), `--json` | List articles newest-first. Filter by status string and/or source ID. |
| `show` | `ARTICLE_ID` | `--json` | Show full article details including claim fields, LLM outputs, and reader flags. |
| `search` | `QUERY` | `--limit` (default 50), `--json` | Full-text search over processed articles using Postgres `tsvector`. Only articles that have been through the processor and have a `search_vector` are matched. |
| `retry` | `[ARTICLE_ID]` | `--status` | Retry a single failed article, or all articles with a given failed status. Provide either an article ID or `--status failed_processing` / `--status failed_ranking`, not both. Resets `claimed_by`, `claimed_at`, `last_error`, and `retry_count`. |
| `rerank` | `ARTICLE_ID` | | Queue a `ready` article for re-ranking by moving it back to `pending_ranking`. |
| `mark-read` | `ARTICLE_ID` | | Set `is_read = true` and record `read_at`. |
| `mark-unread` | `ARTICLE_ID` | | Clear `is_read` and `read_at`. |
| `save` | `ARTICLE_ID` | | Set `is_saved = true`. |
| `unsave` | `ARTICLE_ID` | | Clear `is_saved`. |
| `hide` | `ARTICLE_ID` | | Set `is_hidden = true`. |
| `unhide` | `ARTICLE_ID` | | Clear `is_hidden`. |
| `purge` | | `--status`, `--source SOURCE_ID`, `--before ISO_DATE`, `--yes` | Permanently delete articles matching any combination of status, source, and retrieval date. At least one filter is required. `--before` accepts an ISO date string (e.g. `2024-01-01`). Requires confirmation. |

### `ops` — pipeline diagnostics and maintenance

```
uv run aggregator-admin ops <subcommand>
```

| Subcommand | Options | Description |
|---|---|---|
| `status` | `--json` | Print article counts for every status value, the number of in-flight (claimed) articles, and enabled/disabled source counts. |
| `stuck` | `--lease-seconds`, `--json` | List articles whose claim is older than the lease threshold. Defaults to `CLAIM_LEASE_SECONDS` from config (600 s). These rows would be reaped on the next reaper cycle. |
| `failures` | `--stage processing\|ranking`, `--limit` (default 50), `--json` | List failed articles with `last_error`, `retry_count`, and source name. Omit `--stage` to show all failures. |
| `reap` | `--lease-seconds` | Immediately release all stale claims older than the lease threshold, returning the affected articles to their pending state so workers can re-claim them. Defaults to `CLAIM_LEASE_SECONDS` from config. |

## Known limitations (v1)

- **No article re-extraction.** There is no command to re-run content extraction on a processed article. To force re-processing, use `articles retry --status failed_processing` (if the article previously failed) or reset the article's status directly in the database.
- **Profile management deferred.** The `profile` command group for managing the interest profile used by the summarize-rank service is not included in v1. It will be added as part of the summarize-rank milestone.
