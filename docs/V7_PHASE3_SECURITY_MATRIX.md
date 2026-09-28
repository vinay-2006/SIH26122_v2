# SETUAI V7 — PHASE 3 ENDPOINT SECURITY MATRIX

## Authoritative Endpoint Classification and Security Policies

Total Endpoints Audited: **72**

* **Public**: 18
* **Authenticated Global**: 1
* **Project-Scoped**: 35
* **Schedule-Scoped**: 16
* **Admin / System / Mock**: 2

---

## 1. Public Infrastructure & Health Endpoints (18)

| Endpoint | Method | Auth | Project Context | Schedule Context | Role/Permission | RLS | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `/` | `GET` | Public | None | None | None | N/A | ACTIVE |
| `/health` | `GET` | Public | None | None | None | N/A | ACTIVE |
| `/health/db` | `GET` | Public | None | None | None | N/A | ACTIVE |
| `/health/ai` | `GET` | Public | None | None | None | N/A | ACTIVE |
| `/docs` | `GET` | Public | None | None | None | N/A | ACTIVE |
| `/redoc` | `GET` | Public | None | None | None | N/A | ACTIVE |
| `/openapi.json` | `GET` | Public | None | None | None | N/A | ACTIVE |
| `/docs/oauth2-redirect` | `GET` | Public | None | None | None | N/A | ACTIVE |
| `/api/v1/schedules/health` | `GET` | Public | None | None | None | N/A | ACTIVE |
| `/api/v1/claims/health` | `GET` | Public | None | None | None | N/A | ACTIVE |
| `/api/v1/claims/checks/health` | `GET` | Public | None | None | None | N/A | ACTIVE |
| `/api/v1/claims/matching/health` | `GET` | Public | None | None | None | N/A | ACTIVE |
| `/api/v1/dashboard/health` | `GET` | Public | None | None | None | N/A | ACTIVE |
| `/api/v1/decisions/health` | `GET` | Public | None | None | None | N/A | ACTIVE |
| `/api/v1/export/health` | `GET` | Public | None | None | None | N/A | ACTIVE |
| `/api/v1/intake/health` | `GET` | Public | None | None | None | N/A | ACTIVE |
| `/api/v1/mock-p6/health` | `GET` | Public | None | None | None | N/A | ACTIVE |
| `/api/v1/schedule/health` | `GET` | Public | None | None | None | N/A | ACTIVE |

---

## 2. Authenticated Global Endpoints (1)

| Endpoint | Method | Auth | Project Context | Schedule Context | Role/Permission | RLS | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `/api/v1/auth/me` | `GET` | Bearer JWT | None | None | Authenticated User | profiles RLS | PROTECTED |

---

## 3. Project-Scoped Endpoints (35)

| Endpoint | Method | Auth | Project Context | Schedule Context | Role/Permission | RLS | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `/api/v1/claims` | `GET` | Bearer JWT | Required | Optional | `VIEW_EXECUTION_EVENTS` | execution_events RLS | PROTECTED |
| `/api/v1/claims/text` | `POST` | Bearer JWT | Required | Optional | `CREATE_EXECUTION_EVENT` | execution_events RLS | PROTECTED |
| `/api/v1/claims/file` | `POST` | Bearer JWT | Required | Optional | `CREATE_EXECUTION_EVENT` | execution_events RLS | PROTECTED |
| `/api/v1/claims/schedule-export` | `POST` | Bearer JWT | Required | Optional | `CREATE_EXECUTION_EVENT` | execution_events RLS | PROTECTED |
| `/api/v1/claims/{event_id}` | `GET` | Bearer JWT | Required | Optional | `VIEW_EXECUTION_EVENTS` | execution_events RLS | PROTECTED |
| `/api/v1/claims/{event_id}/candidates` | `GET` | Bearer JWT | Required | Optional | `VIEW_EXECUTION_EVENTS` | candidate_matches RLS | PROTECTED |
| `/api/v1/claims/{event_id}/match` | `POST` | Bearer JWT | Required | Optional | `REVIEW_CLAIM` | execution_events RLS | PROTECTED |
| `/api/v1/claims/{event_id}/rematch` | `POST` | Bearer JWT | Required | Optional | `REVIEW_CLAIM` | execution_events RLS | PROTECTED |
| `/api/v1/claims/{event_id}/check` | `POST` | Bearer JWT | Required | Optional | `REVIEW_CLAIM` | validation_issues RLS | PROTECTED |
| `/api/v1/claims/{event_id}/clarify` | `POST` | Bearer JWT | Required | Optional | `REVIEW_CLAIM` | execution_events RLS | PROTECTED |
| `/api/v1/claims/{event_id}/conflicts` | `GET` | Bearer JWT | Required | Optional | `VIEW_EXECUTION_EVENTS` | conflict_records RLS | PROTECTED |
| `/api/v1/claims/{event_id}/evidence` | `GET` | Bearer JWT | Required | Optional | `VIEW_EXECUTION_EVENTS` | evidence_links RLS | PROTECTED |
| `/api/v1/claims/{event_id}/knowledge-graph` | `GET` | Bearer JWT | Required | Optional | `VIEW_EXECUTION_EVENTS` | execution_events RLS | PROTECTED |
| `/api/v1/claims/{event_id}/photo` | `GET` | Bearer JWT | Required | Optional | `VIEW_EXECUTION_EVENTS` | execution_events RLS | PROTECTED |
| `/api/v1/claims/{event_id}/splits` | `GET` | Bearer JWT | Required | Optional | `VIEW_EXECUTION_EVENTS` | claim_activity_splits RLS | PROTECTED |
| `/api/v1/claims/{event_id}/splits` | `PATCH` | Bearer JWT | Required | Optional | `REVIEW_CLAIM` | claim_activity_splits RLS | PROTECTED |
| `/api/v1/claims/{event_id}/validation` | `GET` | Bearer JWT | Required | Optional | `VIEW_EXECUTION_EVENTS` | validation_issues RLS | PROTECTED |
| `/api/v1/decisions` | `GET` | Bearer JWT | Required | Optional | `VIEW_EXECUTION_EVENTS` | planner_decisions RLS | PROTECTED |
| `/api/v1/decisions` | `POST` | Bearer JWT | Required | Optional | `APPROVE_ACTUAL` | planner_decisions RLS | PROTECTED |
| `/api/v1/digest` | `GET` | Bearer JWT | Required | Optional | `VIEW_PROJECT` | approved_actuals RLS | PROTECTED |
| `/api/v1/digest/bulk-approve` | `POST` | Bearer JWT | Required | Optional | `APPROVE_ACTUAL` | planner_decisions RLS | PROTECTED |
| `/api/v1/review-queue` | `GET` | Bearer JWT | Required | Optional | `REVIEW_CLAIM` | execution_events RLS | PROTECTED |
| `/api/v1/dashboard/summary` | `GET` | Bearer JWT | Required | Optional | `VIEW_PROJECT` | execution_events RLS | PROTECTED |
| `/api/v1/dashboard/delay-reasons` | `GET` | Bearer JWT | Required | Optional | `VIEW_PROJECT` | execution_events RLS | PROTECTED |
| `/api/v1/dashboard/forecast` | `GET` | Bearer JWT | Required | Optional | `VIEW_PROJECT` | execution_events RLS | PROTECTED |
| `/api/v1/dashboard/institutional-memory` | `GET` | Bearer JWT | Required | Optional | `VIEW_PROJECT` | institutional_incidents RLS | PROTECTED |
| `/api/v1/dashboard/silent-activities` | `GET` | Bearer JWT | Required | Optional | `VIEW_PROJECT` | schedule_activities RLS | PROTECTED |
| `/api/v1/claims/silent-activities` | `GET` | Bearer JWT | Required | Optional | `VIEW_PROJECT` | schedule_activities RLS | PROTECTED |
| `/api/v1/claims/checks/silent-activities` | `GET` | Bearer JWT | Required | Optional | `VIEW_PROJECT` | schedule_activities RLS | PROTECTED |
| `/api/v1/alerts/silent-activities` | `GET` | Bearer JWT | Required | Optional | `VIEW_PROJECT` | schedule_activities RLS | PROTECTED |
| `/api/v1/audit` | `GET` | Bearer JWT | Required | Optional | `VIEW_AUDIT` | audit_logs RLS | PROTECTED |
| `/api/v1/audit/{entity_id}` | `GET` | Bearer JWT | Required | Optional | `VIEW_AUDIT` | audit_logs RLS | PROTECTED |
| `/api/v1/reports/execution-summary` | `GET` | Bearer JWT | Required | Optional | `VIEW_PROJECT` | execution_summaries RLS | PROTECTED |
| `/api/v1/reports/translate` | `POST` | Bearer JWT | Required | Optional | `VIEW_PROJECT` | None | PROTECTED |
| `/api/v1/execution-summary` | `GET` | Bearer JWT | Required | Optional | `VIEW_PROJECT` | execution_summaries RLS | PROTECTED |

---

## 4. Schedule-Scoped Endpoints (16)

| Endpoint | Method | Auth | Project Context | Schedule Context | Role/Permission | RLS | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `/api/v1/schedules` | `GET` | Bearer JWT | Required | None | `VIEW_SCHEDULE` | schedules RLS | PROTECTED |
| `/api/v1/schedules` | `POST` | Bearer JWT | Required | None | `MANAGE_SCHEDULE` | schedules RLS | PROTECTED |
| `/api/v1/schedules/active` | `GET` | Bearer JWT | Required | Required | `VIEW_SCHEDULE` | schedules RLS | PROTECTED |
| `/api/v1/schedules/{schedule_id}` | `GET` | Bearer JWT | Required | Required | `VIEW_SCHEDULE` | schedules RLS | PROTECTED |
| `/api/v1/schedules/{schedule_id}/activities` | `GET` | Bearer JWT | Required | Required | `VIEW_SCHEDULE` | schedule_activities RLS | PROTECTED |
| `/api/v1/schedules/{schedule_id}/activities/{activity_id}` | `GET` | Bearer JWT | Required | Required | `VIEW_SCHEDULE` | schedule_activities RLS | PROTECTED |
| `/api/v1/schedules/{schedule_id}/dependencies` | `GET` | Bearer JWT | Required | Required | `VIEW_SCHEDULE` | schedule_dependencies RLS | PROTECTED |
| `/api/v1/schedules/{schedule_id}/wbs-tree` | `GET` | Bearer JWT | Required | Required | `VIEW_SCHEDULE` | schedule_activities RLS | PROTECTED |
| `/api/v1/activities` | `GET` | Bearer JWT | Required | Required | `VIEW_SCHEDULE` | schedule_activities RLS | PROTECTED |
| `/api/v1/activities/{activity_id}/history` | `GET` | Bearer JWT | Required | Required | `VIEW_SCHEDULE` | schedule_activities RLS | PROTECTED |
| `/api/v1/activities/{activity_id}/rollup` | `GET` | Bearer JWT | Required | Required | `VIEW_SCHEDULE` | schedule_activities RLS | PROTECTED |
| `/api/v1/graph/activity/{activity_id}` | `GET` | Bearer JWT | Required | Required | `VIEW_SCHEDULE` | schedule_activities RLS | PROTECTED |
| `/api/v1/graph/explain/{activity_id}` | `GET` | Bearer JWT | Required | Required | `VIEW_SCHEDULE` | schedule_activities RLS | PROTECTED |
| `/api/v1/investigation/activity/{activity_id}` | `GET` | Bearer JWT | Required | Required | `VIEW_SCHEDULE` | schedule_activities RLS | PROTECTED |
| `/api/v1/schedule/{activity_id}/impact-preview` | `GET` | Bearer JWT | Required | Required | `VIEW_SCHEDULE` | schedule_activities RLS | PROTECTED |
| `/api/v1/export/csv` | `GET` | Bearer JWT | Required | Required | `VIEW_SCHEDULE` | schedules RLS | PROTECTED |

---

## 5. Admin / System / Mock Endpoints (2)

| Endpoint | Method | Auth | Project Context | Schedule Context | Role/Permission | RLS | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `/api/v1/mock-p6/activities/{activity_id}` | `POST` | Dev/Internal | None | None | None | None | ISOLATED (Mock) |
| `/api/v1/mock-p6/received` | `GET` | Dev/Internal | None | None | None | None | ISOLATED (Mock) |

---

## 6. Summary Counts

```text
Total endpoints audited: 72
Public: 18
Authenticated global: 1
Project-scoped: 35
Schedule-scoped: 16
Admin/System/Mock: 2
Protected (Auth + Context): 52
```
