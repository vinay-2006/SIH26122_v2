# V7 Route Context Remediation (B2)

Every route that can read or mutate project-owned data now requires a **validated project context** (explicit `X-Project-ID` / path / query, checked against an ACTIVE membership) and, where data is schedule-owned, an **explicit schedule that belongs to that project**. There is no implicit "only project", "active schedule" or "latest schedule" selection anywhere. Header/path/query values that disagree are rejected (400). Roles are the caller's PROJECT role, expressed as RBAC permissions (`backend/rbac/permissions.py`), not the V6 profile role.

Result on the live route table (149 routes): **129 project-scoped**, 4 user-level, 16 health/dev-only. Enforced permanently by `tests/test_route_context_contract.py` (a new unscoped route fails CI).

| Route | Method | Current Context (before) | Required Context | Role (permission) | Fix | Test |
| --- | --- | --- | --- | --- | --- | --- |
| `/api/v1/claims/text` | POST | V6 profile role SITE_ENGINEER only; **newest schedule of ANY project chosen**; event stored with NULL project_id | Project (X-Project-ID) + explicit schedule (body/header/query, must belong to the project) | CREATE_EXECUTION_EVENT | `gates.project_events_create` + `resolve_explicit_schedule`; project_id + stage/contractor/work-package stamped on the event | test_integration_intake_context.py (10); test_m2_intake.py |
| `/api/v1/claims/file` | POST | as above (no schedule parameter at all) | Project + explicit schedule (form field / header) | CREATE_EXECUTION_EVENT | as above; `schedule_id` form field added | test_integration_intake_context.py::test_file_intake_requires_and_uses_the_explicit_schedule |
| `/api/v1/claims/schedule-export` | POST | as above | Project + explicit schedule | CREATE_EXECUTION_EVENT | as above | same |
| `/api/v1/claims` | GET | V6 role; newest-schedule fallback; no project filter | Project + explicit schedule | VIEW_EXECUTION_EVENTS | `gates.events_view`; SQL `schedule_id AND project_id` | test_claim_list_is_schedule_and_project_scoped |
| `/api/v1/claims/{event_id}` | GET | V6 role only; any event by id | Project; event must belong to it | VIEW_EXECUTION_EVENTS | `gates.event_view` (event ∈ project) | test_reading_and_clarifying_another_projects_claim_is_denied |
| `/api/v1/claims/{event_id}/photo` | GET | V6 role only | Project; event ∈ project | VIEW_EXECUTION_EVENTS | `gates.event_view` | same |
| `/api/v1/claims/{event_id}/clarify` | POST | V6 role only | Project; event ∈ project | CREATE_EXECUTION_EVENT or REVIEW_CLAIM | `gates.event_clarify` | same |
| `/api/v1/claims/{event_id}/candidates` | GET | **no authentication** | Project; event ∈ project | VIEW_EXECUTION_EVENTS | `gates.event_view` | test_candidates_authorization / test_no_project_context_is_rejected… |
| `/api/v1/claims/{event_id}/match` | POST | any authenticated user; activity pool not project-filtered | Project; event ∈ project | CREATE_EXECUTION_EVENT or REVIEW_CLAIM | `gates.event_process`; activity pool `schedule_id AND project_id`; matched activity stamps stage/contractor/work package; audit into the project chain | test_phase1b_auth_rbac.py; test_phase6_* |
| `/api/v1/claims/{event_id}/rematch` | POST | any authenticated user | as match | as match | as match | test_phase1b_auth_rbac.py::test_auth_rematch |
| `/api/v1/claims/{event_id}/splits` | GET | any authenticated user | Project; event ∈ project | VIEW_EXECUTION_EVENTS | `gates.event_view` | test_intake_context (denied cross-project) |
| `/api/v1/claims/{event_id}/splits` | PATCH | V6 role SUPERVISOR | Project; event ∈ project | REVIEW_CLAIM | `gates.event_review`; audit into project chain | route contract test |
| `/api/v1/claims/{event_id}/check` | POST | any authenticated user | Project; event ∈ project | CREATE_EXECUTION_EVENT or REVIEW_CLAIM | `gates.event_process`; audit actor = the human, project chain | test_phase1b_auth_rbac.py::test_auth_09 |
| `/api/v1/claims/{event_id}/conflicts` | GET | V6 role SUPERVISOR | Project; event ∈ project | REVIEW_CLAIM | `gates.event_review` | route contract test |
| `/api/v1/claims/{event_id}/validation` | GET | V6 role SUPERVISOR | Project; event ∈ project | REVIEW_CLAIM | `gates.event_review` | route contract test |
| `/api/v1/claims/{event_id}/evidence` | GET | V6 role SUPERVISOR | Project; event ∈ project | REVIEW_CLAIM | `gates.event_review` | route contract test |
| `/api/v1/claims/{event_id}/knowledge-graph` | GET | V6 role SUPERVISOR | Project; event ∈ project | REVIEW_CLAIM | `gates.event_review` | route contract test |
| `/api/v1/claims/{silent-activities,checks/silent-activities}, /alerts/silent-activities, /dashboard/silent-activities` | GET | V6 role SUPERVISOR; **newest schedule of any project** | Project + explicit schedule | VIEW_EXECUTION_EVENTS | `gates.events_view`; fallback removed; SQL `schedule_id AND project_id` | route contract test |
| `/api/v1/review-queue` | GET | V6 role SUPERVISOR; **all schedules of all projects when no schedule given** | Project + explicit schedule | REVIEW_CLAIM | `gates.claim_review_schedule`; SQL `schedule_id AND project_id` | route contract test |
| `/api/v1/activities/{activity_id}/rollup` | GET | V6 role SUPERVISOR; newest schedule fallback | Project + explicit schedule | VIEW_EXECUTION_EVENTS | `gates.events_view`; fallback removed | route contract test |
| `/api/v1/audit` | GET | V6 role SUPERVISOR; **global recent audit of all projects** | Project | VIEW_AUDIT | `gates.audit_view`; `list_recent_audit_logs(project_id=…)` | route contract test |
| `/api/v1/audit/{entity_id}` | GET | V6 role SUPERVISOR; by entity id across projects | Project | VIEW_AUDIT | `get_audit_trail(entity_id, project_id)` | route contract test |
| `/api/v1/decisions` | POST | V6 role SUPERVISOR; claim by id from any project; approved actual **without project_id**; audit after commit, failures swallowed | Project; claim ∈ project | APPROVE_ACTUAL | `gates.project_approve`; claim project check; target activity ∈ claim schedule/project; **completed activity refused (REOPEN_NOT_ALLOWED)**; approved actual stamped project+stage; audit in the SAME transaction | test_priority1_decision_atomicity.py (8) |
| `/api/v1/decisions` | GET | V6 role SUPERVISOR; recent decisions of ALL projects | Project | REVIEW_CLAIM | `gates.project_review`; joined to the project's events | route contract test |
| `/api/v1/digest` | GET | V6 role SUPERVISOR; newest-schedule fallback | Project + explicit schedule | REVIEW_CLAIM | `gates.claim_review_schedule`; SQL `schedule_id AND project_id` | test_priority1_cross_schedule_isolation.py::test_digest_isolation_explicit_schedule_id |
| `/api/v1/digest/bulk-approve` | POST | V6 role SUPERVISOR; newest-schedule fallback | Project + explicit schedule | APPROVE_ACTUAL | `gates.approve_schedule`; each claim via the governed `_record_decision` | test_phase1b_auth_rbac.py::test_auth_05 |
| `/api/v1/dashboard/summary` | GET | V6 role SUPERVISOR; newest-schedule fallback | Project + explicit schedule | REVIEW_CLAIM | `gates.claim_review_schedule` | test_phase4_dashboard.py; smoke |
| `/api/v1/dashboard/delay-reasons` | GET | same | same | REVIEW_CLAIM | same | test_phase4_dashboard.py |
| `/api/v1/dashboard/institutional-memory` | GET | V6 role SUPERVISOR; **history across all projects** | Project | REVIEW_CLAIM | `gates.project_review`; SQL `project_id` | test_phase5_institutional_memory.py |
| `/api/v1/dashboard/forecast` | GET | V6 role SUPERVISOR; first schedule containing the id; global history | Project + explicit schedule | REVIEW_CLAIM | `gates.claim_review_schedule`; ratios from the project's own history | test_phase7_forecast.py |
| `/api/v1/execution-summary` | GET | V6 role SUPERVISOR; newest-schedule fallback (twice) | Project + explicit schedule | REVIEW_CLAIM | fallbacks removed | test_phase7_summary_translation.py |
| `/api/v1/reports/execution-summary` | GET | V6 role SUPERVISOR; newest-schedule fallback | Project + explicit schedule | REVIEW_CLAIM | fallback removed | route contract test |
| `/api/v1/reports/translate` | POST | V6 role (either) | User only (translates supplied text; no stored data) | authenticated | allow-listed as USER-level | route contract test |
| `/api/v1/export/csv` | GET | V6 role SUPERVISOR; **approved actuals of ALL schedules when none given** | Project + explicit schedule | REVIEW_CLAIM | SQL `schedule_id AND project_id`; auto-export file per schedule | test_phase2_export.py |
| `/api/v1/graph/activity/{activity_id}` | GET | V6 role check; activity id resolved across schedules | Project + explicit schedule | VIEW_EXECUTION_EVENTS | `gates.events_view` | test_phase5_knowledge_graph.py |
| `/api/v1/graph/explain/{activity_id}` | GET | V6 role SUPERVISOR; newest-schedule fallback | Project + explicit schedule (+ event ∈ schedule) | REVIEW_CLAIM or VIEW_EXECUTION_EVENTS | `gates.review_or_view_schedule`; event ∈ project | route contract test |
| `/api/v1/investigation/activity/{activity_id}` | GET | V6 role check | Project + explicit schedule | VIEW_EXECUTION_EVENTS | `gates.events_view` | test_phase6_ask_why.py |
| `/api/v1/schedule/{activity_id}/impact-preview` | GET | V6 role SUPERVISOR (legacy quick preview) | Project + explicit schedule | REVIEW_CLAIM | `gates.claim_review_schedule` (superseded by the V7 compound impact engine) | test_phase8_impact_preview.py |
| `/api/v1/activities , /activities/{id}/history` | GET | V6 role SUPERVISOR; **3 silent schedule selections incl. cross-schedule lookup by activity_id** | Project + explicit schedule | REVIEW_CLAIM | `gates.claim_review_schedule`; core requires schedule | test_integration_route_security.py (activities) |
| `/api/v1/schedules/* (9 routes)` | * | **no authentication** | Project (+ schedule) | VIEW_SCHEDULE / MANAGE_SCHEDULE | `gates.schedule_view`, `require_permission` | test_integration_route_security.py |

## Cross-cutting fixes
- `resolve_raw_project_id` / `resolve_raw_schedule_id`: a **path parameter is authoritative**; a differing header/query is rejected. (Confirmed exploitable before: a member of project A could read project B's data by sending a mismatched header.)
- `require_project_context`: the single-membership fallback ("if you only belong to one project, use it") was **removed**; multi-project or single-project, the project must be named.
- `require_schedule_context` / `resolve_explicit_schedule`: schedule must exist (404) and belong to the project (403); a schedule whose `project_id` is NULL (legacy V6) is unreachable through the API.
- Approval path: `_record_decision` verifies the claim's project, that the target activity exists in the claim's schedule of that project, refuses ordinary approval of a COMPLETED activity (`REOPEN_NOT_ALLOWED`; changes go through the governed reopen/revision workflow), stamps `project_id`/`stage_id` on the approved actual, and appends the audit record in the same transaction.
- Removed: `_get_active_schedule_id` (intake), `backend/shared/schedule_context.resolve_schedule_id`, every `ORDER BY created_at DESC LIMIT 1` schedule lookup in `checks.py`, `claim_graph.py`, `summary.py`, `activities.py`.
- Remaining, classified as INTERNAL INDEX USE (not request semantics): `main.py` FAISS warm-up and `shared/schedule_index` (one in-memory index; rebuilt for the event's own schedule).

## Verification
- DB-free: route contract test (4), context resolution tests (17), RBAC/gate unit tests updated to project roles.
- Isolated DB: `test_integration_route_security.py` (24), `test_integration_intake_context.py` (10), `test_priority1_decision_atomicity.py` (8), plus the phase suites.
