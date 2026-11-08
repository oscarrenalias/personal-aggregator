---
id: ADR-9e36df3b
title: "All LLM calls go through litellm with structured output and env-selected models"
status: draft
created_at: 2026-11-08T21:40:18.022943+00:00
authors: ['Renalias, Oscar']
description: "Any service may call an LLM, but only via litellm with telemetry, response_format and a per-service model setting"
tags: [llm]
related_specs: [spec-5d47fa68, spec-5452e3b1]
related_beads: 
supersedes: 
---
# All LLM calls go through litellm with structured output and env-selected models

## Summary

In the context of five services calling LLMs (summarize-rank, clusterer, brief, podcast, plus the shadow framework in common), facing provider churn and model retirements, we decided that every LLM call goes through `litellm` with provider-native structured output, a per-service env-configurable model, and the shared telemetry logger, to achieve provider swaps as config changes and uniform cost visibility, accepting dependence on litellm's abstractions and quirks.

## Context

The summarize-rank spec (spec-5d47fa68) chose litellm and stated that summarize-rank was "the only service that calls an LLM". That stopped being true: the clusterer (classify, merge, facts), brief (tool loop), and podcast (script writing) all call models now. Podcast audio synthesis uses Gemini TTS through the `google-genai` SDK directly (`aggregator_podcast/tts.py`). The LLM telemetry spec (spec-5452e3b1) added a litellm custom logger that writes one `llm_calls` row per completion, which the cost reports and the shadow framework depend on. The gpt-4.1 retirement (2027-01-14) is the first forced model migration.

## Decision Drivers

* Models get retired; switching must not need code changes.
* Outputs feed the DB, so they must parse reliably.
* LLM cost is the main running cost and must be visible per service and operation.

## Considered Options

### Option A — litellm everywhere, structured output, env-selected model, shared telemetry

* Good: model string selects provider; swaps are `*_LLM_MODEL` changes.
* Good: `response_format` with a Pydantic schema maps to OpenAI JSON-schema or Anthropic tool use, so output parses.
* Good: one callback gives cost/token telemetry for every call.
* Bad: litellm param validation sometimes lags new models (e.g. `allowed_openai_params` for `reasoning_effort`, Responses bridge for gpt-6-luna tools).

### Option B — Direct provider SDKs per service

* Good: full access to new provider features immediately.
* Bad: switching provider is a code change in every service.
* Bad: telemetry must be reimplemented per SDK.

### Option C — Keep all LLM calls in summarize-rank only

* Good: one place to manage cost.
* Bad: clustering, brief and podcast generation need their own prompts and timing; routing them through one service would create service-to-service calls (against ADR-05d319a8).

## Decision

Any service MAY call an LLM, but it MUST:

* call through `litellm` for text/chat completions (no direct provider SDKs for completions); non-completion modalities such as TTS MAY use a provider SDK directly when litellm lacks the needed features, as podcast TTS does with `google-genai`;
* select its model through a service-prefixed setting (`LLM_MODEL`, `CLUSTERER_LLM_MODEL`, `BRIEF_LLM_MODEL`, `PODCAST_LLM_MODEL`, …), never a hard-coded model string;
* use structured output (`response_format` or function tools) wherever the result is persisted;
* initialize `setup_llm_telemetry()` so the call is recorded in `llm_calls` with service and operation.

This supersedes the "only summarize-rank calls the LLM" rule in spec-5d47fa68 and CLAUDE.md.

## Consequences

### Positive

* The gpt-4.1 → gpt-6-luna migration is a config change plus prompt variants.
* `llm-stats` covers every service without per-service code.

### Negative

* Every service carries the litellm dependency and its upgrade risk.
* CLAUDE.md wording must be updated to match this rule.
