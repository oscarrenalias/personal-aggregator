---
name: prod-db
description: Read-only queries against the PRODUCTION aggregator database on the Pi through one wrapper script (.claude/skills/prod-db/prod-db.sh). Use whenever you need to look at prod data — a podcast episode's script or tones, a brief, threads, articles, LLM calls, pipeline state — or run aggregator-admin diagnostics against prod. Always use this script instead of hand-written `docker run ... psql` or socat tunnel commands.
---

# prod-db

All production DB access goes through **`.claude/skills/prod-db/prod-db.sh`**, run from the repo root with that exact relative path. The path is allow-listed in `.claude/settings.local.json`, so calls run without permission prompts. Do not rewrite these calls as raw `docker` commands; those prompt every time.

## Why a script

The workstation's VPN/endpoint filters drop host→LAN traffic to the Pi, but Docker container egress isn't filtered. The script runs every connection through a throwaway container (`postgres:16` for psql, `alpine/socat` for the admin CLI tunnel) and removes it afterwards.

Connection details: the Pi is at `PROD_DB_HOST` (default `192.168.68.52`). Credentials are read from `docker-compose.prod.yml`. If connections are refused, check that the Pi's `/opt/personal-aggregator/.env` has `POSTGRES_PUBLISH_ADDR=192.168.68.52`.

## Read-only guarantee

Every session starts with `default_transaction_read_only=on` and a 120 s statement timeout, so any `INSERT`/`UPDATE`/`DELETE`/DDL fails with `cannot execute ... in a read-only transaction`. Arguments that try to change that setting are rejected before anything runs. To make a change to prod data, ask the user. Never work around the guard.

## Commands

```bash
.claude/skills/prod-db/prod-db.sh check                          # connectivity + confirms read_only=on
.claude/skills/prod-db/prod-db.sh tables                         # list tables
.claude/skills/prod-db/prod-db.sh schema podcast_episodes        # columns of one table
.claude/skills/prod-db/prod-db.sh podcast                        # latest ready episode: metadata + per-segment type/tone/pace/category/headline
.claude/skills/prod-db/prod-db.sh podcast 2026-11-20             # episode for a date
.claude/skills/prod-db/prod-db.sh psql -qAt -c "select count(*) from threads where status='active';"   # any SQL (psql passthrough)
.claude/skills/prod-db/prod-db.sh psql -f - <<'SQL'              # multi-statement SQL on stdin
select status, count(*) from articles group by 1;
select status, count(*) from briefs group by 1;
SQL
.claude/skills/prod-db/prod-db.sh admin ops status               # aggregator-admin against prod (tunnel opened + torn down automatically)
.claude/skills/prod-db/prod-db.sh admin llm-stats --days 7
```

Tips:
- `-qAt` gives bare pipe-separated output for scripting; omit it for aligned tables.
- JSON columns (`script_json`, `known_facts`, `deltas`) are easiest with `jsonb_array_elements(...)` / `->>` in SQL rather than dumping whole blobs.
- For health and metrics reviews (baselines and what to flag), use the `metrics-review` skill. It runs its queries through this script.
