# V7 Data Migration Plan (Plan A: fresh environment, Plan B: preserve the shared V6 data)

Nothing in this document has been executed against the shared Supabase database. Every step against shared data needs explicit authorization first (see "Authorization gates"). All steps were rehearsed only on the isolated local integration database (`scripts/integration_db.sh`).

## Facts the plan rests on
* The shared DB holds V6-era data: schedules without `project_id`, execution events/actuals without project or stage stamps, and a `audit_logs` chain written under an older hash scheme (all `previous_hash = GENESIS`). `tests/test_priority1_supabase_rls_and_fks.py::test_historical_orphan_records_accessible` documents the orphan population.
* Migrations 000-012 are applied there (012 by hand, unrecorded). **013 (RLS project scope), 014 (membership role CHECK), 015 (`execution_blockers`) are not applied.** Exact SQL and before/after evidence: `V7_MIGRATION_013_RELEASE_REPORT.md`.
* `init_db()` used to recreate permissive `USING (true)` client policies at every start; `backend/models/schema.sql` no longer creates client policies, so a restart cannot undo migration 013.

## Plan A - fresh V7 environment (recommended for the demo)
1. Provision an empty Supabase project (or the local isolated Postgres for an offline demo).
2. Apply in order: shim (local only), `migrations/000..015`, then `schema.sql` (idempotent). Local one-liner: `scripts/integration_db.sh reset && up && migrate`.
3. `python -m backend.models.verify_v7_schema` -> must print `ALL CHECKS PASSED`.
4. Provision identities (Supabase Auth users + `profiles` + `project_memberships`; roles: OWNER, PROJECT_MANAGER, PLANNER, SUPERVISOR, SITE_ENGINEER, QUALITY_INSPECTOR, AUDITOR).
5. Build the canonical dataset (`python -m backend.canonical.build`, isolated DB only in its current form; for a hosted fresh project the builder's guard must be deliberately extended to an allow-listed host - a conscious, reviewed change).
6. Validation queries (below). Rollback = discard the environment.

## Plan B - preserve existing V6 data in the shared DB
Ordering matters; each step is independently reversible until step 6.
| # | Step | Reversible by |
|---|---|---|
| 0 | Full backup (`pg_dump` of the shared DB) and freeze writers | restore |
| 1 | Apply `013` (RLS project scope; policy replacement only, no data change) | `DROP POLICY`/re-create previous policy list captured in the 013 report |
| 2 | Apply `014` (widen role CHECK; superset, no row change) | re-add old CHECK (only if no row uses a new role) |
| 3 | Apply `015` (new table `execution_blockers`) | `DROP TABLE execution_blockers` |
| 4 | Create a **legacy project** per V6 deployment (`LEGACY-V6`) and memberships for its real users | delete project (cascade only if empty) |
| 5 | Map: `UPDATE schedules SET project_id = <legacy> WHERE project_id IS NULL`; then stamp `schedule_activities.project_id` from its schedule; stamp `execution_events`, `planner_decisions`, `approved_actuals` `project_id` from their schedule. Stages/contractors/work packages stay NULL until a planner attributes activities through the V7 attribution endpoint | set columns back to NULL (mapping is additive; keep the pre-image in a side table `_v6_map`) |
| 6 | Audit chain: leave every legacy row byte-identical. New V7 rows start a marked V7 chain (`entity_context._chain = "V7"`, first link `previous_hash = GENESIS_HASH`); the verifier reports `LEGACY_ONLY` / `VALID` and never rewrites history | nothing to roll back (append-only) |
| 7 | Run the validation queries; enable V7 writers | disable writers |

### Duplicate / orphan / provenance handling
* **Duplicate schedules** for the same project name: keep all, mark exactly one `active` per project (V7 refuses ambiguity: `/schedules/active` answers 409 when several are active). Never delete.
* **Orphan events/actuals** (event without schedule, actual without decision): do not delete; stamp `project_id` only when the schedule chain proves it, otherwise leave NULL and list them in a report. RLS keeps NULL-project rows invisible to project members, which is the safe default.
* **Provenance:** every backfilled value is recorded in `_v6_map(table, pk, column, old, new, migrated_at, migrated_by)` and audited once with action `V6_BACKFILL`.

### Validation queries (run before and after; expected in comments)
```sql
SELECT count(*) FROM schedules WHERE project_id IS NULL;                          -- 0 after step 5 (or the reported orphan set)
SELECT project_id, count(*) FROM schedules WHERE active GROUP BY project_id HAVING count(*) > 1;  -- none
SELECT count(*) FROM schedule_activities a JOIN schedules s USING (schedule_id) WHERE a.project_id IS DISTINCT FROM s.project_id;  -- 0
SELECT count(*) FROM approved_actuals a LEFT JOIN planner_decisions d USING (decision_id) WHERE d.decision_id IS NULL;              -- 0 (V7 rule)
SELECT polname, tablename FROM pg_policies WHERE qual = 'true' AND roles <> '{service_role}';   -- none for project tables
```
plus `GET /projects/{pid}/dossier/audit-verification` -> `VALID` or `LEGACY_ONLY`, never `BROKEN`.

### Rollback
Steps 1-3: the SQL in the 013 report and the DROP statements above. Step 5: restore from `_v6_map`. Full rollback: restore the step-0 backup.

## Authorization gates (STOP points)
The following are **not authorized** and must not run until the user says so: applying 013/014/015 to the shared DB, creating the legacy project, any backfill UPDATE, any write to `audit_logs`. The exact SQL for 013 is in its release report; 014/015 are the migration files themselves (`backend/models/migrations/014_v7_membership_roles.sql`, `015_v7_execution_blockers.sql`).
