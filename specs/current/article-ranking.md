# article-ranking

## Requirement: Named-entity extraction (rank prompt v3)
<!-- source: spec-499532a1 -->
When `SUMMARIZE_RANK_PROMPT_VARIANT=v3`, summarize-rank SHALL extract up to 8 named entities per article and persist them to `articles.entities` as a JSON list of strings, with duplicates (case-insensitive) removed. With `v1` or `v2`, summarize-rank MUST NOT write `articles.entities`.

### Scenario: v3 persists entities
- **WHEN** an article is ranked with variant `v3`, and the model returns entities `["Nvidia", "nvidia", "Jensen Huang", " "]`
- **THEN** `articles.entities` is `["Nvidia", "Jensen Huang"]`

### Scenario: v2 leaves entities untouched
- **WHEN** an article is ranked with variant `v2`
- **THEN** `articles.entities` stays NULL

### Scenario: v3 scoring text unchanged
- **WHEN** the `v3` and `v2` system prompts are compared for both profiled and neutral modes
- **THEN** they differ only by the inserted entity step and the renumbered later steps
