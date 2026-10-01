# V7 Canonical Dataset Specification

Status: the **baseline build** (sections 1-12) is implemented and verified on the isolated integration database. Sections marked **PENDING** are designed but not yet built or proven. Nothing in this document was applied to the shared Supabase database.

Build: `SETUAI_ALLOW_DB_TESTS=1 SETUAI_TEST_ENV=integration DATABASE_URL=postgresql://postgres@127.0.0.1:54329/setuai_integ PYTHONPATH=. python -m backend.canonical.build`
(guarded like the test-suite: refuses any non-isolated database; idempotent - re-running on an existing project only reports `EXISTS`).
Code: `backend/canonical/dataset.py` (pure derivations), `backend/canonical/build.py` (drives the real HTTP API with real HS256 JWTs).
Tests: `tests/test_canonical_dataset_definition.py` (DB-free), `tests/test_integration_canonical_dataset.py` (integration).

## 1. Purpose and source of truth
One realistic project, built from the repository's own benchmark under `sample_data/`, exercising every V7 rule end to end. **No schedule was invented.** `sample_data/canonical/schedule.csv` is used verbatim as the executing baseline; everything else is a documented, deterministic derivation.

## 2. Project
`SIH26122-NFU` - *North Field Utility Corridor (Pump Station 3 Tie-In)*, 2026-08-10 .. 2026-08-22, created through `POST /api/v1/projects` by the Project Manager. A sibling control project `SIH26122-NFU-B` (owned by the "outsider" identity) exists solely to prove isolation.

## 3. Schedule versions
| Version | Role | Source | Activities | Dependencies |
|---|---|---|---|---|
| REV-A | historical, inactive (data date 2026-07-27) | derived from REV-B by `REV_A_CHANGES` | 44 | 34 |
| REV-B | active executing baseline (data date 2026-08-10), supersedes REV-A | `canonical/schedule.csv` verbatim | 45 | 34 |

`REV_A_CHANGES` (in `dataset.py`): drop `HSE-PS3-AUD-001` (Rev A had no close-out audit activity), shift `CIV-PS3-FND-003` and `ELE-PS3-CBL-001` earlier by 2 and 3 days, shorten `PIP-PS3-WLD-024` by one day. Rev A carries **no execution history** - it exists to prove revision comparison and that history never leaks across versions.

## 4. Network
REV-B: 45 activities, 34 dependencies: FS 18, SS 13, FF 2, SF 1; lags -1..+5 days; float known on every activity; 8 critical activities (`CIV-PS3-FND-001/002/003`, `PIP-PS3-HDR-100`, `-A`, `-B`, `-C`, `ELE-PS3-CBL-001`). Multiple predecessors and both critical and non-critical chains are present. Parsed and stored by the real importer (`POST /projects/{pid}/schedules`).

## 5. WBS and stages
Six stages = WBS level-1 branches of the source: Civil, Piping, Static/Rotating Equipment, Electrical, Instrumentation, HSE (created per schedule version, 12 rows). **Stage weights are derived**, not typed: each stage's share of the summed planned durations (days), rounded to 2 dp with the last stage absorbing rounding so the total is exactly 100.00 (Civil 15.45, Piping 22.75, Static/Rotating 9.01, Electrical 20.60, Instrumentation 12.88, HSE 19.31). Activity weight = duration in days (`weight_factor`).
Nested WBS (e.g. `1.02.02` with children `.01/.02/.03`) is kept in `schedule_activities.wbs_code`; the hierarchy is derived from the dotted code (`GET /schedules/{id}/wbs-tree` groups by code). A separate WBS table is deliberately **not** added: stages carry weights/progress, wbs_code carries granularity. **PENDING:** a derived WBS endpoint with rolled-up progress.

## 6. Contractors and work packages
| Contractor | Work packages |
|---|---|
| Apex Civil & Foundations | WP-CIV (Civil), WP-HSE (HSE) |
| Bharat Mechanical & Piping | WP-PIP (Piping), WP-EQP (Static/Rotating) |
| Coastal Electrical & Instrumentation | WP-EI (Electrical), WP-EI-INS (Instrumentation) |

Every activity of both versions is attributed (stage + work package + derived contractor) through `PATCH .../activities/{id}/attribution` - 89 attributions, none NULL.

## 7. Quality, ITP, hold points
One ITP per stage (REV-B) and **one gate per activity (45)** derived from `activity_master.Inspection_Hold_Point`. `Safety_Critical = Yes` -> `HOLD`; `CIV-PS3-FND-002` is an explicit hold override (pre-pour rebar/anchor check, type `INSPECTION`: it follows the rebar work and releases the pour `FND-003` through rule R4, it does not block FND-002 itself); other gates are `WITNESS`. Gate type is derived from the hold-point text (NDT, WELD_INSPECTION, CLIENT_APPROVAL, TEST, SAFETY_AUDIT, INSPECTION). Rules: a failed required gate holds; an unreleased pre-commencement hold blocks the start; a finished activity with an unreleased required gate awaits quality release; **R4** - a finish-to-start successor is held while a predecessor's HOLD gate is unreleased (SS/FF/SF successors are not held; completed work is never un-completed).

## 8. Baseline history
Field reports 2026-08-11..15 (`input/progress-report-csv`, 32 rows) are replayed through the **production path**: schedule-export upload (site engineer) -> match -> check (planner) -> human APPROVE (supervisor). Result: 32 claims, 32 approved, 0 refused, 13 distinct approved actuals (later reports supersede earlier ones), 10 activities at 100 %. Project progress 22.5 % (stage-weighted). For the ten completed activities the quality inspector records evidence and passes 9 gates; the WLD-024 gate is left `SUBMITTED` (RT result outstanding) so the activity is **COMPLETED + QUALITY_HOLD** - the seed of the reopen/rework story. Approvals refuse completed activities (`REOPEN_NOT_ALLOWED`) - none were needed here.

## 9. Operations data
* Blocker (`MATERIAL`) on `INS-PS3-FGS-001` - detector heads held at customs -> derived workflow condition **BLOCKED**.
* Impact scenarios persisted by the real engine: "Cable drum slips 3 days" (`ELE-PS3-CBL-001`), "Header spool B fit-up delay" (`PIP-PS3-HDR-100-B`).

## 10. Institutional memory
Five records, all prefixed `[SYNTHETIC DEMO RECORD]` (memory has no write API; provisioned by SQL): trench flooding, weld RT rejection, late cable drum, an incident whose narrative is a **prompt-injection string** (must be surfaced as data only), and one private incident in the sibling project (must never appear in NFU).

## 11. Identities (real HS256 JWTs, `SUPABASE_JWT_SECRET` = test secret in the builder)
`owner` OWNER, `pm` PROJECT_MANAGER, `planner` PLANNER, `supervisor` SUPERVISOR, `engineer` SITE_ENGINEER, `inspector` QUALITY_INSPECTOR, `auditor` AUDITOR (all members of NFU) and `outsider` (sibling project only). auth.users/profiles/memberships are the only SQL-provisioned records (no membership API exists); the PM's own membership is created by the project API.

## 12. Verified invariants (integration test)
2 versions / 1 active; 45 / 44 activities, 34 dependencies, 6 stages per version; no unattributed activity; stage weights = 100.00 per version; exactly 45 gates on REB-B; every approved actual has a human decision; audit chain `VALID` (V7 records only, 0 legacy) for the auditor; role permission matrix; outsider 403/404, missing token 401, expired token 401; WLD-024 = COMPLETED/QUALITY_HOLD; `INS-PS3-FGS-001` = BLOCKED; injection text stored only as data and no gate changed by it; rebuild is a no-op.

## 13. PENDING - live E2E reports (2026-08-18..22)
The ten required paths on top of the baseline: exact match; semantic/fuzzy; completed-activity exclusion (`PIP-PS3-WLD-024`); conflicting evidence (`ELE-PS3-TR-005` 60 % vs 70 %); quality hold (`CIV-PS3-FND-003` pour while the FND-002 rebar hold is open - rule R4); delay/impact (`ELE-PS3-CBL-001`, `PIP-PS3-HDR-100-B`); reopen/rework/revision (WLD-024 after RT rejection); contractor attribution; ambiguous chainage (MATCH-004); unmatched rain-shelter report (MATCH-005); memory retrieval. These are exercised in the Phase T E2E, not preloaded.

## 14. Known limits
* Dates are absolute (Aug 2026): any CURRENT_DATE-based feature (silent-activity alerts) treats the data as stale unless the clock is adjusted; a date-offset option is not built.
* `execution_events` are only produced from the schedule-export path in the baseline (no LLM). LLM extraction is exercised in the live E2E and is optional (`EXTRACTION_FALLBACK=rules`).
* `activity_master.Status` (22 "Complete") describes the *end* of the dataset window and is deliberately **not** copied into approved actuals; only replayed, human-approved reports create progress.
* The source XER contains no TASKPRED table; dependencies come from the CSV. The exporter writes TASKPRED so an exported XER round-trips its logic.

## 15. Safety
Runs only against the guarded isolated DB. The shared Supabase database was not read from or written to by the builder.

## 16. Rebuild / reset
`scripts/integration_db.sh reset && scripts/integration_db.sh up && scripts/integration_db.sh migrate` then run the builder (local isolated DB only).

## 17. Related documents
`V7_ROUTE_CONTEXT_REMEDIATION.md`, `V7_BLOCKED_STATE_DESIGN.md`, `V7_MIGRATION_013_RELEASE_REPORT.md`, `V7_PRE_E2E_HARDENING_REPORT.md`.
