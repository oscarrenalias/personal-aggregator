---
name: Native share button via Web Share API
id: spec-a616bd8d
description: "Add a Share button to the article reader (and optionally thread detail) that opens the native iOS/Android share sheet via navigator.share({title,url}). Feature-detected with a copy-link fallback so it degrades gracefully. PREREQUISITE: app must be served over HTTPS (secure context) for navigator.share to exist on iOS — currently plain HTTP over Tailscale; user evaluating tailscale serve. No backend change."
dependencies: null
priority: low
complexity: null
status: draft
tags:
- web
- pwa
- ios
- share
- ux
scope:
  in: null
  out: null
feature_root_id: null
---
# Native share button via Web Share API

> **PREREQUISITE — serve the PWA over HTTPS.** `navigator.share()` (and `navigator.clipboard`)
> are only available in a **secure context** (HTTPS or `localhost`). The app is currently
> served over plain HTTP via Tailscale/`raspberrypi.local`, so on iOS the API is `undefined`.
> The button **feature-detects and degrades gracefully**, so this can ship before HTTPS exists
> (it simply won't appear on iOS until then) — but it only becomes *useful* once the app is on
> HTTPS (e.g. `tailscale serve` provisioning a `*.ts.net` cert). Track HTTPS as the unblocker.

## Objective

Add a **Share** button to the article reader (and optionally the thread detail) that opens the
**native iOS/Android share sheet** via the Web Share API — so the user can send an article's
link to Messages, Notes, other apps, etc., straight from the PWA.

## Problems to Fix

- No way to share an article out of the app; the reader is read-only. On mobile the native
  share sheet is the expected, one-tap way to do this.

## Changes

No backend change — the shareable URL is already present (the article's source link, rendered
today as the reader's "open source" link / `data-source-url`).

### 1. Share button (article reader)

In `_article_detail.html`, add a **Share** button to the reader toolbar (next to the existing
open-source / save / read buttons), styled consistently. It carries the article's title and
source URL (e.g. via `data-` attributes already available).

### 2. Client-side share handler (`app.js`)

On tap (user-gesture required), call:
```js
if (navigator.share) {
  navigator.share({ title, url }).catch(() => {});  // user-cancel is not an error to surface
}
```
- **Feature-detect**: only wire/show the Share button when `navigator.share` exists.
- **Fallback** when it doesn't (desktop, or plain-HTTP/non-secure context): a **Copy link**
  affordance using `navigator.clipboard.writeText(url)` (also secure-context-gated) — and if
  *that's* unavailable too, fall back to selecting the URL / a plain link. Never show a button
  that does nothing.

### 3. Thread detail (optional)

Optionally add Share to `_thread_detail.html` — but a thread has no single external URL, so
share the **representative/top-importance member article's** URL (or omit on threads and keep
Share article-only for v1). Decide in Pending Decisions.

### 4. Styling

Reuse the existing reader toolbar button styling; add an icon (share glyph) consistent with the
current flat icon set.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-web/src/aggregator_web/templates/_article_detail.html` | Share button in the reader toolbar (with title/url data) |
| `packages/aggregator-web/src/aggregator_web/static/app.js` | `navigator.share` handler + feature-detect + copy-link fallback |
| `packages/aggregator-web/src/aggregator_web/static/styles.css` | Share button/icon styling |
| `packages/aggregator-web/src/aggregator_web/templates/_thread_detail.html` | (optional) Share on threads via representative article |
| `packages/aggregator-web/tests/` | Button renders with the correct url/title; degrades when API absent |

## Acceptance Criteria

- The article reader shows a **Share** button that, on a secure context with `navigator.share`,
  opens the native share sheet pre-filled with the article title + source URL (verified on iOS
  Safari / installed PWA once HTTPS is in place).
- When `navigator.share` is unavailable, the UI degrades gracefully — a working **Copy link**
  fallback (or, if clipboard is also unavailable, a selectable link) — never a dead button.
- The button is feature-detected so plain-HTTP access shows no broken control; no JS errors.
- User-cancel of the share sheet is swallowed (not surfaced as an error).
- No backend/schema change. Focused `aggregator-web` tests pass; full gate green.

## Pending Decisions

- **HTTPS unblock**: choose the HTTPS approach (likely `tailscale serve` with a `*.ts.net`
  cert, proxying to the web container) — out of scope for *this* spec but required for the
  feature to function on iOS. (User is evaluating options.)
- **Threads**: include Share on thread detail (sharing the top member article's URL) or keep it
  article-only for v1. Lean article-only first.
- **Fallback depth**: copy-link via `navigator.clipboard` (secure-context only) vs. a plain
  selectable link for the truly-insecure case. Lean: Web Share → clipboard → selectable link.
- **What URL for an article**: the original source link (proposed), not the app's private
  Tailscale URL (not useful to share externally).
