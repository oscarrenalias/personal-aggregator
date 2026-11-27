---
id: ADR-60ce8b01
title: Shadow-evaluate model and prompt changes before cutover; keep the shadow table
status: approved
created_at: '2026-11-27T17:36:16.100567+00:00'
authors:
- Renalias, Oscar
description: null
accepted_at: '2026-11-27T17:49:46.037178+00:00'
superseded_at: null
superseded_by: null
supersedes:
- ADR-9687cb01
tags:
- llm
- experiment
related_specs:
- spec-13660fe1
related_beads: []
review_after: null
---

# Shadow-evaluate model and prompt changes before cutover; keep the shadow table

## Summary

> We work on LLM model and prompt changes that silently alter pipeline behaviour, and the shadow framework will be needed again for future model retirements. So we decided to keep ADR-9687cb01's shadow-first rule but change two of its points:
>
> - at cutover the `llm_shadow_results` table is emptied rather than dropped;
> - cutover criteria may be met by human-labelled offline evals (ADR-f8cc0455) together with a reviewed shadow sample.
>
> This means the framework can be reused without a migration and cutovers rest on ground-truth labels. We accept an idle empty table, and that the decision to cut over is the user's judgement rather than a fixed report threshold.

## Context

ADR-9687cb01 requires a dedicated cutover spec that drops `llm_shadow_results`, and a cutover only when the `llm-experiment-report` results meet criteria written before the experiment. The gpt-4.1 cutover (spec-13660fe1) showed two problems with this.

**Dropping the table has costs:**
- The experiment writer (`aggregator_common.experiment`) and `llm-experiment-report` stay in the code base. Every future model change (the next retirement, a cheaper model) would need a migration to recreate the table first.
- The offline merge eval export used to read merge pairs from that table. spec-13660fe1 moves it to `llm_calls`, so the export no longer depends on it.

**Agreement with production was a poor criterion for merge.**
- **The control was weak.** Production is gpt-4.1-mini, which scored 58% against 109 human-labelled pairs. Agreement with it measured closeness to a weak baseline.
- **The labelled eval was a better basis.** It is ground truth, and `v4b` scored 69% overall and 79% on pairs not tagged either-way. It was confirmed on 46 live shadow pairs with no errors.
- **Waiting would add little.** The user judged that another week of shadow data would not change the decision. Classify had already been decided by consensus across new models (2026-11-15) rather than by agreement with production.

## Decision Drivers

* The production call path must never be slowed or changed by an experiment.
* Decisions must rest on measured evidence, not spot checks.
* Prompt changes must be reversible by config.
* The shadow framework should be reusable for the next model migration without schema work.

## Considered Options

### Option A — Keep ADR-9687cb01 unchanged (drop the table, report thresholds only)

* Good: no idle table; one fixed, mechanical rule for every cutover.
* Bad: the next experiment needs a migration to recreate the table and its constraint before any arm can write.
* Bad: a criterion of agreeing with a weak control can block a variant that human labels show is better, and it rewards copying the control's mistakes.

### Option B — Keep the table emptied; allow offline-eval evidence plus a shadow sample

* Good: future experiments need only `LLM_EXPERIMENT` config.
* Good: cutovers can rest on ground truth (human labels) where it exists, with live shadow data confirming the setup works in production (errors, latency, output shape).
* Bad: an empty table remains in the schema between experiments.
* Bad: the go/no-go call is the user's judgement on the evidence, not a single threshold.

### Option C — Keep the table and its rows indefinitely

* Good: the history is available for later analysis.
* Bad: the rows refer to threads and articles the janitor deletes, so they lose meaning. The table also grows with no retention owner (ADR-68d3ed6a).

## Decision

This ADR supersedes ADR-9687cb01 and restates its decision with two changes, marked (changed).

Any change to an LLM model or prompt used in production MUST be evaluated before cutover:

* new prompts are added as registered variants in `PROMPT_VARIANTS`, selected by `*_PROMPT_VARIANT` (default `v1`), never edited in place;
* the change is shadow-tested through `LLM_EXPERIMENT`, with a daily cost cap and an `until` date;
* the spec states the cutover criteria before the experiment starts;
* (changed) cutover happens when the evidence meets those criteria, as judged by the user. The evidence can be `llm-experiment-report` results, or a human-labelled offline eval (ADR-f8cc0455) together with a reviewed shadow sample that shows the setup runs cleanly in production. The cutover spec records which evidence was used;
* cutover is done in a dedicated cutover spec that also cleans up:
  * (changed) `llm_shadow_results` is emptied but kept for future experiments;
  * retention is reverted;
  * the verdict cache is truncated where verdicts depend on the model.

Shadow work MUST NOT block, alter or replace the production result.

**Scope exclusion — podcast prompts.** This is unchanged from ADR-9687cb01. The podcast service is excluded. Its prompts and models MAY be changed in place, without a registered variant or a shadow experiment, for the reasons given there: it makes a handful of calls a day, its output is judged by listening, the framework has no podcast operations, and a bad episode is contained to one day.

`llm_shadow_results` remains excluded from janitor retention (ADR-68d3ed6a). It is emptied at each cutover.

## Consequences

### Positive

* The next model migration is a config change (`LLM_EXPERIMENT`) plus a report. It needs no schema work.
* Human labels, where they exist, outrank agreement with a weak production model.

### Negative

* An empty table sits in the schema between experiments.
* The cutover decision needs the user's judgement, so the cutover spec must state the evidence explicitly so it can be audited later.
