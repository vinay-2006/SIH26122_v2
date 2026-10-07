#!/usr/bin/env bash
# Local v2 development stack: isolated database + seeded demo data + the v2 API (port 8020) for the v2-mode frontend (npm run dev:v2, port 5190).
#   scripts/v2_dev_stack.sh up      create/seed the local dev database, start the API in the background
#   scripts/v2_dev_stack.sh down    stop the API
#   scripts/v2_dev_stack.sh status
#   scripts/v2_dev_stack.sh reset   empty and re-seed the dev database (LOCAL setuai_v2_* only), restart the API
# Safety: only a LOCAL database named setuai_v2_* is ever used (default setuai_v2_dev, created beside the other local v2 databases). The old demo database and
# server are never touched. Two throw-away secrets (token signing key, local sign-in password) are generated once into .local/v2_dev.env (git-ignored, mode 600);
# nothing is printed. Read the sign-in password from that file yourself.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
export DB_V2_NAME="${DB_V2_NAME:-setuai_v2_dev}"
case "$DB_V2_NAME" in setuai_v2_*) ;; *) echo "refusing: database name must start with setuai_v2_" >&2; exit 2;; esac
API_PORT="${V2_API_PORT:-8020}"
ENVF="$HERE/.local/v2_dev.env"
PIDF="$HERE/.local/v2_api_${DB_V2_NAME}.pid"
LOGF="$HERE/.local/v2_api_${DB_V2_NAME}.log"
mkdir -p "$HERE/.local"

ensure_env() {
  if [ ! -f "$ENVF" ]; then
    umask 077
    python3 - "$ENVF" <<'PY'
import secrets, sys
open(sys.argv[1], "w").write(f"SUPABASE_JWT_SECRET={secrets.token_urlsafe(48)}\nV2_LOCAL_LOGIN_PASSWORD={secrets.token_urlsafe(18)}\n")
PY
    echo "generated local dev secrets in .local/v2_dev.env (git-ignored)"
  fi
}
url() { "$HERE/scripts/db_v2.sh" url; }
running() { [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; }

case "${1:-status}" in
  up)
    ensure_env
    "$HERE/scripts/db_v2.sh" up >/dev/null
    set -a; . "$ENVF"; set +a
    export DB_V2_URL="$(url)"
    python3 "$HERE/scripts/seed_v2.py" | tail -3
    if running; then echo "API already running (pid $(cat "$PIDF"))"; exit 0; fi
    if lsof -nP -iTCP:"$API_PORT" -sTCP:LISTEN >/dev/null 2>&1; then echo "refusing: port $API_PORT is already in use by another process" >&2; exit 1; fi
    export V2_CORS_ORIGINS="${V2_CORS_ORIGINS:-http://127.0.0.1:5190,http://localhost:5190}"
    export EXTRACTION_FALLBACK="${EXTRACTION_FALLBACK:-rules}"      # the original extraction: provider when configured, deterministic rules otherwise
    nohup python3 -m uvicorn backend.v2.app:app --host 127.0.0.1 --port "$API_PORT" >"$LOGF" 2>&1 &
    echo $! >"$PIDF"
    for _ in $(seq 1 60); do
      running || { echo "API process exited; see .local/v2_api_${DB_V2_NAME}.log" >&2; tail -5 "$LOGF" >&2; rm -f "$PIDF"; exit 1; }
      curl -fsS "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1 && break
      sleep 0.25
    done
    running && curl -fsS "http://127.0.0.1:$API_PORT/health" >/dev/null && echo "v2 API up on http://127.0.0.1:$API_PORT (database $DB_V2_NAME)" || { echo "API failed to start; see .local/v2_api_${DB_V2_NAME}.log" >&2; exit 1; }
    ;;
  down)
    if running; then kill "$(cat "$PIDF")" && rm -f "$PIDF" && echo "API stopped"; else echo "API not running"; rm -f "$PIDF"; fi
    ;;
  reset)
    "$0" down || true
    ensure_env
    "$HERE/scripts/db_v2.sh" up >/dev/null
    set -a; . "$ENVF"; set +a
    export DB_V2_URL="$(url)"
    python3 "$HERE/scripts/seed_v2.py" --reset-local | tail -3
    "$0" up
    ;;
  status)
    running && echo "API running (pid $(cat "$PIDF")) on port $API_PORT" || echo "API not running"
    ;;
  *) echo "usage: $0 up|down|status|reset" >&2; exit 2;;
esac
