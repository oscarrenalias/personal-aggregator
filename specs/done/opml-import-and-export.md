---
name: OPML import and export
id: spec-88dd9cef
description: "aggregator-admin: add `sources import-opml <file>` and `sources export-opml [file]` to the admin CLI. Import parses an OPML file (e.g. a Feedly export), flattens any folder structure, and adds each feed as a source, deduping by feed URL (DB-unique). Export emits all sources as a valid OPML document. Confined to aggregator-admin; reuses the existing Source model and session helpers. No schema change."
dependencies: null
priority: medium
complexity: low
status: done
tags:
- admin
- opml
- sources
- feeds
scope:
  in: "aggregator-admin only: new import-opml / export-opml commands under the existing `sources` Typer app, plus a small OPML parse/serialize helper module and tests. Reuses aggregator_common Source model + get_session()."
  out: "No schema change (folders are flattened, not stored), no changes to other services, no GUI/web import (admin CLI only in this spec), no feed validation/fetching at import time (URLs are trusted as provided)."
feature_root_id: B-994a0131
---
# OPML import and export

## Objective

Add OPML import/export to the admin CLI so feeds can be bulk-imported from another reader (e.g. a Feedly OPML export) and exported back out. Confined to `aggregator-admin`; reuses the existing `Source` model and `get_session()`. No database schema change — Feedly folder structure is flattened on import.

## Problems to Fix

- There is no bulk way to add sources. Today feeds are added one at a time via `aggregator-admin sources add -n … -u …` (`sources.py`), which is impractical for migrating an existing reader's subscriptions. A user switching from Feedly has an OPML file with many feeds (often grouped in folders) and needs to import them quickly.
- There is no way to export the configured sources for backup or migration to another reader.

## Background (verified against the codebase)

- The `Source` model (`packages/aggregator-common/src/aggregator_common/models.py:25`) has `name`, `feed_url` (**`unique=True`**), `enabled`, `refresh_interval_seconds` (default 3600), `priority` (default 0). The unique constraint on `feed_url` means duplicate imports raise `IntegrityError` — the import must dedupe gracefully rather than abort.
- The admin `sources` commands live in `packages/aggregator-admin/src/aggregator_admin/sources.py` as a Typer app (`sources_app`), using `aggregator_common.db.get_session()` and the `confirm` / `json_or_table` helpers from `aggregator_admin.output`. The existing `add` command constructs `Source(name=…, feed_url=…, enabled=…, refresh_interval_seconds=…, priority=…)` and catches `IntegrityError` for the duplicate-URL case.
- OPML is XML: `<opml><body><outline …>`. Feed entries are `<outline>` elements carrying an `xmlUrl` attribute (and usually `type="rss"`, `text`/`title` for the name). Feeds may be nested inside folder `<outline>` elements (no `xmlUrl`) — these must be walked recursively and flattened.

## Changes

### A. OPML helper module (parse + serialize)
- Add `packages/aggregator-admin/src/aggregator_admin/opml.py` with two pure, testable functions:
  - `parse_opml(text: str) -> list[ParsedFeed]` — parse with the stdlib `xml.etree.ElementTree`, recursively walk all `<outline>` elements, and return one entry per outline that has a non-empty `xmlUrl`. Each entry carries the feed URL and a name (prefer `title`, fall back to `text`, fall back to the URL). Outlines without `xmlUrl` (folders) are descended into but not themselves emitted — i.e. folders are flattened. De-duplicate by URL **within the file**. Raise a clear, catchable error (e.g. `ValueError`) on malformed XML / not-an-OPML-document.
  - `build_opml(sources) -> str` — produce a valid OPML 2.0 document: `<opml version="2.0">` with `<head><title>Personal Aggregator subscriptions</title></head>` and one `<outline type="rss" text="{name}" title="{name}" xmlUrl="{feed_url}"/>` per source, under `<body>`. Properly XML-escape attribute values. Output is **deterministic**: sources ordered by `name` (case-insensitive), then `id` as tiebreak.

### B. `sources import-opml` command
- Add `@sources_app.command("import-opml")` taking a positional `file` (path to the OPML file) and options:
  - `--dry-run` — parse and report what *would* be imported without writing.
  - `--interval` (default 3600) and `--disabled` — applied to all imported sources (mirrors `add`).
- Behavior: read the file (clear error + non-zero exit if missing/unreadable), `parse_opml` it (clear error + non-zero exit on malformed), then for each parsed feed insert a `Source`, **skipping** any whose `feed_url` already exists (check existing URLs up front and/or catch `IntegrityError` per row so one duplicate doesn't abort the batch). Print a summary: counts of **added / skipped (already present) / total parsed**, and the per-feed disposition. Add a `--json` flag (consistent with `sources list`/`show`, which take `as_json` and use `json_or_table`) that emits the summary as a machine-readable JSON object (e.g. `{"added": [...], "skipped": [...], "total": N}`) via `typer.echo(json.dumps(...))`; default output is the human-readable summary.

### C. `sources export-opml` command
- Add `@sources_app.command("export-opml")` taking an optional positional `file` (write target). With no argument, write the OPML document to **stdout**; with a path, write to that file. Export **all** sources (a future `--enabled-only` is out of scope). Use `build_opml`.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-admin/src/aggregator_admin/opml.py` | **New** — `parse_opml()` + `build_opml()` helpers (stdlib `xml.etree.ElementTree`) |
| `packages/aggregator-admin/src/aggregator_admin/sources.py` | Add `import-opml` and `export-opml` commands to `sources_app`, reusing `get_session()` and the `Source` model |
| `packages/aggregator-admin/tests/` | New tests for OPML parse/serialize + the two commands (see Acceptance Criteria) |
| `README.md` (and/or admin docs) | Document the import/export commands under the admin CLI usage |

## Acceptance Criteria

- **Parse + flatten:** `parse_opml` on a Feedly-style OPML with **nested folder outlines** returns every feed (flattened), each with the correct URL and a name derived from `title`/`text`/URL. Folder outlines (no `xmlUrl`) produce no entry. A test asserts this against a representative nested OPML sample.
- **Dedupe:** importing an OPML that contains a URL already in the DB (and/or a URL repeated within the file) adds the new feeds and **skips** the duplicates without aborting; the summary reports added vs skipped counts. A test asserts existing-URL and within-file duplicates are skipped and the rest are added.
- **Dry-run:** `import-opml --dry-run` makes **no** DB changes but prints the same would-import summary. A test asserts the DB is unchanged after a dry-run.
- **Malformed input:** `import-opml` on a malformed/non-OPML file exits non-zero with a clear error and writes nothing. A test asserts the non-zero exit and no DB change.
- **Export + round-trip:** `export-opml` produces a valid OPML document containing every source's name and `xmlUrl`. Re-importing the exported document into the same DB adds **zero** new sources (all skipped as duplicates). A test asserts the round-trip and that `parse_opml(build_opml(sources))` recovers the same set of URLs.
- All existing `aggregator-admin` tests continue to pass; `bash scripts/run-tests.sh` is green.

## Pending Decisions

None for v1. Folder structure is intentionally flattened (no schema change); preserving folders as source groups, `--enabled-only` export, and any web/GUI import are explicitly deferred.
