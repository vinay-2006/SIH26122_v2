#!/usr/bin/env bash
# Isolated LOCAL Postgres database for the clean SIH schema (db/migrations). No Supabase connection of any kind.
# Usage: scripts/db_v2.sh up|migrate|status|reset|url|psql
#   up       start the local cluster if needed, create the database, apply the auth shim + migrations
#   reset    drop and recreate ONLY this local database (setuai_v2_integ), then migrate
set -euo pipefail
PGBIN="${PGBIN:-/opt/homebrew/opt/postgresql@16/bin}"
ROOT="${SETUAI_INTEG_HOME:-$HOME/.setuai-integ}"
DATA="$ROOT/pgdata"; PORT="${SETUAI_INTEG_PORT:-54329}"; DB="${DB_V2_NAME:-setuai_v2_integ}"
URL="postgresql://postgres@127.0.0.1:$PORT/$DB"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
case "$DB" in setuai_v2_*) ;; *) echo "refusing: DB name must start with setuai_v2_"; exit 2;; esac
start_cluster() {
  [ -d "$DATA" ] || { echo "no local cluster at $DATA (run scripts/integration_db.sh up first)"; exit 1; }
  "$PGBIN/pg_ctl" -D "$DATA" status >/dev/null 2>&1 || \
    "$PGBIN/pg_ctl" -D "$DATA" -o "-p $PORT -c listen_addresses=127.0.0.1 -c unix_socket_directories=$ROOT" -l "$ROOT/pg.log" -w start >/dev/null
}
create_db() {
  "$PGBIN/psql" -q -p "$PORT" -h 127.0.0.1 -U postgres -d postgres -tc "select 1 from pg_database where datname='$DB'" | grep -q 1 || \
    "$PGBIN/createdb" -p "$PORT" -h 127.0.0.1 -U postgres "$DB"
}
case "${1:-status}" in
  url) echo "$URL" ;;
  up|migrate) start_cluster; create_db; DB_V2_URL="$URL" python3 "$HERE/db/migrate.py" apply --with-shim ;;
  status) DB_V2_URL="$URL" python3 "$HERE/db/migrate.py" status ;;
  reset) start_cluster
    "$PGBIN/dropdb" -p "$PORT" -h 127.0.0.1 -U postgres --if-exists "$DB"
    "$PGBIN/createdb" -p "$PORT" -h 127.0.0.1 -U postgres "$DB"
    DB_V2_URL="$URL" python3 "$HERE/db/migrate.py" apply --with-shim ;;
  psql) exec "$PGBIN/psql" "$URL" ;;
  *) echo "usage: $0 up|migrate|status|reset|url|psql"; exit 2 ;;
esac
