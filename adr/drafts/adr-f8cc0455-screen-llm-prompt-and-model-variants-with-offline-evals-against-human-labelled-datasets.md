---
id: ADR-f8cc0455
title: "Screen LLM prompt and model variants with offline evals against human-labelled datasets"
status: draft
created_at: 2026-11-20T16:11:34.951591+00:00
authors: ['Renalias, Oscar']
description: 
tags: [llm, experiment, evals]
related_specs: 
related_beads: 
supersedes: 
---
# Screen LLM prompt and model variants with offline evals against human-labelled datasets

## Summary

> In the context of choosing LLM prompt and model variants for the pipeline's judgement calls (thread merge, classify, rank), facing shadow experiments that are slow, limited to live traffic and scored only by agreement with the current production model, we decided to screen variants first with offline promptfoo evals against frozen, human-labelled datasets, to achieve fast and repeatable accuracy measurements against the operator's own notion of correctness, accepting that someone has to label data and that the eval prompts and datasets have to be maintained.

## Context

ADR-9687cb01 makes shadow evaluation (`LLM_EXPERIMENT` arms writing to `llm_shadow_results`) the gate before any model or prompt cutover. Its first full run was the `luna-prompts-v3-2026-11` experiment, and it exposed three gaps:

* **No ground truth.** The experiment report measures each arm's agreement with control. For `clusterer.merge`, agreement with control ranked terra-none-v1 best. When the operator labelled 24 pairs by hand, the ranking reversed: control and terra-v1 were the least accurate arms, because control rarely merges and the operator wants broader threads. Agreement with control rewards copying the production model's mistakes.
* **Slow iteration.** Testing a new prompt variant meant writing code, releasing, editing `LLM_EXPERIMENT` on the Pi and waiting for traffic. Merge decisions only arrive a few dozen a day, so a single comparison took more than a week.
* **Inputs disappear.** Prompts aren't stored (`LLM_TELEMETRY_CAPTURE_PROMPTS=false`). Thread titles and summaries change as threads grow, and a thread is deleted when production merges it. A pair's inputs can only be reconstructed while both threads still exist.

An ad-hoc replay script (2026-11-20) ran four prompts on the 24 labelled pairs in about a minute and found a prompt (v3 with a shopping-event rule) that raised merge accuracy from 60% to 88% with no wrong merges. That is the loop we want to make routine and repeatable.

## Decision Drivers

* Accuracy has to be measured against labels the operator has given or reviewed, not against another model's output.
* Comparing a new prompt variant should take minutes, not a release plus a week of traffic.
* Scores must be comparable across runs, so the inputs they're measured on must not change.
* The prompt under test must be the prompt production would send.
* No paid or non-deterministic LLM calls in CI or the `takt merge` test gate.

## Considered Options

### Option A — Shadow experiments only (status quo)

* Good: tests on real traffic with the real code path, already built and binding under ADR-9687cb01.
* Good: catches operational problems (latency, errors, cost, parse failures) that an offline run won't.
* Bad: one variant comparison needs a release and roughly a week of traffic; low-volume operations like merge may never collect enough items.
* Bad: the only built-in quality metric is agreement with control, which rewards copying control's errors.
* Bad: a pair can't be re-run with a new prompt later, because its inputs aren't stored and change over time.

### Option B — Offline promptfoo evals on frozen, human-labelled datasets, before shadow

* Good: runs every prompt × model combination over the whole labelled set in minutes, with response caching and a side-by-side viewer for failures.
* Good: datasets are frozen snapshots, so a score measured today stays comparable with a score measured next month.
* Good: prompts are imported from the production prompt builders, so the eval tests the text production sends.
* Bad: adds a Node dev-time tool (promptfoo through `npx`) to a Python repo.
* Bad: the labelled set only covers the cases present when it was exported; a variant can be tuned to the dataset (overfitting), so shadow testing on new traffic is still needed.
* Bad: labelling takes the operator's time.

### Option C — Use an LLM judge as ground truth

* Good: labels the whole dataset at once without operator time.
* Bad: the judge's errors become invisible. It scores variants against its own notion of "same story", which may differ from the operator's, exactly as control's did.
* Bad: a strong judge model is likely to favour variants that resemble its own behaviour.

### Option D — In-repo eval harness or Inspect AI instead of promptfoo

* Good: stays Python-only.
* Bad: an in-repo harness means writing and maintaining dataset handling, caching, result storage and a comparison viewer ourselves.
* Bad: Inspect AI is geared towards agent and benchmark evals; promptfoo's prompt-by-provider grid matches this use case more directly.

## Decision

We adopt **Option B**, with these binding rules:

1. **Offline evals screen variants before shadow testing.** A new prompt or model variant for an operation that has an eval dataset is scored offline first, and only variants that beat the current production variant there are added as shadow arms. Shadow evaluation under ADR-9687cb01 remains the gate before cutover. This ADR adds a stage in front of it and does not replace it.
2. **Human labels are the only ground truth.** Accuracy is reported against labels the operator has given or reviewed (`labeled_by=human`). An LLM judge may pre-label items to decide which ones the operator reviews, but judge verdicts never count as labels on their own. Agreement with the production model is not an accuracy metric.
3. **Datasets are frozen.** Each dataset file is a dated snapshot of the exact prompt inputs and is never edited after it's committed. New data goes into a new dataset file. Labels live in separate files keyed by item id, so they can grow without changing the inputs. Scores are only compared on the same dataset file.
4. **Evals use the production prompt builders.** Registered variants are imported from the service code. Draft variants may live in `evals/` while they're being tested; promoting one means moving it into the service code and registering it in `PROMPT_VARIANTS`.
5. **Tooling and scope.** promptfoo, pinned in `evals/package.json`, is the eval runner. LLM calls from evals go through litellm (ADR-9e36df3b). Evals are run by hand and never run in CI or the `takt merge` test gate; only their deterministic tooling tests do.

## Consequences

### Positive

* A prompt idea can be tested on all labelled data in minutes, so shadow experiments only carry variants that are already likely to win.
* Accuracy finally reflects what the operator wants, and wrong merges and missed merges are reported separately.
* Frozen datasets make results comparable across prompt versions, model versions and model retirements (for example, the gpt-4.1 shutdown on 2027-01-14).
* The same structure extends to `clusterer.classify` and `summarize_rank.rank`.

### Negative

* Someone has to label data, and labels reflect one person's judgement.
* A dataset ages: it covers the stories of the weeks it was exported from, and variants tuned on it can overfit, so periodic new exports and shadow testing remain necessary.
* The repo gains a Node dev-time dependency and an `evals/` tree that has to be kept in step with the service prompt builders.
