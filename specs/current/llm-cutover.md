# llm-cutover

## Requirement: Cutover clean-up command
<!-- source: spec-13660fe1 -->
`aggregator-admin ops llm-cutover-cleanup --yes` SHALL delete all rows from `llm_shadow_results` (keeping the table) and truncate `thread_merge_verdicts` in one transaction. It MUST refuse to run while an enabled `LLM_EXPERIMENT` is configured.

### Scenario: Dry run
- **WHEN** the command runs without `--yes`
- **THEN** it prints the row counts it would remove and writes nothing

### Scenario: Experiment still enabled
- **WHEN** `LLM_EXPERIMENT` is set with `"enabled": true`, or holds invalid JSON
- **THEN** the command exits non-zero without writing
