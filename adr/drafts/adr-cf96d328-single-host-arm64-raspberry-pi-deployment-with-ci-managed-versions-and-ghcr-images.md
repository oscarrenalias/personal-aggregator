---
id: ADR-cf96d328
title: "Single-host arm64 Raspberry Pi deployment with CI-managed versions and GHCR images"
status: draft
created_at: 2026-11-08T21:40:20.233545+00:00
authors: ['Renalias, Oscar']
description: "docker-compose.prod.yml on one Pi; CI patch-bumps version and publishes arm64 images; migrations run as a one-shot step"
tags: [deployment]
related_specs: [spec-2c473de7, spec-e2f5a9d1]
related_beads: 
supersedes: 
---
# Single-host arm64 Raspberry Pi deployment with CI-managed versions and GHCR images

## Summary

In the context of a personal service with one user, facing hosting cost and operational simplicity, we decided to run the full stack as a Docker Compose deployment on a single arm64 Raspberry Pi, with CI patch-bumping the version and publishing arm64 images to GHCR on every relevant push to `main`, to achieve hands-off releases on cheap hardware, accepting no high availability and arm64-only images.

## Context

The containerization spec (spec-2c473de7) fixed `linux/arm64` images on `python:3.13-slim` with uv, a non-root user, `postgres:16`, and migrations as a one-shot step. The release spec (spec-e2f5a9d1) moved versioning into CI (`uv version --bump patch`, committed back as `chore: bump version… [skip ci]`), published three tags per image, and attached `docker-compose.prod.yml`, `install.sh`, `aggregator.service` and `.env.example` to a GitHub Release. The Pi updates with `sudo ./install.sh update`.

## Decision Drivers

* Near-zero hosting cost.
* Releases must not need manual version bookkeeping.
* Rollback must be one config change.

## Considered Options

### Option A — Single Pi, compose stack, CI-built GHCR images

* Good: cheap; the whole system is one compose file and one `.env`.
* Good: every merge produces a pinned, rollback-able version (`APP_VERSION=vX.Y.Z`).
* Bad: single point of failure; Pi resources (RAM, SD/SSD I/O) bound design choices.
* Bad: CI writes to `main`, so local clones must pull after every push.

### Option B — Cloud VM or managed services

* Good: more resources and managed Postgres.
* Bad: recurring cost and public exposure, conflicting with ADR-b073f408.

### Option C — Kubernetes (k3s)

* Good: rolling updates.
* Bad: heavy for one node; no real benefit at one replica per service.

## Decision

Production is a single arm64 host running `docker-compose.prod.yml` under the `aggregator` systemd unit. Images are built only in CI for `linux/arm64` and published to `ghcr.io/oscarrenalias/personal-aggregator/aggregator-<service>`. The workspace version in `pyproject.toml` is CI-managed and MUST NOT be bumped by hand; minor/major bumps go through `workflow_dispatch`. Schema migrations run in the one-shot `migrate` service before app services start. Operator config lives only in `/opt/personal-aggregator/.env`, which `install.sh update` never overwrites.

## Consequences

### Positive

* Releases are push-to-main; deploys are one command.
* Rollback is pinning `APP_VERSION`.

### Negative

* Designs must stay light on CPU, RAM and disk (bounded merge windows, retention).
* No redundancy; a Pi failure is an outage.
* New env vars must be added to `.env` by hand on the Pi.
