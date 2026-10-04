# ANVYRA — deployment on Vercel (frontend and API), with Supabase

**Status: prepared and measured locally. Nothing has been deployed, no hosted resource has been created or changed, nothing is committed or pushed.**

## Architecture

```
Browser ──HTTPS──▶ Vercel project 1: frontend (static React build, VITE_BACKEND=v2)
   │                      │ sign-in only
   │                      ▼
   │               Supabase Auth  ──(ES256 JWT)──┐
   └────HTTPS────▶ Vercel project 2: API (FastAPI as a Python function, entrypoint app.py)
                          │  verifies the JWT with the project's JWKS
                          ├──▶ Supabase Postgres (session pooler, sslmode=require)   ← all reads/writes
                          └──▶ Supabase Storage (PRIVATE bucket "evidence")           ← uploads and downloads, service-role key held by the API only
```

The browser never talks to the database or to Storage and never holds the service-role key. Files are downloaded through the API after the membership and role checks.

## The serverless embedding backend (what makes the API fit)

Claim matching uses the sentence model all-MiniLM-L6-v2. Locally it runs on torch + FAISS (default). A function cannot carry torch, so the same model is exported to ONNX and run by onnxruntime:

| | `V2_EMBEDDING_BACKEND=torch` (default) | `V2_EMBEDDING_BACKEND=onnx` (Vercel) |
|---|---|---|
| network | sentence-transformers | the same network exported by `scripts/export_minilm_onnx.py` |
| tokenizer, truncation (256), mean pooling, L2 normalisation | sentence-transformers | the same, in `backend/v2/matching/embedder.py` |
| retrieval | FAISS `IndexFlatIP` | exact NumPy inner product (what `IndexFlatIP` computes) |

The choice is explicit and never silent: an unknown value, a missing dependency or a missing model file raises `EmbeddingBackendError`; the caller then matches without the semantic signal (as it already did when a model could not load) and the start-up warm-up logs an ERROR. The other backend is never substituted.

Parity (tests/v2_matching/test_onnx_parity.py, 812 claims over the four demo schedules, top-3 each): max embedding deviation 3.1e-7, max score deviation 5.1e-7, mean 1.2e-7, **0 ranking differences**. The matching algorithm, weights, thresholds and golden files are unchanged.

## What is in the repository

| File | Purpose |
|---|---|
| `app.py`, `vercel.json`, `.vercelignore` (repository root) | the API project: entrypoint, 300 s function duration, file excludes |
| `requirements.in`, `requirements.txt` | the loose slim dependency list and its **hash-locked** resolution for Linux x86-64 / Python 3.12 (`uv pip compile … --generate-hashes`); Vercel installs `requirements.txt` |
| `requirements-export.txt` | the pinned toolchain that builds the canonical ONNX model (build machine only) |
| `backend/v2/matching/onnx_model/` | `MANIFEST.json` and `tokenizer.json` (committed) and `model.onnx` (git-ignored, 91 MB) |
| `scripts/verify_onnx_model.py` | re-checks the model's hashes and probe vectors against the manifest |
| `backend/v2/request_limit.py` | optional request-size ceiling answering with the API's own JSON error (`V2_MAX_REQUEST_BYTES`) |
| `frontend/.vercelignore` | keeps local env files and e2e artefacts out of the frontend upload |
| `backend/v2/matching/embedder.py` | torch / ONNX selection, the ONNX embedder, NumPy retrieval |
| `scripts/export_minilm_onnx.py` | writes `model.onnx` + `tokenizer.json` (needs torch; a development/build-machine step) |
| `scripts/measure_onnx_runtime.py` | import time, model load, memory and latency with torch/faiss blocked |
| `frontend/vercel.json` | frontend project: build `npm run build:v2` → `dist-v2`, SPA rewrite, security headers |
| `backend/v2/storage.py` | `SupabaseEvidenceStore` (private bucket), `V2_STORAGE_BACKEND=supabase` |
| `db/migrations/0021_project_knowledge.sql`, `scripts/load_project_knowledge.py` | project knowledge table and its loader |

### Model delivery and integrity

`model.onnx` (91 MB) is git-ignored; `MANIFEST.json` (committed) pins it: source model and revision (`sentence-transformers/all-MiniLM-L6-v2` @ `1110a243…`), the sha256 of the source weights, the sha256 and size of `model.onnx` and `tokenizer.json`, the exporting toolchain, and three probe sentences with their reference vectors.

* **Runtime:** `OnnxEmbedder` hashes both files against the manifest before loading (0.05 s on the 91 MB model) and refuses to start on any mismatch, a missing manifest or a missing file. Nothing downloads at run time.
* **Reproducible build:** `python3 -m venv .local/venvs/export && .local/venvs/export/bin/pip install -r requirements-export.txt`, then `.local/venvs/export/bin/python scripts/export_minilm_onnx.py backend/v2/matching/onnx_model`, then `python3 scripts/verify_onnx_model.py backend/v2/matching/onnx_model`. Two exports in the pinned environment produced the identical sha256 (`5f0618c5…`). The bytes depend on the toolchain (a different torch/onnx version gives a different hash, though the same network: probe vectors agree to 6e-7), which is why the toolchain is pinned.
* **Getting it into the deployment:** use the Vercel CLI from the repository root (`vercel deploy`): it uploads the folder, `.vercelignore` does not exclude the model, and the 91 MB file never enters Git. A Git-integration deployment would need Git LFS or a build-time download from a location you control, then `verify_onnx_model.py` as the build's last step.

## Environment

**Vercel, API project** (root directory: repository root) — secrets are entered in the dashboard, never committed:

| Variable | Value |
|---|---|
| `V2_EMBEDDING_BACKEND` | `onnx` |
| `V2_MAX_REQUEST_BYTES` | `4400000` (just under Vercel's 4.5 MB platform limit; see Upload limits) |
| `DB_V2_URL` | Supabase **session pooler** URL, port 5432, `?sslmode=require` and nothing else in the query string |
| `V2_DB_POOL_MAX` | `3` (each warm function instance holds its own pool; the pooler has a small client limit) |
| `V2_ALLOW_HOSTED`, `V2_ALLOWED_PROJECT_REFS`, `V2_DENIED_PROJECT_REFS` | `1`, the ANVYRA project ref, the old demo's ref (or `none` once confirmed) |
| `SUPABASE_URL`, `V2_JWKS_URL` | `https://<ref>.supabase.co`, `https://<ref>.supabase.co/auth/v1/.well-known/jwks.json` |
| `V2_STORAGE_BACKEND`, `V2_STORAGE_BUCKET` | `supabase`, `evidence` |
| `SUPABASE_SERVICE_ROLE_KEY` | used only for Storage; stays on the API |
| `V2_CORS_ORIGINS` | the exact frontend origin(s), no wildcard, no trailing slash |
| `EXTRACTION_FALLBACK` | `rules` |

Not set on purpose: `V2_LOCAL_LOGIN_PASSWORD`, `SETUAI_ALLOW_LIVE_LLM` and any model key.

**Vercel, frontend project** (root directory `frontend`): `VITE_BACKEND=v2`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` (publishable key), `VITE_V2_API_BASE_URL` (the API project's URL), `VITE_MAX_UPLOAD_MB=4`. Leave `VITE_V2_LOCAL_LOGIN` unset. Every `VITE_*` value is public by construction; no secret belongs in this project.

**Supabase dashboard** — Auth → URL configuration: Site URL and redirect URLs = the frontend address. Public sign-ups off. Data API off. Storage bucket `evidence` **private**.

## Security and configuration review (local, read-only review plus two small hardenings)

| Area | Finding |
|---|---|
| Token verification | Strict: the key is chosen by the token's algorithm (ES256/RS256 from the JWKS by `kid`; alg none/HS512/embedded keys refused), `exp`/`iat`/`sub`, audience, **issuer mandatory for hosted targets**, `role=authenticated` only (anon and service-role tokens refused), anonymous sign-ins refused, 24 h maximum lifetime, JWKS https-only, size-capped, rotation refetch rate-limited, fail-closed 503 when keys are unavailable. **Do not set `V2_JWT_SECRET` / `SUPABASE_JWT_SECRET` on Vercel**: that would also enable the HS256 path; the asymmetric JWKS alone is the intended configuration. |
| JWKS on serverless | The key cache is per instance: the first token on a fresh instance pays one JWKS fetch (3 s timeout), later tokens none. Correct, just slower once. |
| CORS | Explicit origins only (`*` refused at start-up), no credentials, bearer-token header. Preview-deployment URLs are not allowed unless listed, which is intended. The upload-size refusal is added before the CORS layer, so it carries the CORS headers. |
| Hosted database guards | Hosted needs `V2_ALLOW_HOSTED=1`, the project ref in `V2_ALLOWED_PROJECT_REFS`, a stated `V2_DENIED_PROJECT_REFS` (the old project's ref, or `none`: on Vercel there is no legacy env file to detect it, so it **must** be set), `sslmode=require`, only `sslmode`/`connect_timeout`/`application_name` in the URL, and the database fingerprint (`env=hosted`, schema, ref) is verified when the pool is created. The API reads `DB_V2_URL` only, never `DATABASE_URL`. Local sign-in is impossible against a hosted target. |
| Environment separation | The frontend project holds only public `VITE_*` values. No service-role reference exists in the frontend source or its `.env.example`; local `.env.local` files are excluded from the upload (`frontend/.vercelignore`). The API project alone holds `DB_V2_URL` and `SUPABASE_SERVICE_ROLE_KEY` (Storage only). |
| **Hardening made** | A hosted API (`V2_ALLOW_HOSTED=1`) no longer serves `/docs`, `/redoc` or `/openapi.json` (`V2_ENABLE_DOCS=1` opts back in), and its unauthenticated `/health` answers only `{"status":"ok"}` instead of the database name, schema, environment and migration count. Local behaviour is unchanged. Tests: `tests/v2_api/test_hosted_surface_unit.py`. |
| Open, not changed | No application-level rate limiting on the API (Vercel's platform protections apply; sign-in throttling lives in Supabase Auth). The session-pooler connection limit is small: keep `V2_DB_POOL_MAX=3` and watch concurrent instances; the transaction pooler (port 6543) was not evaluated against this code's per-transaction settings. |

## Hosted state

Done on 2026-10-04 with explicit approval (Anvyra project only):

1. **Migration 0021 applied.** Preflight: the file creates one new table (`project_knowledge`), two indexes, three triggers, one RLS select policy for project members and `GRANT SELECT` to `authenticated`; it updates and deletes nothing and weakens no control. Result: 21 migrations current, the posture check has no FAIL, every other table's row count is unchanged.
2. **Project knowledge loaded** (`scripts/load_project_knowledge.py --hosted --apply`): 164 entries (AEC-OFFSHORE 42, NNB-CRUDE 41, NRL-EXPANSION 40, SMP-PIPE 41). Verified: project ids equal the seed specification, no orphan rows, every entry created by a Project Manager of its project, provenance split FROM_RECORDS 47 / AUTHORED 77 / ILLUSTRATIVE 28 / NOT_SPECIFIED 12, 164 audit rows, and a second apply reported `unchanged` for every entry (idempotent).
3. **Private bucket `evidence` created and verified**, and the 16 hash-verified documents uploaded (see `docs/V2_STORAGE_MIGRATION.md`).

Vercel (Hobby, account `sanjanaspals1106`): `anvyra-api` https://anvyra-api.vercel.app (function region `bom1`, next to the database) and `anvyra-web` https://anvyra-web.vercel.app, both deployed to production. `V2_CORS_ORIGINS=https://anvyra-web.vercel.app` is set on the API.

**Database connection: use the transaction pooler.** The API first used the Supabase *session* pooler (port 5432), which allows 15 clients in total; a few serverless instances exhausted it (`EMAXCONNSESSION`), which the browser saw as network errors. `DB_V2_URL` on Vercel therefore uses the *transaction* pooler (port 6543). The application already keeps no session state (`prepare_threshold=None`, `set_config(..., true)`, `pg_advisory_xact_lock`), and the read paths were verified against it. Local development and migrations keep using the session pooler / direct connection.

Not done yet: Supabase Auth Site URL and redirect URLs (needs a Supabase access token or the dashboard).

## Upload limits (Vercel: 4.5 MB per request)

* Vercel refuses a request body above 4.5 MB with HTTP 413 (`FUNCTION_PAYLOAD_TOO_LARGE`) **before the function starts**, so no API code runs and nothing can be created. That response is not JSON and carries no CORS headers, so a browser would only report a network error.
* Therefore: the web app checks the size first (`VITE_MAX_UPLOAD_MB=4`, in the report-upload panels, the schedule import and every file picker) and shows "This file is X MB; this server accepts uploads up to 4 MB"; and the API, when `V2_MAX_REQUEST_BYTES=4400000`, answers anything that does reach it above that size with `413 REQUEST_TOO_LARGE` in its own JSON shape (with CORS headers), decided from `Content-Length` or counted while streaming, before any route runs.
* Tests (tests/v2_compat/test_upload_ceiling.py, tests/v2_api/test_request_limit_unit.py): an oversized file to `/claims/file`, `/claims/batch` and `/documents` returns 413 and leaves **zero** new claims and documents; a request under the ceiling is unaffected; the refusal carries the CORS header.
* Consequence: on Vercel, reports and evidence above about 4 MB (a large PDF or photograph) cannot be uploaded. Resize or compress, or move uploads to direct-to-Storage signed uploads in a later phase (not built).

## Measured here

Bundle, built the way Vercel builds it (uv, Linux x86-64 manylinux, Python 3.12, hash-locked `requirements.txt`, files selected by `.vercelignore`/`excludeFiles`):

| part | size |
|---|---|
| dependencies (52 packages) | 345.7 MB |
| application code, db, entrypoint | 3.1 MB |
| model.onnx + tokenizer + manifest | 91.6 MB |
| **uncompressed total** | **440.4 MB** (limit for Python functions: 500 MB; the 250 MB figure applies to other runtimes) |
| zipped | 199.6 MB |

The margin is 59.6 MB. Pruning test directories, type stubs and Hugging Face Hub (not done in the real build) would save about 38 MB if ever needed. A static check of every import in the bundled code against the installed packages found only the intended absences (torch, faiss, sentence-transformers, pytesseract), each of them guarded or lazy.

Run time on this Mac with one ONNX thread (a Vercel function has 1 vCPU and 2 GB on Hobby; Linux x86 will be slower, plan for 2–3×):

* API import 70 MB; integrity hash 0.05 s; model load + first encode 0.14 s; peak 256 MB with the model loaded.
* First claim of a schedule (builds the in-memory index): 31–59 activities 52–192 ms; 500 activities 0.6 s; 2,000 activities 2.5 s; 5,000 activities 6.7 s (peak 438 MB). Later claims about 1.3 ms. The 300 s function limit is not a concern; memory stays under 25% of 2 GB.
* An idle instance is recycled, losing its index and model: the next request pays about 0.5 s plus the index build, and the first token pays one JWKS fetch.
* **Not measured:** an actual execution on Linux/Vercel (no Docker or Linux host here, and deploying is not authorized), real cold-start latency including platform start-up, and the Supabase pooler's behaviour under concurrent instances. The first deployment is the first test of these.

## Development environment

Project tooling that needs extra packages (ONNX export, the Linux dependency resolution) runs in an isolated virtual environment, not in the machine's Anaconda base:

```
python3 -m venv .local/venvs/anvyra-dev                      # .local is git-ignored
.local/venvs/anvyra-dev/bin/pip install -r backend/requirements.txt onnx onnxscript onnxruntime tokenizers uv
```

The whole test suite passes in it (torch and onnx modes). The base Anaconda environment was changed earlier by `pip install onnxscript onnx`: it added `onnx 1.23.1`, `onnx-ir 1.0.0`, `onnxscript 0.7.2` and replaced conda's `protobuf 5.29.3` with `7.36.2`, which `pip check` now reports against `grpcio-status`, `google-ai-generativelanguage`, `googleapis-common-protos`, `google-api-core` and `proto-plus` (and `streamlit` at the boundary). Nothing in this project depends on it. The exact restore, when approved: `conda install -n base protobuf=5.29.3` (the package is still in the conda cache) and `pip uninstall onnx onnx-ir onnxscript`.

## Demo access, sample data and extraction (added 2026-10-04)

* **Demo accounts** are `<name>@anvyra.demo` (renamed from `@seed.setuai.local` on the hosted project by `scripts/rename_demo_accounts_hosted.py`, which changes only the sign-in e-mail). The login's demo buttons fill the NRL-EXPANSION team: Farah Khan (Project Manager), Imran Hussain (Supervisor), Ritu Baruah (Site Engineer).
* **Password autofill.** `VITE_V2_DEMO_PASSWORD` (set on `anvyra-web` as a public config value) makes the buttons fill the password too, so an evaluator signs in with two clicks. **A `VITE_*` value is public JavaScript**: anyone can read the shared demo password and sign in as any role. Remove the variable and redeploy after the evaluation, then rotate the password.
* **Project Intelligence and Project Knowledge** are one page with two tabs for the Project Manager; `/knowledge` redirects to the Knowledge tab.
* **Language-model extraction** is enabled on the API (`LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`; `EXTRACTION_FALLBACK=rules` stays as the safety net). Multi-item TXT/PDF/DOCX reports need it; the free tier allows 8,000 tokens per minute, so a rate-limit error falls back to the rule reader, which cannot read two-part activity ids and matches such items wrongly.
* **Demo progress reports** for NRL-EXPANSION, AEC-OFFSHORE and SMP-PIPE are in `sample_data/demo_v2/` (each with a README of expected results, verified read-only with the real readers and engine); NNB-CRUDE is complete and has none. Generators: `scripts/make_demo_reports_v2.py` (NRL), `scripts/make_demo_reports_project.py <CODE>`; dry run: `scripts/check_demo_reports_v2.py`.
* **Check fix.** A quantity claim whose unit binds to a measured quantity of a multi-measure activity no longer receives the false "no planned quantity" warning (`checks_api._drop_false_missing_plan`).
* **Transaction pooler caution.** Never run a session-level `SET` through port 6543: it persists on the shared server connection (this made every API write fail until it was reset). Use `SET TRANSACTION` / `conn.read_only = True`.

## Known limits
* **No OCR on Vercel** (no Tesseract binary). Images are stored as evidence (hashed, immutable) and are never presented as read; a report image uploaded for extraction is refused with the reason ("OCR … not available"), and no claim is created. PDF, DOCX, XLSX, CSV and text reports are read as before.
* **4.5 MB request limit** on Vercel Functions: uploads larger than that fail at the platform, before the API's own limit.
* In-memory state (schedule indexes, model) is per function instance; an idle instance is recycled and the first request pays the model load (about 0.3 s) and an index build.
* `supabase-py` is not installed on Vercel: the backend never imports it (Storage and Auth admin calls use httpx).
* The Linux bundle has not been built or run here; the first deployment is the first test of it.
