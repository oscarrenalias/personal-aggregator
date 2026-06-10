---
name: Admin Profile Commands and Bulk Rerank
id: spec-f95506ce
description: "Add aggregator-admin 'profile' command group (show/set/clear the interest_profile singleton consumed by summarize-rank; input is a single free-text blob, inline or --file) plus bulk 'articles rerank --all/--status' to apply a changed profile to existing ready articles. Unblocks the deferred admin profile group."
dependencies: null
priority: high
complexity: null
status: planned
tags:
- admin
- cli
- profile
- rerank
- depends-foundation
scope:
  in: null
  out: null
feature_root_id: B-9bd94eab
---
# Admin Profile Commands and Bulk Rerank

## Objective

Add a `profile` command group to `aggregator-admin` so the user can view, set, and clear the **interest profile** (the `interest_profile` singleton consumed by summarize-rank), and add a **bulk** option to `articles rerank` so a changed profile can be applied to already-`ready` articles. This unblocks the `profile` group that the admin CLI spec (`spec-fd43343f`) deferred until summarize-rank landed (it now has).

## Dependencies

Depends on the **foundation** (`interest_profile` model; `article_status` state machine + `can_transition`) and the existing **admin CLI** (`spec-fd43343f`): the Typer app in `aggregator_admin/main.py`, the output/confirmation helpers in `output.py`, and the existing `articles rerank` command. Reuse them.

## Background / Decisions

- **The interest profile is a single free-text blob.** `interest_profile` is a singleton row (boolean PK `id = true`) with one meaningful column, `profile_text`, plus `updated_at`. summarize-rank reads `profile_text` each cycle; empty/missing ⇒ neutral-mode ranking. There is no structured schema — the only input is the text.
- **Input methods (keep simple):** `profile set` takes the text either as a positional argument or from a file via `--file` (for long/multi-line interests). `$EDITOR` and stdin are out of scope for v1.
- **Safety:** `profile clear` and bulk `articles rerank` follow the existing uniform `--yes`/TTY confirmation rule from the admin CLI.
- The admin entrypoint already calls `load_env()` + `configure_logging(..., stream=sys.stderr)`; the new commands inherit that.

## Changes

### 1. `profile` command group (`src/aggregator_admin/profile.py`)

| Command | Behavior |
|---|---|
| `profile show [--json]` | Print `profile_text` and `updated_at`. If no row exists or the text is empty, print `(empty — neutral ranking)`. `--json` always emits exactly `{"profile_text": <str>, "updated_at": <iso-8601 str or null>}` — empty text is `""` and a missing row renders `updated_at` as `null`. |
| `profile set <text>` / `profile set --file <path>` | Upsert the singleton row (`id = true`), setting `profile_text` and `updated_at = now()`. Exactly one of `<text>` or `--file` must be given — error clearly (non-zero) if both or neither. Print a confirmation with the resulting character count. |
| `profile clear` | Reset `profile_text` to `''` (return to neutral mode). Subject to the uniform `--yes`/TTY safety rule. |

Wire the `profile` sub-app into `main.py` alongside `sources`/`articles`/`ops`.

The upsert must preserve the singleton invariant (only ever one row); use `INSERT ... ON CONFLICT (id) DO UPDATE` or read-modify-write the `id = true` row.

### 2. Bulk `articles rerank` (`src/aggregator_admin/articles.py`)

Extend the existing `articles rerank`:
- Keep `articles rerank <id>` (single article, `ready → pending_ranking`).
- Add a bulk form: `articles rerank --all` (every `ready` article). It requires `--yes` (uniform safety rule), transitions each via `can_transition` (`ready → pending_ranking`, clearing claim/error), is **skip-and-continue** (ignores non-`ready` rows), and prints the count requeued.
- `<id>` and `--all` are mutually exclusive (error if combined); exactly one is required.
- (`--status` is intentionally omitted — `ready` is the only valid source state for re-rank, so `--all` suffices.)

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-admin/src/aggregator_admin/profile.py` | new `profile` group (show/set/clear) |
| `packages/aggregator-admin/src/aggregator_admin/main.py` | register the `profile` sub-app |
| `packages/aggregator-admin/src/aggregator_admin/articles.py` | bulk `rerank --all`/`--status` |
| `packages/aggregator-admin/tests/**` | tests for profile + bulk rerank |
| `packages/aggregator-admin/README.md` (+ `CLAUDE.md` if needed) | document the new commands |

## Acceptance Criteria

- `profile set "agentic coding, EVs"` creates the singleton row; `profile show` prints that text and an `updated_at`; running `profile set` again **replaces** the text and leaves exactly **one** row (singleton invariant holds).
- `profile set --file <path>` loads the text from the file; `profile set` with **neither** text nor `--file`, or with **both**, exits non-zero with a clear message.
- `profile show` against an empty/no-row table prints the empty/neutral indicator (and valid JSON under `--json`).
- `profile clear` with `--yes` empties the text; without `--yes` in a non-interactive context it exits non-zero without changing anything.
- `articles rerank --all` with `--yes` transitions every `ready` article to `pending_ranking` and prints the count; non-`ready` rows are untouched; bulk without `--yes` (non-interactive) exits non-zero.
- `articles rerank <id>` still works for a single `ready` article and is rejected (mutually exclusive) if combined with `--all`; invoking `rerank` with neither `<id>` nor `--all` errors non-zero.
- Full suite green via `uv run pytest` (testcontainers Postgres; no external services).

## Pending Decisions

- `$EDITOR`-based `profile edit` and stdin input are deferred (keep v1 simple).
- Whether bulk rerank should also cover `failed_ranking` is out of scope here — `articles retry` already handles failed-state recovery.
