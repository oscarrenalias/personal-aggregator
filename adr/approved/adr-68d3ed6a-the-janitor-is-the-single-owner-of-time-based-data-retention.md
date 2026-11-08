---
id: ADR-68d3ed6a
title: The janitor is the single owner of time-based data retention
status: approved
created_at: '2026-11-08T21:40:20.637808+00:00'
authors:
- Renalias, Oscar
description: Age-based purges live only in aggregator-janitor under its own advisory
  lock; feature services never prune by age
accepted_at: '2026-11-08T21:44:24.941899+00:00'
superseded_at: null
superseded_by: null
supersedes: []
tags:
- architecture
- database
related_specs:
- spec-44ab62a3
related_beads: []
review_after: null
---

# The janitor is the single owner of time-based data retention

## Summary

In the context of tables that grow without bound on a small host, facing retention logic scattered across feature services, we decided that a dedicated `aggregator-janitor` service is the single owner of all time-based data retention, running daily under its own advisory lock, to achieve one place to reason about the data lifecycle, accepting one more container.

## Context

Before spec-44ab62a3, the clusterer pruned expired threads inside its consolidation pass, the brief service pruned old briefs, and nothing purged articles (about 1,000 a week, plus tsvector/GIN growth). The janitor took over all of these and later added LLM telemetry and podcast episode retention. Shadow experiment results (`llm_shadow_results`) are deliberately excluded and dropped by the cutover spec instead.

## Decision Drivers

* Bounded storage on the Pi.
* Retention rules should be visible and tunable in one place.
* Feature daemons should not delete data as a side effect.

## Considered Options

### Option A — Dedicated janitor service

* Good: all `*_RETENTION_DAYS` settings and purge functions (`aggregator_common.retention`) in one place.
* Good: runs at a quiet hour with its own advisory lock, so it never races the clusterer.
* Bad: one more image and container.

### Option B — Each service prunes its own data

* Good: no new service.
* Bad: rules scattered and inconsistent; nobody owned article retention.
* Bad: purges entangled with feature cycles.

### Option C — Postgres-side jobs (pg_cron / partitions)

* Good: no app code.
* Bad: needs extensions and SQL outside the Python codebase; harder to test with testcontainers.

## Decision

Age-based deletion of any table MUST be implemented as a purge function in `aggregator_common.retention` called by the janitor, with a `JANITOR_*_RETENTION_DAYS` setting. Feature services MUST NOT delete rows because they are old. Deletes that are part of a feature's own semantics are allowed outside the janitor: replacing a regenerated brief, absorbing a merged thread, operator-initiated deletes (admin CLI, MCP `remove_*`).

Tables intended to be temporary (such as `llm_shadow_results`) MAY be excluded from retention if a spec documents when they are dropped.

## Consequences

### Positive

* One daily sweep; easy to audit what is kept and for how long.
* Retention can be raised temporarily (e.g. 35 days during experiments) in one setting.

### Negative

* Data may live up to a day past its window.
* New tables need an explicit retention decision, or they grow unbounded.
