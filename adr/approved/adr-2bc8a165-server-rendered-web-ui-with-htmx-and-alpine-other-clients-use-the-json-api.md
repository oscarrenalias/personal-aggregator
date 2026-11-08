---
id: ADR-2bc8a165
title: Server-rendered web UI with HTMX and Alpine; other clients use the JSON API
status: approved
created_at: '2026-11-08T21:40:19.862559+00:00'
authors:
- Renalias, Oscar
description: FastAPI + Jinja + HTMX + Alpine, no SPA framework or JS build step; TUI/mobile
  are pure HTTP clients of /api/v1
accepted_at: '2026-11-08T21:44:24.018430+00:00'
superseded_at: null
superseded_by: null
supersedes: []
tags:
- frontend
- architecture
related_specs:
- spec-b85ac9a3
- spec-0a1c22b3
- spec-41c4d8de
related_beads: []
review_after: null
---

# Server-rendered web UI with HTMX and Alpine; other clients use the JSON API

## Summary

In the context of a reader UI used on desktop and iOS Safari and maintained by agents, facing the cost of a JavaScript build toolchain and SPA state management, we decided on a server-rendered FastAPI + Jinja2 + HTMX + Alpine.js PWA with hand-written CSS and no build step, with non-browser clients (TUI, mobile) using the JSON API, to achieve a fast, simple UI with one rendering path, accepting limited client-side interactivity.

## Context

The web UI spec (spec-b85ac9a3) chose HTMX for partial swaps and infinite scroll, Alpine for keyboard shortcuts and selection, hand-rolled responsive CSS, and a PWA manifest for an app-like iOS experience. Later, the JSON API (spec-0a1c22b3) was added as a thin layer over `aggregator_common.queries` for a TUI (spec-41c4d8de) and a future mobile app, rather than having those clients scrape HTML or touch the DB.

## Decision Drivers

* No Node toolchain in the repo or Docker images.
* One source of truth for queries across web, API and MCP.
* Must work well on iOS Safari as an installed PWA.

## Considered Options

### Option A — Server-rendered HTMX + Alpine; JSON API for other clients

* Good: no build step; templates and Python share the same data.
* Good: small payloads and fast first paint on the Pi.
* Good: TUI/mobile get a typed REST contract without a second backend.
* Bad: complex client state (e.g. global nav highlight) needs Alpine stores and care with HTMX swaps.

### Option B — SPA (React/Vue/Svelte) over the JSON API

* Good: rich interactivity; one API for all clients.
* Bad: adds Node, bundling and a separate deploy artifact.
* Bad: duplicates rendering logic in JS.

## Decision

The web UI is server-rendered with Jinja2, HTMX and Alpine.js, styled by hand-written CSS (mobile overrides in the single `@media (max-width: 639px)` block). Adding a JavaScript build step or SPA framework requires superseding this ADR. Non-browser clients MUST use `/api/v1` (mounted in the web service); they MUST NOT access Postgres directly. Read and write logic lives in `aggregator_common.queries` / `management`, shared by web, API and MCP.

## Consequences

### Positive

* One deployable web container serves UI and API.
* Agents can change UI with plain templates and CSS.

### Negative

* Interaction-heavy features need careful HTMX/Alpine coordination (refresh triggers, store registration).
* Every API route change needs `make openapi` to keep `docs/openapi.json` current.
