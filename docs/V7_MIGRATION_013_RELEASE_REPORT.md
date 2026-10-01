# Migration 013 — Release Report (RLS project-scope fixes)

**Status: validated on the isolated database. NOT applied to the shared Supabase database (requires your explicit authorization).**

## 1. What it changes

| Table | Before (shared DB today) | After |
| --- | --- | --- |
| `claim_activity_splits` | `SELECT USING (true)` for every authenticated user, OR-ed with the member policy (so the member policy protected nothing) | legacy policy dropped; `v7_activity_splits_select/insert` (project member via the claim's event) remain |
| `evidence_links` | `SELECT USING (true)` | `v7_evidence_links_select`: caller must be an active member of the project of **both** linked events |
| `execution_summaries` | `SELECT USING (true)` (a project-less global cache: any authenticated user could read every project's summary text) | no client policy: service connection only (the backend reads/writes it through its own connection) |
| `source_documents` | RLS enabled, **no policy** (invisible to authenticated; readable only through the service role) | `v7_source_documents_select`: own upload, or referenced by an event / quality evidence / incident evidence of a project the caller belongs to |
| `impact_scenarios` | INSERT/UPDATE/DELETE policies exist on the shared DB but in **no migration** (drift) | codified with identical semantics (`project_id IS NOT NULL AND is_project_member(project_id)`) |

Root cause of the leak: migration 011 already dropped the `USING (true)` policies, but `init_db()` re-runs `backend/models/schema.sql` on **every application start** and re-created them. `schema.sql` no longer creates any client policy (changed in the same release), so an app restart can no longer resurrect the leak.

## 2. The migration (exact SQL)

```sql
-- Migration 013: Close cross-project RLS leaks (pre-E2E hardening)
-- Description:
-- 1. claim_activity_splits: drop the legacy USING (true) SELECT policy (policies are OR-ed, so it
--    defeated v7_activity_splits_select). Member-scoped policies from 011 remain.
-- 2. evidence_links: replace USING (true) with a policy scoped through BOTH linked execution events.
-- 3. execution_summaries: global (project-less) aggregate cache. Drop the authenticated policy so it is
--    service-connection only. Project scoping needs an additive project_id column (proposed, not done here).
-- 4. source_documents: had RLS enabled and no policy. Add a SELECT policy scoped through the records
--    that reference a document (execution_events, quality_evidence, incident_evidence) or own upload.
-- 5. impact_scenarios: codify the INSERT/UPDATE/DELETE policies that exist on the shared DB but in no
--    migration (schema drift), so a clean build matches.
-- Idempotent. No data is modified.

DROP POLICY IF EXISTS "authenticated_activity_splits_select" ON claim_activity_splits;
DROP POLICY IF EXISTS "authenticated_evidence_links_select" ON evidence_links;
DROP POLICY IF EXISTS "authenticated_execution_summaries_select" ON execution_summaries;

DROP POLICY IF EXISTS "v7_evidence_links_select" ON evidence_links;
CREATE POLICY "v7_evidence_links_select" ON evidence_links FOR SELECT TO authenticated
USING (
    EXISTS (SELECT 1 FROM execution_events a
            WHERE a.event_id = evidence_links.event_id_a
              AND a.project_id IS NOT NULL AND is_project_member(a.project_id))
    AND EXISTS (SELECT 1 FROM execution_events b
            WHERE b.event_id = evidence_links.event_id_b
              AND b.project_id IS NOT NULL AND is_project_member(b.project_id))
);

DROP POLICY IF EXISTS "v7_source_documents_select" ON source_documents;
CREATE POLICY "v7_source_documents_select" ON source_documents FOR SELECT TO authenticated
USING (
    uploader_id = auth.uid()
    OR EXISTS (SELECT 1 FROM execution_events ee
               WHERE ee.document_id = source_documents.document_id
                 AND ee.project_id IS NOT NULL AND is_project_member(ee.project_id))
    OR EXISTS (SELECT 1 FROM quality_evidence qe
               JOIN quality_gates qg ON qg.quality_gate_id = qe.quality_gate_id
               WHERE qe.source_document_id = source_documents.document_id
                 AND is_project_member(qg.project_id))
    OR EXISTS (SELECT 1 FROM incident_evidence ie
               JOIN institutional_incidents ii ON ii.incident_id = ie.incident_id
               WHERE ie.source_document_id = source_documents.document_id
                 AND ii.project_id IS NOT NULL AND is_project_member(ii.project_id))
);

DROP POLICY IF EXISTS "v7_scenarios_insert" ON impact_scenarios;
CREATE POLICY "v7_scenarios_insert" ON impact_scenarios FOR INSERT TO authenticated
WITH CHECK (project_id IS NOT NULL AND is_project_member(project_id));
DROP POLICY IF EXISTS "v7_scenarios_update" ON impact_scenarios;
CREATE POLICY "v7_scenarios_update" ON impact_scenarios FOR UPDATE TO authenticated
USING (project_id IS NOT NULL AND is_project_member(project_id))
WITH CHECK (project_id IS NOT NULL AND is_project_member(project_id));
DROP POLICY IF EXISTS "v7_scenarios_delete" ON impact_scenarios;
CREATE POLICY "v7_scenarios_delete" ON impact_scenarios FOR DELETE TO authenticated
USING (project_id IS NOT NULL AND is_project_member(project_id));
```

## 3. Review

- **SQL correctness:** applied to a clean build and re-applied; `psql -v ON_ERROR_STOP=1` clean both times.
- **Idempotency:** every statement is `DROP POLICY IF EXISTS` followed by `CREATE POLICY` (no `IF NOT EXISTS` guards that could keep a stale definition). Re-running produces the same catalog.
- **Data:** no row is read, moved or changed. Only policies change.
- **Policy behaviour:** all new policies use `is_project_member(...)`, which returns FALSE for a NULL project id or when `auth.uid()` is NULL (migration 011). NULL-project rows are therefore invisible to every member.
- **anon:** no policy grants anything to `anon`; with Supabase's default grants that means zero rows (verified below). Note: Supabase grants `anon` ALL privileges including TRUNCATE on every public table, and TRUNCATE is not subject to RLS. It is only unreachable because PostgREST does not expose TRUNCATE. Revoking those grants is recommended defence-in-depth but is **not** part of 013.

## 4. Test evidence (isolated database, runtime roles)

Test files: `tests/test_integration_rls_and_audit_chain.py` (23), plus the RLS isolation checks in `tests/test_integration_seed_integrity.py` (9 tables × project A/B) and `tests/test_v7_rls_security_hardening.py`. Roles are exercised the way Supabase does it: `SET ROLE authenticated|anon` + `request.jwt.claim.sub`.

| Property | Result |
| --- | --- |
| member of A sees A's `claim_activity_splits`, `evidence_links`, `source_documents`; member of B sees none of them | pass |
| a user with no membership sees none of them | pass |
| `execution_summaries` unreadable by every authenticated user, readable by the service connection | pass |
| uploader can read their own upload even with no referencing record; nobody else can | pass |
| `authenticated` with no identity (`auth.uid()` NULL) sees nothing | pass |
| `anon` sees nothing (or is denied) on 5 core tables | pass |
| NULL-project rows invisible to members; NULL-project INSERT rejected | pass |
| cross-project INSERT rejected; UPDATE/DELETE of foreign rows affect 0 rows; moving a row to another project rejected | pass |
| **mutation test:** re-creating the four `USING (true)` policies makes 5 tests fail; restoring them passes 23/23 | pass |
| `init_db()` (schema.sql) does not recreate any `USING (true)` policy | pass |
| a clean database builds through 000–015 + schema.sql and matches the shared schema (tables, columns, constraints, indexes) | pass |

## 5. Deployment procedure for the shared Supabase database (after authorization)

The shared DB has migrations **000–011** recorded in `schema_migrations`. `012` (quality/ITP) is **not recorded**, although its objects exist (applied by hand); 013/014/015 are pending.

1. **Freeze**: tell the team; the shared DB is written to continuously by test suites.
2. **Snapshot**: create a Supabase branch/backup, or export the policy catalog:
   `psql "$DATABASE_URL" -c "\copy (select * from pg_policies where schemaname='public') to 'policies_before.csv' csv header"`
3. **Apply 013** in one transaction (SQL editor or psql):
   `BEGIN; \i backend/models/migrations/013_v7_rls_project_scope.sql; COMMIT;`
   (013 is independent of 014/015 and can be applied alone. 014 and 015 are separate reviewed migrations; see their headers.)
4. **Verify** (read-only):
   ```sql
   select tablename, policyname, cmd from pg_policies
   where schemaname='public' and (qual = 'true' or with_check = 'true') and tablename <> 'profiles';   -- expect 0 rows
   select policyname from pg_policies where tablename in ('evidence_links','source_documents')
     and policyname in ('v7_evidence_links_select','v7_source_documents_select');                        -- expect 2 rows
   ```
5. **Record**: `insert into schema_migrations(version) values ('013_v7_rls_project_scope.sql') on conflict do nothing;` (the project migrator does this itself when used).
6. **Smoke**: log in as two members of different projects and confirm each sees only its own rows on the four tables.

## 6. Rollback

013 only touches policies. To return to the previous state (this **re-opens the cross-project leak**; use only if a legitimate client turns out to depend on it):

```sql
BEGIN;
DROP POLICY IF EXISTS "v7_evidence_links_select" ON evidence_links;
DROP POLICY IF EXISTS "v7_source_documents_select" ON source_documents;
CREATE POLICY "authenticated_evidence_links_select" ON evidence_links FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated_activity_splits_select" ON claim_activity_splits FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated_execution_summaries_select" ON execution_summaries FOR SELECT TO authenticated USING (true);
COMMIT;
```
(The `impact_scenarios` policies are unchanged in meaning and need no rollback.) No data is affected in either direction.

## 7. Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| A client reading `execution_summaries`/`source_documents` directly through PostgREST loses access | Low: the app reads them through the backend connection | verified with the app's own routes; the rollback above |
| `source_documents` policy cost (EXISTS over four tables) | Low at current volume | indexes exist on `execution_events.document_id`? **No**: add `CREATE INDEX ON execution_events(document_id)` if document volume grows (not needed for correctness) |
| App restart on a checkout that still has the old `schema.sql` recreates the leaking policies | Medium until everyone has this branch | merge the `schema.sql` change together with 013 |
| Shared DB keeps its unlinked legacy audit rows | Known | handled by the legacy/V7 verifier (no data change); see `V7_PRE_E2E_HARDENING_REPORT.md` |

**Authorization needed to proceed:** apply 013 (and separately 014, 015) to the shared Supabase DB.
