#!/usr/bin/env bash
# Isolated LOCAL Postgres for SETUAI V7 integration tests. No connection to Supabase.
# Usage: scripts/integration_db.sh up|down|status|reset|migrate|url
set -euo pipefail
PGBIN="${PGBIN:-/opt/homebrew/opt/postgresql@16/bin}"
ROOT="${SETUAI_INTEG_HOME:-$HOME/.setuai-integ}"
DATA="$ROOT/pgdata"; PORT="${SETUAI_INTEG_PORT:-54329}"; DB="${SETUAI_INTEG_DB:-setuai_integ}"   # e.g. setuai_integ_demo: the 4-project demo dataset, kept apart from the test DB
URL="postgresql://postgres@127.0.0.1:$PORT/$DB"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
run_sql() { "$PGBIN/psql" -v ON_ERROR_STOP=1 -q "$URL" "$@"; }
case "${1:-status}" in
  url) echo "$URL" ;;
  up)
    mkdir -p "$ROOT"
    [ -d "$DATA" ] || "$PGBIN/initdb" -D "$DATA" -U postgres --auth=trust -E UTF8 >/dev/null
    "$PGBIN/pg_ctl" -D "$DATA" status >/dev/null 2>&1 || \
      "$PGBIN/pg_ctl" -D "$DATA" -o "-p $PORT -c listen_addresses=127.0.0.1 -c unix_socket_directories=$ROOT" -l "$ROOT/pg.log" -w start >/dev/null
    "$PGBIN/psql" -q -p $PORT -h 127.0.0.1 -U postgres -d postgres -tc "select 1 from pg_database where datname='$DB'" | grep -q 1 || \
      "$PGBIN/createdb" -p $PORT -h 127.0.0.1 -U postgres $DB
    "$PGBIN/psql" -q "$URL" -c "CREATE TABLE IF NOT EXISTS public._setuai_env(key text primary key, value text not null); INSERT INTO public._setuai_env VALUES ('env','integration') ON CONFLICT (key) DO NOTHING; ALTER TABLE public._setuai_env ENABLE ROW LEVEL SECURITY; REVOKE ALL ON public._setuai_env FROM anon, authenticated;" >/dev/null
    echo "up: $URL" ;;
  migrate) # shim + migrations (in order) + schema.sql, into the local integration DB only
    run_sql -f "$HERE/backend/models/integration/000_supabase_shim.sql" >/dev/null
    for f in "$HERE"/backend/models/migrations/*.sql; do run_sql -f "$f" >/dev/null || { echo "FAILED: $f"; exit 1; }; done
    run_sql -f "$HERE/backend/models/schema.sql" >/dev/null
    echo "migrated $URL" ;;
  down) "$PGBIN/pg_ctl" -D "$DATA" -m fast stop ;;
  status) "$PGBIN/pg_ctl" -D "$DATA" status ;;
  reset) # destroys ONLY the local integration DB
    "$PGBIN/dropdb" -p $PORT -h 127.0.0.1 -U postgres --if-exists $DB && "$PGBIN/createdb" -p $PORT -h 127.0.0.1 -U postgres $DB
    echo "reset local $DB (now re-run: up, apply shim + migrations)" ;;
  *) echo "usage: $0 up|down|status|reset|migrate|url"; exit 2 ;;
esac
