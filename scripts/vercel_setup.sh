#!/usr/bin/env bash
# Creates and configures the two Vercel projects (Hobby plan). It does NOT deploy, and it does NOT set V2_CORS_ORIGINS (hosted CORS) or touch Supabase Auth settings.
#   npx vercel login                      (once, interactive)
#   bash scripts/vercel_setup.sh [--apply]   without --apply it only prints what it would do
# Secrets are read from .local/hosted.env / frontend/.env.local and piped to the Vercel CLI; they are never echoed.
set -euo pipefail
cd "$(dirname "$0")/.."
APPLY=0; [ "${1:-}" = "--apply" ] && APPLY=1
API_PROJECT="${ANVYRA_API_PROJECT:-anvyra-api}"; WEB_PROJECT="${ANVYRA_WEB_PROJECT:-anvyra-web}"
set -a; . .local/hosted.env; set +a
run() { if [ "$APPLY" = 1 ]; then "$@"; else echo "[dry run] $*"; fi; }
setenv() {   # setenv DIR NAME VALUE [--sensitive]
  local dir="$1" name="$2" value="$3" flag="${4:-}"
  [ -n "$value" ] || { echo "refusing: $name is empty" >&2; exit 1; }
  if [ "$APPLY" = 1 ]; then ( cd "$dir" && printf '%s' "$value" | npx vercel env add "$name" production $flag >/dev/null ) && echo "set $name ($dir)"; else echo "[dry run] set $name in $dir"; fi
}
[ "$APPLY" = 1 ] && { npx vercel whoami >/dev/null 2>&1 || { echo "not logged in: run  npx vercel login" >&2; exit 1; }; }

# ---- API project: linked from the staged bundle (the root render.yaml of the original demo would otherwise be auto-detected by the CLI as a service)
bash scripts/stage_vercel_api.sh
API_DIR=.local/vercel-api
( cd "$API_DIR" && run npx vercel link --yes --project "$API_PROJECT" )
for kv in "V2_EMBEDDING_BACKEND=onnx" "V2_MAX_REQUEST_BYTES=4400000" "V2_DB_POOL_MAX=3" "V2_ALLOW_HOSTED=1" "V2_STORAGE_BACKEND=supabase" "V2_STORAGE_BUCKET=evidence" "EXTRACTION_FALLBACK=rules"; do
  setenv "$API_DIR" "${kv%%=*}" "${kv#*=}"
done
setenv "$API_DIR" V2_ALLOWED_PROJECT_REFS "$V2_ALLOWED_PROJECT_REFS"
setenv "$API_DIR" V2_DENIED_PROJECT_REFS "$V2_DENIED_PROJECT_REFS"
setenv "$API_DIR" SUPABASE_URL "$SUPABASE_URL"
setenv "$API_DIR" V2_JWKS_URL "$V2_JWKS_URL"
setenv "$API_DIR" DB_V2_URL "$DB_V2_URL" --sensitive
setenv "$API_DIR" SUPABASE_SERVICE_ROLE_KEY "$SUPABASE_SERVICE_ROLE_KEY" --sensitive
# deliberately NOT set: V2_CORS_ORIGINS (needs the frontend URL and your approval), V2_JWT_SECRET / SUPABASE_JWT_SECRET, V2_LOCAL_LOGIN_PASSWORD, any LLM key

# ---- frontend project: ./frontend (public VITE_* values only)
( cd frontend && run npx vercel link --yes --project "$WEB_PROJECT" )
WEB_ENV=frontend/.env.local
val() { grep -E "^$1=" "$WEB_ENV" | head -1 | cut -d= -f2- | tr -d '\r'; }
setenv frontend VITE_BACKEND v2
setenv frontend VITE_SUPABASE_URL "$(val VITE_SUPABASE_URL)"
setenv frontend VITE_SUPABASE_ANON_KEY "$(val VITE_SUPABASE_ANON_KEY)"
setenv frontend VITE_MAX_UPLOAD_MB 4
# VITE_V2_API_BASE_URL is set after the API project's URL is known (printed by `vercel link`/the dashboard); it is public and is not a CORS or Auth setting.
echo "done (dry run: $((1-APPLY)))"
