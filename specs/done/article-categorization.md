---
name: Article Categorization
id: spec-2eabb3e7
description: "LLM-driven controlled categorization for topic feeds: data-driven categories table (seeded, runtime-editable via admin), articles.categories jsonb + GIN, summarize-rank classifies into the enabled set (dynamic prompt + post-hoc filtering), admin categories command group, re-rank backfill. Prerequisite for the web UI."
dependencies: null
priority: high
complexity: null
status: done
tags:
- categorization
- llm
- schema
- admin
- web-prereq
scope:
  in: null
  out: null
feature_root_id: B-298f8d73
---
# Article Categorization

## Objective

Add **LLM-driven controlled categorization** so articles can be grouped into stable, navigable topics (e.g. AI, Gaming) regardless of source — the foundation for the web UI's "topic feeds". The category set is **data, not hardcoded**: it lives in a runtime-editable `categories` table (seeded with defaults, managed via the admin CLI). summarize-rank classifies each article into the *currently enabled* categories using a dynamically-built prompt, and stores the result on the article.

This is the prerequisite for the Web UI spec (topic feeds).

## Dependencies

- **Foundation** (`spec-9e974b88`): models, Alembic (head `a1b2c3d4e5f6`), the shared `updated_at` trigger function.
- **summarize-rank** (`spec-5d47fa68`, merged): extend its schema/prompt/persistence + per-cycle config read.
- **admin CLI** (`spec-fd43343f` + profile increment): add a `categories` command group; reuse `output.py` + the uniform `--yes`/TTY safety rule.
- **Bulk re-rank** (`articles rerank --all`, merged): used to backfill existing articles after the set changes.

## Background / Decisions

Decided with the user; implement to them.

- **Categories are DB-managed config, not hardcoded.** A `categories` table holds the set; seeded with defaults but fully editable at runtime via the admin CLI — no code change/redeploy to add/rename/remove a category.
- **Multi-category per article:** `articles.categories` is a **jsonb array of category names** (an article can be in several). Querying a topic feed = `WHERE categories ? :name` (GIN-indexed).
- **Names (not ids) stored on articles** — readable and simple for topic-feed queries. Renaming a category updates the table; existing articles are refreshed by a re-rank (documented), not auto-rewritten.
- **summarize-rank reads the enabled categories each cycle** (like it reads `interest_profile`) and builds the classification instruction dynamically. Because the set is dynamic, the LLM returns free strings which are then **validated/filtered against the enabled set** (case-insensitive → canonical name; unknowns dropped; none allowed = uncategorized). No hard cap on count.
- **Changing the set ⇒ `articles rerank --all`** to re-classify existing articles. The category set is genuinely live config.

## Changes

### 1. Schema — new Alembic migration (down_revision = `a1b2c3d4e5f6`)

- **`categories` table:** `id` bigint PK identity; `name` text not null **unique**; `description` text null (optional hint to steer the LLM); `enabled` boolean not null default true; `sort_order` int not null default 0; `created_at`/`updated_at` timestamptz not null (apply the shared `BEFORE UPDATE` trigger).
- **`articles.categories`** jsonb null (array of category-name strings).
- **GIN index** on `articles.categories`.
- **Seed** the default set in the migration (data insert): `Technology & IT`, `Cloud & Architecture`, `Software Engineering`, `AI`, `Gaming` — each with a short `description`. These are seed values only; editable afterward.
- `downgrade` drops the index, column, and table.

### 2. `aggregator-common` models

- Add a `Category` ORM model mapping the `categories` table.
- Add `categories: Mapped[list | None]` (jsonb) to the `Article` model.

### 3. summarize-rank — classification

- **`schema.py`:** add `categories: list[str]` to `RankResult` (free strings; validated post-hoc, since the allowed set is dynamic).
- **`prompt.py`:** accept the enabled categories (name + description) and add an instruction: *"Assign the article to zero or more of these categories, using the exact names. Only use names from this list: …"*, listing each name + description. Bump `PROMPT_VERSION`.
- **`loop.py`:** read the enabled categories (ordered, `enabled = true`) once per cycle alongside the interest profile, and pass them to the prompt builder.
- **`rank.py`/`ranker.py`:** after parsing `RankResult`, **filter `categories` to the enabled set** — case-insensitive match to the canonical `name`, dedupe, drop unknowns; store the result in `article.categories` (empty list if none) as part of the success transition. Record nothing extra if the set is empty (no categories table rows → skip classification, store `[]`). **Note:** "article matched no enabled categories" and "LLM returned only unknown names" both intentionally collapse to `categories = []`; distinguishing a genuine "Uncategorized" bucket is deferred to the Web UI spec.

### 4. admin `categories` command group (`aggregator_admin/categories.py`)

| Command | Behavior |
|---|---|
| `categories list [--json]` | List id, name, enabled, sort_order, description. |
| `categories add <name> [--description <t>] [--sort-order <n>] [--disabled]` | Insert a category (unique name; clear error on duplicate). |
| `categories rename <id\|name> <new-name>` | Rename; prints a hint to run `articles rerank --all` to refresh existing tags. |
| `categories set-description <id\|name> <text>` | Update the hint. |
| `categories set-order <id\|name> <n>` | Update `sort_order`. |
| `categories enable <id\|name>` / `disable <id\|name>` | Toggle `enabled` (disabled categories are excluded from the summarize-rank prompt). |
| `categories remove <id\|name>` | Delete; destructive → uniform `--yes`/TTY rule. |

**Target resolution (`<id|name>`):** if the argument is all-digits and matches a row `id`, resolve by id; otherwise match by exact `name`; if neither matches, exit non-zero with a clear "category not found" error. All mutating commands (`rename`, `set-description`, `set-order`, `enable`, `disable`, `remove`) apply this and error non-zero on a missing target.

Wire the `categories` sub-app into `main.py` alongside `sources`/`articles`/`ops`/`profile`.

### 5. Backfill (operator step, documented)

After the migration is applied and the new summarize-rank is running, run `aggregator-admin articles rerank --all --yes` then let summarize-rank process the queue, so existing articles get categorized against the seeded set.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-common/src/aggregator_common/migrations/versions/<new>.py` | categories table + `articles.categories` + GIN + seed + trigger |
| `packages/aggregator-common/src/aggregator_common/models.py` | `Category` model + `Article.categories` |
| `packages/aggregator-summarize-rank/src/aggregator_summarize_rank/schema.py` | `RankResult.categories` |
| `…/summarize_rank/prompt.py` | dynamic category list in prompt + `PROMPT_VERSION` bump |
| `…/summarize_rank/loop.py` | read enabled categories per cycle |
| `…/summarize_rank/rank.py` (+ `ranker.py`) | filter to enabled set + store on article |
| `packages/aggregator-admin/src/aggregator_admin/categories.py` | new `categories` group |
| `packages/aggregator-admin/src/aggregator_admin/main.py` | register `categories` |
| `packages/aggregator-common/tests/**`, `…summarize-rank/tests/**`, `…admin/tests/**` | tests |
| `CLAUDE.md` | document categories (data-driven config) |

## Acceptance Criteria

- Migration `upgrade head` creates `categories` (seeded with the 5 defaults), adds `articles.categories` jsonb + GIN index; `downgrade` reverses cleanly (verified via testcontainers).
- The `Category` model and `Article.categories` are usable via the ORM.
- summarize-rank: with enabled categories present, the built prompt contains each enabled category's name + description; a **mocked** LLM response returning a mix of valid + invalid + differently-cased category names results in `article.categories` containing only the enabled names (canonicalized, deduped); a response with no categories → `[]`; with zero category rows, classification is skipped and `categories = []`.
- An article can hold multiple categories; `WHERE categories ? 'AI'` returns AI-tagged articles (GIN path).
- Disabled categories are excluded from the prompt (asserted).
- admin `categories`: add/list/rename/set-description/set-order/enable/disable/remove all work; duplicate `add` errors non-zero; `remove` obeys the `--yes` rule; every mutating command errors non-zero on a non-existent `<id|name>` target; `<id|name>` resolves all-digits→id else exact name.
- Full suite green via `uv run pytest` (testcontainers + mocked litellm; no live LLM).

## Pending Decisions

- Category **names** stored on articles (vs ids); rename refreshed via re-rank (not auto-rewrite) — accepted.
- No hard cap on categories per article (LLM assigns applicable); revisit if noisy.
- Post-hoc filtering against the enabled set (vs a dynamic JSON-schema enum) — chosen for simplicity with a runtime-variable set.
- "Uncategorized" presentation is a **Web UI** concern (Spec 2).
- Live re-rank backfill of the existing ~180 articles incurs a small LLM cost (operator-run).
