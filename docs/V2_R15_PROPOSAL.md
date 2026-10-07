# R15 — Contractors, work packages and stage-linked scope: scoped proposal

**Status: PROPOSAL ONLY. Nothing in this document is implemented. It waits for approval (decision R15 is deferred).**
Prepared from the original code; every claim below was read in the repository, not assumed.

## 1. What the original demo has

| Area | Where | What it does |
|---|---|---|
| Schema | `backend/models/migrations/005_v7_contractors_work_packages.sql` | `contractors` (project-scoped: code, company name, category, contract reference, contact e-mail, status ACTIVE/INACTIVE/SUSPENDED) and `work_packages` (project-scoped: optional contractor, optional stage, code, name, discipline, planned dates, status). `activities` carry optional `contractor_id` / `work_package_id`. |
| API | `routers/contractors.py`, `routers/work_packages.py` (+ `repositories/*_repo.py`, services) | create / list / get / patch for each, inside a project context. |
| Screens that **display** them | `components/WBSActivityExplorer.tsx` (chips + free-text search by contractor / package), `pages/ReviewWorkspace.tsx` (chips under the selected activity, "Contractor / Work Package" lines in the candidate breakdown, activity picker label) | read-only display of `contractor_name` / `work_package_code` on an activity. |
| Screens that **manage** them | none found in `frontend/src` | the original UI never created or edited a contractor or a work package; it only showed what the seed/API had stored. |
| Other consumers | `agents/tools/contractor.py`, `agents/context_builder.py`, `memory/*`, `dossier/*` | the supervising agent, institutional memory and the dossier can mention the contractor of an activity. |

## 2. What v2 has today

* Stages exist (WBS `STAGE` nodes of the schedule version; the stage tree, stage progress and stage state are served in the legacy shape).
* Contractors / work packages: **no tables**. The legacy read model returns `contractor_id`, `work_package_id` as NULL, so the original screens render "Not assigned" and the chips do not appear. No screen breaks.
* Nothing in matching, approval, progress or the ledgers depends on contractors or work packages (they never did in the original either: they are descriptive).

## 3. Proposed scope (smallest useful slice)

1. **Additive migration** (no change to existing tables):
   * `contractors(contractor_id, project_id, contractor_code, company_name, category, contract_reference, contact_email, status, created_at)` — unique `(project_id, contractor_code)`.
   * `work_packages(work_package_id, project_id, contractor_id?, package_code, package_name, discipline?, planned_start?, planned_finish?, status, created_at)` — unique `(project_id, package_code)`.
   * `activity_assignments(project_id, version_id, activity_uid, contractor_id?, work_package_id?, assigned_by, assigned_at)` — **append-only**, newest row wins, so an assignment never rewrites history and is tied to the schedule version it was made on. (Activities stay immutable baseline rows; assignment is a separate fact.)
2. **Permissions** (server-enforced, RLS + service check, mirroring the existing posture tests): Project Manager creates / edits contractors, work packages and assignments; Supervisor and Site Engineer read them; no role can see another project's rows.
3. **Read model**: `lrm.schedule_activities` returns the latest assignment, so the **existing** WBS Explorer and Review Workspace chips light up with **no frontend change**.
4. **API**: the original `contractors` / `work-packages` routes behind the compat layer (same shapes), plus audit entries for every change.
5. **PM UI**: one small page under Project Settings for managing contractors / packages and assigning them to activities (the original had no management UI, so this is the only new screen, and it is optional — assignments could also be loaded by CSV on schedule import).
6. **Tests**: RLS posture for the three tables, isolation between projects, append-only assignment history, and the two original screens showing the chips in the browser.

## 4. Explicitly out of scope

* No automatic assignment of activities to contractors or packages (no inference from text, discipline or WBS).
* No WBS split / allocation of one claim across activities (owner decision 3 stands).
* No contractor-level progress, payment, or performance scoring.
* No change to matching, scoring, approval or ledger behaviour.
* Stage dependencies / gates beyond what v2 already serves.

## 5. Decisions needed from the owner

1. Approve the slice above (or trim it: e.g. skip the PM management page and load assignments by CSV only).
2. Should assignments be **per schedule version** (proposed: yes, so a revised schedule can reassign without touching history) or **per project** (simpler; survives version changes automatically)?
3. May the Supervisor edit assignments, or is that Project-Manager-only (proposed: Project Manager only)?
4. Is the supervising agent allowed to cite a contractor in its briefing (proposed: yes, read-only fact)?

Estimated size once approved: one migration, one domain module, one compat router, one PM settings panel, ~25 tests. No existing file's behaviour changes.
