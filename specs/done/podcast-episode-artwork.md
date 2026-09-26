---
name: Podcast episode artwork
id: spec-4ea29988
description: "Hotlink a lead-story article image as podcast episode cover art, exposed via artwork_url on the podcast API and rendered in the web UI"
dependencies: null
priority: medium
complexity: null
status: done
tags:
- podcast
- api
- web
- ios
scope:
  in: null
  out: null
feature_root_id: B-d50b31ed
---
# Podcast episode artwork

## Objective

Give every podcast episode a cover image, sourced by reusing the header image of
one of the articles the episode covers. The image is **hotlinked** — the
original remote URL is stored and served as-is, exactly like article and thread
images elsewhere in the web UI. No downloading, no local storage, no image
processing.

The artwork must be exposed as a field on the podcast JSON API so both the web
UI and the in-progress iOS client can display it.

## Background and rationale

Episodes currently have no visual identity; the web list shows a wall of text
cards and the iOS client has nothing to render.

Hotlinking is sufficient here because the janitor purges podcast episodes after
`JANITOR_PODCAST_RETENTION_DAYS` (default 7). A news CDN URL will not rot inside
a week, so the usual argument for downloading and self-hosting does not apply.
This keeps the feature to a URL field with zero storage and nothing for the
janitor to clean up.

Embedding artwork in the MP3's ID3 tags is explicitly **out of scope**. Both
consumers render the image themselves — the web UI with an `<img>`, the iOS
client by fetching the URL and setting `MPNowPlayingInfoCenter` artwork. ID3
only matters for third-party podcast clients consuming an RSS feed, which does
not exist.

## Where the image comes from

Story segments in `script_json` already carry a `thread_id`. Verified shape from
production episode 2801:

```json
{
  "type": "story",
  "thread_id": "15545",
  "headline": "Iran couples a proposed regional ceasefire ...",
  "topic_category": "World Politics",
  "sources": ["BBC News", "Kyodo News", "HT News"],
  "is_developing": true,
  "text": "..."
}
```

Note `thread_id` is serialised as a **string** in the stored JSON — coerce to
int before querying.

A thread's image is not a column. `queries.list_threads()` derives it by
batch-loading `Article.header_image_url` joined through `ThreadMembership`
(`packages/aggregator-common/src/aggregator_common/queries.py`, around lines
773-793). Reuse that existing pattern rather than inventing a new query.

### Selection rule

Walk the story segments **in order** and take the first one that yields an
image:

1. For each `type == "story"` segment in script order, resolve its `thread_id`
   to a member article's `header_image_url` (non-null).
2. If no story segment yields one, fall back to `Source.default_image_url` of
   the source behind the first story segment.
3. If still nothing, the episode has no artwork — the field is `null` and both
   clients omit the image.

Taking the lead story's image is deliberate: it is the episode's most important
item, so the cover reflects what the episode leads with.

## Changes

### 1. Resolve artwork during generation

Resolve the URL in `packages/aggregator-podcast/src/aggregator_podcast/generate.py`
and write it into the assembled script as a top-level `artwork_url` key, next to
the existing `episode_theme`.

Generation is the right place because it already holds the DB session and the
selected threads. Writing it into `script_json` means completion can derive it
the same way it now derives `episode_theme`, keeping a single source of truth.

### 2. Persist to a column

Add a nullable `artwork_url` Text column to `podcast_episodes` via an Alembic
migration, and populate it in `complete_podcast()`
(`packages/aggregator-common/src/aggregator_common/podcast_claim.py`) by
deriving from `script_json.get("artwork_url")` — mirroring exactly how
`episode_theme` is handled there. Treat empty string as `NULL`.

No backfill. Episodes generated before this change have no `artwork_url` in
their `script_json`, and 7-day retention means they age out on their own.

### 3. Expose on the API

Add `artwork_url: Optional[str]` to `PodcastEpisodeResponse` in
`packages/aggregator-api/src/aggregator_api/routes/podcasts.py` and populate it
in `_to_response()`. It flows automatically to every endpoint returning that
model — list, latest, by-id, by-date.

Re-run `make openapi` and commit `docs/openapi.json`, or the CI drift check
added in `B-5404e9df` will fail the build.

### 4. Render in the web UI

Show the artwork on the podcast list card and in the episode detail header.

Follow the existing thread-card image markup exactly
(`packages/aggregator-web/src/aggregator_web/templates/_thread_card.html`,
lines 14-20):

```html
{% if episode.artwork_url %}
<div class="card-image">
  <img src="{{ episode.artwork_url | e }}"
       alt=""
       loading="lazy"
       decoding="async"
       onerror="this.closest('.card-image').remove()">
</div>
{% endif %}
```

The `onerror` handler is **required**, not optional. It is what makes
hotlinking safe: if a remote CDN blocks the request or the image 404s, the
container removes itself rather than leaving a broken-image icon. Reuse the
existing `.card-image` CSS class; do not introduce a parallel style.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-podcast/src/aggregator_podcast/generate.py` | Resolve lead-story artwork URL; add `artwork_url` to assembled `script_json` |
| `packages/aggregator-common/src/aggregator_common/models.py` | Add `artwork_url` column to `PodcastEpisode` |
| `packages/aggregator-common/src/aggregator_common/migrations/versions/` | New migration adding nullable `artwork_url` Text column |
| `packages/aggregator-common/src/aggregator_common/podcast_claim.py` | Derive and persist `artwork_url` in `complete_podcast()` |
| `packages/aggregator-api/src/aggregator_api/routes/podcasts.py` | Add `artwork_url` to `PodcastEpisodeResponse` and `_to_response()` |
| `packages/aggregator-web/src/aggregator_web/templates/podcasts.html` | Render artwork on the episode list card |
| `packages/aggregator-web/src/aggregator_web/templates/_podcast_detail.html` | Render artwork in the detail header |
| `docs/openapi.json` | Regenerate via `make openapi` |
| `CLAUDE.md` | Document `artwork_url` in the podcast API endpoint list |

## Acceptance Criteria

1. A newly generated episode whose lead story's thread has a member article with
   a `header_image_url` stores that URL in `podcast_episodes.artwork_url`.
2. When the lead story yields no image, the next story segment in script order
   is tried, then `Source.default_image_url`, then the column is `NULL`.
3. `GET /api/v1/podcasts`, `/latest`, `/{id}` and `/by-date/{date}` all include
   `artwork_url` in the response body.
4. `artwork_url` is `null` (not absent, not `""`) for an episode with no
   resolvable image.
5. The podcast list card and detail header render an `<img>` when
   `artwork_url` is present, and render no image container when it is `null`.
6. The rendered `<img>` carries the `onerror` handler that removes its
   `.card-image` parent, matching `_thread_card.html`.
7. `docs/openapi.json` includes `artwork_url` and the CI drift check passes.
8. `alembic heads` reports exactly one head after the new migration.
9. Existing episodes without `artwork_url` in `script_json` continue to serve
   successfully with `artwork_url: null` — no errors, no migration backfill.

## Out of Scope

- Downloading, caching, or self-hosting images. Hotlink only.
- Image resizing, cropping, or square aspect-ratio conversion.
- Embedding artwork in MP3 ID3 tags.
- A `/api/v1/podcasts/{id}/artwork` endpoint.
- Generating artwork with an image model.
- Backfilling artwork for episodes created before this change.
- Janitor changes — nothing is stored locally, so nothing needs purging.

## Pending Decisions

None. The hotlink-versus-download question was settled in favour of hotlinking
on the basis of the 7-day retention window; see rationale above.
