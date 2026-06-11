# aggregator-admin

Command-line interface for operating the personal-aggregator datastore. It provides four command groups — `sources`, `articles`, `ops`, and `profile` — that give direct read/write access to the shared Postgres state used by the retriever, processor, and summarize-rank pipeline services.

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

### `--file`

`profile set` accepts either an inline text argument or `--file <path>` pointing to a plain-text file. The two are mutually exclusive — providing both is an error.

### `--yes` / TTY confirmation

Destructive commands (`sources remove`, `articles purge`, `profile clear`, `articles rerank --all`) require confirmation before acting:

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
| `show` | `ARTICLE_ID` | `--json` | Show full details for a single article. Human display renders all columns (except `search_vector`) as a field/value table; long fields (`clean_text`, `raw_payload`) are truncated at 300 characters with a `… [truncated]` indicator. `--json` emits every DB column (including `header_image_url`) at full length with no truncation. |
| `search` | `QUERY` | `--limit` (default 50), `--json` | Full-text search over processed articles using Postgres `tsvector`. Only articles that have been through the processor and have a `search_vector` are matched. |
| `retry` | `[ARTICLE_ID]` | `--status` | Retry a single failed article, or all articles with a given failed status. Provide either an article ID or `--status failed_processing` / `--status failed_ranking`, not both. Resets `claimed_by`, `claimed_at`, `last_error`, and `retry_count`. |
| `rerank` | `[ARTICLE_ID]` | `--all`, `--yes/-y` | Queue a `ready` article for re-ranking by moving it back to `pending_ranking`. Pass `--all` instead of an article ID to requeue every `ready` article at once. `ARTICLE_ID` and `--all` are mutually exclusive; providing both is an error. `--all` requires confirmation. |
| `mark-read` | `ARTICLE_ID` | | Set `is_read = true` and record `read_at`. |
| `mark-unread` | `ARTICLE_ID` | | Clear `is_read` and `read_at`. |
| `save` | `ARTICLE_ID` | | Set `is_saved = true`. |
| `unsave` | `ARTICLE_ID` | | Clear `is_saved`. |
| `hide` | `ARTICLE_ID` | | Set `is_hidden = true`. |
| `unhide` | `ARTICLE_ID` | | Clear `is_hidden`. |
| `purge` | | `--status`, `--source SOURCE_ID`, `--before ISO_DATE`, `--yes` | Permanently delete articles matching any combination of status, source, and retrieval date. At least one filter is required. `--before` accepts an ISO date string (e.g. `2024-01-01`). Requires confirmation. |

> **Column sets:** `list` and `search` return a fixed subset of columns (no `raw_payload`, `clean_text`, or internal metadata). `show` returns every DB column except `search_vector`, so it is the canonical way to inspect any field — including `header_image_url`, `raw_payload`, and `clean_text` — on a single article.

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

### `profile` — manage the interest profile

The `profile` group manages the free-text interest profile that the summarize-rank service uses when scoring articles. The profile is a singleton row in the database: there is at most one active profile at any time.

```
uv run aggregator-admin profile <subcommand>
```

| Subcommand | Arguments | Options | Description |
|---|---|---|---|
| `show` | | `--json` | Print the current profile text and its last-updated timestamp. Prints `(empty — neutral ranking)` when no profile is set. `--json` emits `{"profile_text": "...", "updated_at": "..."}`. |
| `set` | `[TEXT]` | `--file PATH` | Set (or replace) the interest profile. Pass the profile as an inline argument **or** via `--file <path>`; providing both is an error. Prints the character count of the saved profile on success. |
| `clear` | | `--yes/-y` | Clear the profile (sets it to an empty string). Requires confirmation; pass `--yes` to skip the prompt in non-interactive sessions. |

**Examples:**

```bash
# Set profile from inline text
uv run aggregator-admin profile set "I'm interested in AI, distributed systems, and climate tech."

# Set profile from a file
uv run aggregator-admin profile set --file ~/my-interests.txt

# Show the current profile
uv run aggregator-admin profile show

# Show as JSON (for scripting)
uv run aggregator-admin profile show --json

# Clear the profile (interactive confirmation)
uv run aggregator-admin profile clear

# Clear without prompting (non-interactive / CI)
uv run aggregator-admin profile clear --yes
```

## Known limitations (v1)

- **No article re-extraction.** There is no command to re-run content extraction on a processed article. To force re-processing, use `articles retry --status failed_processing` (if the article previously failed) or reset the article's status directly in the database.
