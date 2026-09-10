# AI-Native News Aggregator: Story and Topic Clustering

## Purpose

This document captures the next intended product direction for the news aggregator.

The application currently has a Feedly-like RSS reader foundation, and daily briefings have already been implemented. The next step is to move beyond article-by-article browsing and introduce **story/topic clustering** so that the system can group related articles, detect what is genuinely new, and present users with a smaller set of meaningful intelligence items.

The central product shift is:

```text
From: sources → articles → unread count → user browses
To:   sources → articles → story clusters → topic memory → briefings / follow-up
```

The goal is not simply to summarize articles. The goal is to reduce cognitive load by identifying:

- which articles describe the same story,
- which stories belong to a longer-running topic,
- what has changed since the user last saw the topic,
- which items are worth promoting into the briefing,
- which items can be safely suppressed as duplicates or low-value rewrites.

---

## Product Principle

The primary unit of value should no longer be the individual article. It should be the **story**, **event**, or **topic**.

An RSS reader says:

```text
Here are 300 unread articles.
```

An AI-native aggregator should say:

```text
Here are the 7 things worth knowing, the 3 stories worth tracking, and the 40 items safely ignored as duplicates or low-value updates.
```

Articles remain important as source material and evidence, but they should not be the main object in the default user experience.

---

## Current State

Implemented or assumed:

1. RSS source retrieval.
2. Raw article persistence.
3. Article processing and cleanup.
4. Article summarization and ranking.
5. Daily briefing generation.
6. Web UI for browsing articles and/or briefings.

Current limitation:

- The system still risks behaving like Feedly: too many article-level items, with too much browsing left to the user.

Desired next capability:

- Group related articles into story clusters and use those clusters as the input to briefing generation.

---

## Target Functional Model

The future pipeline should look like this:

```text
Retriever
  ↓
Raw Articles
  ↓
Processor
  ↓
Processed Articles
  ↓
Story Clusterer
  ↓
Story Clusters
  ↓
Topic Memory
  ↓
Relevance / Novelty / Importance Scoring
  ↓
Briefing Generator
  ↓
Web UI
```

The daily briefing should consume **clusters**, not individual articles, whenever possible.

---

## Key Concepts

### Article

A single retrieved item from an RSS feed or source.

An article may contain:

- title,
- source,
- author,
- publication date,
- canonical URL,
- full text or cleaned text,
- extracted media,
- article summary,
- article embedding,
- processing status.

Articles are evidence. They should not be the primary intelligence object.

---

### Story Cluster

A group of articles describing the same event, announcement, development, or concrete story.

Examples:

```text
Apple changes App Store rules in response to EU pressure
OpenAI signs infrastructure agreement with cloud provider
Nvidia announces new GPU export restrictions impact
```

A story cluster should represent a specific news development, usually bounded in time.

A cluster may contain:

- representative title,
- short summary,
- list of articles,
- best source,
- source diversity metadata,
- first seen timestamp,
- last updated timestamp,
- confidence score,
- novelty classification,
- importance score,
- relevance explanation,
- previous summary snapshot.

---

### Topic

A longer-running subject that may contain many story clusters over time.

Examples:

```text
EU regulation of Apple
AI coding agents
Cloud provider AI infrastructure investments
EV charging regulation in Finland
```

A topic is broader and more persistent than a story cluster.

A topic may contain:

- topic name,
- description,
- active/inactive status,
- associated story clusters,
- user interest score,
- last material update,
- timeline of important developments,
- open questions,
- standing summary.

---

### Briefing Item

A promoted item shown to the user in the daily briefing.

A briefing item should usually be derived from a story cluster, not a single article.

It should answer:

- what happened,
- what changed,
- why it matters,
- why it is relevant to the user,
- which sources support it,
- whether it is new, developing, repetitive, or low-confidence.

---

## Story Clustering Requirements

### Core Requirement

The system should group processed articles that refer to the same underlying story.

For each incoming article, determine whether it is:

1. part of an existing story cluster,
2. the start of a new story cluster,
3. a duplicate or low-value rewrite of an existing article,
4. an update to an existing story,
5. related to a broader topic but not the same story.

---

## Classification Model

Each processed article should be classified against nearby existing articles and clusters.

Suggested classification labels:

```text
new_story
same_story_new_fact
same_story_new_angle
same_story_duplicate
same_story_background_only
correction_or_clarification
related_topic_new_story
irrelevant_or_low_value
```

### Definitions

#### new_story

The article describes a materially new event or development that is not already represented by an existing story cluster.

#### same_story_new_fact

The article belongs to an existing cluster and adds a factual development not previously captured.

Example:

```text
Yesterday: Company announced investigation.
Today: Regulator confirmed formal charges.
```

#### same_story_new_angle

The article does not add many new facts, but provides useful interpretation, analysis, or stakeholder perspective.

#### same_story_duplicate

The article mostly repeats information already captured in the cluster.

#### same_story_background_only

The article provides background context but no meaningful new information.

#### correction_or_clarification

The article corrects, revises, or clarifies previous reporting.

#### related_topic_new_story

The article belongs to a known topic but represents a separate story.

Example:

```text
Topic: Apple EU regulation
Story A: DMA compliance changes
Story B: New fine from Commission
```

#### irrelevant_or_low_value

The article is not useful enough to keep as a first-class item for briefings.

---

## Clustering Strategy

The implementation does not need to be perfect initially. A pragmatic hybrid approach is recommended.

### Step 1: Pre-filter Candidate Articles

For each new processed article, retrieve candidate matches from recent articles and active clusters.

Candidate retrieval should consider:

- publication time window,
- embedding similarity,
- overlapping named entities,
- source/topic tags,
- canonical URL similarity,
- title similarity.

A reasonable initial time window might be:

```text
24–72 hours for fast-moving news
7–30 days for slower-moving topics
```

This should be configurable.

---

### Step 2: Generate Article Embeddings

Each processed article should have embeddings generated from a stable representation, for example:

```text
Title + subtitle + cleaned summary + key extracted entities
```

Avoid embedding the entire article body if cost or noise becomes a problem.

The embedding should be used for candidate retrieval, not as the sole clustering decision.

---

### Step 3: Use Similarity Thresholds

Initial rough logic:

```text
High similarity + overlapping entities + close time window
  → likely same story

Medium similarity + overlapping topic but different event details
  → likely same topic, different story

Low similarity
  → likely unrelated
```

The exact thresholds should be tuned empirically.

The system should store similarity scores for later inspection and debugging.

---

### Step 4: LLM-Assisted Final Decision

After candidate retrieval, use an LLM to decide whether the article belongs to an existing cluster.

The prompt should compare:

- new article title,
- new article summary,
- article entities,
- candidate cluster summary,
- candidate cluster known facts,
- candidate cluster articles,
- last cluster update.

The LLM should return structured output only.

Example output:

```json
{
  "classification": "same_story_new_fact",
  "cluster_id": "cluster_123",
  "confidence": 0.82,
  "new_facts": [
    "The regulator has now opened a formal investigation."
  ],
  "reason": "The article covers the same Apple EU compliance story but adds a formal regulatory step not present in the cluster summary."
}
```

---

## Story Cluster Data Model Guidance

Do not lock into exact table schemas yet, but the implementation should support these logical entities.

### Processed Article

Should support:

- article ID,
- source ID,
- canonical URL,
- publication timestamp,
- title,
- cleaned text,
- summary,
- extracted entities,
- embedding reference,
- processing status,
- assigned story cluster ID if available.

---

### Story Cluster

Should support:

- cluster ID,
- representative title,
- cluster summary,
- known facts,
- article IDs,
- primary topic ID if available,
- first seen timestamp,
- last updated timestamp,
- status: active, dormant, archived,
- confidence score,
- importance score,
- novelty score,
- relevance score,
- source diversity score,
- last briefing timestamp,
- previous summary snapshot.

---

### Topic

Should support:

- topic ID,
- topic name,
- description,
- user interest score,
- active/dormant status,
- associated cluster IDs,
- standing summary,
- timeline entries,
- open questions,
- last material update timestamp.

---

## Topic Memory

Story clusters should optionally roll up into persistent topics.

This enables the system to say:

```text
You already saw the core announcement yesterday. Today's only meaningful update is the regulator's response.
```

Topic memory should allow:

- connecting new stories to existing topics,
- building a topic timeline,
- detecting stale or repetitive coverage,
- identifying material updates,
- supporting user queries such as “what changed this week?”

---

## Briefing Integration

Daily briefings already exist. The next change is to generate them from clusters.

### Current Likely Flow

```text
Processed articles → rank/summarize → daily briefing
```

### Target Flow

```text
Processed articles → story clusters → cluster ranking → daily briefing
```

The briefing generator should prefer story clusters over standalone articles.

A briefing item should include:

```text
Title
One-paragraph summary
What changed
Why it matters
Why it is relevant to the user
Best source
Other supporting sources
Novelty label
Confidence level
Suggested action: read / follow / ignore
```

---

## Briefing Categories

The daily briefing can be organized into a small number of sections.

Recommended sections:

```text
Must know
Worth tracking
Deep reads
Low-noise updates
Ignored / suppressed summary
```

### Must know

High relevance, high novelty, high importance.

### Worth tracking

Developing stories that may become important but do not yet require full attention.

### Deep reads

High-quality analysis or long-form context.

### Low-noise updates

Minor but still useful updates to topics the user follows.

### Ignored / suppressed summary

Optional transparency section showing what the system chose not to surface.

Example:

```text
Suppressed today:
- 18 duplicate articles about the same product announcement
- 6 low-information rewrites of press releases
- 4 stories outside your configured interests
```

This builds trust by showing the system is actively reducing noise.

---

## Ranking Inputs

Cluster ranking should be explainable and based on multiple dimensions.

Suggested scoring dimensions:

```text
User relevance
Novelty
Importance / impact
Source quality
Source diversity
Confidence
Time sensitivity
User history
Topic freshness
```

The final priority should be derived from these dimensions.

Example:

```text
Priority: Must know
Reason: New regulatory development affecting a topic the user follows closely.
```

Avoid a black-box “AI score” without explanation.

---

## Novelty Detection

Novelty detection is one of the most important features.

For each incoming article or cluster update, determine:

```text
Does this add anything materially new compared with what the system already knows?
```

Possible novelty outputs:

```text
new_event
new_fact
new_analysis
new_source_confirmation
duplicate
background_only
correction
```

The system should store a compact “known facts” representation per cluster. New articles should be compared against this known-facts set.

---

## Explainability Requirements

For every briefing item, the system should be able to explain why it was shown.

For every suppressed item, the system should ideally be able to explain why it was not shown.

Examples:

```text
Shown because:
- It adds a new factual development.
- It is related to a high-priority user interest.
- It is covered by multiple credible sources.

Suppressed because:
- It repeats information already included in yesterday's briefing.
- It is a syndicated rewrite of the same source material.
- It does not match the user's configured interests.
```

---

## User Feedback Loop

The UI should allow simple feedback actions:

```text
More like this
Less like this
Always follow this topic
Ignore this topic
Ignore this source
This was useful
This was not useful
Mark as duplicate
```

Feedback should influence:

- user interest profile,
- source weighting,
- topic relevance,
- future briefing ranking,
- suppression rules.

---

## Suggested User Interest Profile

A simple initial format could be stored as JSON or YAML.

Example:

```yaml
interests:
  - ai agents
  - cloud transformation
  - apple ecosystem
  - ev charging
  - finland
  - software architecture
avoid:
  - celebrity news
  - sports
  - generic startup funding
preferred_sources:
  - official blogs
  - primary reporting
  - technical analysis
ranking_style:
  prefer_depth_over_speed: true
  prefer_original_sources: true
  max_daily_items: 10
```

This does not need to be final. The important point is that relevance should be grounded in explicit or learned user interests.

---

## UI Implications

The web UI should not make the feed the only primary experience.

Recommended top-level views:

### Briefing View

The default view.

Shows ranked briefing items derived from story clusters.

### Topic View

Shows persistent topics with timelines, summaries, related clusters, and open questions.

Example:

```text
Topic: AI coding agents
Status: active
Last material update: 2 hours ago
Recent clusters:
- New Codex release
- Claude Code pricing change
- Enterprise adoption report
Open questions:
- Are vendors converging on a workflow model?
- Are costs becoming predictable enough for teams?
```

### Story Cluster View

Shows one clustered story with:

- cluster summary,
- what changed,
- known facts,
- article list,
- source comparison,
- duplicate/suppressed articles,
- related topics.

### Source View

Traditional RSS browsing by source.

This should remain available, but it should no longer be the main product experience.

### Ask View

Query the user’s news corpus.

Example queries:

```text
What changed in AI coding tools this week?
Which stories about Apple regulation are still developing?
Show only original reporting about EV charging in Finland.
What did I miss yesterday?
Which stories were suppressed as duplicates?
```

---

## Implementation Discussion Points for the Agent

The following questions should be discussed and resolved during implementation planning.

### 1. Where should clustering happen?

Options:

- inside the processor,
- as a separate story-clustering module,
- as part of briefing generation.

Recommendation:

Keep it as a separate module between processing and briefing generation.

```text
processor → story_clusterer → briefing_generator
```

This keeps responsibilities clean and allows clusters to be reused outside daily briefings.

---

### 2. Should clusters be generated incrementally or in batches?

Options:

- Incremental: assign each article to a cluster as soon as it is processed.
- Batch: periodically cluster all recent processed articles.
- Hybrid: incremental assignment plus periodic reconciliation.

Recommendation:

Use a hybrid model.

Initial implementation can be batch-based for simplicity, but the design should not prevent later incremental updates.

---

### 3. How should duplicate detection work?

Use multiple signals:

- canonical URL,
- title similarity,
- embedding similarity,
- source syndication patterns,
- entity overlap,
- publication time proximity,
- LLM comparison for borderline cases.

Do not rely only on embeddings.

---

### 4. How should “same story” differ from “same topic”?

This distinction matters.

Same story:

```text
Multiple articles about the same concrete event or development.
```

Same topic:

```text
Different stories connected by a broader subject.
```

Example:

```text
Topic: EU regulation of Apple
Story 1: Apple changes App Store steering rules
Story 2: EU Commission opens new investigation
Story 3: Spotify responds to Apple's compliance proposal
```

---

### 5. How should briefings avoid repetition?

Each cluster should track whether it has already appeared in a briefing.

If it has appeared before, only promote it again when there is a material update.

Possible logic:

```text
If cluster was briefed before:
  promote only if novelty is new_fact, correction, escalation, or high-value analysis.
Else:
  promote based on relevance and importance.
```

---

### 6. What should be stored for auditability?

Store enough metadata to debug ranking and clustering decisions.

Useful fields/logs:

- candidate clusters considered,
- similarity scores,
- LLM classification result,
- final assignment decision,
- ranking dimensions,
- reason shown to user,
- reason suppressed.

This is important because clustering and ranking errors will otherwise be hard to diagnose.

---

## Suggested MVP Scope

### MVP 1: Cluster Recent Articles

Implement clustering over articles from the last 24–72 hours.

Output:

- story clusters,
- article-to-cluster assignments,
- cluster summaries,
- duplicate labels.

### MVP 2: Generate Briefings from Clusters

Modify the daily briefing generator so it consumes story clusters instead of raw articles.

Output:

- fewer briefing items,
- better source grouping,
- less duplication.

### MVP 3: Add “What Changed”

Track previous cluster summaries and known facts.

For updated clusters, generate:

```text
What changed since last briefing?
```

### MVP 4: Add Topic Memory

Start grouping clusters into longer-running topics.

Output:

- topic pages,
- timelines,
- “follow this topic” behavior.

---

## Non-Goals for Now

Avoid starting with:

- chat as the main interface,
- social features,
- infinite scroll improvements,
- generic AI summaries only,
- complex dashboards,
- fully autonomous source discovery,
- trying to replace RSS source control.

The main value is not fetching more content. The main value is reducing the amount of content the user has to inspect manually.

---

## North Star

The product should become a personal intelligence briefing system built on RSS, not just a nicer RSS reader.

The decisive next step is:

```text
Stop treating articles as the main unit.
Start treating clustered stories and persistent topics as the main unit.
```

Daily briefings are already a strong foundation. Story clustering and topic memory are the next layers needed to make the product meaningfully different from Feedly.
