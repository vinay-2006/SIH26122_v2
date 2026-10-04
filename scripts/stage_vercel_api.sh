#!/usr/bin/env bash
# Assemble exactly what the API deployment uploads into .local/vercel-api/ (git-ignored), and verify the model there. The Vercel CLI is linked/deployed FROM that
# directory, so the upload is the reviewed bundle (no tests, docs, frontend, scripts, the legacy render.yaml, local env files) and is the same every time.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=.local/vercel-api
mkdir -p "$OUT"
# keep .vercel (the project link) across re-staging; everything else is replaced
KEEP="$(mktemp -d)"; [ -d "$OUT/.vercel" ] && mv "$OUT/.vercel" "$KEEP/.vercel"      # the project link survives re-staging
rsync -a --delete --delete-excluded \
  --exclude='__pycache__/' --exclude='*.pyc' --exclude='test_*.py' --exclude='conftest.py' --exclude='smoke_test.py' --exclude='tests/' \
  --exclude='backend/requirements.txt' \
  --include='app.py' --include='vercel.json' --include='requirements.txt' \
  --include='backend/***' --include='db/***' \
  --exclude='*' ./ "$OUT/"
[ -d "$KEEP/.vercel" ] && mv "$KEEP/.vercel" "$OUT/.vercel"; rmdir "$KEEP" 2>/dev/null || true
rm -f "$OUT/.env.local" "$OUT/.gitignore"                                     # written by `vercel link`: a local OIDC token must never ride along in an upload
python3 scripts/verify_onnx_model.py "$OUT/backend/v2/matching/onnx_model"
echo "staged: $(find "$OUT" -type f -not -path '*/.vercel/*' | wc -l | tr -d ' ') files, $(du -sk "$OUT" | cut -f1) KB in $OUT"
