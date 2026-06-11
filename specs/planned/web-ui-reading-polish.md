---
name: Web UI reading polish
id: spec-1e85f57e
description: "aggregator-web reading-experience polish: live sidebar counter refresh on read/save (HX-Trigger), readable paragraph rendering for summary + article body, a labelled/separated article body section, and spacing between the detail header chips and action buttons. Presentation + one small HTMX behavior change, all within aggregator-web. No pipeline/schema/data changes."
dependencies: null
priority: medium
complexity: low
status: planned
tags:
- web
- ui
- htmx
- polish
scope:
  in: "aggregator-web only: app.py interaction endpoints (read/unread/save/unsave + read-all), _article_detail.html, _article_card.html, _sidebar.html (or its container in app.html), a Jinja paragraph helper/filter, and styles.css."
  out: "No changes to the summarize-rank pipeline, the summary text content, article extraction, the DB schema, or any other service. Not changing what content is shown — only how it is presented and refreshed."
feature_root_id: B-1769a01c
---
# Web UI reading polish

## Objective

Fix four reading-experience issues found in live testing of the article reader. All four are isolated to the `aggregator-web` package: three are presentation-only; one (counter refresh) is a small HTMX wiring change. No pipeline, schema, or data changes.

## Problems to Fix

1. **Sidebar counters are stale after read/save.** Marking an article read (or saved) does not update the left-column unread counts. The `/article/{id}/read|unread|save|unsave` endpoints (`app.py` ~304–361) and the `read-all` endpoints (~260–290) return only the re-rendered article card/detail fragment via `_render_interaction_response`; nothing refreshes the separate `/sidebar` endpoint, so counts only recompute on a full page reload.

2. **Summary and article body render as one dense blob.** `_article_detail.html` emits the summary (`summary-block`) and the article body (`article-text`) each as a single escaped `<div>` (`{{ article.summary | e }}` / `{{ article.clean_text | e }}`). The stored text separates paragraphs with single `\n` characters (verified: ~7–16 newlines per body, zero `\n\n`), but newlines collapse to spaces in HTML and there are no `<p>` elements, so the existing `.reader-pane p` paragraph spacing (`styles.css` ~672) never applies. Result: an unreadable wall of text.

3. **The article body is visually indistinguishable from the summary.** The detail view is actually two sections: `detail-summary` (heading "Summary" → the ~60-word LLM summary) followed by `detail-body` (the full `clean_text`) which has **no heading**. Combined with problem 2, the full article reads as a continuation of the summary, so users think the summary contains the whole article.

4. **No spacing between the chips row and the action buttons in the detail header.** `.detail-chips` and `.detail-actions` have **no CSS rules at all** (only the `.card-*` variants are styled), so the category/topic chips sit flush against the Mark read / Save / Open source buttons.

## Changes

### 1. Live sidebar counter refresh (behavior)
- Add an `HX-Trigger: refreshSidebar` response header to all article-interaction endpoints that change read/saved state: `/article/{id}/read`, `/unread`, `/save`, `/unsave`, and the three `/feed/.../read-all` endpoints.
- Give the sidebar container (in `app.html` or `_sidebar.html`) HTMX attributes to reload itself on that event: `hx-get="/sidebar" hx-trigger="refreshSidebar from:body" hx-swap="outerHTML"`. Ensure the swapped-in markup keeps the same trigger wiring so it works repeatedly.
- The active feed/list and selected article must not be disturbed by the sidebar refresh (only the sidebar fragment swaps).

### 2. Readable paragraph rendering
- Add a small reusable Jinja helper/filter (e.g. `paragraphs`) that splits text on newlines, drops blank lines, escapes each line, and wraps each in `<p>…</p>` (returning markup safe to render). Register it on the Jinja environment created at `app.py:45` (`templates = Jinja2Templates(...)`) via `templates.env.filters[...]`.
- Use it for both the summary block and the article body so each renders as real paragraphs and picks up the existing `.reader-pane p` styling. Preserve existing escaping semantics (no raw HTML injection from article text).

### 3. Label and separate the article body
- Add a section heading with the literal text **"Article"** to the `detail-body` block, using the existing `.detail-section-heading` style, mirroring the "Summary" heading.
- Add a light visual separation (e.g. a top border/divider on the body section) so the summary and the full article are clearly distinct.

### 4. Header chip/action spacing
- Add CSS so `.detail-chips` has bottom margin and `.detail-actions` is laid out consistently with the existing `.card-actions` pattern (`display: flex; gap; margin-top`). Mirror existing spacing tokens/variables; do not introduce new design primitives.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-web/src/aggregator_web/app.py` | Add `HX-Trigger: refreshSidebar` header to read/unread/save/unsave + read-all responses; register the `paragraphs` Jinja helper/filter |
| `packages/aggregator-web/src/aggregator_web/templates/_article_detail.html` | Render summary + body via the paragraph helper; add "Article" heading + divider to the body section |
| `packages/aggregator-web/src/aggregator_web/templates/app.html` (or `_sidebar.html`) | Add `hx-get="/sidebar" hx-trigger="refreshSidebar from:body" hx-swap="outerHTML"` to the sidebar container |
| `packages/aggregator-web/src/aggregator_web/static/styles.css` | Add `.detail-chips` / `.detail-actions` spacing rules; any body-divider styling |
| `packages/aggregator-web/tests/` | Tests for the new behavior (see Acceptance Criteria) |

## Acceptance Criteria

- After `POST /article/{id}/read` (and `/unread`, `/save`, `/unsave`, and `read-all`), the response carries an `HX-Trigger` header naming the `refreshSidebar` event. A test asserts the header is present.
- `GET /sidebar` returns counts reflecting current DB state (existing behavior), and the sidebar container markup includes the `refreshSidebar` trigger wiring.
- A multi-paragraph summary and a multi-paragraph `clean_text` each render as multiple `<p>` elements in `/article/{id}` (test asserts more than one `<p>` for multi-line input). Article text remains HTML-escaped (a test asserts injected markup in `clean_text` is escaped, not live).
- The article body section renders a heading with the literal text "Article", distinct from the "Summary" heading.
- The article body section has a visual separator (e.g. a top border/divider) distinguishing it from the summary — a corresponding CSS rule exists.
- `.detail-chips` and `.detail-actions` have explicit spacing rules in `styles.css` (chips no longer flush against the buttons).
- All existing `aggregator-web` tests continue to pass; `bash scripts/run-tests.sh` is green.

## Pending Decisions

None — scope is fixed and confined to `aggregator-web`.
