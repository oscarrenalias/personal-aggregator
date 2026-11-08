---
id: ADR-07c72564
title: "Postgres full-text search instead of an external search engine"
status: draft
created_at: 2026-11-08T21:40:17.653449+00:00
authors: ['Renalias, Oscar']
description: "tsvector + GIN populated by the processor; no Elasticsearch/Meilisearch"
tags: [database, search]
related_specs: [spec-09a0a914]
related_beads: 
supersedes: 
---
# Postgres full-text search instead of an external search engine

## Summary

In the context of reader search over a single user's few-thousand-article corpus, facing the choice of a search engine, we decided on Postgres full-text search (`tsvector` + GIN) populated by the processor, to achieve search with no extra service, accepting basic ranking and language handling.

## Context

The web UI, API (`/api/v1/articles/search`) and MCP all expose full-text search. The corpus is small (about 1,000 new articles a week, purged by the janitor after the retention window) and runs on a Raspberry Pi. The processor spec (spec-09a0a914) builds `search_vector` when it cleans an article; `queries.search_articles()` serves all three surfaces.

## Decision Drivers

* No additional stateful service on the Pi.
* Search results must be consistent with the DB (no index lag or sync job).
* Corpus size is small and bounded by retention.

## Considered Options

### Option A — Postgres `tsvector` + GIN

* Good: transactional with the article row; no sync.
* Good: no extra container, memory or backup.
* Bad: relevance ranking and typo tolerance are basic.
* Bad: GIN index grows with the table (bounded by janitor retention).

### Option B — Elasticsearch / OpenSearch

* Good: rich relevance and analyzers.
* Bad: memory-heavy JVM service, unsuitable for the Pi.
* Bad: needs an indexing pipeline kept in sync with Postgres.

### Option C — Meilisearch / Typesense

* Good: lightweight, typo-tolerant.
* Bad: still a second store to sync and back up, for little gain at this corpus size.

## Decision

Full-text search uses Postgres only. The processor writes `articles.search_vector`; all surfaces search through `aggregator_common.queries.search_articles()`. Adding an external search engine requires superseding this ADR.

## Consequences

### Positive

* Zero operational overhead; search works wherever the DB works.
* One search implementation shared by web, API and MCP.

### Negative

* No fuzzy matching or semantic search out of the box.
* Search quality changes mean SQL/tsvector tuning rather than engine config.
