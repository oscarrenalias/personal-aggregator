---
id: ADR-a7f74539
title: Claim-based work distribution with SKIP LOCKED leases and a reaper
status: approved
created_at: '2026-11-08T21:40:16.843530+00:00'
authors:
- Renalias, Oscar
description: In-flight = claimed_at set, not a status; reaper releases stale claims;
  advisory locks for singleton jobs
accepted_at: '2026-11-08T21:44:20.676904+00:00'
superseded_at: null
superseded_by: null
supersedes: []
tags:
- architecture
- database
related_specs:
- spec-9e974b88
- spec-09a0a914
related_beads: []
review_after: null
---

# Claim-based work distribution with SKIP LOCKED leases and a reaper

## Summary

In the context of several workers pulling pending rows from shared Postgres tables, facing crashes and concurrent workers, we decided to claim work with `SELECT … FOR UPDATE SKIP LOCKED` plus a lease (`claimed_by`/`claimed_at`) and a reaper, and to use Postgres advisory locks for jobs that must be singletons, to achieve safe concurrency and automatic recovery, accepting a lease-length delay before stranded work is retried.

## Context

Because services integrate only through Postgres (ADR-05d319a8), each worker must find and own its pending items without a coordinator. Workers run thread pools, may be restarted at any time by a deploy, and LLM calls can hang. The foundation spec (spec-9e974b88) chose claim-based in-flight modeling: `status` never takes a `processing`/`ranking` value. Later services with whole-table batch work (clusterer consolidation, janitor purges) needed mutual exclusion over a job rather than per-row claims.

## Decision Drivers

* No item may be lost when a worker crashes mid-flight.
* Multiple workers/threads must not process the same row.
* No extra infrastructure beyond Postgres.

## Considered Options

### Option A — SKIP LOCKED claim + lease + reaper; advisory locks for singleton jobs

* Good: concurrent workers never block each other or double-claim a row.
* Good: in-flight is derivable (`claimed_at IS NOT NULL`), so the status enum stays a pure pipeline position.
* Good: the reaper turns crashes into a delayed retry with no manual intervention.
* Bad: a crashed claim waits up to `CLAIM_LEASE_SECONDS` (600 s) before it is retried.
* Bad: a call that runs longer than the lease could be reclaimed and processed twice, so writes must be idempotent.

### Option B — Explicit in-progress statuses (`processing`, `ranking`)

* Good: visible in a simple status count.
* Bad: a crash leaves rows stuck in an in-progress status; needs the same timeout logic anyway, plus extra transitions.
* Bad: doubles the state-machine surface.

### Option C — External job queue (Celery/RQ)

* Good: mature retry tooling.
* Bad: adds a broker, contradicting ADR-05d319a8.

## Decision

Per-row work MUST be claimed through `aggregator_common.claim` (`claim_batch` / `complete` / `fail` / `reap_stale_claims`), which sets `status` + `claimed_by` + `claimed_at` under `FOR UPDATE SKIP LOCKED` and calls `can_transition()` before every status write. Statuses describe pipeline position only; no "in progress" status may be added. Failures use `claim.fail()` with exponential backoff and a caller-chosen `max_retries`.

Batch jobs that must not run concurrently (clusterer cycles, janitor sweeps) MUST take a `pg_try_advisory_lock` with a key distinct per job, and skip the cycle if the lock is held. Work done under claims or locks must be idempotent (for example `assign_article_to_thread` is a no-op if membership exists).

## Consequences

### Positive

* Horizontal scaling of a stage is just more worker threads or processes.
* Deploys and crashes need no cleanup.
* The same pattern was reused for brief and podcast jobs.

### Negative

* Recovery is lease-bound, not immediate.
* Every new stage must remember to run the reaper and to write idempotently.
