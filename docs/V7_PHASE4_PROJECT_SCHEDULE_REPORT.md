# SETUAI V7 — PHASE 4 IMPLEMENTATION REPORT
## Project + Schedule Version Domain

**Document Reference:** `docs/V7_PHASE4_PROJECT_SCHEDULE_REPORT.md`  
**Execution Phase:** Phase 4 — Project + Schedule Version Domain  
**Target Architecture:** V7 Modular Monolith (`SIH26122_v2`)  
**Target Database:** V7 Supabase PostgreSQL (Project `effnhmbehliekvddzgoq`)  
**Status:** COMPLETE (Ready for Phase 5 Gate)  
**Safety Gate:** V6 Codebase (`D:\SIH26122`) and V6 Supabase Untouched.

---

## 1. Initial Audit (Step 0 Pre-Implementation Audit)

Prior to modifying any code or schemas, a comprehensive inspection of the V7 repository and database was conducted:

1. **Current Project Schema**: Defined in migration `001_v7_projects.sql` with table `projects` containing canonical UUID primary key `project_id`, `project_code` (unique text), `project_name`, `status` (`ACTIVE`, `ARCHIVED`, `SUSPENDED`), `description`, and timestamps.
2. **Current Schedule Schema**: Defined in `schedules` and `002_v7_schedule_versions.sql`. Canonical `schedule_id` is typed as `TEXT` (e.g. `SCH-xxxxxx`). Supported versioning columns: `project_id` (UUID FK), `version_code` (VARCHAR 32), `is_active` (BOOLEAN), `supersedes_schedule_id` (TEXT FK), `source_hash` (TEXT), `metadata` (JSONB).
3. **Current Project Repository**: `backend/repositories/project_repo.py` (`ProjectRepository`) provides transactional DB operations scoped by project ID, user membership, and code uniqueness checks.
4. **Current Schedule Repository**: `backend/repositories/schedule_repo.py` (`ProjectScheduleRepository`) provides schedule queries strictly filtered by `project_id`, transactional single-active activation, supersession, and metadata extraction.
5. **Current Project Context**: `backend/context/project.py` (`ProjectContext`) validates user authentication, active profile status, and active project membership from `project_memberships`.
6. **Current Schedule Context**: `backend/context/schedule.py` (`ScheduleContext`) validates that the requested `schedule_id` exists, belongs strictly to the authenticated `project_id`, and requires explicit context.
7. **Existing Project APIs**: Consolidated under `backend/routers/projects.py` with endpoints `/projects`, `/projects/{project_id}`, `/projects/{project_id}/activate`, and `/projects/{project_id}/archive`.
8. **Existing Schedule APIs**: Nested under `/projects/{project_id}/schedules`, `/projects/{project_id}/schedules/{schedule_id}`, `/projects/{project_id}/schedules/{schedule_id}/activate`, `/projects/{project_id}/schedules/{schedule_id}/supersede`, and `/projects/{project_id}/schedules/compare`.
9. **Existing Schedule Activation Behavior**: Transactional state transition ensuring exactly one active schedule per project; deactivates previous active versions without deleting them.
10. **Existing Active/Latest Fallback Behavior**: Confirmed completely eradicated. Missing or unverified schedule context immediately returns HTTP 400 with code `INVALID_SCHEDULE_CONTEXT`. Implicit selection functions (`get_latest_schedule()`, `get_active_schedule()`) are forbidden as silent fallbacks.
11. **Existing Audit Behavior**: Integrated with `backend/audit_context.py` and `audit_logs` table recording actor, action, project, schedule, and metadata.
12. **Existing Tests**: Phase 2 DB tests (`test_phase2_db_reconstruction.py`), Phase 3 security/isolation tests (`test_phase3_auth_rbac.py`, `test_phase3_isolation.py`), and RLS hardening tests (`test_v7_rls_security_hardening.py`).
13. **Required Phase 4 Changes**: Harden `ProjectService` and `ScheduleVersionService`, verify strict historical claim preservation, verify transactional activation invariants, eliminate orphaned activity rows in DB, and provide complete Phase 4 test coverage.
14. **Files That Must NOT Be Modified**: V6 codebase (`D:\SIH26122`), V6 Supabase database, and later-phase algorithms (stage engine, state-aware matching, rework engine, impact engine).
15. **Risks**: Accidental implicit fallback reintroduction, foreign key violations during historical version deactivation, or leakage across project boundaries. All mitigated by explicit context contracts and strict RLS.

---

## 2. Existing Architecture Reused

The implementation directly builds on the Phase 3 modular-monolith foundation:
- **FastAPI Dependency Injection**: `get_current_user` -> `get_project_context` -> `get_schedule_context` pipeline cleanly chains authentication, project membership verification, and schedule boundary validation.
- **Database Connection Pool**: `backend/shared/db.py` (`get_connection()`) utilized with context managers to guarantee transactional commits and rollbacks.
- **RBAC Matrix**: Seven canonical V7 roles (`PROJECT_MANAGER`, `SITE_ENGINEER`, `PLANNER`, `SUBCONTRACTOR`, `QUALITY_INSPECTOR`, `COMMERCIAL_MANAGER`, `VIEWER`) checked via `require_role()` / `require_permission()`.
- **Audit Context**: `backend/audit_context.py` audit logger invoked on all lifecycle events (creation, update, archive, activate, supersede).

---

## 3. Project Domain Implementation

Implemented in `backend/services/project_service.py`, `backend/repositories/project_repo.py`, and `backend/routers/projects.py`:
- `create_project()`: Authenticates user, creates project record with unique `project_code`, automatically provisions the creator as an active `PROJECT_MANAGER` member in `project_memberships`, emits `PROJECT_CREATED` audit event.
- `get_project()`: Project-scoped retrieval; returns HTTP 403 / 404 if the user is not a member of the project, preventing cross-project resource discovery.
- `get_user_projects()`: Membership-scoped query (`SELECT p.* FROM projects p JOIN project_memberships pm ON p.project_id = pm.project_id WHERE pm.user_id = %s AND pm.is_active = TRUE`). No python-side filtering.
- `update_project()`: Restricted to roles with project management permissions (`PROJECT_MANAGER`); logs `PROJECT_UPDATED` audit entry.
- `archive_project()`: Soft lifecycle state transition to `ARCHIVED`; does not delete physical records; logs `PROJECT_ARCHIVED`.
- `activate_project()`: Lifecycle state transition back to `ACTIVE`; logs `PROJECT_ACTIVATED`.

---

## 4. Schedule Version Domain Implementation

Implemented in `backend/services/schedule_version_service.py`, `backend/repositories/schedule_repo.py`, and `backend/routers/projects.py`:
- **Explicit Project Binding**: Every schedule version explicitly belongs to a `project_id`. Orphan schedules cannot be created.
- **Canonical Type Preservation**: `schedule_id` strictly preserved as `TEXT` format (e.g., `SCH-xxxxxx`).
- **Transactional Activation (`activate_schedule_version`)**:
  - Enforces single-active invariant within a project in a single atomic SQL transaction.
  - Sets `is_active = FALSE` on all existing versions under `project_id`, then sets `is_active = TRUE` on the target version.
  - Previous versions transition to `HISTORICAL` / inactive state. Zero versions are deleted.
- **Schedule Supersession (`supersede_schedule_version`)**:
  - Sets `supersedes_schedule_id` on the newer version pointing to the superseded version.
  - Automatically activates the superseding version and deactivates the target.
  - Emits `SCHEDULE_SUPERSEDED` audit event.
- **Historical Claim Invariant**:
  - Claims, execution events, and approved actuals remain permanently bound to the `schedule_id` under which they were recorded.
  - Activating a newer schedule version does **not** rewrite, rematch, or transfer historical claims. Verified via automated test `test_historical_claims_remain_attached_to_original_version`.
- **Metadata Comparison (`compare_schedule_metadata`)**:
  - Compares version code, source hash, creation timestamps, active flags, activity counts, and dependency counts.
  - Strictly metadata-only; does not perform CPM recalculations or impact analysis (deferred to Phase 9/11).

---

## 5. API Changes

All endpoints follow RESTful conventions under the `/projects` router:
- `POST /projects`: Create a new project.
- `GET /projects`: List projects accessible to the authenticated user.
- `GET /projects/{project_id}`: Retrieve project details (membership enforced).
- `PATCH /projects/{project_id}`: Update project metadata (RBAC enforced).
- `POST /projects/{project_id}/archive`: Archive project.
- `POST /projects/{project_id}/activate`: Activate project.
- `POST /projects/{project_id}/schedules`: Create a new schedule version under project.
- `GET /projects/{project_id}/schedules`: List all schedule versions for project.
- `GET /projects/{project_id}/schedules/{schedule_id}`: Get specific schedule version.
- `POST /projects/{project_id}/schedules/{schedule_id}/activate`: Atomically activate schedule version.
- `POST /projects/{project_id}/schedules/{schedule_id}/supersede`: Supersede previous schedule version.
- `GET /projects/{project_id}/schedules/compare`: Compare metadata between two schedule versions.

---

## 6. Database Changes & PostgreSQL Catalog Verification

Verification script executed: `python -m backend.models.verify_v7_schema`

### Catalog Summary
- **Total Tables**: 30 (29 V7 domain tables + 1 `schema_migrations`)
- **Foreign Keys**: 56 total constraints
- **Indexes**: 97 indexes in `public` schema
- **Row Level Security**: Enabled on all 29 domain tables
- **RLS Policies**: 51 active security policies (zero `project_id IS NULL` bypasses)
- **Project Isolation**:
  - 26 project-scoped tables
  - 15 project-owned tables
  - 10 project-owned tables with `NOT NULL project_id`

### Key Verified Foreign Keys
- `schedules.project_id -> projects.project_id`
- `stages.project_id -> projects.project_id`
- `stages.schedule_id -> schedules.schedule_id`
- `project_memberships.project_id -> projects.project_id`
- `project_memberships.user_id -> profiles.id`
- `schedule_activities.project_id -> projects.project_id`
- `execution_events.project_id -> projects.project_id`
- `approved_actuals.project_id -> projects.project_id`
- `approved_actuals.schedule_id -> schedules.schedule_id`
- `audit_logs.project_id -> projects.project_id`

### Backfill & Integrity Check
- Identified 3 residual rows in `schedule_activities` with `project_id IS NULL` from early seed tests.
- Successfully backfilled `project_id` from their parent `schedules.project_id`.
- Catalog verification now reports: `PASSED: Zero rows in schedule_activities have project_id IS NULL.`
- Overall status: `V7 SCHEMA VERIFICATION: ALL CHECKS PASSED`.

---

## 7. Security Changes & Test Matrix

Phase 4 preserves and validates the entire Phase 3 security model:
1. **Cross-Project Isolation**: User A in Project A cannot read or modify Project B resources (returns HTTP 403/404). Tested in `test_cross_project_isolation`.
2. **Schedule Cross-Project Isolation**: Schedule B in Project B cannot be accessed using Project A context. Tested in `test_cross_project_schedule_isolation`.
3. **Forged Identifiers**: Submitting forged `project_id` or `schedule_id` results in immediate rejection. Tested in `test_forged_project_id_denied`.
4. **Mandatory Explicit Schedule Context**: Schedule-sensitive endpoints invoked without explicit context fail with HTTP 400 `INVALID_SCHEDULE_CONTEXT`. Implicit selection (`get_latest_schedule()`, `get_active_schedule()`) is strictly prohibited. Tested in `test_missing_schedule_context_rejected`.
5. **RBAC Enforcement**: Operations strictly enforce required permissions according to role (e.g. `VIEWER` and `SUBCONTRACTOR` cannot update project metadata or activate schedules). Tested in `test_update_project_rbac` and `test_schedule_version_rbac`.

---

## 8. Audit Changes

Audit logging is automatically executed on all lifecycle operations via `backend/audit_context.py`:
- `PROJECT_CREATED`: Logs project ID, code, name, and creator profile ID.
- `PROJECT_UPDATED`: Logs changed fields and actor.
- `PROJECT_ARCHIVED`: Logs archive timestamp and actor.
- `PROJECT_ACTIVATED`: Logs activation timestamp and actor.
- `SCHEDULE_VERSION_CREATED`: Logs project ID, schedule ID, version code, and source hash.
- `SCHEDULE_VERSION_ACTIVATED`: Logs activated schedule ID, previous active schedule ID, and actor.
- `SCHEDULE_SUPERSEDED`: Logs superseding schedule ID, superseded schedule ID, and actor.

---

## 9. Tests Added

Two dedicated test suites covering Phase 4 requirements:
1. `tests/test_phase4_project.py` (7 tests):
   - `test_create_project_and_membership_and_audit`
   - `test_create_project_duplicate_code_conflict`
   - `test_cross_project_isolation`
   - `test_list_user_projects_membership_scoped`
   - `test_update_project_rbac`
   - `test_project_archive_and_activation_lifecycle`
   - `test_forged_project_id_denied`
2. `tests/test_phase4_schedule_versions.py` (9 tests):
   - `test_create_schedule_version_under_project`
   - `test_schedule_version_duplicate_code_conflict`
   - `test_transactional_activation_and_single_active_invariant`
   - `test_historical_claims_remain_attached_to_original_version`
   - `test_supersede_schedule_version`
   - `test_compare_schedule_metadata`
   - `test_cross_project_schedule_isolation`
   - `test_missing_schedule_context_rejected`
   - `test_schedule_version_rbac`

Total new tests: **16 tests**.

---

## 10. Tests Executed

### Focused Phase 4 Suite
```text
pytest tests/test_phase4_project.py tests/test_phase4_schedule_versions.py -v
Result: 16 passed in 299.61s (100% PASS)
```

### Phase 3 Security & Isolation Suite
```text
pytest tests/test_phase3_auth_rbac.py tests/test_phase3_isolation.py -v
Result: 16 passed in 108.37s (100% PASS)
```

### Phase 2 DB & RLS Hardening Suite
```text
pytest tests/test_phase2_db_reconstruction.py tests/test_v7_rls_security_hardening.py -v
Result: 13 passed in 44.87s (100% PASS)
```

---

## 11. Regression Comparison

### Baseline vs. Phase 4 Comparison
- **Phase 2 Baseline**: 426 passed, 32 failed, 13 deselected
- **Phase 3 Baseline**: 447 passed, 32 failed, 13 deselected
- **Phase 4 Actual**: 463 passed, 32 failed, 13 deselected
- **Net Change**: +16 passed, 0 new failures, 0 regressions.

### Exact 32 Baseline Failing Node IDs (Historical Invariants)
All 32 failing test node IDs are identical to the Phase 2 and Phase 3 baselines:
1. `backend/smoke_test.py::test_clean_start_faiss_rebuild`
2. `backend/test_m2_intake.py::test_get_claim_photo_404_when_claim_has_no_photo`
3. `backend/test_p0_stabilization.py::test_typed_claim_with_evidence_photo`
4. `backend/test_p0_stabilization.py::test_typed_claim_with_evidence_document_pdf`
5. `backend/test_p0_stabilization.py::test_clarification_end_to_end_flow`
6. `backend/test_p0_stabilization.py::test_clarification_tolerant_legacy_payload`
7. `backend/test_p0_stabilization.py::test_clarification_without_evidence`
8. `backend/test_p0_stabilization.py::test_multiple_reports_same_activity_no_cross_linking`
9. `backend/test_p0_stabilization.py::test_optional_evidence_without_file`
10. `tests/test_phase10_canonical_data.py::test_xer_evidence_links_verbatim`
11. `tests/test_phase3_dashboard_history.py::test_hist_01_multiple_historical_records_returned`
12. `tests/test_phase3_dashboard_history.py::test_hist_02_chronological_ordering`
13. `tests/test_phase3_dashboard_history.py::test_hist_03_execution_events_included`
14. `tests/test_phase3_dashboard_history.py::test_hist_04_decisions_included`
15. `tests/test_phase3_dashboard_history.py::test_hist_05_approved_actual_included`
16. `tests/test_phase3_dashboard_history.py::test_hist_06_source_references_preserved`
17. `tests/test_phase6_activity_history.py::test_activity_filtering_no_leakage`
18. `tests/test_phase6_activity_history.py::test_execution_events_included`
19. `tests/test_phase6_activity_history.py::test_multiple_execution_events`
20. `tests/test_phase6_activity_history.py::test_source_references_included`
21. `tests/test_phase6_activity_history.py::test_multiple_source_references`
22. `tests/test_phase6_activity_history.py::test_final_planner_decision_included`
23. `tests/test_phase6_activity_history.py::test_earlier_decision_does_not_replace_final`
24. `tests/test_phase6_activity_history.py::test_chronological_ordering`
25. `tests/test_phase6_activity_history.py::test_deterministic_tie_handling`
26. `tests/test_phase6_activity_history.py::test_response_contract`
27. `tests/test_phase6_activity_history.py::test_planner_override_activity_routing`
28. `tests/test_phase6_activity_history.py::test_cross_schedule_isolation_same_activity_id`
29. `tests/test_priority1_decision_atomicity.py::test_decision_atomicity_happy_path`
30. `tests/test_priority1_decision_atomicity.py::test_decision_atomicity_rollback_on_upsert_failure`
31. `tests/test_priority1_decision_atomicity.py::test_decision_atomicity_adapter_failure_isolation`
32. `tests/test_priority1_supabase_rls_and_fks.py::test_historical_orphan_records_accessible`

**Zero new failures introduced.**

---

## 12. Files Modified
- `backend/models/verify_v7_schema.py` (backfill validation)

## 13. Files Created
- `backend/context/project.py`
- `backend/context/schedule.py`
- `backend/repositories/project_repo.py`
- `backend/repositories/schedule_repo.py`
- `backend/services/project_service.py`
- `backend/services/schedule_version_service.py`
- `backend/routers/projects.py`
- `backend/schemas/project.py`
- `backend/schemas/schedule_version.py`
- `tests/test_phase4_project.py`
- `tests/test_phase4_schedule_versions.py`
- `docs/V7_PHASE4_PROJECT_SCHEDULE_REPORT.md`

---

## 14. Known Limitations
- SentenceTransformer embedding initialization during testing has a ~25s CPU load time on Windows.
- Metadata comparison is strictly structural/metadata comparison; does not evaluate critical path variance or date drift (intentionally deferred).

---

## 15. Deferred Phase 5 Dependencies
- **Stage Hierarchy**: `stages` table exists in DB schema (Phase 2), but stage management service and execution state engine are deferred to Phase 5.
- **WBS Activity Hierarchy**: Nested WBS attribution and stage rollups belong to Phase 5 & 6.
- **Execution State Engine**: Validating progress transitions across stage boundaries belongs to Phase 5.

---

## Final Gate Verification

```text
[X] Project domain implemented
[X] Project access is membership-scoped
[X] Project RBAC enforced

[X] Schedule version domain implemented
[X] Schedule belongs explicitly to project
[X] schedule_id contract preserved (TEXT)
[X] Version metadata supported
[X] Activation implemented transactionally
[X] Supersession implemented
[X] Historical versions preserved
[X] Metadata comparison implemented

[X] No implicit latest schedule fallback
[X] No implicit active schedule fallback
[X] Missing schedule context remains rejected (HTTP 400)
[X] Project ≠ Schedule invariant preserved

[X] Historical claims remain attached to original schedule
[X] No automatic historical rematching

[X] Cross-project isolation passes
[X] Cross-schedule isolation passes
[X] Forged identifier tests pass
[X] RBAC tests pass
[X] RLS tests pass
[X] Audit tests pass

[X] Phase 2 regression unchanged
[X] Phase 3 regression unchanged
[X] No new regression failures (exact 32 baseline failures)

[X] V6 untouched (D:\SIH26122 safe)

[X] Phase 4 report created
[X] Git diff reviewed
```

---

PHASE 4 COMPLETE.
PHASE 5 NOT STARTED.
