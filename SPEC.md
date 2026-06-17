# Personal RSS Reader and News Aggregator — SPEC.md

# Purpose

Build a personal RSS reader and news aggregator that periodically retrieves articles from a configured list of RSS/Atom feeds, processes the retrieved content into a clean readable form, uses an LLM to summarize and rank articles according to user interests, and exposes the resulting feed through a web-based UI.

The system should be modular from the start, with clear separation of concerns between retrieval, processing, summarization/ranking, and presentation. Each major module should be capable of running independently, ideally as an operating system service, while sharing a common persistence layer.

Initial scope is RSS/Atom only. General-purpose web scraping is explicitly out of scope for the first version.

---

# Goals

The solution should:

- Maintain a configured list of RSS/Atom sources.
- Refresh sources according to per-source schedule/frequency settings.
- Retrieve new article metadata and raw feed content.
- Persist raw article data before further processing.
- Process raw article data into a cleaner, display-ready form.
- Retrieve or infer useful article media, such as a header image, where possible.
- Use an LLM to:
  - summarize articles,
  - identify key topics,
  - evaluate article importance based on user interests,
  - provide a short reason for the assigned importance.
- Provide a web UI for reading and managing articles.
- Support basic reader operations:
  - unread feed,
  - mark as read,
  - mark all as read,
  - saved/bookmarked articles,
  - search.
- Keep the architecture simple enough for personal self-hosting.
- Allow future integration with agent-based interfaces, such as OpenClaw or MCP-style tools.

---

# High-Level Architecture

The system consists of the following primary modules:

1. **Retriever**
2. **Processor**
3. **Summarize & Rank**
4. **Clustering & Threading**
5. **Daily Brief**
6. **Web UI**

Supporting functions include an agent interface (see *Agent Integration*) and a scheduled data-retention/janitor function (see *Persistence Expectations*).

All modules share a common persistence layer. The persistence layer stores source configuration, retrieved article data, processed article data, LLM outputs, article status, thread/cluster state, generated briefs, and user interaction state.

The system should be modular but not over-distributed. Each module should have a clear responsibility and should communicate indirectly through persisted state rather than synchronous service-to-service calls.

```text
sources
   ↓
retriever → raw articles
   ↓
processor → processed articles
   ↓
summarize & rank → summarized/ranked articles
   ↓
clustering & threading → story threads
   ↓
   ├─→ web UI (feeds, threads, today)
   └─→ daily brief (scheduled) → today view
```

---

## Core Functional Concepts

### Source

A source represents an RSS or Atom feed that the system should monitor.

Each source should support, at minimum:

- source name,
- feed URL,
- enabled/disabled state,
- refresh frequency,
- last checked timestamp,
- next check timestamp or equivalent scheduling mechanism,
- fetch metadata needed for efficient polling where applicable,
- failure tracking,
- optional priority or weighting.

The exact data model is left to the implementation.

### Article

An article represents an item discovered from a source.

An article should move through the following conceptual stages:

```text
retrieved
   ↓
processed
   ↓
summarized/ranked
   ↓
ready to display
```

The same article record may contain raw, processed, LLM-generated, and user-interaction information, or the implementation may split those concerns across related tables. The specification does not prescribe table schemas.

### Article State

The system should track article state sufficiently to allow each module to find its pending work.

At minimum, the implementation should distinguish:

- article retrieved but not yet processed,
- article currently being processed,
- article processed and ready for summarization,
- article currently being summarized/ranked,
- article summarized/ranked and ready for full display,
- article failed during processing,
- article failed during summarization,
- article skipped where appropriate.

State should be durable so the system can recover after restart.

### Thread (Story Cluster)

A thread represents a group of related articles covering the same developing story, drawn from one
or more sources. Threads let the system collapse many near-duplicate or follow-up articles into a
single, evolving item rather than showing each report separately.

A thread should support, at minimum:

- a representative title and a rolling summary that evolves as new articles are added,
- membership linking its articles, with a per-article classification (e.g. new story, new fact,
  new angle, duplicate, correction, background, low value),
- signals describing its quality and prominence (source diversity, member count, a grade/score and
  tier),
- a surfaced flag indicating whether the thread is prominent enough to show on its own,
- lifecycle state (active, dormant, archived),
- a user-controlled dismissed flag, kept independent of lifecycle state so dismissal persists,
- timestamps supporting an "updated since last viewed" indicator.

### Daily Brief

A daily brief is a single, scheduled, LLM-generated digest of the most relevant recent articles.
It provides a structured overview (a headline, a short intro, and several topic sections each with
what happened, why it matters, and references) so the user can catch up quickly without scanning
the full feed. Briefs are persisted and served on the Today view; recent briefs may be retained
for continuity context.

---

# Module Responsibilities

## Retriever

The retriever is responsible for polling configured RSS/Atom sources and persisting newly discovered raw article data.

### Responsibilities

The retriever should:

- run on a regular schedule,
- inspect configured sources,
- determine which sources are due for refresh,
- fetch due RSS/Atom feeds,
- use efficient feed polling mechanisms where available, such as conditional requests,
- parse feed entries,
- normalize basic article identity information,
- detect obvious duplicates,
- persist new articles in raw form,
- mark new articles as pending processing,
- update source refresh metadata,
- record fetch failures and apply retry/backoff behavior.

### Inputs

- Configured source list.
- Per-source refresh settings.
- Previous fetch metadata.

### Outputs

- Raw article records.
- Updated source check status.
- Retrieval errors or status logs.

### Rules

- The retriever should not clean full article text.
- The retriever should not summarize articles.
- The retriever should not rank articles.
- The retriever should not retrieve additional article media beyond what is provided directly in the feed, except where trivial and cheap.
- The retriever should store enough raw feed data for debugging and reprocessing.

### Duplicate Handling

The retriever should avoid inserting duplicates where this can be determined cheaply.

Possible duplicate signals include:

- feed item GUID,
- feed item URL,
- normalized article URL,
- same source and same title/date combination.

The exact deduplication strategy is left to the implementation.

---

## Processor

The processor is responsible for turning raw retrieved article data into a cleaner and richer article representation suitable for display and LLM processing.

### Responsibilities

The processor should:

- find articles pending processing,
- claim an article for processing,
- retrieve the article page where needed,
- extract readable text,
- clean title and summary fields,
- extract or infer author and publication date where possible,
- identify a suitable header image where possible,
- compute basic article metadata such as word count and language where useful,
- preserve enough source information for attribution,
- mark successfully processed articles as ready for summarization/ranking,
- mark failed articles appropriately.

### Inputs

- Raw article records from the retriever.
- Feed-provided article metadata and content.
- Article URL.

### Outputs

- Cleaned article title.
- Cleaned article text or HTML.
- Article excerpt/summary from source if applicable.
- Header image URL where available.
- Normalized metadata.
- Processing status.

### Header Image Selection

The processor should attempt to identify a suitable image using a simple priority order, such as:

1. Open Graph image.
2. Twitter card image.
3. Feed-provided media image.
4. First suitable article image.
5. Source-level default image if supported.
6. No image.

Initial implementation may store remote image URLs rather than downloading and caching image files.

### Rules

- The processor should not call the LLM.
- The processor should not decide user-specific importance.
- The processor should tolerate partial extraction.
- If full article extraction fails but feed content is usable, the article may still be marked as processed using feed-provided content.
- Processing failures should be isolated to individual articles.

---

## Summarize & Rank

The summarize & rank module is responsible for applying LLM-based analysis to processed articles.

This module combines summarization and ranking in the initial architecture to avoid unnecessary component splitting.

### Responsibilities

The summarize & rank module should:

- find processed articles pending LLM analysis,
- claim an article for summarization/ranking,
- prepare a bounded LLM input from article title, source, cleaned text, and available metadata,
- generate a concise article summary,
- identify key topics,
- optionally identify key entities,
- assess article importance based on configured user interests,
- produce an importance score,
- produce a short explanation for the importance score,
- persist the LLM output,
- mark the article as summarized/ranked,
- handle LLM failures without blocking the rest of the pipeline.

### Inputs

- Processed article content.
- Source metadata.
- User interest profile or ranking instructions.

### Outputs

- LLM-generated summary.
- Topic list.
- Importance score.
- Importance explanation.
- LLM processing metadata.

### Importance Scoring

The implementation should use a simple scoring model initially.

Suggested interpretation:

- `0–30`: low relevance,
- `31–60`: potentially useful,
- `61–80`: relevant,
- `81–100`: important.

The score should reflect the user's configured interests, source relevance, article novelty, and practical usefulness.

### User Interest Profile

The system should support a configurable user interest profile. Initially this can be plain text or structured configuration.

Example interests may include:

- agentic coding,
- cloud transformation,
- software engineering,
- Apple platforms,
- self-hosting,
- EVs,
- technology and business news.

The exact interests should be user-configurable.

### Rules

- The module should not retrieve RSS feeds.
- The module should not perform article extraction.
- The module should avoid processing the same article repeatedly unless explicitly requested.
- The module should be able to skip articles that are too short, unsupported, or not worth summarizing.
- The prompt and output format should be deterministic enough for downstream UI use.

---

## Clustering & Threading

The clustering & threading module groups related articles into story threads after they have been
summarized/ranked, then scores and tiers those threads so the most significant developing stories
can be surfaced.

### Responsibilities

The clustering & threading module should:

- find ranked articles that are ready but not yet assigned to a thread,
- select candidate threads for each article using overlap signals (shared entities, shared topics,
  and full-text similarity),
- use an LLM to classify whether an article belongs to a candidate thread and how (new story, new
  fact, new angle, duplicate, correction, background, or low value),
- create a new thread or attach the article to an existing one, idempotently,
- maintain each thread's representative title and rolling summary as members change,
- score threads (e.g. relevance, novelty, importance, source diversity, time sensitivity) and
  assign a grade and tier,
- decide which threads are prominent enough to surface, based on grade, distinct source count, and
  member count,
- periodically consolidate threads by merging near-duplicate threads,
- transition stale threads to dormant and then archived over time,
- run safely as a single active worker (guarding against concurrent runs).

### Inputs

- Summarized/ranked articles with topics, entities, and search index.
- Existing threads and their membership.
- Configurable overlap thresholds, surfacing thresholds, and scheduling/window settings.

### Outputs

- Threads with membership, rolling summary, scores, grade/tier, and surfaced state.
- Per-article thread classification and reason.
- Updated thread lifecycle state.

### Rules

- The module should not retrieve feeds, extract content, or generate per-article summaries.
- Thread assignment should be idempotent and should not duplicate membership.
- A user-controlled dismissed flag should never be overwritten by recomputation or consolidation.
- Consolidation/merging should be throttled and bounded so it does not run unnecessarily or
  unboundedly; an explicit reclustering request may bypass the throttle.
- The module should not block the pipeline when the LLM is slow or unavailable.

---

## Daily Brief

The daily brief module generates a single scheduled digest of recent, relevant articles.

### Responsibilities

The daily brief module should:

- run on a schedule (typically once per day at a configurable hour),
- select a bounded set of candidate recent articles to consider,
- use an LLM to produce a structured brief: a headline, a short intro, and several topic sections,
  each with what happened, why it matters, and references back to source articles,
- reconcile references so they point to real articles where possible,
- optionally include recent prior briefs for continuity,
- persist the generated brief and its topics,
- handle LLM failure without affecting the rest of the pipeline.

### Inputs

- Recent summarized/ranked articles within a configurable window.
- A bounded candidate set and topic limit.
- Optional recent prior briefs for continuity context.

### Outputs

- A persisted brief (headline, intro, topics with references) served on the Today view.

### Rules

- Only the summarize & rank, clustering, and brief modules call the LLM.
- The brief should be regenerable on demand if implemented, but normally runs on its schedule.
- The module should not retrieve feeds or process article content.

---

## Web UI

The web UI is responsible for presenting articles and supporting reader interactions.

### Responsibilities

The web UI should:

- show a feed of articles,
- prioritize unread articles,
- support sorting by recency and/or importance,
- display source, title, publication date, image, summary, topics, and importance reason where available,
- gracefully display articles that are retrieved but not yet summarized,
- present a **Threads** view of grouped story threads, showing each thread's title, rolling
  summary, prominence, and an "updated since last viewed" indicator,
- allow threads to be dismissed and restored, with dismissal persisting across recomputation,
- present a **Today** view that shows the latest daily brief,
- allow articles to be marked as read,
- allow all visible articles to be marked as read,
- allow articles to be saved/bookmarked,
- allow articles to be hidden or dismissed if implemented,
- provide search over article title, source, summary, and content where feasible,
- provide basic source management if included in the first version,
- expose enough status information for troubleshooting.

### Feed Display Rules

The UI should handle articles at different processing stages.

Suggested behavior:

- If the article is summarized/ranked, show the LLM summary, topics, score, and importance reason.
- If the article is processed but not summarized, show the cleaned title, image, and excerpt.
- If the article is only retrieved, show the feed-provided title and summary.
- If the article failed processing, show a minimal fallback item or hide it by default depending on implementation choice.

### Reader Operations

The UI should support:

- mark article as read,
- mark article as unread,
- mark all visible articles as read,
- save/bookmark article,
- remove saved/bookmark state,
- search articles,
- filter by unread/read/saved/source/topic where feasible.

### Rules

- The web UI should not fetch feeds directly.
- The web UI should not run scheduled processing jobs.
- The web UI may trigger explicit user actions, such as refreshing a source or reprocessing an article, if implemented.
- The UI should remain useful even when summarization is delayed or unavailable.

---

# Functional Process Flows

## Source Refresh Flow

```text
1. Retriever wakes up on schedule.
2. Retriever checks configured sources.
3. Retriever selects enabled sources due for refresh.
4. Retriever fetches each due feed.
5. Retriever parses feed entries.
6. Retriever identifies new entries.
7. Retriever persists new entries as raw articles.
8. Retriever marks new articles as pending processing.
9. Retriever updates source check metadata.
10. Retriever records failures where applicable.
```

## Article Processing Flow

```text
1. Processor finds articles pending processing.
2. Processor claims an article.
3. Processor retrieves the article page if needed.
4. Processor extracts readable content.
5. Processor cleans and normalizes article fields.
6. Processor identifies a suitable header image where possible.
7. Processor stores processed article content and metadata.
8. Processor marks the article as ready for summarization/ranking.
9. Processor records failures where applicable.
```

## Summarization and Ranking Flow

```text
1. Summarize & Rank finds processed articles pending LLM analysis.
2. Module claims an article.
3. Module prepares LLM input from cleaned article data and user interests.
4. LLM returns structured summary, topics, importance score, and explanation.
5. Module validates and stores the result.
6. Module marks the article as summarized/ranked.
7. Module records failures where applicable.
```

## Web Reading Flow

```text
1. User opens the web UI.
2. UI retrieves articles from the persistence layer.
3. UI shows articles according to selected view, such as unread, ranked, recent, saved, or search results.
4. User reads, opens, saves, hides, or marks articles as read.
5. UI persists user interaction state.
6. Future feed views reflect updated user state.
```

---

# Failure Handling

Each module should fail independently.

A failure in one article, source, or LLM call should not block the whole system.

The system should support:

- per-source fetch failure tracking,
- per-article processing failure tracking,
- per-article summarization failure tracking,
- retry behavior where appropriate,
- skip behavior where retry is unlikely to help,
- visible diagnostics for failed sources or articles.

Failures should be durable and inspectable.

---

# Scheduling and Execution Model

Each module should be independently runnable.

Preferred execution model:

```text
aggregator-retriever
aggregator-processor
aggregator-summarize-rank
aggregator-web
```

The implementation may run these as:

- OS services,
- systemd services,
- containers,
- local development processes.

The specification does not mandate a deployment mechanism.

## Suggested Behavior

- Retriever: scheduled periodic execution or long-running scheduler.
- Processor: long-running worker or periodic worker.
- Summarize & Rank: long-running worker or periodic worker.
- Web UI: always-on web process.

Each one of these may run with multiple concurrent threads to parallelize operations where it makes sense, e.g., for retriaval of articles, article processing, and so on. 

A database-backed polling model is acceptable for initial implementation. A message queue is not required for the first version.

---

# Persistence Expectations

The system should persist:

- configured sources,
- source refresh state,
- raw retrieved article data,
- processed article content,
- article media references,
- LLM summaries,
- topics,
- importance scores,
- user interest profile,
- read/saved/hidden state,
- module status and failure information.

The exact schema is intentionally left to the implementation.

---

# Security and Access

The web UI is intended for personal use.

Initial deployment may assume access through a private network or secure tunnel such as Tailscale.

The system should not assume anonymous public access.

At minimum, the implementation should consider:

- authentication or network-level access control,
- protection of LLM API keys,
- safe handling of environment variables and secrets,
- avoidance of unauthenticated administrative actions,
- no public exposure unless deliberately configured.

---

# Agent Integration

The architecture should allow an agent interface to be added later without changing the core pipeline.

Potential agent capabilities:

- list latest unread articles,
- retrieve top-ranked articles,
- summarize recent updates,
- explain why an article was ranked highly,
- add or disable a source,
- update user interests,
- mark articles as read or saved.

The agent should interact through a controlled API (e.g., MCP) or command interface rather than directly manipulating arbitrary persistence records.

---

# Design Principles

- Keep the system modular but simple.
- Prefer durable article states over synchronous service chaining.
- Avoid unnecessary components until the need is proven.
- Make pipeline state visible and debuggable.
- Preserve raw retrieved data for troubleshooting.
- Let the UI remain useful even when processing or summarization lags behind.
- Keep LLM functionality isolated in the summarize & rank module.
- Avoid premature optimization.
- Optimize first for correctness, inspectability, and maintainability.