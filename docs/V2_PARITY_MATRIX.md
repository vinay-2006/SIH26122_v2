# SetuAI v2 — legacy-to-v2 feature parity matrix

Reference: the legacy demo (`backend/routers`, `backend/services`, `backend/shared`, `frontend/src/pages|components`), which stays untouched.
Target: v2 (`backend/v2`, `frontend/src/v2`, migrations 0001–0014).

**Status vocabulary:** `complete` · `partial` · `missing` · `incompatible` (conflicts with a v2 rule; capability must be adapted, not copied) · `excluded` (intentionally not carried over, with the reason).

**Honesty note.** Statuses are from code inspection (routes, tables, pages, tests). A row becomes `complete` only after an end-to-end test; until
then it keeps the status it earned. The "Verified" column is updated as batches land.

Decisions fixed by the owner: (1) additive nullable `asset_tag`; (2) planned quantity/UOM derived from the explicitly designated progress-measuring assignment, never summed or converted, `NULL` when absent or ambiguous; (3) NO automatic WBS split allocation — a claim is decided against a single activity and the PM/Supervisor assigns the child.

## 1. Matching and claim intake

| # | Legacy feature (implementation) | v2 today | Status | Integration work | Role | Depends on | Acceptance test | Verified |
|---|---|---|---|---|---|---|---|---|
| M1 | 4-tier cascade `match_claim` (`routers/matching.py`: EXACT_ID → EXACT_ASSET → HYBRID_FALLBACK → HARD_MISMATCH) | Not present; v2 only has manual `rematch_claim` | complete (backend) | Adapter feeds v2 active-version activities to the unchanged functions | system | `baseline_activities`, `candidate_matches` | Golden `sample_data/expected/matching_results.json` reproduced; legacy-vs-adapter output equal | backend: tests/v2_matching (33), engine parity vs legacy + pinned snapshot |
| M2 | Scoring (0.50 sem + 0.25 fuzzy + 0.15 location + 0.10 discipline + adjustments), ambiguity < 0.05, MATCHED > 0.40 | none | complete (backend) | reuse as is | system | – | same as M1 | backend: tests/v2_matching (33), engine parity vs legacy + pinned snapshot |
| M3 | Semantic retrieval (FAISS `schedule_index`, all-MiniLM-L6-v2) | none | complete (backend) | Per-project/per-version in-memory index; same model, same text builder; no persistence | system | faiss, sentence-transformers present, weights cached | semantic golden case; two projects never share candidates | backend: tests/v2_matching (33), engine parity vs legacy + pinned snapshot |
| M4 | Candidate persistence (top 3, rank, tier, 4 sub-scores, signals) | table `candidate_matches` exists, never written; `get_claim` reads it | complete (backend) | write on submit/rematch | system | `candidate_matches` | rows == engine output | backend: tests/v2_matching (33), engine parity vs legacy + pinned snapshot |
| M5 | Eligibility filter (completed / other schedule / stage) | none | complete (backend) | eligibility from v2 ledger state (activity at 100% / finished) of the ACTIVE version only | system | ledgers | completed activity never offered | backend: tests/v2_matching (33), engine parity vs legacy + pinned snapshot |
| M6 | Exact asset tier needs `schedule_activities.asset_tag` | no column | complete | additive migration 0015 `baseline_activities.asset_tag` (nullable) + importer column alias | PM (import) | schedules importer | asset golden case | migration 0015 + importer alias; test_asset_tag_is_imported_and_drives_the_exact_asset_tier |
| M7 | Quantity signal needs planned qty/UOM | per-assignment `baseline_qty` | complete | derive from the single `measures_progress` assignment, else NULL | system | `baseline_resources` | ambiguous/none → NULL, no guess | test_planned_quantity_comes_only_from_the_single_measuring_assignment |
| M8 | Re-run match (`POST /claims/{id}/rematch`) | `rematch_claim` = manual pick only | complete (backend); UI pending | add automatic re-match; keep manual override | Supervisor | claims domain | auto rematch refreshes candidates; manual override audited | test_supervisor_manual_override_and_explicit_automatic_rematch |
| M9 | Automatic match must not approve or touch progress | n/a | complete (backend) | matching writes only `candidate_matches` + `matched_activity_uid`/status | system | ledgers | ledger row count unchanged after matching | backend: tests/v2_matching (33), engine parity vs legacy + pinned snapshot |
| M10 | Candidate review (`ReviewWorkspace`, `ConfidenceBar`, explanations) | review queue + decision panel, no candidates UI | missing | candidate list in claim detail (reuse `ConfidenceBar`) | Supervisor | M4 | UI shows ranked candidates; pick = manual override | – |
| M11 | WBS split allocation (`wbs_split.py`, `claim_activity_splits`, `WBSSplitEditor`) | table exists; rule: single activity only | excluded | none — owner decision 3 | – | – | – | n/a |
| I1 | Free-text report → structured claim (`/claims/text`, LLM + rules fallback) | form requires an activity pick; document extraction is deterministic (CSV/XLSX/PDF/TXT) | partial | text intake endpoint using existing rule extraction, then M1; LLM only under `SETUAI_ALLOW_LIVE_LLM=1` | Site Engineer | `backend/v2/extraction.py`, legacy `rule_extraction.py` | text with/without activity id lands MATCHED/EXTRACTED correctly | – |
| I2 | File upload extraction (`/claims/file`: txt/csv/xlsx/pdf/image) | `documents/{id}/extract` | partial | route extracted lines through M1 when no activity cited | Site Engineer | documents service | uploaded report claims get candidates | – |
| I3 | Batch upload (`BatchUploadPanel`, `/claims/batch`) | `upload_batches` table, no route/UI | missing | batch endpoint + panel | Site Engineer | batches | N files → N claims, one batch record | – |
| I4 | Schedule-export / P6 claim import (`/claims/schedule-export`, `P6SyncStagingModal`, mock P6) | PM schedule import (XER/MSPDI/CSV) | excluded (mock P6 push) / complete (import) | none | – | – | – | n/a |
| I5 | Clarification (`/claims/{id}/clarify`) | request + answer endpoints, UI | complete (to verify E2E) | – | SE/Sup | – | existing e2e | existing |
| I6 | Photo evidence, `ImageLightbox`, EXIF | evidence store, EXIF, upload | partial | lightbox on claim detail | SE/Sup | documents | – | – |
| I7 | Field provenance badges (`FieldProvenanceBadge`, `SourceReferenceCard`) | `field_provenance` JSON, `source_references` table | partial | show badges on claim detail | Sup | – | – | – |
| I8 | Voice transcript / language detection / translation (`/translate`) | channel enum only | missing | translate reuse (opt-in LLM) | SE | LLM opt-in | – | – |

## 2. Review, decisions, progress

| # | Legacy feature | v2 today | Status | Work | Role |
|---|---|---|---|---|---|
| D1 | Approve / edit / reject / hold, with justification | `decisions.decide` + `DecisionPanel`, DB guards, idempotency | complete | – | Sup |
| D2 | Decision preview ("what would change") | `decision-preview` | complete | – | Sup |
| D3 | Review queue with priority | `review-queue` (priority_score) | complete | – | Sup |
| D4 | Conflicts (`/claims/{id}/conflicts`) | `conflict_records` written on submit; not surfaced | partial | show in claim detail | Sup |
| D5 | Validation findings | `claim_validations`, shown | complete | – | Sup |
| D6 | Daily digest + bulk approve (`DailyDigest`) | none | missing | digest by date; bulk approve through `decide` one by one (each audited, server-side re-verified) | Sup |
| D7 | Governed reopen (`reopen*`, `ReopenRequestModal`, `ReopenReviewModal`) | ledger is append-only; later approvals supersede; no reopen workflow/table | incompatible → adapt | design: reopen as an auditable decision type that appends a ledger entry; needs owner approval (touches ledger rules) | SE request / Sup decide |
| D8 | Quality gates / ITP / hold points (`quality.py`, `QualityGateModal`) | no tables | missing | additive tables + gate check inside `decide`; changes approval rules → needs owner approval | Sup |
| D9 | Approved actuals per activity | quantity ledgers + `activity_progress_as_of` | complete (different model, by design) | – | all |
| D10 | Legacy percent-based `approved_actuals` | replaced by quantity ledger | excluded | – | – |

## 3. Dashboards, reporting, intelligence

| # | Legacy feature | v2 today | Status | Work | Role |
|---|---|---|---|---|---|
| R1 | Project dashboard summary / SPI | `dashboard/summary` (+ OverviewPage) | complete | – | all |
| R2 | WBS / stage / discipline progress | `dashboard/wbs|stages|disciplines`, `WBSActivityExplorer` legacy component not used | partial | WBS explorer page on v2 data | all |
| R3 | Activity history (`ActivityHistory`, `/activities/{id}/history`) | `activities/{uid}/timeline`, ActivitiesPage | partial | timeline view | Sup/PM |
| R4 | Delay reasons, institutional memory panel | issues/root causes/memory endpoints | partial | dashboard panels | Sup |
| R5 | Forecast (`/dashboard/forecast`) | none | missing | reuse forecast on v2 ledgers (read-only) | Sup/PM |
| R6 | Silent-activity alerts | none | missing | reuse, read-only | Sup |
| R7 | CSV export (`/export/csv`) | none | missing | export approved progress for the project (no claim text for PM) | Sup/PM |
| R8 | AI execution summary + translate (`AIExecutionSummary`) | none | missing | opt-in LLM, deterministic fallback | Sup |
| R9 | Impact preview / compound impact / ripple graph (`ImpactPreview`, `CompoundImpactModal`, `PrecedenceRippleGraph`) | `schedule_dependencies` exist; no service | missing | port `impact_service` onto v2 dependencies (read-only) | Sup |
| R10 | Knowledge graph / ask-why (`claim_graph`, `AskWhyPanel`) | none | missing | reuse on v2 data | Sup |
| R11 | Supervising agent (`agent.py`, `ProjectIntelligence`) | none | missing | read-only findings; LLM opt-in | all members |
| R12 | Audit dossier | none | missing | export of hash-verified audit | Sup/PM |
| R13 | Audit trail + chain verification | `audit`, `audit/verify`, AuditPage | complete | – | Sup/PM |
| R14 | Notifications / my updates | `notifications`, `my-claims` | complete | – | all |
| R15 | Work packages / contractors / stages CRUD | none (stages derived from WBS rules) | missing | additive tables; scope to be confirmed | PM |

## 4. Issues, root causes, memory

| # | Legacy feature | v2 today | Status |
|---|---|---|---|
| S1 | Issues (create/list/resolve/evidence) | complete in v2 (`issues` router + IssuesPage) | complete |
| S2 | Blockers derived | `blockers` | complete |
| S3 | Root causes + analysis | create/group; analysis view | partial |
| S4 | Memory record/search/for-issue | list + promote; no search | partial |

## 5. Schedule, projects, access

| # | Legacy feature | v2 today | Status |
|---|---|---|---|
| P1 | Schedule upload/versions/activate | PM pipeline, compare, rollback | complete (superior) |
| P2 | WBS tree / dependencies | `schedule-versions/{id}/wbs`, `schedule_dependencies` | complete |
| P3 | Projects CRUD, members, archive | complete | complete |
| P4 | Roles planner/owner/admin | three roles only | excluded (owner constraint 9) |
| P5 | Local sign-in | `local-login` dev-only | complete |
| P6 | `X-Project-ID` / `X-Schedule-ID` headers | path project id + ACTIVE version | excluded (isolation model) |

## 5b. Findings from the matching regression (reported, nothing adjusted)

* `sample_data/expected/matching_results.json` records tier names (`TIER_2_ASSET_TAG`, `TIER_3_SEMANTIC`...) and confidences (0.92, 0.86, 0.12) that the existing
  engine does not produce (it returns `EXACT_ASSET` 0.875, `HYBRID_FALLBACK` ~0.74, ...). The file is therefore compared on the OUTCOME it describes (which
  activity, matched / ambiguous / unmatched); the engine's real numbers are pinned in `tests/v2_matching/legacy_snapshot.json`, generated by the legacy path.
* MATCH-005 (no matching activity): golden says no candidates; the engine keeps three low-confidence guesses (best 0.189) as candidates, never matched. The
  legacy rule also labels it "ambiguous" (rank-1/rank-2 gap 0.0016 < 0.05) although both are far below 0.40 — status is UNMATCHED either way.
* Free-text activity id scores 0.97 (documented in the engine), 1.0 only for an id reported as a field.
* v2 has no UNMATCHED claim status: an unmatched claim stays EXTRACTED with no activity (the legacy stored its best guess on the claim; v2 keeps it only as a
  candidate so a Supervisor must choose explicitly).

## 6. Matching-adapter design (Phase B)

* `backend/v2/matching/` — `adapter.py` (v2 → legacy dict shapes), `index.py` (per-project/per-version FAISS using the legacy `build_searchable_text` and model), `service.py` (orchestration, persistence).
* Project + ACTIVE version scoping happens in SQL; the engine never sees another project's activities.
* The legacy functions (`match_claim`, tiers, scoring) are imported and called unchanged; the legacy orchestration (`run_claim_match`) is re-implemented only for data access — same ambiguity (< 0.05) and MATCHED (> 0.40) rules, same top-3 persistence.
* Candidate ids: deterministic per (claim, activity) from the engine; `candidate_matches.activity_uid` maps via `external_activity_id`.
* Matching writes `candidate_matches`, `matched_activity_uid` and status only. It never calls the decision/ledger code.

## 7. Open items needing owner approval before implementation

* D7 governed reopen — touches the append-only ledger rules.
* D8 quality gates / ITP — adds an approval precondition.
* R15 work packages / contractors / stages tables — scope.
