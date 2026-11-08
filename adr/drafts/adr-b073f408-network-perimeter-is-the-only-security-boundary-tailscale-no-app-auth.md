---
id: ADR-b073f408
title: "Network perimeter is the only security boundary (Tailscale, no app auth)"
status: draft
created_at: 2026-11-08T21:40:19.422934+00:00
authors: ['Renalias, Oscar']
description: "Services bind 127.0.0.1 and are reached over Tailscale; API/MCP writes are unauthenticated; public exposure requires an auth phase first"
tags: [security, deployment]
related_specs: [spec-b85ac9a3, spec-0a1c22b3, spec-b5d86383]
related_beads: 
supersedes: 
---
# Network perimeter is the only security boundary (Tailscale, no app auth)

## Summary

In the context of a single-user reader running on a home Raspberry Pi, facing the cost of building and maintaining authentication, we decided that the network perimeter (services bound to `127.0.0.1`/LAN, reached via Tailscale) is the only security boundary and that the web, API and MCP have no application auth, to achieve zero-friction access from personal devices, accepting that anything reaching the network can read and change reader state.

## Context

The web UI spec (spec-b85ac9a3) decided "Tailscale-only… no application auth — the tailnet is the boundary". The JSON API (spec-0a1c22b3) and MCP server (spec-b5d86383) kept that model and deferred auth to a later phase (Cloudflare Access JWT). The API exposes non-destructive writes (read/save/dismiss); MCP additionally exposes destructive admin tools (source/category removal, recluster). Postgres uses default credentials and publishes only on `POSTGRES_PUBLISH_ADDR` (default `127.0.0.1`).

## Decision Drivers

* One user; all clients are the user's own devices on the tailnet.
* Auth adds token handling to the PWA, TUI, mobile and MCP clients.
* Tailscale already provides device identity and encryption.

## Considered Options

### Option A — Perimeter only (Tailscale + local binding)

* Good: no login, tokens or sessions in any client.
* Good: Tailscale gives encrypted, device-authenticated transport.
* Bad: anyone on the tailnet or LAN (where bound) has full access, including MCP destructive tools.
* Bad: any accidental public exposure is a full compromise.

### Option B — Application auth now (Cloudflare Access / OIDC / API keys)

* Good: defence in depth; enables public exposure.
* Bad: work across every client before there is a need.

## Decision

Until an auth ADR supersedes this one:

* services MUST bind to `127.0.0.1` by default (`WEB_HOST`), and Postgres MUST NOT publish on `0.0.0.0`;
* no service may be exposed to the public internet, including via tunnels or port forwards;
* API and MCP write endpoints stay unauthenticated, and destructive operations MUST NOT be added to `/api/v1`;
* adding any public exposure requires an auth phase (planned: Cloudflare Access JWT) first.

## Consequences

### Positive

* Clients stay simple; the PWA and TUI need no credentials.

### Negative

* Security depends entirely on tailnet/LAN hygiene.
* `API_CORS_ALLOW_ORIGINS=*` and default DB credentials are only acceptable while this holds.
