# Clean schema baseline (`db/`)

The authoritative schema for the new database. It is **not** an extension of `backend/models` (the old shared-database model, which is
left untouched until the backend is ported).

```
db/shim/000_supabase_shim.sql   local-only stand-in for Supabase auth (roles, auth.users, auth.uid()). Never applied to Supabase.
db/migrations/0001..0008_*.sql  immutable, checksummed migrations
db/migrate.py                   runner: `apply` / `status`; refuses anything but a local setuai_v2_* database (for now)
scripts/db_v2.sh                up | migrate | status | reset | url | psql   (database `setuai_v2_integ` on the local :54329 cluster)
tests/db_v2/                    schema, authorization, ledger, versioning, RLS and runner/isolation tests
```

## Local use
```bash
scripts/integration_db.sh up          # once: the local cluster (already used by the other integration DBs)
scripts/db_v2.sh up                   # create setuai_v2_integ, apply shim + migrations (rerunnable)
scripts/db_v2.sh status
SETUAI_ALLOW_DB_TESTS=1 SETUAI_TEST_ENV=integration DATABASE_URL="$(scripts/db_v2.sh url)" python3 -m pytest tests/db_v2
```
The runner reads `DB_V2_URL` (set by the script) and **never** `DATABASE_URL`, so the old stack's configuration cannot be picked up.

## Model in one paragraph
`projects` hold identity; `schedule_versions` hold the baseline fields (name, data date, planned dates, lock) and a lifecycle
(DRAFT → VALIDATED → ACTIVE ⇄ SUPERSEDED). WBS (`schedule_wbs`), activities (`baseline_activities`) and resource assignments
(`baseline_resources`) are **per version** and immutable once the version is locked. Identity is stable across versions via
`activities.activity_uid`, `assignments.assignment_uid` and `schedule_wbs.wbs_uid`; external P6/MSP ids are only attributes of a version.
Claims (`execution_events`) record the version they were filed in. A supervisor decision (`planner_decisions`, append-only) is the only
way progress enters the two append-only ledgers (`approved_activity_progress`, `approved_resource_progress`); every dashboard number is
a view over those ledgers (`0007_progress_views.sql`).

## Authorization (database side — the second wall; the backend enforces first)
* Roles are memberships (`SITE_ENGINEER`, `SUPERVISOR`, `PROJECT_MANAGER`); `CREATE_PROJECT` / `PLATFORM_ADMIN` are revocable platform grants.
* Role checks on the data itself: claims only by a Site Engineer, decisions/root causes/memory only by a Supervisor, schedule files and all
  schedule/project/member writes only by a Project Manager of that project.
* Write guards read `SET LOCAL app.actor_id = '<uuid>'` (the backend must set it per transaction). `app.system = 'on'` is for migrations and seeds.
* RLS: deny by default; `anon` has nothing; `authenticated` has RLS-filtered SELECT only. Views use `security_invoker`.

## Not in this baseline yet (deferred until the code using them is ported)
quality gates / ITPs, contractors / work packages, impact scenarios, agent briefings, the reopen workflow, dependency-cycle detection
(done by the import service), hosted-Supabase application of these migrations (needs separate approval).
