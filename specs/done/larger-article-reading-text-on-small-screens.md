---
name: Larger article reading text on small screens
id: spec-5295121c
description: "Increase only the article reading text (detail summary + body, and possibly card excerpt) font size on the mobile breakpoint for legibility on phones/iOS, without scaling the overall UI or changing desktop sizing. Uses the existing @media(max-width:639px) block; mobile sizes in rem so they also honor user font-size preference. Web CSS only, additive."
dependencies: null
priority: medium
complexity: null
status: done
tags:
- web
- ux
- css
- mobile
- accessibility
- typography
scope:
  in: null
  out: null
feature_root_id: null
---
# Larger article reading text on small screens

## Objective

Make the **article reading text** more legible on small screens (phones / iOS) by increasing its
font size on the mobile breakpoint only — **without** scaling the overall UI or changing any
desktop sizing. The reading surface (the LLM summary and article body in the detail view) is
comfortable on a large screen but too small on a phone; this targets *only* that text.

## Problems to Fix

- The article reading text is sized for desktop viewing distance and is too small on a phone.
- A global root-font-size bump would scale the *entire* UI (sidebar, controls, headers), which is
  more than wanted — the rest of the UI is fine. The fix must be scoped to article text.

## Changes

Add font-size (and line-height where helpful) overrides for the **article reading-text selectors
only**, inside the existing mobile breakpoint(s). No change to the root font-size, layout, or any
desktop (default) rule.

### In scope — selectors to bump on small screens
- `.summary-block` (and `.summary-block p`) — the article detail LLM summary (currently `0.9em`,
  line-height 1.6).
- `.detail-body` text (and its paragraphs) — the full article body in the detail view (currently
  inherits the default body size).
- `.card-excerpt` — the list-card excerpt (currently `13px`, line-clamped to 3 lines). **In scope**
  (decided). Verify the `-webkit-line-clamp` still looks right at the larger size.

### Mechanism
- Use the **existing `@media (max-width: 639px)` phone breakpoint** in `styles.css` (**phones
  only** — tablets and desktop are explicitly excluded, decided). Add rules that raise the
  in-scope selectors to a comfortable reading size — target roughly **16px** body reading text on
  phones (iOS treats <16px inputs specially and ~16px is the mobile readability floor), with
  line-height kept around 1.6 for the body/summary.
- Keep the change additive: default (desktop) rules are untouched; only the mobile media block
  gains the larger sizes.
- **Ship default (use unless device testing says otherwise):** `1rem` (16px) body/summary reading
  text with `line-height: 1.6`; `.card-excerpt` to ~`0.95rem`–`1rem`.
- Express the mobile sizes in `rem` (relative to the root) so the reading text scales with the
  root font-size and the user's **browser default-font-size setting** — applied **only** to the
  in-scope article-text selectors, so the rest of the UI does not scale. Note: because the root
  font-size is intentionally **not** changed, this does not alter behaviour under page zoom and
  does not implement OS Dynamic Type; it only ties the article text to the existing root.

### Explicitly out of scope
- Root/`html` font-size, the sidebar, feed controls, list-pane headers, meta rows, buttons, and
  **all desktop sizing**. No global scale change.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-web/src/aggregator_web/static/styles.css` | In the `@media (max-width: 639px)` block, bump font-size/line-height for `.summary-block`(+`p`), `.detail-body` text, and `.card-excerpt`; mobile sizes in `rem` |
| tests | **Primary verification is manual visual inspection** at ≤639px (DevTools / iPhone): larger reading text, surrounding UI unchanged. Optionally add a lightweight regression assertion that the `@media (max-width: 639px)` block defines a font-size for the in-scope selectors. |

## Acceptance Criteria

- On a viewport ≤639px, the article detail reading text (summary + body) renders visibly larger
  (~16px) with comfortable line-height; it is legible on an iPhone-sized screen.
- Desktop / large-screen rendering is **unchanged** (the default rules are not modified).
- The sidebar, feed/list controls, headers, and all other UI text are **unchanged** at every
  breakpoint — no global scale shift.
- The change is confined to the article reading-text selectors and the mobile media block(s).

## Resolved Decisions

- **Card excerpt:** **included** — `.card-excerpt` gets the bump along with the detail reading
  view.
- **Breakpoint:** **phones only (≤639px)** — tablets (≤1023px) and desktop unchanged.
- **Line-clamp:** **keep `-webkit-line-clamp: 3`** on `.card-excerpt`; accept that the larger font
  shows slightly less text per card / marginally taller cards. (Only revisit — e.g. drop to 2
  lines — if cards look obviously unbalanced on a real device.)
- **Verification:** primarily **manual visual** at ≤639px; an automated CSS-rule-presence
  assertion is optional/secondary.

## Pending Decisions

- **Exact target size:** ship default is `1rem`/16px + line-height 1.6 (see Mechanism); confirm
  the precise value against a real device during implementation, adjusting only if needed.
