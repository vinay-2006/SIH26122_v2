# SETUAI V7 — Final Component Matrix (Audit Snapshot)

Audit date: 2026-09-29. Branch: `main` @ 68e58f1. Read-only audit. One code-only fix so far (see the correction below). Database findings are in `V7_DATABASE_SAFETY_AUDIT.md`.

## Audit environment — read this first

| Item | State |
|---|---|
| `.env` | **Now present** (root). Connects to a shared Supabase dev DB (31 tables, all required V7 tables, RLS on). Write tests are blocked by decision; only read-only queries were run. |
| `frontend/node_modules` | **Absent.** The frontend build has not been run. |
| Python | 3.13.9 (`render.yaml` pins 3.11.1) |
| `python -m compileall backend` | PASS |
| Full `pytest` baseline | **439 passed, 55 failed, 141 errors**, 13 deselected (`live_llm`), 18.6 s |

Nothing that needs write access or an authenticated (non-`postgres`) DB role can be marked VERIFIED from this audit. Status below is capped accordingly.
The four highest-risk phases (agent, dossier, compound impact, quality) are the ones whose tests all error out for lack of a DB.

## Baseline classification (55 failed + 141 errors)

| Bucket | Count | Class | Note |
|---|---|---|---|
| `DATABASE_URL is not configured` | 174 | ENVIRONMENT | Needs a V7 database. Covers phase 2-5, 7-10, 12, 13, RLS, atomicity. |
| `activities.py:254` `'sqlite3.Row' has no attribute 'get'` | ~18 | **TEST GAP, FIXED** | Test fixtures use SQLite rows; production psycopg dict rows do have `.get`. Earlier labelled a production bug — that was wrong. Fixed with `event["…"]`; those tests now pass. |
| `assert 500 == 401` `test_jwt_user_not_in_profiles_rejected` | 1 | BUG / ENV | Returns 500 instead of 401 when the profile lookup can't reach the DB; verify with a DB. |
| `backend/shared/test_schedule_*`, `routers/test_schedules.py`, `smoke_test`, `test_p0_stabilization`, `test_m2_intake` | ~26 | Unclassified (likely ENV/legacy V6) | Need triage once a DB is available. |

Nothing is yet classified as PRE-EXISTING vs NEW: there is no earlier baseline to compare against.

## Component matrix

Legend: DB = migration/table exists; Backend = service/repo code exists; API = route registered; FE = frontend calls a **matching** route; Tests = tests exist; Integrated = cross-phase wiring seen in code. Status is capped at IMPLEMENTED/TESTED where a DB was needed.

| Component | DB | Backend | API | Frontend | Tests | Integrated | Status |
|---|---|---|---|---|---|---|---|
| Projects | ✔ 001 | ✔ | ✔ `/projects` | ✔ ProjectSwitcher | ✔ (DB-blocked) | partial | IMPLEMENTED |
| Schedule Versions | ✔ 002 | ✔ | ✔ project-scoped **and** legacy `/schedules` | ✖ FE uses legacy `/schedules/active` | ✔ (DB-blocked) | **NO** | PARTIAL |
| Stages | ✔ 003 | ✔ | ✔ | ✖ no call found | ✔ (DB-blocked) | partial | IMPLEMENTED |
| Activities | ✔ | ✔ | ✔ | ✔ `/schedules/{id}/activities` (legacy, unauth) | ✔ | partial | PARTIAL |
| Dependencies | ✔ | ✔ | ✔ legacy `/schedules/{id}/dependencies` (unauth) | ✔ legacy | ✔ | partial | PARTIAL |
| Contractors | ✔ 005 | ✔ | ✔ `/projects/{pid}/contractors` | ✖ FE calls `/api/v1/contractors` | ✔ (DB-blocked) | **NO** | PARTIAL |
| Work Packages | ✔ 005 | ✔ | ✔ `/projects/{pid}/work-packages` | ✖ FE calls `/api/v1/work-packages` | ✔ (DB-blocked) | **NO** | PARTIAL |
| Quality Gates | ✔ 006, 012 | ✔ | ✔ `/projects/{pid}/quality-gates/{id}/pass\|fail\|waive` | ✖ FE calls `/quality-gates/{id}/complete` (no such route) | ✔ (DB-blocked) | **NO** | PARTIAL |
| Field Intake | ✔ | ✔ V6 | ✔ `/claims/*` | ✔ | ✔ | V6 shape | IMPLEMENTED |
| Extraction | ✔ | ✔ | ✔ | ✔ | ✔ | V6 shape | IMPLEMENTED |
| Matching | ✔ | ✔ | ✔ `/claims/{id}/match` | ✔ | ✔ | see legacy findings | IMPLEMENTED |
| Validation | ✔ | ✔ | ✔ `/claims/{id}/check` | ✔ | ✔ | V6 shape | IMPLEMENTED |
| Review | ✔ | ✔ | ✔ `/review-queue` `/digest` | ✔ | ✔ | V6 shape | IMPLEMENTED |
| Approved Actuals | ✔ | ✔ | ✔ `/decisions` | ✔ | ✔ | partial | TESTED (unit) |
| Reopen/Rework | via `execution_events` columns (007); no dedicated tables | ✔ | ✔ project-scoped | ✖ FE calls `/api/v1/reopen-requests` | ✔ (DB-blocked) | **NO** | PARTIAL |
| Progress | ✔ 008 | ✔ ProgressService | ✔ `/projects/{pid}/schedules/{sid}/progress/breakdown` | ✖ FE calls `/api/v1/progress/{project,stages,wbs,…}`; also has a duplicate client-side `lib/progressEngine.ts` | ✔ (DB-blocked) | **NO** | PARTIAL |
| Impact | ✔ 009 | ✔ ImpactService | ✔ v7 scenarios/preview | ✖ FE uses legacy `/schedule/{id}/impact-preview` + client-side `lib/impactEngine.ts` | ✔ (DB-blocked) | **NO** | PARTIAL |
| Memory | ✔ 009 | ✔ | ✔ `/projects/{pid}/memory/search` | ✖ FE only calls legacy `/dashboard/institutional-memory` | ✔ | **NO** | PARTIAL |
| Agent | ✔ `agent_briefings` | ✔ | ✔ `/agent/*` | ✖ **no frontend call at all** | ✔ (DB-blocked) | backend only | IMPLEMENTED |
| Dossier | ✔ | ✔ | ✔ `/dossier*` | ✖ **no frontend call at all** | ✔ (DB-blocked) | backend only | IMPLEMENTED |
| Audit | ✔ `audit_logs` (hash chain defective in `ProjectAuditRepository`, see 13a) | ✔ | ✔ `/audit` | ✔ | ✔ tamper test | partial | PARTIAL |
| RBAC | ✔ | ✔ | ✔ | ✔ | ✔ | partial | IMPLEMENTED |
| RLS | ✔ 010, 011 (30/31 tables enabled, 68 policies, NULL-project bypass closed; gaps R1–R6 in the safety audit) | n/a | n/a | n/a | ✔ (write-blocked) | n/a | IMPLEMENTED, unproven |
| Export | ✔ | ✔ | ✔ `/export`, `/claims/schedule-export` | ✔ | ✔ | partial | TESTED (unit) |
| P6 Mock | n/a | ✔ | ✔ `/mock-p6` (**no auth**) | ✔ | ✔ | ✔ | TESTED (unit) |

No component is VERIFIED, INTEGRATED-end-to-end or proven with a real database.

## Findings, ranked

### Critical / High — security
1. **S1. Legacy `/api/v1/schedules/*` (9 routes) has no authentication.** It exposes schedules, activities, dependencies and the WBS tree across projects, and `POST /schedules` is also open. This breaks the four-question API rule (project, schedule, user, role) and defeats isolation. The frontend depends on these routes.
2. **S2. `GET /api/v1/claims/{event_id}/candidates` has no auth** and no project scope. It leaks candidate rows by event id.
3. **S3. `/api/v1/mock-p6/*` (3 routes) has no auth.** The write path (`POST /activities/{id}`) is open. Lower severity if only enabled in demo mode, but it should be gated.

### High — V6 assumptions still on operational paths
4. `intake.py` (`_get_active_schedule_id`, lines 531, 822, 868, 1012) silently picks the "active" schedule.
5. `activities.py` (lines 34, 455) and `shared/schedule_context.py` fall back to `get_active_schedule()`.
6. `main.py:66` calls `get_active_schedule()` at startup.
7. `schedule_index` holds a process-global active schedule id, and `matching.py:1125` compares against it. This is the "global matching" the brief prohibits.

### High — frontend/backend contract gaps
8. The frontend calls 19 paths with no backend route, including all the progress, contractors, work-packages, quality-gates and reopen-requests calls listed above. In real (non-mock) mode these calls return 404.
9. `api.ts` (2,803 lines) routes to `mockData` under `VITE_USE_MOCKS`, and `ProjectContext.tsx` imports `mockData` directly. That must be confirmed as demo-only and not the default.
10. Agent, dossier, memory-search and v7 impact have **zero frontend screens or calls**. The demo flows 7-10 cannot run in the UI as it stands.
11. Duplicate calculations: `lib/progressEngine.ts` and `lib/impactEngine.ts` reimplement the backend engines. That violates the single-authority rule (Phase 8, no second progress calculation).
12. Both `/api/v1` and `/api/v7` route prefixes exist for the agent/dossier/memory routes.

### Medium
13. ~~`activities.py:254` `.get()` on `sqlite3.Row`~~ — reclassified as a test-harness mismatch and fixed (see the baseline table).
13a. **HIGH (new, DB audit):** `ProjectAuditRepository.log()` hard-codes `previous_hash = "GENESIS"`, so V7 project audit chains are unlinked (32/32 rows). The supervising agent audits with a NULL project id (31 rows).
13b. **HIGH (new):** `SELECT USING (true)` policies on `claim_activity_splits`, `evidence_links` and `execution_summaries` leak across projects for any authenticated user. `source_documents` has RLS but no policy.
13c. **MEDIUM (new):** no revision-history table; `approved_actuals` is unique per (schedule, activity), so a reopen revision overwrites the current row.
13d. **MEDIUM (new):** the agent, dossier and memory read paths use the plain `postgres` connection, so RLS is not a second line of defence there.
13e. **MEDIUM (new):** 34 test files write to the DB and `pytest` auto-loads `.env`; the shared DB holds 378 generated test projects and a teammate is writing to it live.
14. Invalid/expired token behaviour is unproven (500 rather than 401 in one test).
15. `backend/routers/test_*.py` are test files placed inside the routers package.
16. `verify_step*.py` and `verify_m3_*.py` are loose at the repo root.
17. The `PYTHON_VERSION` in `render.yaml` (3.11.1) differs from the local interpreter (3.13.9), and there are no Dockerfile/Vercel configs.
18. Error contract: I did not find the required codes (`PROJECT_ACCESS_DENIED`, `QUALITY_HOLD`, …) surfaced consistently. Not yet verified.

### Environment gaps that block verification
- The only DB is shared, so write-based RLS, isolation, RBAC, XER, reopen, progress, quality, impact, memory, agent, dossier and audit-chain checks are on hold. `auth.users` is empty, so no real JWT can be issued for the test profiles.
- No `node_modules`, so the frontend build and browser E2E are not yet run.
- No LLM keys, so live AI checks are not run (deterministic fallback tests can still run offline).

## Release-gate position

None of the 33 release-gate boxes can be ticked yet.

## Delta after pre-E2E hardening (see `V7_PRE_E2E_HARDENING_REPORT.md`)

| Component | Change | Status now |
|---|---|---|
| Audit | One locked, project-scoped append path; hash scheme now matches the verifier; concurrency + tamper verified in the isolated DB | IMPLEMENTED, TESTED (isolated DB); shared-DB rows still unlinked |
| RLS | `claim_activity_splits`, `evidence_links`, `execution_summaries`, `source_documents` fixed in repo + isolated DB (migration 013); **not applied to shared** | IMPLEMENTED, TESTED (runtime, `authenticated`/`anon`); shared DB still leaks |
| Schedule Versions / Activities API | `/schedules/*`, `/activities*` authenticated, project-derived, no silent schedule; header/path substitution fixed globally | TESTED (isolated DB); frontend not yet adapted |
| Reopen/Rework revision history | Existing design sufficient (no new table); concurrency verified | TESTED (isolated DB) |
| Field Intake / Extraction / Matching / Validation / Review | **Not project-aware**: no `project_id` stamped on events, latest-schedule fallback remains (B1/B2) | PARTIAL, blocks E2E |
| Contractors / Work Packages / Quality / Progress / Impact / Memory / Agent / Dossier | Unchanged; regression green on isolated DB; agent active-schedule lookup no longer picks silently | TESTED (isolated DB) |
| RBAC | DB role CHECK does not allow `OWNER`/`QUALITY_INSPECTOR`/`AUDITOR` (B4) | PARTIAL |
| P6 Mock | dev-mode gated (`AUTH_DEV_MODE=true`), otherwise 404 | TESTED |

Still no component is VERIFIED end-to-end; no browser or JWKS-with-real-users verification has been done.
