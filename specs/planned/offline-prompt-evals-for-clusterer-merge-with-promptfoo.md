---
name: Offline prompt evals for clusterer merge with promptfoo
id: spec-84a6008f
description: "Offline promptfoo eval for the clusterer.merge prompt: frozen dataset export, human labels, prompt × model runs and score reports (ADR-f8cc0455)"
dependencies: null
priority: medium
complexity: null
status: planned
tags:
- llm
- evals
- clusterer
scope:
  in: null
  out: null
feature_root_id: B-996c3175
---
# Offline prompt evals for clusterer merge with promptfoo

## Objective

Build a repeatable offline eval for the `clusterer.merge` prompt ("are these two threads the same story?") using [promptfoo](https://www.promptfoo.dev/), as decided in ADR-f8cc0455. After this spec, the operator can:

1. export the merge pairs seen by the shadow experiment into a frozen, dated dataset with one command;
2. label pairs in a spreadsheet and fold the labels back into a labels file;
3. run every prompt variant × model over all labelled pairs with one command, and see accuracy, wrong merges and missed merges per combination, overall and with tagged subsets excluded;
4. browse the results side by side in the promptfoo viewer.

This spec builds and tests the tooling only. **It does not run a real (paid) eval and does not export production data**; the operator does both after merge. No service code changes: the production merge prompt, `PROMPT_VARIANTS` and the clusterer are untouched.

## Problems to Fix

1. **Prompt comparisons are manual.** Comparing merge prompts has meant ad-hoc scratch scripts, hand-built spreadsheets and hand-tallied scores, about 24 pairs at a time.
2. **No ground truth in the pipeline.** `aggregator-admin llm-experiment-report` measures agreement with control. On the operator's 24 hand-labelled pairs this ranked the arms in the opposite order to real accuracy (see ADR-f8cc0455, Context).
3. **Inputs decay.** Merge prompts send each thread's `representative_title` and `rolling_summary`. Both change as threads grow, and threads disappear when production merges them. Without a frozen snapshot, a pair cannot be re-evaluated later.

## Changes

### 1. Directory layout

Create a top-level `evals/` tree. It is not a uv workspace member, ships in no image, and is not under `packages/`.

```
evals/
  README.md
  package.json               # devDependency promptfoo pinned to exactly 0.125.1
  package-lock.json          # committed
  .gitignore                 # node_modules/, results/
  shared/
    __init__.py
    litellm_provider.py      # promptfoo Python provider that calls litellm
    stub_provider.py         # deterministic offline provider for the smoke run
  clusterer_merge/
    __init__.py
    promptfooconfig.yaml     # the real eval
    smoke.promptfooconfig.yaml  # offline smoke eval (stub provider + fixture dataset)
    prompts.py               # promptfoo prompt functions
    assert_same_story.py     # promptfoo Python assertion
    export.sql               # read-only export query
    build_dataset.py         # export output -> frozen dataset file
    render_tests.py          # dataset + labels -> promptfoo test cases
    label_sheet.py           # labels <-> xlsx labelling sheet
    report.py                # promptfoo results JSON -> score table
    datasets/                # frozen dataset files (committed)
    labels/
      merge-labels.csv       # human labels (committed), seeded in this spec
    fixtures/
      smoke-dataset.jsonl    # 3 tiny synthetic pairs for the smoke run and tests
      smoke-labels.csv
  tests/
    conftest.py
    test_*.py                # deterministic unit tests (no network, no DB)
  results/                   # promptfoo outputs and rendered tests (gitignored)
```

Use underscores in directory names (`clusterer_merge`) so tests can import the modules.

### 2. Dataset format (frozen inputs)

A dataset is a JSONL file `evals/clusterer_merge/datasets/pairs-YYYY-MM-DD.jsonl`, one pair per line, sorted by `pair_id`:

```json
{"pair_id": "23327:23773",
 "exported_at": "2026-11-20T17:00:00Z",
 "first_seen": "2026-11-15T18:47:55Z",
 "thread_a": {"id": 23327, "title": "...", "summary": "...", "member_count": 3,
              "recent_articles": [{"title": "...", "source": "MacRumors", "assigned_at": "2026-11-14T09:00:00Z", "summary": "..."}]},
 "thread_b": { "...same shape..." },
 "shadow_verdicts": {"luna-prompts-v3-2026-11/control": false, "luna-prompts-v3-2026-11/terra-none-v2": true}}
```

* `pair_id` is the merge `ref_id`, `"<min_id>:<max_id>"` (see `_make_llm_merge_fn` in `packages/aggregator-clusterer/src/aggregator_clusterer/worker.py`). `thread_a` is always the lower id, `thread_b` the higher.
* `title` and `summary` are `threads.representative_title` and `threads.rolling_summary` at export time: exactly what the merge prompt sends. Keep `null` as `null`.
* `recent_articles` (at most 3: the earliest member plus the two most recent by `thread_memberships.assigned_at`, deduplicated) is labelling context only. It is never sent to the model under test. Article `title` is `coalesce(clean_title, feed_title)`, `summary` is `articles.summary`.
* `shadow_verdicts` maps `"<experiment>/<arm>"` to the boolean `output->>'same_story'` of every `ok` row in `llm_shadow_results` for that `ref_id`. It is for analysis only; `report.py` and the labelling sheet never use it as a label, and the labelling sheet never shows it.
* `first_seen` is the earliest `llm_shadow_results.created_at` for the pair.
* A dataset file is **never modified after it is written**. `build_dataset.py` refuses to overwrite an existing file (exit 1, clear message).

### 3. Labels format

`evals/clusterer_merge/labels/merge-labels.csv`, UTF-8, header row:

```
pair_id,label,tags,notes,labeled_by,labeled_at
```

* `label` is `merge` or `separate`.
* `tags` is a `;`-separated list of free-form lowercase tags (e.g. `shopping`, `ambiguous`); may be empty.
* `labeled_by` is `human` or `judge`. Only `human` rows are ground truth (ADR-f8cc0455 rule 2). `judge` rows are kept in the file for triage but `render_tests.py` excludes them unless `--include-judge` is passed, and `report.py` labels such runs clearly.
* Labels are keyed by `pair_id` and apply to any dataset that contains the pair. One row per `pair_id`; a later import replaces the earlier row.

Seed the file with exactly these 24 rows (the operator's labels from 2026-11-20):

```
pair_id,label,tags,notes,labeled_by,labeled_at
23327:23773,merge,shopping,,human,2026-11-20
23478:24127,merge,,,human,2026-11-20
24325:24621,merge,,,human,2026-11-20
24276:24480,merge,,,human,2026-11-20
23967:24673,separate,,,human,2026-11-20
23773:24686,merge,shopping,,human,2026-11-20
23327:24686,merge,shopping,,human,2026-11-20
24634:24761,merge,,,human,2026-11-20
23478:24479,merge,,,human,2026-11-20
24459:24765,separate,ambiguous,somewhat related but only because they're iPhone Duo. Could go either way.,human,2026-11-20
24750:24765,separate,ambiguous,somewhat related but only because they're iPhone Duo. Could go either way.,human,2026-11-20
24415:24840,separate,,,human,2026-11-20
24822:24857,merge,,,human,2026-11-20
24502:24726,merge,,,human,2026-11-20
23094:24330,separate,,,human,2026-11-20
24652:24799,merge,,,human,2026-11-20
24963:25116,separate,,these topics are not even close,human,2026-11-20
24339:25195,separate,,same main chracter (Le Pen) but different underlying topics,human,2026-11-20
24686:25271,merge,shopping,black Friday deals,human,2026-11-20
25271:25316,merge,shopping,moar black Friday deals,human,2026-11-20
24686:25316,merge,shopping,moar black Friday deals,human,2026-11-20
25289:25295,merge,,,human,2026-11-20
24716:25349,separate,ambiguous,"may go either way, it's all about the Vegas GP",human,2026-11-20
24339:24799,separate,ambiguous,could go either way (I think this is a repeat?),human,2026-11-20
```

### 4. Export (`export.sql` + `build_dataset.py`)

* `export.sql` is a single read-only `SELECT` that emits **one JSON object per output row** (via `json_build_object` / `json_agg`), matching the dataset shape in §2 except `exported_at` (added by `build_dataset.py`). It selects every distinct `ref_id` from `llm_shadow_results` where `operation = 'merge'` across all experiments, and keeps only pairs where **both** threads still exist. It must not use `read_only`, `SET`, or any DDL/DML, so it passes the guard in `.claude/skills/prod-db/prod-db.sh`.
* It runs against production through the existing script, reading SQL from stdin:
  `.claude/skills/prod-db/prod-db.sh psql -qAt -f - < evals/clusterer_merge/export.sql`
* `build_dataset.py --out <path> [--input <file>|-]` reads that output (one JSON object per line; ignore blank lines), validates every record (required keys, `pair_id` format, `thread_a.id < thread_b.id`, `pair_id` matches the two ids), drops duplicates by `pair_id`, adds `exported_at` (UTC now), sorts by `pair_id`, and writes the JSONL file. It refuses to overwrite an existing output file. It prints `wrote N pairs to <path>` and a count of how many of those pairs have human labels in `labels/merge-labels.csv`. Invalid records fail the whole run with the line number and reason; nothing is written.
* It does not connect to any database itself.

### 5. Prompts (`prompts.py`)

Each prompt is a promptfoo Python prompt function `def <name>(context) -> list[dict]` that reads `context["vars"]` and returns the chat messages list.

* **Registered variants come from the service code.** `merge_v1` and `merge_v2` build two lightweight objects with `id`, `representative_title` and `rolling_summary` from the vars (`thread_a_id`, `thread_a_title`, `thread_a_summary`, `thread_b_*`) and return `aggregator_clusterer.worker._build_merge_messages(a, b, variant="v1"|"v2")`. They must not copy the prompt text.
* **Draft variants live in this file**, built with the same user-message template as `_build_merge_messages` but their own system prompt. Add two drafts with exactly this text (tested in an ad-hoc replay on 2026-11-20):

```python
MERGE_SYSTEM_PROMPT_V3 = (
    "You are a news editor deciding whether two news threads should be combined into one. "
    "Reply with a JSON object only — no markdown: "
    '{"same_story": true|false, "reason": "..."}. '
    "Decide by the underlying matter each thread is about, not by shared names, companies or keywords.\n"
    "Answer true when both threads follow the same underlying story: one event or situation together "
    "with its causes, later developments, consequences and reactions. In particular:\n"
    "- Successive developments in one chain of events are the same story, even when a later one "
    "reverses an earlier one — e.g. a minister's corruption allegation, the party backing him, his "
    "resignation, and the succession fight it triggers.\n"
    "- The same situation reported at a different scope is the same story — e.g. a region-wide "
    "drought emergency and one country in that region rationing water because of it.\n"
    "- Different stages of one event are the same story — e.g. polls and the result of the same "
    "election, qualifying and the race result of the same grand prix, the features and the security "
    "fixes of the same software release.\n"
    "Answer false when the threads only share a person, company, product, place or occasion but are "
    "about different matters — e.g. a president's state visit and a domestic tax bill he comments on; "
    "a carmaker's recall and a separate antitrust probe into the same carmaker; a console launch and "
    "a guide to that console's settings; a celebrity appearance at a festival and the festival's "
    "ticketing problems; two different startups raising money in the same sector.\n"
    "If you are still unsure after applying these rules, answer false."
)

MERGE_SYSTEM_PROMPT_V3_DEALS = MERGE_SYSTEM_PROMPT_V3.replace(
    "fixes of the same software release.\n",
    "fixes of the same software release.\n"
    "- Deal and discount coverage for the same shopping event is the same story, whichever retailer, "
    "brand or product each thread features — e.g. two threads of Prime Day deals, one on laptops at "
    "one retailer and one on headphones at another. This applies only to deal and price coverage of a "
    "named sales event (Black Friday, Cyber Monday, Prime Day, Singles' Day and the like), not to other "
    "news that happens to mention it.\n",
)
assert MERGE_SYSTEM_PROMPT_V3_DEALS != MERGE_SYSTEM_PROMPT_V3
```

  Prompt functions: `merge_v1`, `merge_v2`, `merge_v3`, `merge_v3_deals`. Each has a promptfoo `label` in the config equal to its variant name (`v1`, `v2`, `v3`, `v3-deals`).
* The user message for draft variants is produced by one shared helper that is **identical** to the user message `_build_merge_messages` produces (including the `(no title)` / `(no summary)` fallbacks). A unit test enforces this.

### 6. Provider (`shared/litellm_provider.py`)

A promptfoo Python provider (`call_api(prompt, options, context)`) that:

* parses `prompt` as the JSON messages list produced by the prompt functions;
* calls `litellm.completion(model=config["model"], messages=..., **config.get("params", {}))`, where `config` is `options["config"]`;
* if `reasoning_effort` is in the params, adds `allowed_openai_params=["reasoning_effort"]` (same rule as `build_arm_kwargs` in `aggregator_common.experiment`);
* calls `aggregator_common.load_env()` once at import so `OPENAI_API_KEY` etc. come from the repo `.env`; it does **not** set up LLM telemetry or touch any database;
* returns `{"output": <message content>, "tokenUsage": {"prompt", "completion", "total"}, "cost": <litellm.completion_cost or omitted on failure>}`, or `{"error": "<message>"}` on any exception.

`promptfooconfig.yaml` defines two providers that mirror the shadow arms' settings:

| label | model | params |
|---|---|---|
| `terra-none` | `gpt-6.1-terra` | `reasoning_effort: none`, `max_completion_tokens: 128`, `temperature: 0.0`, `timeout: 60` |
| `luna-none` | `gpt-6-luna` | `reasoning_effort: none`, `max_completion_tokens: 128`, `temperature: 0.0`, `timeout: 60` |

Adding a provider must only require editing the YAML.

### 7. Test cases (`render_tests.py`) and assertion (`assert_same_story.py`)

* `render_tests.py --dataset <file> --labels <file> --out <file> [--include-judge] [--only-tag T]... [--exclude-tag T]...` writes promptfoo test cases (YAML or JSON list) for every dataset pair that has a usable label. Each test has `vars` (`pair_id`, `thread_a_id`, `thread_a_title`, `thread_a_summary`, `thread_b_id`, `thread_b_title`, `thread_b_summary`, `label`, `tags`), `metadata` with `pair_id`, `label`, `tags`, `labeled_by`, and a description `"<pair_id> (<label>)"`. It prints how many labelled pairs it wrote and how many labels reference pairs not in the dataset (warning, not an error).
* `assert_same_story.py` defines `get_assert(output, context)`. It parses the model output as JSON (tolerating a surrounding ```` ```json ```` fence), reads `same_story` as a boolean, and compares it to `context["vars"]["label"]` (`merge` → true). It returns a GradingResult dict with `pass`, `score` (1/0), a `reason` that includes the model's own `reason`, and `namedScores` with exactly one of `correct`, `wrong_merge`, `missed_merge` or `parse_error` set to 1. A reply that is not valid JSON or has a non-boolean `same_story` is a `parse_error` and fails.

### 8. Report (`report.py`)

`report.py <promptfoo-results.json> [--exclude-tag T]... [--only-tag T]...` reads a promptfoo JSON output file (`promptfoo eval -o`) and prints a table with one row per prompt × provider:

```
prompt     provider    n   correct  accuracy  wrong_merge  missed_merge  parse_error
v3-deals   terra-none  24  21       88%       0            3             0
```

Rows are sorted by accuracy, highest first. With tag filters it recomputes from the per-test results using the test metadata tags and prints the filters in the header. If any test in the file came from `judge` labels, it prints a warning line above the table. It supports promptfoo results produced with `--repeat N` (counts every run).

### 9. Labelling sheet (`label_sheet.py`)

* `label_sheet.py export --dataset <file> --labels <file> --out <sheet.xlsx> [--only-unlabeled] [--limit N] [--seed S]` writes an Excel sheet (openpyxl) with one row per pair: `pair_id` (column A), thread A title, summary, recent articles (one per line: `date · source: title — summary`), member count, the same for thread B, then empty `your_decision (merge/separate)`, `tags` and `notes` columns. Wrapped text, top-aligned, frozen header row, decision columns highlighted. Pairs are shuffled with the given seed. It never includes `shadow_verdicts` or existing labels.
* `label_sheet.py import --sheet <sheet.xlsx> --labels <file>` reads filled rows (case-insensitive `merge`/`separate`; blank decision = skip), writes them into the labels CSV with `labeled_by=human` and today's date, replacing existing rows for the same `pair_id`, keeps the CSV sorted by `pair_id`, and prints how many were added, replaced and skipped. Any other decision value fails the import with the row number; nothing is written.

### 10. Smoke eval

`smoke.promptfooconfig.yaml` runs all four prompts against `shared/stub_provider.py` on `fixtures/smoke-dataset.jsonl` + `fixtures/smoke-labels.csv` (3 synthetic pairs: one `merge`, one `separate`, one with `null` title/summary). The stub returns `{"same_story": true, "reason": "stub"}` when the system prompt contains `"same story"` or `"same underlying story"` and the thread A title starts with `"MERGE"`, else `{"same_story": false, "reason": "stub"}`. No network, no API key, deterministic. It proves the config loads, every prompt renders, Python interop works, and the assertion and report run end to end.

### 11. Makefile targets

Add to the existing root `Makefile` (and to `.PHONY`). All Python runs through `uv run --all-packages --with openpyxl` so `aggregator_clusterer` is importable; promptfoo runs from `evals/` with `PROMPTFOO_PYTHON` pointing at that interpreter. Variables: `DATASET` defaults to the newest file in `evals/clusterer_merge/datasets/`; `LABELS` defaults to `evals/clusterer_merge/labels/merge-labels.csv`.

| Target | Effect |
|---|---|
| `eval-install` | `npm ci --prefix evals` |
| `eval-merge-export` | Runs `export.sql` through `.claude/skills/prod-db/prod-db.sh psql -qAt -f -` and pipes it into `build_dataset.py --out evals/clusterer_merge/datasets/pairs-$(date -u +%F).jsonl` |
| `eval-merge` | `render_tests.py` into `evals/results/`, then `promptfoo eval -c evals/clusterer_merge/promptfooconfig.yaml -o evals/results/clusterer-merge-<timestamp>.json`, then `report.py` on that file. Pass-through variables: `REPEAT` (→ `--repeat`, default 1), `EXCLUDE_TAGS` / `ONLY_TAGS` (space-separated, applied to `render_tests.py`), `FILTER` (→ promptfoo `--filter-pattern`, optional) |
| `eval-merge-report` | `report.py` on the newest results file; accepts `EXCLUDE_TAGS` / `ONLY_TAGS` |
| `eval-merge-smoke` | Runs the smoke eval and its report; must exit 0 with no network or API key |
| `eval-merge-sheet` | `label_sheet.py export --only-unlabeled` into `evals/results/merge-label-sheet.xlsx`; accepts `LIMIT`, `SEED` |
| `eval-merge-import` | `label_sheet.py import --sheet $(SHEET)` (default `evals/results/merge-label-sheet.xlsx`) |
| `eval-view` | `promptfoo view` from `evals/` |

The real `eval-merge` uses promptfoo's response cache (default on). `make eval-merge NOCACHE=1` passes `--no-cache`.

### 12. Tests in the gate

* `evals/tests/` holds deterministic pytest tests. They must not call any LLM, network or database, and must not need Node.
* Extend `scripts/run-tests.sh`: after the per-package loop, if `evals/tests` exists, run `uv run --all-packages --with openpyxl pytest evals/tests`. Keep the per-package loop unchanged.
* The smoke eval (`make eval-merge-smoke`) needs Node and is **not** part of `run-tests.sh`; the tester runs it once and records the output.
* No change to the root `pyproject.toml` or `uv.lock` (openpyxl comes in through `--with`).

### 13. Documentation

* `evals/README.md`: purpose (link ADR-f8cc0455), layout, prerequisites (Node ≥ 20, `make eval-install`, `OPENAI_API_KEY` in `.env`), and the operator loop: export → label (sheet export/import) → eval → report/view. Also: how to add a draft prompt variant, how to promote a winner into `worker.py` + `PROMPT_VARIANTS` and switch `prompts.py` to import it, how to add a provider, why datasets are never edited, and that only `labeled_by=human` labels count.
* `CLAUDE.md`: add a short **Offline evals** subsection under the LLM shadow evaluation framework section: what `evals/` is, the `make eval-*` targets, and the rule that evals are screened offline first and shadow-tested before cutover (ADR-f8cc0455, ADR-9687cb01). Keep it to a few lines; point to `evals/README.md`.

## Files to Modify

| File | Change |
|---|---|
| `evals/**` | New tree as described in §1–§10 |
| `Makefile` | New `eval-*` targets (§11) |
| `scripts/run-tests.sh` | Also run `evals/tests` (§12) |
| `CLAUDE.md` | Short Offline evals subsection (§13) |

No changes under `packages/`.

## Acceptance Criteria

1. `evals/package.json` pins `promptfoo` to exactly `0.125.1` and `evals/package-lock.json` is committed; `evals/node_modules/` and `evals/results/` are gitignored.
2. `evals/clusterer_merge/labels/merge-labels.csv` contains exactly the 24 rows in §3.
3. A unit test asserts that `merge_v1` and `merge_v2` return exactly `_build_merge_messages(..., variant="v1"|"v2")` for the same inputs, including `None` title/summary.
4. A unit test asserts that the draft variants' user message equals the user message `_build_merge_messages` produces for the same inputs, and that `merge_v3_deals`'s system prompt contains the deals rule and otherwise starts with the same text as `merge_v3`.
5. Unit tests for `assert_same_story.get_assert` cover: correct merge, correct separate, wrong merge, missed merge, fenced JSON, invalid JSON, missing or non-boolean `same_story` — each with the expected `pass` and `namedScores` key.
6. Unit tests for `build_dataset.py` cover: valid input written sorted with `exported_at`; duplicates dropped; invalid record (bad `pair_id`, ids out of order, missing key) fails with line number and writes nothing; refusal to overwrite an existing file.
7. Unit tests for `render_tests.py` cover: only labelled pairs included; `judge` labels excluded by default and included with `--include-judge`; `--exclude-tag` / `--only-tag`; unknown label pair_ids reported as a warning.
8. Unit tests for `label_sheet.py` cover an export → fill → import round trip with openpyxl (blank decisions skipped, replacement of an existing row, invalid value rejected with nothing written) and assert the exported sheet contains no `shadow_verdicts` data.
9. Unit tests for `report.py` use a small fixture in promptfoo's JSON output format and check counts, accuracy, sorting, tag filters, `--repeat` handling and the judge warning.
10. `export.sql` contains no `read_only`, `SET`, `INSERT`, `UPDATE`, `DELETE` or DDL (asserted by a unit test), and its column/JSON key names match §2 (asserted by a unit test that parses the SQL text for the expected keys). The tester does not run it against production.
11. `make eval-merge-smoke` exits 0 with no network and no API key, and prints a report table with 12 rows' worth of results (4 prompts × 1 stub provider × 3 pairs, shown as 4 table rows). The tester records the command output in the handoff.
12. `bash scripts/run-tests.sh` passes and runs `evals/tests`.
13. `make eval-merge` and `make eval-merge-export` are not run by any worker (paid API / production DB); the reviewer checks them by reading the Makefile.
14. `evals/README.md` and the CLAUDE.md subsection exist as described in §13.
15. Nothing under `packages/` changes; `pyproject.toml` and `uv.lock` are unchanged.

## Pending Decisions

* **Judge-assisted labelling** (pre-labelling all pairs with a strong model and reviewing only uncertain ones): follow-up spec, after the first real eval run.
* **classify / rank evals**: follow-up specs using the same layout.
* **Passing recent headlines to the merge call** (would fix pairs whose summaries don't show the link, e.g. the Huang resignation pairs): a production prompt-input change, out of scope here; can be tried first as a draft variant in `prompts.py` once datasets carry `recent_articles`.
