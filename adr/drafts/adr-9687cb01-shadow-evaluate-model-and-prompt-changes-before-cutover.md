---
id: ADR-9687cb01
title: "Shadow-evaluate model and prompt changes before cutover"
status: draft
created_at: 2026-11-08T21:40:18.713164+00:00
authors: ['Renalias, Oscar']
description: "LLM_EXPERIMENT shadow arms, PROMPT_VARIANTS registry, per-operation *_PROMPT_VARIANT settings, cutover criteria written before the data"
tags: [llm, experiment]
related_specs: [spec-5f4aeb9f, spec-9eac0646, spec-1b31bd43]
related_beads: 
supersedes: 
---
# Shadow-evaluate model and prompt changes before cutover

## Summary

In the context of LLM model and prompt changes that silently alter pipeline behaviour, facing the forced gpt-4.1 retirement on 2027-01-14, we decided to evaluate every model or prompt change as a shadow A/B experiment against production traffic, with prompt variants registered in code and cutover criteria written before the data arrives, to achieve evidence-based cutovers with no user impact, accepting extra LLM spend and a multi-week lead time per change.

## Context

Offline probes showed gpt-6-luna behaves differently from gpt-4.1-mini (compressed rank scores, more thread splitting), and only real traffic exposes those differences. Spec A (spec-5f4aeb9f) built the shadow framework (`aggregator_common.experiment`, `llm_shadow_results`, `llm-experiment-report`). spec-9eac0646 added prompt variants with a `PROMPT_VARIANTS` registry and per-operation `*_PROMPT_VARIANT` settings; spec-1b31bd43 added classify v3 and arm-specific thresholds. In practice the first variant round failed classify and rank, which would have shipped as regressions without the shadow data.

## Decision Drivers

* The production call path must never be slowed or changed by an experiment.
* Decisions must rest on measured agreement, not spot checks.
* Prompt changes must be reversible by config.

## Considered Options

### Option A — Shadow arms on live traffic, registered prompt variants, pre-written criteria

* Good: same inputs for control and arm, so differences are attributable.
* Good: variants ship dark (default `v1`) and are switched on by config at cutover; rollback is a config change.
* Good: criteria fixed in the spec before results, so they are not tuned to fit the data.
* Bad: shadow spend (capped by `max_daily_cost_usd`) and article retention must be raised for the window.
* Bad: each round takes about a week of data.

### Option B — Offline evaluation on a fixed sample set

* Good: fast and repeatable.
* Bad: small, stale samples missed the real failure modes (thread splitting depends on live thread state).

### Option C — Switch the model directly and watch

* Good: no framework needed.
* Bad: regressions reach the reader; no control to compare against; slow to detect drift in thresholds.

## Decision

Any change to an LLM model or prompt used in production MUST be shadow-evaluated before cutover:

* new prompts are added as registered variants in `PROMPT_VARIANTS`, selected by `*_PROMPT_VARIANT` (default `v1`), never edited in place;
* the experiment is configured through `LLM_EXPERIMENT` with a daily cost cap and an `until` date;
* the spec states the cutover criteria (agreement and outcome thresholds, manual review rules) before the experiment starts;
* cutover happens only via `llm-experiment-report` results meeting those criteria, and is done in a dedicated cutover spec that also cleans up (`llm_shadow_results` drop, retention revert, verdict cache truncation where verdicts depend on the model).

Shadow work MUST NOT block, alter or replace the production result.

## Consequences

### Positive

* Model migrations become measurable and reversible.
* Failed variants (classify v2, rank v2 at 70) were caught before reaching the reader.

### Negative

* Extra cost and calendar time per change.
* Temporary operational changes during experiments (`JANITOR_ARTICLE_RETENTION_DAYS=35`).
