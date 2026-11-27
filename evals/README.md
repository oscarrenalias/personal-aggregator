# Offline prompt evals

Offline evaluation tooling for LLM prompt variants, using [promptfoo](https://www.promptfoo.dev/). Covers two operations:

- **`clusterer.merge`** — "are these two threads the same story?" (accuracy against human labels)
- **`summarize_rank.rank`** — regression/stability eval for prompt v2 vs v3 (entity extraction); no human labels required

**Why offline evals?** ADR-f8cc0455 requires that every prompt or model change be screened against a frozen, human-labelled dataset before being promoted to production or entered into a shadow experiment. ADR-9687cb01 covers the shadow experiment phase that follows.

## Directory layout

```
evals/
  README.md
  package.json               # promptfoo pinned to 0.125.1
  package-lock.json
  .gitignore                 # node_modules/, results/, .cache/
  promptfoo-python.sh        # Python wrapper for promptfoo; execs .venv/bin/python when
                             #   present (fast), falls back to uv run --all-packages python
  shared/
    litellm_provider.py      # promptfoo Python provider — calls litellm, with disk cache;
                             #   supports response_format by import path in config
    stub_provider.py         # deterministic offline provider for smoke tests;
                             #   handles both merge (same_story) and rank (RankResult) prompts
  clusterer_merge/
    promptfooconfig.yaml     # the real eval (needs OPENAI_API_KEY + network)
    smoke.promptfooconfig.yaml  # offline smoke eval (stub provider + fixture dataset)
    prompts.py               # prompt functions — v1/v2 delegate to production code;
                             #   v3/v3-deals are draft variants
    assert_same_story.py     # promptfoo Python assertion
    export.sql               # read-only export query (run via prod-db.sh)
    build_dataset.py         # export output → frozen dataset JSONL
    render_tests.py          # dataset + labels → promptfoo test cases
    label_sheet.py           # labels ↔ xlsx labelling sheet
    report.py                # promptfoo results JSON → score table
    datasets/                # frozen dataset files (committed; never edited)
    labels/
      merge-labels.csv       # human labels (committed)
    fixtures/
      smoke-dataset.jsonl    # 3 synthetic pairs for smoke tests
      smoke-labels.csv
  summarize_rank/
    promptfooconfig.yaml     # the real eval (needs OPENAI_API_KEY + network)
    smoke.promptfooconfig.yaml  # offline smoke eval (stub provider + fixture dataset)
    prompts.py               # rank_v2, rank_v3 delegate to production build_messages
    assert_rank_result.py    # promptfoo Python assertion (checks RankResult parse)
    export.sql               # read-only export query (run via prod-db.sh)
    build_dataset.py         # export output → frozen dataset JSONL
    render_tests.py          # dataset → promptfoo test cases (no labels)
    report.py                # promptfoo results JSON → metrics + PASS/FAIL
    datasets/                # frozen dataset files (committed; never edited)
    fixtures/
      smoke-dataset.jsonl    # 3 synthetic articles for smoke tests
  tests/                     # deterministic pytest unit tests (no LLM, no DB)
  results/                   # promptfoo outputs and rendered test files (gitignored)
  .cache/litellm/            # disk response cache (gitignored); see Caching below
```

## Prerequisites

- **Node ≥ 20** in `PATH`
- Run `make eval-install` once to install promptfoo: `npm ci --prefix evals`
- `OPENAI_API_KEY` set in the repo-root `.env` (only needed for real eval runs, not smoke)

## Performance: venv and caching

**Python startup** — `eval-merge` and `eval-merge-smoke` run `uv sync --all-packages --inexact` before invoking promptfoo. This creates `.venv/` at the repo root (once, fast on subsequent runs). `promptfoo-python.sh` then execs `.venv/bin/python` directly instead of `uv run`, cutting per-call Python startup from ~0.17 s to ~0.06 s. At concurrency 8 and 768 provider calls the wall-clock reduction is roughly 10–15 s.

**Response cache** — `litellm_provider.py` caches every successful LLM response under `evals/.cache/litellm/` (gitignored). The cache key is a SHA-256 over model + messages + params + repeat index, so identical reruns are free. Files are written atomically (temp file + rename) so concurrent promptfoo workers are safe.

**Repeat semantics** — promptfoo exposes `context['repeatIndex']` (0-based integer) to Python providers. Each distinct `repeatIndex` produces a **different cache key**, so repeat runs remain independent samples — repeat 0 and repeat 1 of the same test case are stored and retrieved separately. This holds for all repeat counts.

**Disabling the cache:**

```
make eval-merge NOCACHE=1        # passes --no-cache to promptfoo AND sets EVAL_NO_CACHE=1
EVAL_NO_CACHE=1 make eval-merge  # alternative: set env var directly
```

Both disable cache reads and writes for that run. Errors are never cached regardless.

**Clearing the cache:**

```
make eval-cache-clear            # deletes evals/.cache/litellm/
```

**Concurrency:**

```
make eval-merge MAXCONCURRENCY=4   # default is 8
```

Passed as `--max-concurrency` to `promptfoo eval`.

## Operator loop

### 1. Export a frozen dataset from production

```
make eval-merge-export
```

Runs `export.sql` against production via `.claude/skills/prod-db/prod-db.sh`, pipes the output through `build_dataset.py`, and writes a dated JSONL file to `evals/clusterer_merge/datasets/`. Dataset files are **never modified after creation**; each export creates a new file.

**Pair source:** `export.sql` reads merge pairs from `llm_calls` (filtered by `service='clusterer'` AND `operation='merge'`), not from `llm_shadow_results`. `llm_calls` rows are retained for `JANITOR_LLM_TELEMETRY_RETENTION_DAYS` (default 30 days), so a fresh export covers approximately the last month of production merge decisions.

**`shadow_verdicts`:** From the gpt-4.1 cutover onward, `shadow_verdicts` is always `{}` (an empty JSON object) in every exported row. The shadow experiment table is no longer used as the pair source. The field is kept in the output schema so `build_dataset.py` requires no modification.

### 2. Label pairs

**Export a labelling sheet** (only unlabeled pairs, random order):

```
make eval-merge-sheet            # writes evals/results/merge-label-sheet.xlsx
make eval-merge-sheet LIMIT=50 SEED=42   # sample 50 pairs
```

Fill in the `your_decision (merge/separate)` column (`merge` or `separate`; leave blank to skip). The sheet intentionally does not show `shadow_verdicts` to avoid biasing labels.

**Import filled labels back:**

```
make eval-merge-import                       # reads evals/results/merge-label-sheet.xlsx
make eval-merge-import SHEET=my-sheet.xlsx   # or specify a path
```

Labels are written to `evals/clusterer_merge/labels/merge-labels.csv`. An existing row for the same `pair_id` is replaced. Any invalid decision value (`merge`/`separate` case-insensitive are the only valid values) fails the import without writing anything.

**Only `labeled_by=human` rows count as ground truth.** `judge` rows are retained in the labels file for triage but `render_tests.py` excludes them by default; pass `--include-judge` to `render_tests.py` or the report targets to include them (the report will label such runs clearly).

### 3. Run the eval

```
make eval-merge                        # run all prompts × providers, print report
make eval-merge REPEAT=3               # run each test 3 times (averages out non-determinism)
make eval-merge EXCLUDE_TAGS=ambiguous # skip pairs tagged ambiguous
make eval-merge ONLY_TAGS=shopping     # run only shopping-tagged pairs
make eval-merge NOCACHE=1              # disable both litellm cache and promptfoo cache
make eval-merge MAXCONCURRENCY=4       # limit concurrency (default 8)
```

This renders test cases from the latest dataset and labels file, runs `promptfoo eval`, and prints a score table. Responses are cached in `evals/.cache/litellm/` by default; a second run with the same inputs returns cached results without calling the API.

### 4. View results in the promptfoo UI

```
make eval-view
```

Opens the promptfoo viewer at `http://localhost:15500` where you can browse per-pair outputs side by side.

### 5. Report on a previous run

```
make eval-merge-report             # re-print report for the latest results file
make eval-merge-report EXCLUDE_TAGS=ambiguous
```

## Smoke eval (no network, no API key)

```
make eval-merge-smoke
```

Runs all four prompt variants against the stub provider on `fixtures/smoke-dataset.jsonl`. Exits 0, prints a report table with 4 rows (4 prompts × 1 provider × 3 pairs). Use this to verify the config loads and Python interop works without spending money.

---

## summarize_rank operator loop

The `summarize_rank/` eval screens `summarize_rank.rank` prompt v3 (entity extraction) against v2. No human labels are needed — this is a regression/stability eval.

### 1. Export a frozen dataset from production

```
make eval-rank-export
```

Runs `export.sql` against production via `.claude/skills/prod-db/prod-db.sh`, pipes the output through `build_dataset.py`, and writes a dated JSONL file to `evals/summarize_rank/datasets/`. Stratified sampling: ~50 articles with `importance_score ≥ 60` and ~100 below, covering the threshold band.

### 2. Run the eval

```
make eval-rank                        # run v2 and v3 against gpt-6-luna, print report
make eval-rank REPEAT=3               # run each article 3 times
make eval-rank NOCACHE=1              # disable both litellm cache and promptfoo cache
make eval-rank MAXCONCURRENCY=4       # limit concurrency (default 8)
make eval-rank RANK_DATASET=evals/summarize_rank/datasets/articles-2026-11-28.jsonl
```

### 3. Report on results

```
make eval-rank-report                 # re-print report for the latest results file
```

The report prints: n, parse failure rate per variant; v3-vs-v2 mae, mean signed diff, Spearman rho, flip rate across threshold (default 66); v3 entity coverage and median entity count; v2-vs-production mae as a noise floor. PASS/FAIL verdicts per spec-499532a1 criteria.

### 4. Entity review sheet

```
uv run --all-packages python evals/summarize_rank/report.py <results.json> \
  --entity-sheet evals/results/entity-review.csv --sample-size 30 --seed 42
```

Writes a random sample of v3 outputs with article title + entity list + empty `ok`/`notes` columns for human review.

### 5. Smoke eval (offline)

```
make eval-rank-smoke
```

Runs v2 and v3 against the stub provider on `fixtures/smoke-dataset.jsonl`. No network or API key required. Use this to verify config loads and Python interop work.

## Registered production variants

| Variant | Status | Notes |
|---|---|---|
| `v1` | production default | Conservative: unambiguous same-event only |
| `v2` | registered | Broader: same ongoing story, different angles |
| `v4b` | registered (screened offline) | Specific-subject prompt; shadow-test before cutover per ADR-9687cb01 |

`v3`, `v3-deals`, and `v4` remain draft-only (in `prompts.py` only; not in `PROMPT_VARIANTS`).

## How to add a draft prompt variant

1. Add a new system prompt constant and prompt function to `evals/clusterer_merge/prompts.py` following the `merge_v3`/`merge_v3_deals` pattern. The user message must be byte-for-byte identical to what `_build_merge_messages` produces (the shared `_build_user_message` helper does this).
2. Add the prompt to `promptfooconfig.yaml` under `prompts:` with a `label`.
3. Run `make eval-merge-smoke` to verify the config loads. Run `make eval-merge` with real labels to benchmark it.

## How to promote a winner into production

Once a draft variant achieves the accuracy target on the offline eval and passes the shadow experiment (ADR-9687cb01):

1. Add the new system prompt to `packages/aggregator-clusterer/src/aggregator_clusterer/worker.py` (see the `_build_merge_messages` function and `MERGE_SYSTEM_PROMPT_V*` constants there).
2. Register the new variant in `PROMPT_VARIANTS["clusterer.merge"]` in `aggregator_common/experiment.py`.
3. Update `evals/clusterer_merge/prompts.py`: replace the draft function body to delegate to `_build_merge_messages(a, b, variant="v<N>")`, like `merge_v1` and `merge_v2` do.
4. Update `CLUSTERER_MERGE_PROMPT_VARIANT` in `.env` to activate the new variant.

## How to add a provider

Adding a provider requires **only editing `promptfooconfig.yaml`**: add a new entry under `providers:` with the same `id: "file://../shared/litellm_provider.py"` and a `config` block specifying `model` and `params`. No Python changes needed.

## Why datasets are never edited

A dataset file is a frozen snapshot of the prompt inputs as they existed at export time — `representative_title` and `rolling_summary` for each thread. These fields change as threads grow, and threads can disappear when production merges them. Editing a dataset after the fact would invalidate any results computed against it. If you need a new snapshot, run `make eval-merge-export` to create a new dated file.

## Ground truth rule

Only rows with `labeled_by=human` in `merge-labels.csv` are ground truth. Labels from an LLM judge (`labeled_by=judge`) are stored for triage but excluded from accuracy metrics by default, per ADR-f8cc0455 rule 2.
