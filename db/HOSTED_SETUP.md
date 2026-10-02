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
| `SUPABASE_URL` | issuer = `<SUPABASE_URL>/auth/v1` (mandatory when hosted) |
| `V2_JWKS_URL` or `SUPABASE_JWT_SECRET` | token verification key source (JWKS preferred) |
| `V2_DB_POOL_MAX`, `V2_DB_ACQUIRE_TIMEOUT_S` | pool size / wait |

## Order of operations once approved
1. Run `python db/migrate.py status` against the new URL: it refuses unless the guard rules hold. On a brand-new EMPTY database the first `apply`
   stamps `env=hosted`, `project_ref=<ref>`, `schema=sih-v2-baseline` exactly once; a database that has tables but no marker is never stamped.
2. `python db/migrate.py apply` (the local Supabase shim is never applied to a hosted target).
3. `DB_V2_URL=... python db/posture.py` must print only PASS lines.
4. Create the first `PLATFORM_ADMIN` grant (one-off SQL by you or a seed step), then start the API; it refuses to start on a fingerprint mismatch.

## Known differences from the local test environment (verify on first hosted run)
* Local PostgreSQL is 16 with a shim for `auth`; the runner requires **PostgreSQL 15+**. Not yet run on a real PG15/17 or Supabase.
* Through a transaction-mode pooler: no session state is used, prepared statements are disabled, locks are transaction-level (all tested
  against a direct local connection; the pooler itself is untested).
* `auth.users` triggers (profile creation / email sync / deactivation) are written to sit on Supabase's real table; untested against it.
