---
id: ADR-05d319a8
title: Services integrate only through shared Postgres state
status: approved
created_at: '2026-11-08T21:40:12.087820+00:00'
authors:
- Renalias, Oscar
description: No service-to-service calls; schema + article state machine are the inter-service
  API
accepted_at: '2026-11-08T21:44:16.626319+00:00'
superseded_at: null
superseded_by: null
supersedes: []
tags:
- architecture
- database
related_specs:
- spec-9e974b88
related_beads: []
review_after: null
---

# Services integrate only through shared Postgres state

## Summary

In the context of a multi-service news pipeline (retriever, processor, summarize-rank, clusterer, brief, podcast, janitor, web/API/MCP), facing the need for independently deployable services on a single small host, we decided that services integrate only through shared Postgres state, to achieve crash-safe, decoupled stages with no runtime coupling, accepting that the schema and state machine become a hard API that is costly to change.

## Context

The aggregator is a pipeline: feeds are fetched, cleaned, summarized and ranked, clustered into threads, and served. Each stage runs as its own container on a Raspberry Pi. Stages run at very different rates (feed polling vs. LLM calls vs. a once-a-day brief) and must tolerate any one of them being down or restarting. The foundation spec (spec-9e974b88) fixed this before any service was built: "the schema and the allowed state transitions defined here are the API between services."

## Decision Drivers

* Each service must be independently deployable and restartable without coordinating with others.
* A crash mid-item must never lose an article.
* One small host: no budget for extra infrastructure (broker, service mesh).
* Postgres is already required for persistence and search.

## Considered Options

### Option A — Shared Postgres state with a durable state machine

* Good: no extra infrastructure; durability and transactions come for free.
* Good: every service finds its own pending work by querying; no producer needs to know its consumers.
* Good: the full pipeline state can be inspected with SQL (used heavily by the metrics-review skill).
* Bad: the schema is a shared contract; breaking changes need coordinated migrations.
* Bad: polling adds latency (seconds to a minute per stage), acceptable for a news reader.

### Option B — Message broker (Redis/RabbitMQ/NATS) between stages

* Good: push-based, lower latency.
* Bad: another stateful component to run and back up on the Pi.
* Bad: work state is split between the broker and the DB, so recovery after a crash needs reconciliation.

### Option C — Synchronous HTTP calls between services

* Good: familiar request/response model.
* Bad: couples availability: a downstream outage stalls or fails the upstream service.
* Bad: needs retries, timeouts and service discovery that the DB approach gets for free.

## Decision

Services MUST NOT call each other synchronously or through a broker. All integration goes through Postgres tables defined in `aggregator-common`. The `articles.status` state machine (`state.py`, `_ALLOWED_TRANSITIONS`) and the shared models are the inter-service API: changes to them are API changes and must be made in `aggregator-common` with an Alembic migration.

The only HTTP surfaces are user/agent-facing (web, `/api/v1`, MCP). Clients of those surfaces (TUI, mobile) talk HTTP to the web service, never to the DB directly.

## Consequences

### Positive

* Any service can be stopped, upgraded or rolled back on its own; work queues in the DB.
* Pipeline health is observable with plain SQL.
* Adding a consumer (clusterer, brief, podcast, janitor) needed no change to existing producers.

### Negative

* Schema and state transitions are effectively frozen; changes ripple across services.
* Stage latency is bounded by poll intervals.
* Postgres is a single point of failure for the whole system.
