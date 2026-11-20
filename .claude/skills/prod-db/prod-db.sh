#!/usr/bin/env bash
# Read-only access to the PRODUCTION aggregator Postgres on the Pi.
#
# The workstation's VPN/endpoint filters drop host->LAN traffic, but Docker
# container egress is not filtered, so every connection goes through a
# throwaway container (postgres:16 for psql, alpine/socat for the admin CLI).
#
# Read-only is enforced by Postgres itself: every session starts with
# default_transaction_read_only=on, so INSERT/UPDATE/DELETE/DDL fail with
# "cannot execute ... in a read-only transaction". Arguments that try to turn
# that off are rejected before anything runs.
#
# Usage (run from anywhere):
#   prod-db.sh check                         connectivity + read-only sanity check
#   prod-db.sh psql [psql args...]           psql passthrough, e.g. -qAt -c "select 1"
#                                            (SQL on stdin: prod-db.sh psql -f - < q.sql)
#   prod-db.sh tables                        list tables
#   prod-db.sh schema <table>                columns of a table
#   prod-db.sh podcast [YYYY-MM-DD]          podcast episode segments (default: latest ready)
#   prod-db.sh admin <aggregator-admin args> run the admin CLI against prod (read-only session)
#
# Env overrides: PROD_DB_HOST (default 192.168.68.52), PROD_DB_TUNNEL_PORT (default 15432)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
COMPOSE="$REPO_ROOT/docker-compose.prod.yml"
PI_HOST="${PROD_DB_HOST:-192.168.68.52}"
TUNNEL_PORT="${PROD_DB_TUNNEL_PORT:-15432}"
PG_IMAGE="postgres:16"
READONLY_OPTS="-c default_transaction_read_only=on -c statement_timeout=120000"

_compose_val() { awk -v k="$1:" '$1==k{print $2; exit}' "$COMPOSE"; }
PG_USER="$(_compose_val POSTGRES_USER)"; PG_USER="${PG_USER:-aggregator}"
PG_PASS="$(_compose_val POSTGRES_PASSWORD)"; PG_PASS="${PG_PASS:-aggregator}"
PG_DB="$(_compose_val POSTGRES_DB)"; PG_DB="${PG_DB:-aggregator}"
PG_URL="postgresql://$PG_USER:$PG_PASS@$PI_HOST:5432/$PG_DB"

die() { echo "prod-db: $*" >&2; exit 1; }

_guard() {
  local a
  for a in "$@"; do
    if printf '%s' "$a" | grep -qiE 'read_only|set[[:space:]]+session[[:space:]]+characteristics|set[[:space:]]+role|reset[[:space:]]+all'; then
      die "refusing: arguments try to change the read-only session setting"
    fi
  done
}

# Built-in commands call _psql_raw (their SQL is fixed); user-supplied args go through _psql.
_psql_raw() {
  docker run --rm -i -e PGOPTIONS="$READONLY_OPTS" "$PG_IMAGE" \
    psql "$PG_URL" -X -v ON_ERROR_STOP=1 "$@"
}

_psql() { _guard "$@"; _psql_raw "$@"; }

cmd="${1:-help}"; shift || true
case "$cmd" in
  check)
    _psql_raw -qAt -c "select 'connected to '||current_database()||' as '||current_user||' at '||now()::text, 'read_only='||current_setting('transaction_read_only');"
    ;;
  psql)
    [ $# -gt 0 ] || die "usage: prod-db.sh psql [psql args...]"
    _psql "$@"
    ;;
  tables)
    _psql_raw -qAt -c "select table_name from information_schema.tables where table_schema='public' order by 1;"
    ;;
  schema)
    [ $# -eq 1 ] || die "usage: prod-db.sh schema <table>"
    [[ "$1" =~ ^[a-z_][a-z0-9_]*$ ]] || die "invalid table name: $1"
    _psql_raw -c "select column_name, data_type, is_nullable from information_schema.columns where table_schema='public' and table_name='$1' order by ordinal_position;"
    ;;
  podcast)
    if [ $# -ge 1 ]; then
      [[ "$1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || die "date must be YYYY-MM-DD"
      where="e.date = '$1'"
    else
      where="e.id = (select max(id) from podcast_episodes where status='ready')"
    fi
    _psql_raw -c "select e.id, e.date, e.status, e.generated_at, e.duration_seconds, e.tts_model, e.tts_voice from podcast_episodes e where $where;"
    _psql_raw -c "select ord as n, s->>'type' as type, s->'tts_hints'->>'tone' as tone, s->'tts_hints'->>'pace' as pace, s->>'topic_category' as category, left(coalesce(s->>'headline', s->>'text'), 90) as headline_or_text, length(s->>'text') as chars from podcast_episodes e, jsonb_array_elements(e.script_json::jsonb->'segments') with ordinality as t(s, ord) where $where order by ord;"
    ;;
  admin)
    [ $# -gt 0 ] || die "usage: prod-db.sh admin <aggregator-admin args>"
    _guard "$@"
    name="prod-db-tunnel-$$"
    docker run -d --rm --name "$name" -p "127.0.0.1:$TUNNEL_PORT:5432" alpine/socat \
      tcp-listen:5432,fork,reuseaddr "tcp-connect:$PI_HOST:5432" >/dev/null
    trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT INT TERM
    sleep 2
    cd "$REPO_ROOT"
    DATABASE_URL="postgresql://$PG_USER:$PG_PASS@localhost:$TUNNEL_PORT/$PG_DB" \
      PGOPTIONS="$READONLY_OPTS" uv run aggregator-admin "$@"
    ;;
  help|-h|--help)
    sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    ;;
  *)
    die "unknown command '$cmd' (try: prod-db.sh help)"
    ;;
esac
