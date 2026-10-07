# V7 BLOCKED State — Design (and the quality-hold integration found alongside it)

## Problem
`WorkflowCondition.BLOCKED` (and `QUALITY_HOLD`) were domain concepts with **no authoritative persisted source**.
`StageService.get_workflow_condition()` looked for a `status` key on the activity dict, but no table stores one on
activities or approved actuals and no bulk query selected quality or blocker facts. Result: in progress, stage state,
matching eligibility, dossier and the agent, neither condition could ever be produced from real data.
`QualityService.get_quality_status()` was documented as the "Member 1 integration contract" and was never called by
any state engine.

## Decision: what is the source of truth?

| Candidate | Why not |
| --- | --- |
| A boolean `is_blocked` on `schedule_activities` | Not explainable (no reason/source/who/when), mutates the baseline schedule row (violates "the schedule is not modified by execution"), can drift from reality, and cannot express a stage-level block. |
| `stages.status = 'BLOCKED'` (exists) | Stage granularity only, manually edited, no reason/source/history. Kept as a stage lifecycle value, but is **not** used to derive activity conditions. |
| `execution_events` of type `BLOCKER` | A field report is a *claim*. It is evidence for a blocker, not the blocker's lifecycle (raise, confirm, resolve). It becomes the blocker's **source** (`source_event_id`), so the chain field report → blocker is traceable. |
| Existing quality tables | A quality hold is its own condition (below); a blocker is broader (material, permit, access...). |

**Chosen: one small additive table `execution_blockers`** (migration 015), because none of the existing tables can
carry the required lifecycle:

```
activity/stage -> blocker_type + reason -> source_event_id (field report) -> created_by/at -> resolved_by/at -> status
```

Columns: `blocker_id, project_id, schedule_id, activity_id | stage_id (at least one), blocker_type, reason,
source_event_id, status ACTIVE|RESOLVED, created_by, created_at, resolved_by, resolved_at, resolution_notes`.

Integrity (all in the database, not only in code): `CHECK` that a target exists; composite FK
`(schedule_id, activity_id) -> schedule_activities`; lifecycle `CHECK` (ACTIVE rows have no resolver, RESOLVED rows have both);
partial indexes on ACTIVE rows; RLS (project members; SELECT/INSERT/UPDATE, **no DELETE**: a blocker is history).

## No duplicated state
`BLOCKED` is never written anywhere else. It is **derived**, in bulk, by one LATERAL join (`backend/shared/workflow_flags.py`)
that every activity query uses: a blocker applies to an activity when it targets the activity, or targets its stage with
no activity. Resolving the row clears the condition everywhere at once. Progress, actuals and the baseline schedule are
never touched.

## Workflow condition precedence (deterministic)
```
REOPEN_REQUESTED  >  REWORK_IN_PROGRESS  >  QUALITY_HOLD  >  BLOCKED  >  NONE
```
It is separate from the canonical execution state (`NOT_STARTED / IN_PROGRESS / COMPLETED`, from approved actuals only):
a 100% activity on quality hold is still COMPLETED.

## QUALITY_HOLD derivation (the integration gap this fixed)
Over the activity's **required** gates (`required` and status ≠ NOT_REQUIRED), scoped to project **and** schedule version:

| Rule | Condition | Explanation string |
| --- | --- | --- |
| R1 non-conformance | any gate `FAILED` | `FAILED_GATE` (at any progress) |
| R2 pre-commencement | a `PRE_COMMENCEMENT` hold-point gate not `PASSED`/`WAIVED` | `PRE_COMMENCEMENT_HOLD_POINT_OPEN` (until released; not once completed) |
| R4 predecessor hold | a finish-to-start predecessor has an unreleased `HOLD`-category gate | `PREDECESSOR_HOLD_POINT_OPEN` (the successor cannot proceed: no pour before the rebar inspection is released) |
| R3 completion | progress ≥ 100% and a gate `PENDING`/`SUBMITTED`, **or** `quality_gate_required` with no gate recorded | `COMPLETION_PENDING_QUALITY_RELEASE` / `QUALITY_REQUIRED_BUT_NO_GATE_RECORDED` |

A pending non-hold gate on work in progress does **not** hold it (inspection follows the work). `PASSED`/`WAIVED` clear the
hold; `NOT_REQUIRED` gates and gates with `required = false` are ignored. `quality_hold_reason()` is the single pure
function used by the state engine and every explanation surface.

## API
`POST/GET /api/v1/projects/{pid}/schedules/{sid}/blockers`, `POST .../blockers/{id}/resolve`.
New permission `MANAGE_BLOCKERS` (owner, project manager, supervisor, planner). A site engineer reports a blocker in a field
report (event type `BLOCKER`); a reviewer registers it with `source_event_id`. Every raise/resolve is audited in the same
transaction (`BLOCKER_RAISED` / `BLOCKER_RESOLVED`, project hash chain).

## Verification
`tests/test_integration_workflow_state.py` (7, isolated DB): every quality rule and transition, V1/V2 isolation, stage-level
and activity-level blockers, resolve lifecycle (double-resolve → 409), governance (a site engineer cannot raise; other
projects' activities/schedules rejected), audit chain valid, no DELETE policy, condition priority.

## Also fixed while integrating
`agents/tools/activity_state.py` joined `approved_actuals` without `schedule_id` (V1/V2 mixing and row multiplication) and its
single-activity query selected non-existent columns (`aa.approved_by/approved_at`), so it silently returned nothing. Both
queries are corrected and carry the workflow flags.
