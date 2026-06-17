---
name: "Thread and Today visuals: show article media images"
id: spec-d7622e88
description: "Make Threads and Today (brief) views visually richer by surfacing a representative image from member/referenced articles' header_image_url (already extracted by the processor for ~87% of articles). Adds ThreadResult.image_url (best member image), per-topic images on the brief, thumbnails on the thread list + hero on detail, with graceful no-image fallback. No schema change. No top-level brief hero (removed as duplicate of first topic thumbnail)."
dependencies: null
priority: medium
complexity: null
status: planned
tags:
- web
- threads
- today
- brief
- ux
- media
scope:
  in: null
  out: null
feature_root_id: B-cb872003
---
# Thread and Today visuals: show article media images

## Objective

Make the **Threads** and **Today (brief)** views visually richer by surfacing a representative
**image** drawn from one of the underlying articles' `header_image_url`. The processor already
extracts `header_image_url` for ~87% of articles, so the media is available on every thread's
members and every brief topic's referenced articles — it's just not displayed in these two
surfaces yet.

## Problems to Fix

- The Threads list/detail and the Today brief are text-only, which looks flat. Articles in
  those collections almost always have a header image that could make both surfaces far more
  engaging, at no extra fetch cost (the URL is already stored).

## Changes

No schema change — everything reuses `Article.header_image_url`.

### 1. Pick a representative image per thread

In `aggregator_common.queries`, surface a thread-level `image_url`:
- Add `image_url: Optional[str]` to `ThreadResult`, chosen from the thread's **non-suppressed
  member articles** that have a non-empty `header_image_url`, preferring the **highest
  `importance_score`** (tie-break: most recent). `None` when no member has an image.
- Compute it efficiently in `list_threads`/`get_thread` (reuse the membership/article joins
  already loaded; one extra column, no N+1).

### 2. Pick a representative image per brief topic (Today)

For the Today/brief view, resolve an image for each `BriefTopic` from its `topic_refs`
(article references): look up the referenced articles' `header_image_url` and pick one
(highest importance, then most recent). Resolve in the today/brief route (`app.py`) — one batched
query over the referenced article ids, not per-ref.

> **Decision (B-2f04249a):** A top-level brief hero image (derived from the first topic's image)
> was tried and removed because it duplicated the first topic's thumbnail immediately below,
> creating visual redundancy. **Do not re-add a brief-level hero image.** Per-topic thumbnails
> are sufficient.

### 3. Rendering

- **Threads list** (`_thread_list.html` / `_thread_card.html`): a thumbnail (fixed aspect
  ratio, `object-fit: cover`, lazy-loaded) on each thread row that has an `image_url`; rows
  without one keep the current text-only layout (no broken image, no layout shift).
- **Thread detail** (`_thread_detail.html`): a larger hero image at the top when present
  (mirroring the article reader's `detail-hero` treatment).
- **Today/brief** (today templates): a thumbnail per topic shown only when an image is
  available. No top-level brief hero (see decision note in §2 above).

### 4. Styling & robustness

- Add CSS for the thread thumbnail, thread hero, and brief topic image (fixed aspect ratio,
  `object-fit: cover`, rounded corners, lazy `loading="lazy"`).
- Graceful fallback everywhere: missing/empty `image_url` → render nothing (no placeholder
  box, no broken-image icon). Consider `onerror` hiding for dead image URLs.
- Reuse the existing header-image styling conventions from the article reader where possible.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-common/src/aggregator_common/queries.py` | Add `image_url` to `ThreadResult`; pick best member image in `list_threads`/`get_thread` |
| `packages/aggregator-web/src/aggregator_web/app.py` | Resolve per-topic (and hero) images for the Today/brief view from `topic_refs` |
| `packages/aggregator-web/src/aggregator_web/templates/_thread_list.html` (+ `_thread_card.html`) | Thread thumbnail when `image_url` present |
| `packages/aggregator-web/src/aggregator_web/templates/_thread_detail.html` | Thread hero image when present |
| `packages/aggregator-web/src/aggregator_web/templates/` (today/brief) | Topic/hero images when present |
| `packages/aggregator-web/src/aggregator_web/static/styles.css` | Thumbnail/hero image styles (aspect ratio, cover, lazy) |
| `packages/aggregator-common/tests/`, `packages/aggregator-web/tests/` | Image selection + rendering/fallback tests |

## Acceptance Criteria

- `ThreadResult.image_url` is the `header_image_url` of the thread's highest-importance member
  that has one (most-recent tie-break), or `None` when none do — covered by a unit test.
- Threads list shows a thumbnail for threads with an image and is unchanged (no gap/placeholder)
  for those without; thread detail shows a hero image when present.
- Today/brief shows a per-topic image drawn from the topic's referenced articles when
  available, and renders cleanly when none are. No top-level brief hero image.
- Image resolution adds no N+1 (batched queries); no schema change.
- Missing/empty image URLs never produce a broken-image icon or layout shift.
- Focused aggregator-common + aggregator-web tests pass; full gate green.

## Pending Decisions

- **Selection heuristic**: highest `importance_score` member/ref with an image, most-recent
  tie-break (proposed). Alternative: most-recent first. Cheap to change.
- **Thread list density**: thumbnail vs none — if thumbnails make the list feel heavy, fall
  back to showing images only in thread *detail* + Today. Revisit after seeing it.
- **MCP**: `ThreadResult.image_url` will flow through MCP `list_threads`/`get_thread` via
  `asdict` automatically — harmless extra field; no MCP work needed.
- **Image proxying/caching**: out of scope — images are hotlinked from source `header_image_url`
  (same as the article reader already does). Revisit only if mixed-content/hotlink issues arise.
