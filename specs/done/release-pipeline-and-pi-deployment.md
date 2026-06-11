---
name: Release Pipeline and Pi Deployment
id: spec-e2f5a9d1
description: "Release pipeline + Pi deployment (Spec B). CI mirrors oscarrenalias/takt: uv version --bump (patch on push, minor/major via workflow_dispatch input), committed back [skip ci], GitHub Release vX.Y.Z --generate-notes per push. Adapted for Docker: buildx arm64 -> GHCR (:vX.Y.Z/:latest/:main-sha). Root pyproject as CI-managed version source (package=false). Pi: systemd unit + install.sh + runbook. Depends on Spec A."
dependencies: null
priority: high
complexity: null
status: done
tags:
- release
- ci
- ghcr
- deploy
- raspberry-pi
- depends-spec-a
scope:
  in: null
  out: null
feature_root_id: B-5967336f
---
# Release Pipeline and Pi Deployment

## Objective

Add the **release pipeline** and **Raspberry Pi deployment** layer on top of the containerization work (Spec A). On every push to `main`, CI runs the tests, bumps the version, builds the arm64 images, publishes them to GHCR, and cuts a GitHub Release carrying the Pi deploy assets. On the Pi, a systemd unit + install script run the stack headless and on boot.

The CI/versioning **mirrors the user's `oscarrenalias/takt` repo** (`uv version --bump`, committed back, a GitHub Release per push with `--generate-notes`), adapted from PyPI-style packaging to Docker/GHCR.

## Dependencies

Depends on **Spec A** (`spec-2c473de7`, merged): per-service arm64 Dockerfiles, `docker-compose.prod.yml`, `scripts/build-images.sh`, `scripts/run-tests.sh` (pre-existing per-package test runner), the `aggregator-migrate` entrypoint, `APP_VERSION` build-arg. Remote `origin` = `github.com/oscarrenalias/personal-aggregator`. GHCR path `ghcr.io/oscarrenalias/personal-aggregator/<svc>`.

## Background / Decisions

- **Versioning is 100% CI-managed — the user never hand-edits `pyproject.toml`.** Mirrors takt: CI runs `uv version --bump <part>`, commits the bump back to `main` with `[skip ci]`, and the bumped version drives image tags + the GitHub Release.
  - **push to `main`** → auto-bump **patch**.
  - **minor/major** → a `workflow_dispatch` input `bump: patch|minor|major` (default `patch`); the user clicks "Run workflow" and picks — no file editing.
- **Version source (workspace adaptation):** the root `pyproject.toml` gets a minimal `[project]` (`name = "personal-aggregator"`, `version = "0.1.0"` seeded once) plus `[tool.uv] package = false` so it is the bump target but is not itself built/installed. (takt is a single package; we are a workspace, hence this.) If `uv version` cannot operate with `package = false`, fall back to a root `VERSION` file + a tiny bump step — decide during implementation/validation.
- **GHCR: public** (no Pi login needed).
- **Image tags per push:** `:vX.Y.Z` + `:latest` + `:main-<shortsha>`. No `edge`.
- **arm64 build on hosted (amd64) runners** via `docker/setup-qemu-action` + `docker/setup-buildx-action` (emulated; acceptable for a personal cadence).
- **GitHub Release on every push** (`gh release create vX.Y.Z --generate-notes`), with the deploy assets attached.
- **Distribution:** GitHub Release assets (compose, systemd unit, install script, `.env.example`).
- **Pi runs `:latest`** by default (continuous — every release lands on the next pull); pinning a `:vX.Y.Z` is documented for rollback. Updates via `docker compose pull && up -d` (an optional systemd timer can automate this; default off).

## Changes

### 1. Version source (root `pyproject.toml`)

Add a minimal `[project]` table (`name`, `version = "0.1.0"`, `requires-python`) + `[tool.uv] package = false`. This is the single version the CI bumps. Update `scripts/build-images.sh` to read the version from `uv version` (so local builds match CI) instead of `git describe`.

### 2. CI workflow (`.github/workflows/ci.yml`)

Mirror takt's `test → build → publish` structure:

- **Triggers:** `push` to `main` (paths: `packages/**`, `pyproject.toml`, `Dockerfile`s, `docker-compose.prod.yml`, `scripts/**`) and `workflow_dispatch` with input `bump` (`patch`|`minor`|`major`, default `patch`).
- **test job** (`ubuntu-latest`): checkout, setup uv, `uv sync`, run `bash scripts/run-tests.sh` (testcontainers works on hosted runners — Docker is preinstalled).
- **build job** (`needs: test`; `permissions: contents: write, packages: write`; outputs `version`):
  - configure git identity; `uv version --bump ${{ inputs.bump || 'patch' }}`; read version; commit `chore: bump version to vX.Y.Z [skip ci]` + `git pull --rebase` + push to `main`.
  - `docker/setup-qemu-action` + `docker/setup-buildx-action`; `docker/login-action` to `ghcr.io` with `GITHUB_TOKEN`.
  - For each of `{retriever, processor, summarize-rank, admin}`: buildx build `--platform linux/arm64 --build-arg APP_VERSION=vX.Y.Z --push` tagged `ghcr.io/oscarrenalias/personal-aggregator/<svc>:{vX.Y.Z, latest, main-<shortsha>}`.
- **publish job** (`needs: build`; `permissions: contents: write`): `gh release create "vX.Y.Z" --title "Release vX.Y.Z" --generate-notes` with the deploy assets (`docker-compose.prod.yml`, `deploy/aggregator.service`, `deploy/install.sh`, `.env.example`) attached.

### 3. Pi deploy assets

- **`deploy/aggregator.service`** — systemd unit: `Type=oneshot`, `RemainAfterExit=yes`, `WorkingDirectory=/opt/personal-aggregator`, `ExecStart=/usr/bin/docker compose -f docker-compose.prod.yml up -d`, `ExecStop=… down`, `WantedBy=multi-user.target`. (Compose services keep `restart: unless-stopped`.)
- **`deploy/install.sh`** — idempotent installer for the Pi: checks Docker is present; creates `/opt/personal-aggregator`; downloads/copies `docker-compose.prod.yml` + `.env.example` (→ `.env` if absent, prompting the user to fill in their **LLM API key** — `OPENAI_API_KEY` by default since `LLM_MODEL=gpt-4.1-mini`, or `ANTHROPIC_API_KEY` if they switch to a Claude model — plus any other required vars; `.env.example` already lists these); sets `IMAGE_PREFIX=ghcr.io/oscarrenalias/personal-aggregator` and the image tag (`latest` by default); installs + enables `aggregator.service`. Supports an `update` action (`docker compose pull && up -d`) and a **`--check` dry-run** (validate prerequisites + print planned actions, change nothing).
- **`docker-compose.prod.yml`** — confirm it parameterizes `IMAGE_PREFIX`/`APP_VERSION` (from Spec A) and that the Pi can default to `:latest`.

### 4. Pi runbook (`deploy/README.md`)

Prerequisites (64-bit Raspberry Pi OS, Docker, optional Tailscale for the future web UI), one-time install (download `install.sh` from the latest GitHub Release, run it, fill `.env`), start/enable, view logs (`docker compose logs -f` / `journalctl -u aggregator`), update (`install.sh update`), rollback (pin `APP_VERSION=vX.Y.Z`), and a note on Postgres volume backups.

## Files to Modify

| File | Change |
|---|---|
| `pyproject.toml` | root `[project]` (version source) + `[tool.uv] package = false` |
| `scripts/build-images.sh` | read version from `uv version` (match CI) |
| `.github/workflows/ci.yml` | test → build (bump + buildx arm64 → GHCR) → publish (GitHub Release) |
| `deploy/aggregator.service` | systemd unit |
| `deploy/install.sh` | Pi installer + `update` action |
| `deploy/README.md` | Pi runbook |
| `CLAUDE.md` | release/versioning + deploy docs |

## Acceptance Criteria

- The root `[project].version` exists and `uv version --bump patch` increments it; `scripts/build-images.sh` tags images with that version.
- `ci.yml` is valid (e.g. `actionlint`/`yamllint` clean) and structured `test → build → publish`; `workflow_dispatch` exposes the `bump` input.
- The build job tags images `:vX.Y.Z` + `:latest` + `:main-<shortsha>` under `ghcr.io/oscarrenalias/personal-aggregator/<svc>` and builds `--platform linux/arm64`.
- `deploy/install.sh` implements a `--check` dry-run, passes shellcheck, and is idempotent; on a Docker-equipped host it lays down `/opt/personal-aggregator` with the compose file + `.env` template and enables `aggregator.service` (verified via the `--check` dry-run + a local non-Pi smoke run with `IMAGE_PREFIX` pointed at locally-built images).
- `aggregator.service` is a valid unit (`systemd-analyze verify`) that brings the stack up and restarts on failure/boot.
- Existing pytest suite stays green.

### Validation (operator, post-merge — not the pytest gate)

CI behavior can't be exercised by the pytest gate. After merge, **push `main` to `origin`** and confirm the first `ci.yml` run: tests pass, a version-bump commit lands on `main`, four arm64 images appear in GHCR (`:vX.Y.Z`/`:latest`/`:main-<sha>`), and a GitHub Release `vX.Y.Z` is created with the deploy assets. Then dry-run `install.sh` (locally or on the Pi).

## Pending Decisions

- **Pi auto-update timer:** default off (manual `install.sh update`); a systemd timer doing `docker compose pull && up -d` can be added if hands-off continuous deploy is wanted.
- `uv version` + `package = false` viability — fall back to a `VERSION` file if needed (decide at implementation).
- arm64 via QEMU on hosted runners now; native arm64 runners later if build time becomes a problem.
- `web` is added to the compose + a Dockerfile + the build matrix when the web service is built (separate spec).
