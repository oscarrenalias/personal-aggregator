---
id: ADR-ec4f1b4b
title: "Relevance is graded once by summarize-rank; threads surface by deterministic rule"
status: draft
created_at: 2026-11-08T21:40:18.375209+00:00
authors: ['Renalias, Oscar']
description: "Single importance_score per article; clusterer groups but never re-ranks; surfacing is a rule over member grades and cluster shape"
tags: [llm, clustering]
related_specs: [spec-899062cd]
related_beads: 
supersedes: 
---
# Relevance is graded once by summarize-rank; threads surface by deterministic rule

## Summary

In the context of deciding which articles and threads the reader sees first, facing three overlapping relevance judgments that disagreed and inflated, we decided that relevance is graded once (summarize-rank's `importance_score`) and threads surface by a deterministic rule over member grades and cluster shape, to achieve one consistent notion of importance, accepting that grading quality rests entirely on one prompt.

## Context

Before spec-899062cd, relevance was judged three times: summarize-rank's importance score, the clusterer's composite tier score, and a consolidation relevance gate. Because feeds are curated to the user's interests, topical match was conflated with importance (71% of articles scored ≥50) and the `worth_tracking` tier swelled to about 294 threads. The fix collapsed relevance into one grade and replaced tiers with a surfacing rule.

## Decision Drivers

* One number should mean "how important is this to me", used everywhere.
* Thread visibility must be explainable and tunable without LLM calls.
* Fewer LLM calls per article.

## Considered Options

### Option A — Single grade from summarize-rank; deterministic surfacing

* Good: one prompt to calibrate; consistent across articles, threads, important view, brief.
* Good: surfacing is a pure function (`scoring.compute_surfaced`) tuned by env vars.
* Bad: a mis-calibrated rank prompt or model distorts everything downstream (seen with gpt-6-luna's compressed scores).

### Option B — Keep separate article and thread scoring

* Good: threads could weigh momentum and breadth with their own model.
* Bad: the two scores disagreed and both inflated, which caused the sprawl.
* Bad: extra LLM cost per cycle.

## Decision

`summarize-rank` is the only producer of relevance (`articles.importance_score`, 0–100). The clusterer MUST NOT re-rank; it groups articles and computes `surfaced` with `compute_surfaced`: a thread surfaces if its top member grade ≥ `CLUSTERER_SURFACE_MIN_GRADE` (80), or it has ≥ `CLUSTERER_SURFACE_MIN_SOURCES` (2) distinct sources, or ≥ `CLUSTERER_SURFACE_MIN_MEMBERS` (3) members. Un-surfaced threads stay in the DB so later articles can join them. Any "important" filter uses `importance_score ≥ WEB_IMPORTANT_THRESHOLD`.

## Consequences

### Positive

* Threads view shows a bounded, explainable set.
* Recalibrating relevance is a rank-prompt change plus one threshold.

### Negative

* Changing the ranking model shifts scores everywhere, so thresholds need recalibration with each model (see ADR-1cb48609).
