# offline-evals

## Requirement: Merge-pair export source
<!-- source: spec-13660fe1 -->
The clusterer.merge eval export SHALL read merge pairs from `llm_calls` (`service='clusterer'`, `operation='merge'`) and MUST NOT depend on `llm_shadow_results`.

### Scenario: Export with no experiment running
- **WHEN** `llm_shadow_results` is empty and `llm_calls` has merge calls in the retention window
- **THEN** the export emits one record per distinct merge `ref_id` whose threads still exist, with `shadow_verdicts` = `{}`
