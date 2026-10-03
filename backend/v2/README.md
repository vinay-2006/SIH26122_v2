# SIH v2 API (`backend/v2`)

A separate FastAPI app for the clean schema in `db/`. **The legacy app (`backend/main.py`) and its database are untouched.**
It reads `DB_V2_URL` (never `DATABASE_URL`) and refuses anything but a local `setuai_v2_*` database until hosted use is approved.

```bash
scripts/db_v2.sh up
DB_V2_URL="$(scripts/db_v2.sh url)" SUPABASE_JWT_SECRET=<local-secret> uvicorn backend.v2.app:app --port 8020
```

## Configuration (names only; see db/HOSTED_SETUP.md)
`DB_V2_URL` (never `DATABASE_URL`) · `V2_DB_POOL_MAX` · `V2_DB_ACQUIRE_TIMEOUT_S` · token verification: `V2_JWKS_URL` (ES256/RS256) and/or
`SUPABASE_JWT_SECRET` (HS256), `SUPABASE_URL` / `V2_JWT_ISSUER`, `V2_JWT_AUDIENCE` · hosted opt-in: `V2_ALLOW_HOSTED=1`, `V2_ALLOWED_PROJECT_REFS`.
Tokens must carry `aud=authenticated`, `role=authenticated`, `exp`, `iat`, a UUID `sub`, and (when configured / hosted) the right issuer; the key is chosen
by the token's algorithm, `alg=none` and anonymous users are refused, and an admin can revoke earlier sessions. The API refuses to start with no
verification key, with a hosted target but no issuer, or with a database whose fingerprint marker does not match.

## Authorization model
* Identity: verified JWT. Authority: an ACTIVE `project_memberships` row, looked up per request (the URL's project id proves nothing), and re-verified inside every domain transaction.
* Roles: `PROJECT_MANAGER` (projects, settings, members, schedules, aggregate claim counts and dashboards; never claim content, never decisions),
  `SUPERVISOR` (review queue, decisions, issue resolution, root causes, memory, audit), `SITE_ENGINEER` (own claims, evidence, issues; never schedules).
  `CREATE_PROJECT` / `PLATFORM_ADMIN` are separate, revocable platform grants.
* Every write transaction sets `app.actor_id`; the database applies its own role guards (see `db/README.md`). Nothing here sets `app.system`.

## Endpoints
The complete, generated inventory (every route, who may call it, every error code) is **`docs/V2_API.md`**; the live schema is at `/docs` and `/openapi.json`.
Groups: projects / members / invitations · schedule imports and versions (Project Manager) · `activities`, `claims`, `my-claims`, `review-queue`, `claim-counts`
· `documents` (+ `/extract`) · `issues`, `blockers`, `root-causes`, `memory` · `dashboard/*`, `activities/{id}/timeline`, `audit`, `notifications`.

Conventions: errors are `{"error": {"code", "message", "details"}}`; lists are `{"items", "limit", "offset", "next_offset"}` (limit 1-200); create endpoints
(`POST claims`, `claims/{id}/correction`, `issues`) accept an `Idempotency-Key` header (8-128 chars; the same key and body replays the first response with
`Idempotent-Replay: true`, the same key with a different body is `IDEMPOTENCY_KEY_REUSED`; keys expire after 24 h; migration 0014).

## Execution workflow (Phase 3)
* A **claim** is a proposal. It stores exactly what was reported and never changes progress. Only a Supervisor **decision** (`APPROVE`, `EDIT`, `REJECT`, `HOLD`)
  creates approved progress, atomically with its ledger rows, notification and audit records (`backend/v2/domain/`; migrations 0013-0014).
* A percentage-only claim on a measured activity is never converted silently: the Supervisor must choose `APPLY_PCT_TO_ASSIGNMENTS` or enter quantities.
  Over-baseline quantities are never clamped; beyond the project tolerance (default 10%) the deciding Supervisor's acknowledgement and note are required and kept.
* A Site Engineer may withdraw only their own pending claim; a correction after a rejection is a NEW claim linked to it. Reported and approved values are both kept.
* Progress rollups (`dashboard/*`) are derived from the approved ledgers by as-of SQL functions and always state their weight basis, the data date and that planned
  progress is a **linear approximation** (SPI is not earned value).
* Schedule revisions never transfer progress across a split, merge or retirement. **Not implemented (documented limitation):** allocating one claim across the
  children of a split (`claim_activity_splits`); a claim decides against a single activity only, and a PM/Supervisor must file against the child that did the work.

## Evidence and extraction
* `POST /documents` validates the CONTENT (PDF, PNG, JPEG, CSV, TXT, XLSX; per-type size limits; macro workbooks and PDFs with scripts refused), stores it under a
  generated name behind `backend/v2/storage.py` (local adapter; `V2_EVIDENCE_DIR`, default `.local/v2_evidence`, mode 0600), and records a SHA-256 and metadata.
  Responses never contain a storage key or path; downloads are attachments, hash-checked, and role-filtered (a PM cannot read claim evidence).
* `POST /documents/{id}/extract` is synchronous, deterministic and strict (CSV, XLSX, text, text-layer PDF; bounded by a time budget and row / page caps). Ambiguous
  rows are skipped with reasons, a failed extraction files no claim, and no LLM or network is used. Scanned / handwritten documents (OCR) are a later phase.
* A hosted object-storage adapter implements the same three methods (`put`, `read`, `delete`); none is connected in this phase.

## Demo data
`scripts/seed_v2.py` builds four deterministic demo projects in a LOCAL `setuai_v2_*` database through the real services (see `docs/V2_PHASE3.md`).
`scripts/v2_dev_token.py <person>` mints a local development token for a seeded person.

## Formats
Primavera `.xer`, Microsoft Project XML (MSPDI), CSV (+ resource CSV). **Native `.mpp` is not supported** (the error tells the user to export XML).
