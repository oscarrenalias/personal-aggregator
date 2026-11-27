# llm-call-configuration

## Requirement: Per-operation model and reasoning parameters
<!-- source: spec-13660fe1 -->
Each production LLM operation (`summarize_rank.rank`, `clusterer.classify`, `clusterer.merge`, `clusterer.consolidate_facts`, `brief.generate`) SHALL use its own configurable model, reasoning effort and completion budget. With the new settings unset, it MUST send exactly the kwargs it sends today.

### Scenario: Merge on a different model than classify
- **WHEN** `CLUSTERER_LLM_MODEL=gpt-6-luna` and `CLUSTERER_MERGE_LLM_MODEL=gpt-6.1-terra` are set
- **THEN** merge calls use `gpt-6.1-terra`
- **AND** classify and consolidate_facts calls use `gpt-6-luna`

### Scenario: Reasoning parameters applied
- **WHEN** an operation's reasoning effort is set to `low`
- **THEN** its call carries `reasoning_effort="low"`, `max_completion_tokens` and `allowed_openai_params` containing `reasoning_effort`
- **AND** carries neither `max_tokens` nor `temperature`

### Scenario: Reasoning effort none keeps temperature
- **WHEN** an operation's reasoning effort is set to `none`
- **THEN** its call carries `reasoning_effort="none"`, `max_completion_tokens` and `temperature`
- **AND** carries no `max_tokens`

### Scenario: Invalid reasoning effort
- **WHEN** `CLUSTERER_MERGE_LLM_REASONING_EFFORT=bogus` is set
- **THEN** clusterer startup fails with a message naming the setting and the valid values

### Scenario: Defaults unchanged
- **WHEN** none of the new settings are set
- **THEN** every operation's litellm kwargs are identical to the kwargs before this change
