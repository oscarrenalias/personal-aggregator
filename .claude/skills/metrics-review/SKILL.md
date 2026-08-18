---
name: metrics-review
description: Retrieve, review, and analyze PRODUCTION metrics/health for the personal-aggregator running on the Raspberry Pi — pipeline state-machine counts, stuck/failed work, clustering (threads/tiers/fragmentation), archival & retention, daily briefs, and LLM cost/errors. Use whenever asked to "check metrics", "is prod healthy", "review clustering/archival", "how much is the LLM costing", or to sanity-check the pipeline after a deploy. Read-only by default. Connects over a Docker-container tunnel because the workstation's LAN filters block host-CLI access to the Pi (container egress is not filtered).
tools: Bash, Read
license: MIT
---

# metrics-review

A repeatable playbook to pull and analyze the **production** aggregator's health from this workstation. The prod stack runs on the Pi; its Postgres is **not** published to the Pi's LAN in the repo compose, but in practice the Pi exposes `5432` on its LAN interface and a **Docker container on this Mac can reach it** even though host CLI (`psql`/`curl`/`ssh`) cannot — the workstation's GlobalProtect/Zscaler filters drop host→LAN traffic, but a container's egress bypasses them.

**READ-ONLY by default.** Only run `SELECT`/status/list commands. Never run mutations (`recluster`, `brief generate`, `ops reap`, thread dismiss, any `INSERT/UPDATE/DELETE`) unless the user explicitly asks. **Always tear down the tunnel container when done** (host/Pi hygiene — this deployment has hit disk-full before).

## 1. Resolve connection details (don't hardcode)

```bash
cd <repo-root>
PI=$(ssh -G raspberrypi 2>/dev/null | awk '/^hostname /{print $2}')   # e.g. 192.168.68.52, from ~/.ssh/config
PGUSER=$(awk '/POSTGRES_USER:/{print $2; exit}' docker-compose.prod.yml)
PGPASS=$(awk '/POSTGRES_PASSWORD:/{print $2; exit}' docker-compose.prod.yml)
PGDB=$(awk '/POSTGRES_DB:/{print $2; exit}' docker-compose.prod.yml)
PG="postgresql://$PGUSER:$PGPASS@$PI:5432/$PGDB"     # for the direct psql-container method
echo "Pi=$PI db=$PGDB user=$PGUSER"
```
(As of 2026-08: `raspberrypi` → `192.168.68.52`; creds are the compose defaults `aggregator/aggregator/aggregator`. DB is LAN/Tailscale-only, not public.)

## 2. Two ways to query

**A. Direct psql container** (best for ad-hoc aggregate SQL — one shot, self-contained):
```bash
docker run --rm postgres:16 psql "$PG" -qAt -c "select count(*) from threads;"
```
The container egresses straight to the Pi, bypassing the host filter.

**B. socat forwarder + admin CLI** (needed for the `aggregator-admin` diagnostics, which run on the host and connect to `localhost`):
```bash
docker rm -f pgtunnel >/dev/null 2>&1
docker run -d --name pgtunnel -p 15432:5432 alpine/socat tcp-listen:5432,fork,reuseaddr tcp-connect:$PI:5432
sleep 2
export DATABASE_URL="postgresql://$PGUSER:$PGPASS@localhost:15432/$PGDB"
# ... run: uv run aggregator-admin <cmd> ...
```
Host→`localhost:15432` is local (not filtered); the container forwards to the Pi. **Teardown:** `docker rm -f pgtunnel`.

## 3. The standard review (all read-only)

**Pipeline / state machine** (admin CLI via tunnel — method B):
```bash
uv run aggregator-admin ops status        # article counts by status, in-flight, source counts
uv run aggregator-admin ops stuck          # stale claims (should be empty)
uv run aggregator-admin ops failures       # failed articles + last_error (should be empty)
uv run aggregator-admin clusters list      # threads: id, tier, title, member_count, last_updated
uv run aggregator-admin brief list         # recent briefs: status/origin/generated_at/headline
uv run aggregator-admin llm-stats --days 7 # per-service cost/tokens/errors (table is wide/truncates)
```

**Aggregates** (direct psql — method A; `llm-stats` truncates, so get cost from SQL):
```bash
# LLM cost + errors by service/operation (7d)
docker run --rm postgres:16 psql "$PG" -qAt -c "
 select service||' / '||operation||': \$'||round(sum(cost_usd)::numeric,3)||'  ('||count(*)||' calls'||
   case when count(*) filter (where status<>'success')>0 then ', '||count(*) filter (where status<>'success')||' ERR' else '' end||')'
 from llm_calls where created_at > now()-interval '7 days' group by 1,2 order by sum(cost_usd) desc nulls last;
 select '---- TOTAL 7d = \$'||round(sum(cost_usd)::numeric,2) from llm_calls where created_at > now()-interval '7 days';"

# LLM errors detail (error_type breakdown)
docker run --rm postgres:16 psql "$PG" -c "
 select service, operation, status, error_type, count(*) from llm_calls
 where created_at > now()-interval '7 days' and status<>'success' group by 1,2,3,4 order by 5 desc;"

# Threads: status + tier distribution, fragmentation, dismissed
docker run --rm postgres:16 psql "$PG" -qAt -c "
 select 'status '||status::text||' = '||count(*) from threads group by status;
 select 'tier '||coalesce(tier::text,'(none)')||' = '||count(*) from threads group by tier;
 select 'single-member = '||count(*) filter (where c=1)||' | multi = '||count(*) filter (where c>1)||' | total = '||count(*)
   from (select thread_id, count(*) c from thread_memberships group by thread_id) t;
 select 'threads idle >7d = '||count(*) from threads where last_updated < now()-interval '7 days';"

# Retention: article/thread age spans
docker run --rm postgres:16 psql "$PG" -qAt -c "
 select 'oldest ready article = '||min(retrieved_at)::text from articles where status='ready';
 select 'oldest thread last_updated = '||min(last_updated)::text from threads;
 select 'total threads = '||count(*) from threads;"
```

## 4. What healthy looks like — flag deviations

- **Pipeline:** `failed_processing`/`failed_ranking` and `stuck` should be **0**; `in_flight` transient. `ready` is the terminal state and grows with volume (bounded by janitor retention). Flag any nonzero failures/stuck.
- **Briefs:** exactly **one `ready` brief per day** at `BRIEF_GENERATION_HOUR` (06:00 UTC), `origin=auto`. Flag gaps, `pending`/`failed`, or duplicates.
- **LLM:** expect **0 errors**. Flag any `error_type` cluster (esp. `timeout`). Sanity-check cost and **flag any single operation dominating** (e.g. `consolidate_facts` should NOT be ~50% of spend). Watch model-name consistency in `llm_calls`.
- **Clusters:** `status` distribution should include `dormant`/`archived` once thread-aging is live (before that, all `active` is a known gap). `tier` is currently **unused/NULL by design** (surfacing uses `surfaced`/`top_grade`, not `tier`) — don't alarm on null tier until tier classification is implemented. **Fragmentation:** high single-member share (>~70%) signals a clustering-recall problem.
- **Retention:** `oldest ready article` should be within a sane multiple of `JANITOR_ARTICLE_RETENTION_DAYS` (14) — very old ready articles usually mean they're pinned by a live thread. Thread age is bounded by the janitor's by-age purge (~30d).

## 5. Baseline snapshot (2026-08-18, for comparison)

Pipeline: 7750 ready, 340 skipped, 0 failed/stuck/in-flight, 15 sources enabled. Briefs: daily 06:00 UTC, ready. LLM 7d: **$5.64 total** — `consolidate_facts` $3.07 (**54%**, ~370 calls/day, 40 `timeout` errors), `rank` $1.36, `classify` $1.11, `merge` $0.02, `brief` $0.09. Threads: **4635, all `active`, tier all NULL** (aging + tier were unimplemented — being fixed), 3469 idle >7d; **77% single-member** (3588/1047). Oldest ready article 2026-06-12; oldest thread 2026-07-19.

## 6. Cleanup (always)

```bash
docker rm -f pgtunnel >/dev/null 2>&1 && echo "tunnel removed"
```
