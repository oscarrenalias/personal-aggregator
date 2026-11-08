---
id: ADR-1cb48609
title: "Clusterer biases toward attaching articles to existing threads; important threshold is 66 under gpt-6-luna"
status: draft
created_at: 2026-11-08T21:40:19.062078+00:00
authors: ['Renalias, Oscar']
description: "Classify v3 attach-by-default rule and recalibrated WEB_IMPORTANT_THRESHOLD for the Luna cutover"
tags: [llm, clustering]
related_specs: [spec-1b31bd43, spec-9eac0646]
related_beads: 
supersedes: 
---
# Clusterer biases toward attaching articles to existing threads; important threshold is 66 under gpt-6-luna

## Summary

In the context of migrating the clusterer and ranker from gpt-4.1(-mini) to gpt-6-luna, facing Luna over-splitting stories into new threads and scoring importance lower, we decided that thread classification biases toward attaching to an existing thread when in doubt and that the "important" threshold becomes 66 at cutover, to achieve thread and importance behaviour matching what the reader saw under gpt-4.1, accepting occasional over-grouping of distinct stories.

## Context

Shadow results (luna-prompts-v2-2026-11, evaluated 2026-11-07) showed classify v2 split stories that control attached (attach→new 6.3%, outcome agreement 88.2%, failing the criteria), and rank v2 put only 13.6% of articles at ≥70, against a target share of about 20–32%. At 66, which is where the v2 rank prompt's "important" band starts, the share was 22.8%. spec-1b31bd43 introduced classify v3 (attach-by-default decision steps) and made `WEB_IMPORTANT_THRESHOLD` drive the web, API and MCP important views. `CLUSTERER_SURFACE_MIN_GRADE` stays at 80 because share ≥80 was identical (0.8%) under both models.

**Status note:** classify v3 is in shadow (luna-prompts-v3-2026-11, until 2026-12-07). Approve this ADR only once v3 meets the spec-1b31bd43 criteria; otherwise reject or revise it.

## Decision Drivers

* A fragmented story (many one-article threads) is worse for the reader than an occasional over-grouped thread, which merge and dismissal can handle.
* Importance must mean the same share of articles across models.

## Considered Options

### Option A — Attach-by-default classify rule; threshold 66

* Good: directly targets the measured failure (over-splitting).
* Good: 66 matches the prompt's own band boundary rather than an arbitrary number.
* Bad: some genuinely new developments may be folded into an existing thread.

### Option B — Keep neutral classify prompt, lower candidate overlap thresholds instead

* Good: no prompt bias.
* Bad: the split decision is made by the LLM given candidates; more candidates did not fix the bias in v2.

### Option C — Rewrite the rank prompt (v3) to restore scores ≥70

* Good: keeps the historical threshold.
* Bad: another prompt round and another week; the score compression is a model trait, and moving the threshold is cheaper and equivalent.

## Decision

Under gpt-6-luna:

* `clusterer.classify` uses the attach-by-default rule: split into a new thread only when the main subject differs; a new development is never by itself a reason to split; if unsure, attach;
* `WEB_IMPORTANT_THRESHOLD=66` is set at cutover together with `SUMMARIZE_RANK_PROMPT_VARIANT=v2`;
* `CLUSTERER_SURFACE_MIN_GRADE` stays 80.

Thresholds are model-specific: any future ranking-model change MUST re-derive the threshold via shadow evaluation (ADR-9687cb01).

## Consequences

### Positive

* Thread counts and important-view size stay close to the gpt-4.1 experience.

### Negative

* Some over-grouping; readers may need to dismiss or rely on merge logic.
* The threshold is no longer the round number 70; docs and defaults must say why.
