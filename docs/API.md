# Personal Aggregator — JSON API reference

Self-contained reference for building a **client** against the aggregator's JSON
API (mobile/iOS app, the terminal `aggregator-tui`, scripts). The API is a
read-mostly REST surface mounted at **`/api/v1`** inside the `web` service.

> For deeper architecture (services, state machine, clustering) see
> [`../CLAUDE.md`](../CLAUDE.md). This file is the client-facing contract.

## Base URL

```
https://<host>/api/v1
```

- **Self-hosted behind Cloudflare Access** (recommended public access): e.g.
  `https://aggregator-api.renaliaslabs.net/api/v1`.
- **Local / LAN**: `http://<host>:8000/api/v1` (default `http://localhost:8000/api/v1`).

## Authentication

The API itself is **unauthenticated** — it relies on the network perimeter.
For public access it is published behind **Cloudflare Access** with a
**service-token (Service Auth) policy**. Clients send the token on **every**
request as two headers:

```
CF-Access-Client-Id:     <client-id>.access
CF-Access-Client-Secret: <client-secret>
```

Cloudflare validates them at the edge; requests without valid headers get an
HTTP `403` and an HTML Cloudflare Access error page (not JSON). Create the token
in the Zero Trust dashboard under **Access → Service Auth → Service Tokens**, and
attach it to the API application via a policy whose **Action is "Service Auth"**.

Store the secret securely (iOS **Keychain**, never in source or `Info.plist`).
A single shared service token suits a personal, single-user app; for per-user
identity use Cloudflare Access OIDC via `ASWebAuthenticationSession` instead.

### iOS (URLSession) sketch

```swift
var req = URLRequest(url: URL(string: "\(baseURL)/articles?limit=20")!)
req.setValue(clientId,     forHTTPHeaderField: "CF-Access-Client-Id")
req.setValue(clientSecret, forHTTPHeaderField: "CF-Access-Client-Secret")
let (data, resp) = try await URLSession.shared.data(for: req)
```

Prefer generating a typed client with
[`swift-openapi-generator`](https://github.com/apple/swift-openapi-generator)
from [`openapi.json`](./openapi.json), and inject the two headers via a transport
middleware so every request carries them.

## Response conventions

- **Pagination envelope.** Every list endpoint returns
  `{ "items": [...], "next_cursor": "<opaque-string>" | null }`.
  - `next_cursor` is an **opaque** base64 string. Pass it **verbatim** as the
    `cursor` query param to fetch the next page. `null` means no more pages.
    Never parse, construct, or compare cursors.
- **Passive reads.** `GET` endpoints never mutate state. In particular,
  `GET /threads/{id}` does **not** stamp "last viewed" / clear the unread
  indicator — that is intentional, so a client polling the API doesn't reset the
  web UI's update markers. Use the explicit write endpoints to change state.
- **Errors.** Application errors are FastAPI-style JSON: `{ "detail": "..." }`
  with the appropriate 4xx/5xx status (`404` unknown id, `422` bad params).
  A `403` with an **HTML** body is the Cloudflare Access layer, not the API —
  it means the token headers are missing/invalid.
- **Content type.** `application/json` for all API responses.
- **Version.** `info.version` in the OpenAPI doc is the package version, not the
  deployed release. For the running version call `GET /healthz`
  (`{ "version": "v0.1.43", "db": "ok" }`).

## Endpoints

### Reads

| Method & path | Purpose | Key query params |
|---|---|---|
| `GET /healthz` | Liveness + running version | — |
| `GET /articles` | List articles | `view`, `category`, `source_id`, `unread_only`, `limit`, `cursor` |
| `GET /articles/search` | Full-text search | `q` (required), `category`, `source_id`, `limit`, `cursor` |
| `GET /articles/{id}` | Single article | — |
| `GET /threads` | List story threads | `sort`, `show_dismissed`, `limit`, `cursor` |
| `GET /threads/{id}` | Single thread (passive) | — |
| `GET /threads/{id}/members` | Articles in a thread | — |
| `GET /brief/today` | Today's generated brief | — |
| `GET /sources` | All feed sources | — |
| `GET /categories` | All categories | — |
| `GET /interest-profile` | Current interest-profile text | — |

### Writes

| Method & path | Purpose |
|---|---|
| `POST /articles/{id}/read` | Mark article read |
| `POST /articles/{id}/unread` | Mark article unread |
| `POST /articles/{id}/save` | Save article |
| `POST /articles/{id}/unsave` | Unsave article |
| `POST /threads/{id}/dismiss` | Dismiss thread |
| `POST /threads/{id}/restore` | Restore dismissed thread |

Write endpoints take no body; the resource is identified by the path. They are
**unauthenticated at the app layer** — they are protected only by the perimeter
(Cloudflare Access / Tailscale / localhost). Do not expose them publicly without
that perimeter.

### Enumerations

- **`view`** (on `GET /articles`): `all` · `unread` · `important` · `saved` ·
  `today` · `uncategorized`.
- **`sort`** (on `GET /threads`): `importance` (default) · `recent`.
- **`unread_only`**, **`show_dismissed`**: boolean (`true`/`false`).
- **`limit`**: page size (server default ~50).

The exact field-level schemas for every response object live in
[`openapi.json`](./openapi.json) — treat that as the source of truth.

## Examples

All examples assume the two CF-Access headers are set (omitted for brevity).

```bash
BASE=https://aggregator-api.renaliaslabs.net/api/v1
H=(-H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET")

# Health + running version
curl -sS "${H[@]}" "$BASE/healthz"

# First page of unread articles
curl -sS "${H[@]}" "$BASE/articles?view=unread&limit=20"

# Next page (pass next_cursor verbatim)
curl -sS "${H[@]}" "$BASE/articles?view=unread&limit=20&cursor=<next_cursor>"

# Articles from one source
curl -sS "${H[@]}" "$BASE/articles?source_id=5&limit=20"

# Full-text search
curl -sS "${H[@]}" "$BASE/articles/search?q=anthropic&limit=10"

# Threads, most recent first
curl -sS "${H[@]}" "$BASE/threads?sort=recent&limit=10"

# A thread and its members
curl -sS "${H[@]}" "$BASE/threads/42"
curl -sS "${H[@]}" "$BASE/threads/42/members"

# Mark an article read / save it
curl -sS -X POST "${H[@]}" "$BASE/articles/14522/read"
curl -sS -X POST "${H[@]}" "$BASE/articles/14522/save"
```

## The OpenAPI spec

- **In this repo:** [`docs/openapi.json`](./openapi.json) — a committed snapshot,
  regenerated and attached to every GitHub release as `openapi.json` so a client
  repo can pin the contract to a release tag.
- **Live:** `GET /api/v1/openapi.json` (and Swagger UI at `/api/v1/docs`),
  reachable through Cloudflare Access with the token headers.
- **Regenerate locally:**
  ```bash
  uv run --package aggregator-api python -c \
    "import json; from aggregator_api.app import app; \
     open('docs/openapi.json','w').write(json.dumps(app.openapi(), indent=2))"
  ```
