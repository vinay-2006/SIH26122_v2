# Hosted Supabase: manual checklist and guard rails (nothing here has been executed)

Status: **not started.** No hosted project exists for v2 and no code has connected to one. This file is the plan the guards enforce.

## Architecture (decided)
* The **backend mediates all database access.** It connects as the database owner (`postgres`) over the **session pooler** (IPv4) or a direct
  connection, sets `app.actor_id` per transaction, and enforces project membership itself.
* **Supabase Data API (PostgREST) stays OFF** for this project. The frontend uses Supabase only for sign-in (Auth), never for table reads/writes.
* The schema is nevertheless kept safe if the Data API is ever turned on: RLS everywhere, `anon` has nothing, `authenticated` can only `SELECT`
  through RLS policies, no client write policies, helper functions not callable. `python db/posture.py` verifies this (read-only).

## What only you can do (dashboard)
1. Create a **new** Supabase project (not the existing shared one). Choose region, set a strong DB password. *Never paste it in chat.*
2. Authentication: disable public sign-ups; set the Site URL / redirect URLs; decide email confirmation / SMTP.
3. Authentication > JWT: note whether the project uses **asymmetric signing keys** (JWKS) or the legacy shared secret.
4. Project Settings > Data API: **disable the Data API** (or leave no schema exposed).
5. Put the secrets in a git-ignored env file on the server / your machine: the DB URL (pooler, `sslmode=require`), `SUPABASE_URL`, the JWKS URL
   (or the JWT secret), and the service-role key *only if* the backend needs the Auth admin API. Add the project ref to the allow-list (below).

## Environment the backend/runner will read (names only)
| variable | meaning |
|---|---|
| `DB_V2_URL` | the ONLY database URL read (never `DATABASE_URL`). Hosted: `postgresql://postgres.<ref>:<pw>@<pooler-host>:5432/postgres?sslmode=require` |
| `V2_ALLOW_HOSTED=1` | explicit opt-in to a hosted target |
| `V2_ALLOWED_PROJECT_REFS=<ref>[,<ref>]` | explicit allow-list; the legacy project's ref is refused even if listed |
| `V2_DENIED_PROJECT_REFS` | legacy project ref(s) that can never be targeted; a hosted target requires it (or a discoverable legacy `.env`), `none` only after confirming there is no legacy project |
| `SUPABASE_URL` | issuer = `<SUPABASE_URL>/auth/v1` (mandatory when hosted) |
| `V2_JWKS_URL` or `SUPABASE_JWT_SECRET` | token verification key source (JWKS preferred) |
| `V2_DB_POOL_MAX`, `V2_DB_ACQUIRE_TIMEOUT_S` | pool size / wait |

## Order of operations once approved
1. Run `python db/migrate.py status` against the new URL: it refuses unless the guard rules hold. On a brand-new EMPTY database the first `apply`
   stamps `env=hosted`, `project_ref=<ref>`, `schema=sih-v2-baseline` exactly once; a database that has tables but no marker is never stamped.
2. `python db/migrate.py apply` (the local Supabase shim is never applied to a hosted target).
3. `DB_V2_URL=... python db/posture.py` must print only PASS lines.
4. Demo data (optional, separate from the local seed): `python scripts/seed_v2_hosted.py --check` (offline preflight), then `--apply`. It creates the 15 fictional
   users through the Supabase Auth **Admin API** (never by writing `auth.users`), the 3 `CREATE_PROJECT` grants and the four demo projects through the normal
   services. Needs `V2_EVIDENCE_DIR` (a dedicated hosted-only directory, NOT `.local/v2_evidence`: the local `--reset-local` deletes files there for the same project ids), `SUPABASE_URL` (same project as the DB), `SUPABASE_SERVICE_ROLE_KEY`, `V2_HOSTED_DEMO_PASSWORD` (12+ chars). Idempotent; an interrupted run
   is detected (marker `hosted_seed`) and refused, never resumed or reset.
5. Start the API; it refuses to start on a fingerprint mismatch.

## Known differences from the local test environment (verify on first hosted run)
* Local PostgreSQL is 16 with a shim for `auth`; the runner requires **PostgreSQL 15+**. Not yet run on a real PG15/17 or Supabase.
* Through a transaction-mode pooler: no session state is used, prepared statements are disabled, locks are transaction-level (all tested
  against a direct local connection; the pooler itself is untested).
* `auth.users` triggers (profile creation / email sync / deactivation) are written to sit on Supabase's real table; untested against it.
