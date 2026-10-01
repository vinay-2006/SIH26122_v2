#!/usr/bin/env bash
# Runs the whole V7 prototype against the ISOLATED local demo database (never the shared Supabase DB):
#   scripts/run_local_demo.sh setup      -> start local Postgres, create + migrate the demo DB, seed the four projects (idempotent)
#   scripts/run_local_demo.sh backend    -> API on :8010 (real HS256 JWTs, local-login enabled)
#   scripts/run_local_demo.sh frontend   -> Vite dev server on :5183 (local demo login screen)
# Demo logins (password Demo123456!): supervisor@setuai.demo  (SUPERVISOR)   engineer@setuai.demo  (SITE_ENGINEER)
# The demo database (setuai_integ_demo) is separate from the test database (setuai_integ) so tests never touch demo data.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
export SETUAI_INTEG_DB="${SETUAI_INTEG_DB:-setuai_integ_demo}"
case "${1:-}" in
  setup)
    "$HERE/scripts/integration_db.sh" up
    "$HERE/scripts/integration_db.sh" migrate
    export SETUAI_ALLOW_DB_TESTS=1 SETUAI_TEST_ENV=integration
    export DATABASE_URL="$("$HERE/scripts/integration_db.sh" url)"
    cd "$HERE" && python3 -m backend.prototype_seed.seed ;;
  backend)
    export SETUAI_ALLOW_DB_TESTS=1 SETUAI_TEST_ENV=integration SETUAI_LOCAL_DEMO_AUTH=1
    export DATABASE_URL="$("$HERE/scripts/integration_db.sh" url)"
    export SUPABASE_JWT_SECRET="canonical-build-test-secret-not-a-real-credential-0123456789"
    export CORS_ORIGINS="http://127.0.0.1:${FRONTEND_PORT:-5183},http://localhost:${FRONTEND_PORT:-5183}"
    export SUPABASE_URL="" SUPABASE_ANON_KEY="" SUPABASE_SERVICE_ROLE_KEY="" SUPABASE_JWKS_URL="" AUTH_DEV_MODE=false
    # Live LLM calls are OPT-IN (SETUAI_ALLOW_LIVE_LLM=1). Otherwise the keys in .env are blanked and extraction uses the
    # deterministic rules extractor, so the demo is repeatable offline and never sends anything to an external provider.
    if [ "${SETUAI_ALLOW_LIVE_LLM:-0}" != "1" ]; then
      export LLM_API_KEY="" GROQ_API_KEY="" GEMINI_API_KEY="" EXTRACTION_FALLBACK=rules
      echo "[demo] live LLM disabled (set SETUAI_ALLOW_LIVE_LLM=1 to enable); using rule-based extraction" >&2
    fi
    cd "$HERE" && exec python3 -m uvicorn backend.main:app --host 127.0.0.1 --port "${BACKEND_PORT:-8010}" ;;
  frontend)
    cd "$HERE/frontend" && VITE_LOCAL_DEMO_AUTH=true VITE_API_BASE_URL=http://127.0.0.1:${BACKEND_PORT:-8010} exec npx vite --host 127.0.0.1 --port "${FRONTEND_PORT:-5183}" ;;
  *) echo "usage: $0 setup|backend|frontend"; exit 2 ;;
esac
