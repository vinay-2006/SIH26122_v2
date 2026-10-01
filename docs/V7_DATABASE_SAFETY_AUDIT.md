# SETUAI V7 — Database Safety Audit (read-only)

Date: 2026-09-29. Method: a single read-only session (`conn.read_only = True`) over `pg_catalog`, `information_schema` and SELECT-only queries. **No writes, no DDL, no data changed.**
Target: Supabase Postgres 17.6, connected as `postgres` (bypasses RLS). Shared V7 dev/integration environment.

## DATABASE STATUS

| Area | Status | Summary |
|---|---|---|
| schema | **PASS** | 31 public tables. All 24 requested tables exist (+ `schema_migrations`, `claim_*_splits`, `conflict_records`, `evidence_links`, `execution_summaries`, `validation_issues`). No `actual_revisions`, `rework_items` or `reopen_requests` table exists (see Gap D1). |
| constraints | **PASS WITH LIMITATION** | 125 constraints: PKs everywhere, ~60 FKs, 8 uniques, ~22 CHECKs. Gaps D2–D5 below. |
| indexes | **PASS WITH LIMITATION** | 105 indexes. Partial-unique per-project schedule version and stage code are present. No unique index enforcing "one active schedule per project". |
| RLS | **PASS WITH LIMITATION** | RLS enabled on 30/31 tables (not `schema_migrations`). `FORCE RLS` not set anywhere. The backend connects as `postgres`, which bypasses RLS. `rls_connection()` does `SET LOCAL ROLE authenticated` only in 56 repository call sites. |
| policies | **PASS WITH LIMITATION** | 68 policies, all `TO authenticated`; there are none for `anon`. NULL-project bypass **is closed** on all project-bearing tables: `(project_id IS NOT NULL AND is_project_member(project_id))`. Gaps R1–R4 below. |
| data quality | **PASS WITH LIMITATION** | Referential integrity is clean, except for the items in the Data-quality section. The data is generated test data, not real project data. |
| project isolation | **PARTIAL** | Enforced by RLS + app filtering. Agent/dossier/memory read paths use plain `get_connection()`, so they rely on app filtering alone. Not proven under the `authenticated` role. |
| schedule isolation | **PARTIAL** | Schema is sound (schedule-scoped PK, FKs). V6 "active schedule" fallbacks remain in `intake.py`, `activities.py`, `schedule_context.py`, `main.py` and `schedule_index.py`. |
| write-test safety | **UNSAFE as-is** | See below: 34 of 66 test files write to the DB, and `pytest` auto-loads `.env`. |

## 1. Schema and constraints

- Activity identity is the composite `(schedule_id, activity_id)`. There are no FK columns from `execution_events.matched_activity_id/reported_activity_id`, `approved_actuals.activity_id` or `schedule_dependencies.{predecessor,successor}_activity_id` to `schedule_activities`. Integrity is application-enforced. Currently 0 violations in the data.
- **D1. Revision history has no table.** `approved_actuals` is UNIQUE `(schedule_id, activity_id)` with `is_reopened/reopened_at/reopened_by/rework_notes` columns. A reopen revision therefore overwrites the current row. "Original actual preserved + revision recorded" can only rely on `audit_logs` before/after state. `reopen_repo` chains those correctly; no other store keeps the prior value.
- **D2. `project_id` is nullable** on `schedules`, `schedule_activities`, `execution_events`, `approved_actuals`, `audit_logs` (kept for legacy V6 rows). RLS hides such rows from `authenticated`, but the backend's service connection sees them. There is 1 activity row with a NULL project id today.
- **D3. No composite consistency constraint** that `schedule_activities.project_id = schedules.project_id` (or the same for events, stages). Currently 1 mismatch (the NULL-project activity above), otherwise 0.
- **D4. Missing CHECKs:** `schedules` and `execution_events.status`, `approved_actuals` state, `weight_factor` upper bound. Only `weight_factor >= 0`, `weight_pct 0–100` and `actual_pct 0–100` exist. Quality status enumerates all six values correctly.
- **D5. `profiles.role` CHECK** allows only `SITE_ENGINEER`/`SUPERVISOR`, while memberships allow PLANNER, PROJECT_MANAGER, VIEWER. That is consistent with per-project roles, but shows profile role ≠ effective role.
- No triggers exist in `public`, and no views.
- 1 function, `is_project_member(uuid)`. It is `SECURITY DEFINER`, returns FALSE when `auth.uid()` is NULL, and checks `active = TRUE`. It does not check `status = 'ACTIVE'`, so a SUSPENDED membership with `active = TRUE` still passes (memberships have both fields; all 283 currently agree).

## 2. RLS policy audit

| # | Finding | Severity |
|---|---|---|
| R1 | **`anon` holds ALL privileges (incl. TRUNCATE, DELETE) on every public table** — Supabase default grants. RLS blocks anon row access (no anon policies) but **TRUNCATE is not subject to RLS**; it's only unreachable because PostgREST doesn't expose it. Revoke anon table grants for defence in depth. | MEDIUM |
| R2 | `SELECT USING (true)` on `profiles`, `evidence_links`, `execution_summaries`, and a redundant `true` SELECT on `claim_activity_splits` (which defeats its member-only policy — policies are OR-ed). All `authenticated` users can read those rows across projects. | **HIGH** for `claim_activity_splits`, `evidence_links`, `execution_summaries` (cross-project leakage); MEDIUM for `profiles` |
| R3 | `source_documents` has RLS on and **no policy** → invisible to `authenticated`. Anything using the RLS path cannot read documents; the backend currently gets them only via the service role. | MEDIUM (integration) |
| R4 | `quality_evidence`, `quality_gates` policies use `is_project_member(qg.project_id)` without an explicit `IS NOT NULL`. Safe today because `quality_gates.project_id` is NOT NULL. | LOW |
| R5 | No tables have FORCE RLS; table owner (`postgres`) bypasses. Expected for Supabase, but it means the app's `SET LOCAL ROLE` discipline is the only enforcement. `SET LOCAL` is transaction-scoped: after any `commit()` inside a `rls_connection()` block the connection silently reverts to `postgres`. `ProjectAuditRepository.log` commits once at the end, so it is fine; other callers need a review. | MEDIUM |
| R6 | Write policies are absent for many tables that are read-only for members (`projects`, `candidate_matches`, `planner_decisions`, `institutional_incidents`, `agent_briefings`, `contractor_disputes`, …). Writes to them succeed only via the service role. This is coherent with a service-layer-write design, but it means RLS does not protect those writes. | LOW |

NULL-project bypass: **not present** in any live policy. The migration file `010` still contains the old `project_id IS NULL OR …` policy text for `schedules`; migration `011` replaced it. The live DB is correct.

## 3. Project / schedule isolation map

| Table | project_id | schedule_id | Notes |
|---|---|---|---|
| projects, project_memberships, contractors, work_packages, institutional_incidents, contractor_disputes, agent_briefings | NOT NULL | — | project-scoped only |
| stages, quality_gates, impact_scenarios, itps | NOT NULL | yes | fully scoped |
| schedules, schedule_activities, execution_events, approved_actuals | **nullable** | yes | see D2/D3 |
| audit_logs | **nullable** | nullable | 101 of 133 rows have NULL project |
| candidate_matches, conflict_records, validation_issues, claim_*_splits, schedule_dependencies | **no column** | yes | scope implicit via schedule; RLS goes through a join |
| planner_decisions, source_references | no column | no | scope implicit via `event_id` → `execution_events` |
| quality_evidence, incident_evidence | no column | no | implicit via gate/incident |
| profiles, evidence_links, execution_summaries | no column | no | **global** (R2) |

Remaining V6 fallbacks (code): `intake.py` L348/531/822/868/1012, `activities.py` L34/455, `shared/schedule_context.py`, `main.py` L66, `shared/schedule_index.py` (process-global active id, compared in `matching.py:1125`). The routes `/api/v1/schedules/*`, `/claims/{event_id}/candidates` and `/mock-p6/*` are unauthenticated.

## 4. Data-quality audit (existing data — untouched)

**Conclusion: this is generated test data, not real project data.** Evidence:
- All 378 projects were created 2026-09-26 → 2026-09-29; project codes like `PRJ-A-9fe7af`, `PRJ-B-4BB15D`; every project name matches test patterns (`Project Alpha/Beta`, `Schedule A/B`, `Phase 12 Project A`, `Activity Test Project`, `Stage Test Project`, `Schedule Version Test Project`).
- Profiles are `Member A/B` ×70 each, `Engineer Alpha`, `PM Project Alpha/Beta`, `Auditor Alpha` ×21. **All 283 profiles have no `auth.users` row** (auth.users is empty) — synthetic identities.
- 178 projects have no schedule; 160 have no membership. Both are orphan-like fixtures left by the test suites (nothing cleans up).
- 173 of 220 schedules have zero stages; 20 schedules have root-stage weights ≠ 100%.
- Someone is writing to it right now: counts rose during the audit (profiles 280→283, schedules 219→220, audit rows up to `2026-09-29 12:30:45Z`). The suites appear to be run against this DB by teammates, who don't clean up.

Integrity checks, all 0 unless noted: events with project ≠ schedule project; stages with project ≠ schedule project; activity/stage in different schedules; activity → contractor/work-package in another project; work-package contractor in another project; events matched to non-existent activity; actuals without activity; dependencies with missing activity; duplicate contractor names; multiple active schedules per project; duplicate audit hashes; `approved_actuals` rows = 0. Exceptions: 1 activity row with NULL `project_id` (`SCH-5c9189`/`CIV-PS3-FND-001`).

**Audit chain — defect (HIGH):** `ProjectAuditRepository.log()` (`backend/repositories/audit_repo.py`) hard-codes `previous_hash = "GENESIS"` for every row. 32 of 32 project-scoped audit rows are therefore unlinked and would verify as BROKEN. `reopen_repo` chains correctly (reads the previous hash per project). The legacy V6 global chain (project_id NULL, 101 rows) has 7 breaks. The 31 `SUPERVISING_AGENT` audit rows are written through the legacy `write_audit_log` with a NULL project id, so agent activity is not in any project chain or in the RLS-visible audit. I have not changed this: a fix needs per-project locking to avoid concurrent forks and mixes with existing rows, so it needs your decision.

`agent_briefings`: 8 rows, 7 DEGRADED (fallback engaged) / 1 NORMAL.

## 5. Test audit — which tests are safe against this DB

`pytest` calls `load_dotenv()` from `backend/shared/db.py`, so a bare `pytest` now **writes to the shared DB**. Before `.env` existed these tests just errored (that is the 141 errors in the earlier baseline).

| Class | Files | Safe now? |
|---|---|---|
| UNIT, no DB (14) | matching_semantic, llm_extraction, shared/test_schedule, tabular_extraction, xer_parser, phase12_auth_jwks, phase5_execution_state, phase6_completed_exclusion, phase6_matching_eligibility, phase7_rework_matching, priority1_audit_chain_tamper, v6_m4_algorithms, v6_rule_fallback, v6_wbs_split | **Yes** |
| UNIT, SQLite in-memory (16) | activities_api, phase10_canonical_data, phase1_foundation, phase1a_approved_actuals, phase2_a1_core/propagation, phase2_export, phase3_auto_export, phase3_dashboard_history, phase4_dashboard, phase5_institutional_memory, phase5_knowledge_graph, phase6_activity_history, phase6_ask_why, phase7_forecast, phase9_p6_adapter | **Yes**, with `DATABASE_URL=""` (2 tests need a DB) |
| DB-WRITE (12) | m2_intake, p0_stabilization, phase11_memory_retrieval, phase12_integration, phase1b_auth_rbac, phase2_db_reconstruction, phase6_matching_isolation, phase6_stage_scoping, phase7_summary_translation, phase8_impact_preview, priority1_cross_schedule_isolation, priority1_supabase_rls_and_fks | **No** |
| DB-WRITE incl. DELETE/DDL patterns (22) | phase3/4/5/7/8/9/10/12/13 suites, v7_rls_security_hardening, priority1_decision_atomicity, shared/test_schedule_index, test_schedule_repository_integration, routers/test_schedules, … | **No** |
| DB-READ? (2) | shared/test_schedule_repository, smoke_test | Not verified; treat as unsafe |
| live_llm (13, deselected) | — | Not run |

Verified: with `DATABASE_URL=""` the guard makes `get_connection()` raise. The 30 safe files: **315 passed, 2 failed** (both need a DB), 2 deselected. Nothing reached the database.

Classification is by static scan of the test source (heuristic); E2E class = none exist yet.

## 6. Code changes made (code-only, no DB)

1. `backend/routers/activities.py:254-255`: `event.get("photo_path")` / `event.get("document_id")` → `event["…"]`. The SQLite test rows have no `.get`; the columns are always selected, so behaviour on psycopg dict rows is identical. Effect: ~18 failing tests pass. **My earlier report called this a production bug; that was wrong — it was a test-harness/driver mismatch, now resolved.**

## 7. What we need before the full write-based integration and E2E run

**Blocking (must exist first):**
1. **An isolated database.** A Supabase branch or a separate project seeded from `backend/models/migrations/000–012` (plus `schema_migrations`). The current DB is live-shared; teammates are writing to it now, and 34 test files write to it and never clean up. Do not run the E2E on the shared one.
2. **Real auth identities.** `auth.users` is empty, so no JWT can be issued for the 283 synthetic profiles. E2E and RLS checks need at least ~6 real Supabase users (2 projects × supervisor/engineer/PM), or `AUTH_DEV_MODE` with an explicit, documented decision on unsigned tokens. `SUPABASE_JWT_SECRET` is empty; confirm the JWKS URL alone verifies your tokens.
3. **A test-DB guard.** A `conftest.py` check that refuses to run when `DATABASE_URL` matches the shared host unless `SETUAI_ALLOW_DB_TESTS=1`, and a `V7-INTEG-` prefix or dedicated schema so cleanup is exact.
4. **A decision on the audit-chain defect** (`GENESIS` on every row). The E2E's "audit chain updated / dossier verification VALID" steps cannot pass until this is fixed.
5. **RLS-testable role.** A second connection string for the `authenticated` role (or `SET ROLE` support) so isolation tests exercise RLS, not just app filtering. The `postgres` connection can't prove RLS.

**Should be fixed before the E2E (code/schema, each needs your OK):**
6. Add auth to `/api/v1/schedules/*`, `/claims/{event_id}/candidates` and `/mock-p6/*`.
7. Drop the `true` SELECT policies on `claim_activity_splits`, `evidence_links` and `execution_summaries`; add a `source_documents` policy.
8. Decide the revision-history model (D1) — the reopen "original preserved" test needs somewhere to look.
9. Frontend: repoint 19 stale paths to the project-scoped routes, and add agent/dossier/memory/impact screens or the E2E steps 9–10 have nothing to drive.
10. Node dependencies installed, so the frontend build and browser E2E can run.

**Optional hardening:** revoke `anon` grants (R1), unique-active-schedule index, composite FK/CHECKs (D3/D4).

No write-based test will run until you explicitly authorize it.
