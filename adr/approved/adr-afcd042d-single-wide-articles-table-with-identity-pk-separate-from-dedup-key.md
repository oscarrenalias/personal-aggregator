---
id: ADR-afcd042d
title: Single wide articles table with identity PK separate from dedup key
status: approved
created_at: '2026-11-08T21:40:17.299133+00:00'
authors:
- Renalias, Oscar
description: One row per article across all pipeline stages; bigint identity PK; dedup_key
  is a separate unique column
accepted_at: '2026-11-08T21:44:21.177620+00:00'
superseded_at: null
superseded_by: null
supersedes: []
tags:
- database
related_specs:
- spec-9e974b88
- spec-ee787452
related_beads: []
review_after: null
---

# Single wide articles table with identity PK separate from dedup key

## Summary

In the context of an article that passes through retrieve, process, rank and read stages, facing the choice between one table and per-stage tables, we decided on a single wide `articles` table with a bigint identity primary key and a separate `UNIQUE (source_id, dedup_key)` constraint, to achieve simple queries and one row per article for its whole life, accepting many nullable columns.

## Context

The foundation spec (spec-9e974b88) had to fix the article shape before any service existed. Each stage adds data to the same article: raw feed fields (retriever), cleaned text, image and search vector (processor), summary, topics and importance (summarize-rank), and reader state (web). Dedup was deferred to the retriever spec (spec-ee787452), which builds `dedup_key` from the feed entry id, falling back to a normalized link.

## Decision Drivers

* Most reads (feed views, search, ranking queues) need fields from several stages at once.
* The state machine is a single `status` column per article.
* Feeds re-publish or edit entries; dedup must be stable and per source.

## Considered Options

### Option A — One wide table, identity PK, separate dedup key

* Good: list and search queries need no joins across stages.
* Good: one row lock covers a claim regardless of stage.
* Good: the PK never changes even if dedup rules change.
* Bad: many nullable columns whose meaning depends on `status`.
* Bad: the row grows as features add columns.

### Option B — One table per stage (raw, processed, ranked)

* Good: each stage owns its own columns.
* Bad: every read joins three or four tables; claims and state span tables.
* Bad: more migrations and more chances for orphaned rows.

### Option C — Natural key (URL or GUID) as primary key

* Good: dedup is implicit.
* Bad: feed GUIDs and URLs are unreliable and change; a key change would cascade through threads, memberships and reader state.

## Decision

Articles live in one `articles` table. The primary key is a `bigint GENERATED ALWAYS AS IDENTITY` and carries no meaning. Deduplication is enforced by `UNIQUE (source_id, dedup_key)` and its construction lives in the retriever. New per-article data is added as columns on `articles` unless it is one-to-many (threads, memberships, LLM telemetry), which gets its own table.

## Consequences

### Positive

* Simple, fast reads for the web, API and MCP.
* Dedup rules can change without touching identity.

### Negative

* Column meaning depends on status; nullable fields must be handled defensively.
* The same story from two sources is two articles; cross-source grouping is the clusterer's job.
