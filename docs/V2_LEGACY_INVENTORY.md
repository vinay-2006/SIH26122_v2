# Legacy demo → v2: inventory and dependency map (Phase A)

Scope: the 14 sidebar pages of the previous demo, the components they mount, every backend call they make (76 distinct client calls, extracted from
`frontend/src/api.ts`, `api/prototype.ts`, `api/intelligence.ts` and the pages), and what v2 has behind each today.

**Inspection depth is stated per row.** `read` = implementation read; `outline` = state, imports and API calls read, logic to be read in full before it is
ported (the port step re-reads it and this file is updated). Nothing here is classed from file names or sizes alone.

## 0. Architecture decision (no conflict with the constraints)

Legacy pages are NOT rewritten. In v2 mode they are mounted unchanged, and v2 serves the legacy API contract through an adapter:

* **Frontend (small, shared):** v2 mode points the legacy `apiFetch` at the v2 server with the v2 token and sets the explicit project/version context
  (`X-Project-ID`, `X-Schedule-ID`). `AuthProviderV2`/`ProjectProviderV2` already provide `useAuth()`/`useProjectState()` for the legacy pages. The sidebar becomes
  the original 14 items plus the Project Manager items. Pages themselves are untouched.
* **Backend:** `backend/v2/compat/` mounts `/api/v1/*` on the v2 app. Every request is authorised exactly like a v2 request (token → ACTIVE membership of the project
  named by `X-Project-ID` → role permission → domain-service re-check → database guards). `X-Schedule-ID` must be a version of that same project. Handlers are thin
  translators onto the v2 domain services; **no second source of truth, no legacy tables**.
* **Identifiers:** legacy `activity_id` = `external_activity_id` within the viewed schedule version; legacy `schedule_id` = v2 `version_id`; legacy `event_id` = v2
  claim id. The mapping is explicit and resolved only inside the caller's project/version; a v2 `activity_uid` never appears where a legacy id is expected.
* **Role conflicts are surfaced, not bent** (see §4).

## 1. Sidebar pages

| # | Page (route) | Legacy role gate | Components mounted | Endpoints used | v2 backing today | Gap / work | Inspected |
|---|---|---|---|---|---|---|---|
| 1 | Claim Intake (`/intake`) | CREATE_EXECUTION_EVENT | BatchUploadPanel, StatusBadge, ExecutionStateBadge, ReopenRequestModal, tabs batch/text/voice/file; Web-Speech voice (browser API) | claims/text, claims/file, claims/schedule-export, claims/{id}/match, /check, /clarify, /{id}; schedules/{v}/activities; quality-gates; impact | claim submit (needs picked activity), matching adapter (done), documents/extract | text→extraction (legacy `extract_claim_fields`, rules fallback) → claim; clarification copilot (REPORTED + ASKED); match/check endpoints; file/photo/P6-export/batch intake; quality-gate + impact reads | read (pipeline, state) |
| 2 | Issues & Delays (`/issues`) | REPORT_ISSUE / MANAGE_BLOCKERS | evidence upload, memory suggestions | issues (categories, report, list, resolve, evidence), memory/for-issue | issues domain exists | thin contract adapter; memory suggestions needs memory search | outline |
| 3 | My Updates (`/updates`) | CREATE_EXECUTION_EVENT | decision/notification feed | my-claims, notifications, read, read-all | notifications + my-claims exist | thin contract adapter | outline |
| 4 | Daily Digest (`/digest`) | REVIEW_CLAIM | calendar, bulk approve | digest, digest/bulk-approve | none | port digest query on v2; bulk approve = per-claim `decide` (each audited and re-verified) | outline |
| 5 | Review Workspace (`/review`) | REVIEW_CLAIM | ConfidenceBar, Provenance/SourceReference cards, WBSSplitEditor (read), ReopenRequest/Review modals, QualityGateModal, CompoundImpactModal, AskWhyPanel | review-queue, claims/{id}, candidates, conflicts, validation, decisions, splits, reopen-requests, quality-gates, impact, graph/explain | queue + decision exist (basic UI) | contract adapter for all; candidates (done in DB); reopen + quality-gate semantics (approved: D7, D8); splits are read-only (owner decision 3) | outline |
| 6 | Time Agent (`/time-agent`) | REVIEW_CLAIM | chat + voice | claims/text, match, check, schedules/activities | none | **role conflict, see §4** | outline |
| 7 | Dashboard (`/dashboard`) | REVIEW_CLAIM | ProjectProgressPanel, ScheduleImportModal, forecast, silent activities, delay reasons, memory, exec summary | dashboard/{delay-reasons, forecast, institutional-memory}, alerts/silent-activities, decisions, digest, execution-summary, export/csv, dashboard (project) | `dashboard/summary|wbs|stages|disciplines` | port forecast / silent / delay reasons / summary / csv on v2 ledgers | outline |
| 8 | Activity History (`/history`) | REVIEW_CLAIM | ImageLightbox, evidence, audit tab | activities (list), activities/{id}/history, audit | `activities/{uid}/timeline`, audit | contract adapter + history shape (claims, decisions, evidence, reopen) | outline |
| 9 | Impact Preview (`/impact`) | REVIEW_CLAIM | ImpactNetworkGraph, ImpactTimelineView, ImpactTable | schedule/{id}/impact-preview, impact/preview, scenarios, watchlist | `schedule_dependencies` only | port `impact_service` (CPM / ripple) onto v2 dependencies | outline |
| 10 | WBS Explorer (`/wbs`) | VIEW_SCHEDULE | WBSActivityExplorer | schedules/{v}/wbs-tree, activities, quality-gates, reopen-requests | version wbs + activities | contract adapter | outline |
| 11 | AI Execution Summary (`/summary`) | REVIEW_CLAIM | period/discipline/language filters | reports/execution-summary, reports/translate | none | port; LLM only under the existing opt-in, deterministic otherwise | outline |
| 12 | Root Cause & Memory (`/root-cause`) | MANAGE_BLOCKERS | root-cause groups, memory browse/record | issues root-cause-analysis, root-causes, memory browse/record | root causes + memory (list/promote) | contract adapter + browse/record + analysis | outline |
| 13 | Project Intelligence (`/intelligence`) | VIEW_PROJECT | briefing, ask | agent/briefing, agent/query (+ findings, review-queue) | none | port supervising agent on v2 data (read-only) | outline |
| 14 | Audit Trail (`/audit`) | VIEW_AUDIT | verification, dossier download | audit, dossier, dossier/audit-verification | `audit`, `audit/verify` | contract adapter + dossier port | outline |

## 2. Backend services behind those pages (legacy implementation → v2 plan)

| Legacy | Size | Reads (legacy tables) | v2 plan |
|---|---|---|---|
| `routers/intake.py` (+ `shared/llm_extraction`, `rule_extraction`, `tabular_extraction`, `uploads`) | 1119 | execution_events, source_documents | reuse extraction modules unchanged; new domain `file_report` (REPORTED / clarification ASKED) |
| `routers/matching.py` | 1574 | — | **done** (adapter + 33 tests); splits stay read-only |
| `routers/checks.py` | 3172 | validation_issues, conflict_records, candidate_matches, evidence_links | `/check`: validation + conflicts on v2 tables; priority already stored |
| `routers/decisions.py`, digest | 542 | planner_decisions | `decide` (exists); digest + bulk wrapper |
| `routers/dashboard.py`, `summary.py`, `reports.py`, `export.py` | 668/681/228/278 | approved_actuals, execution_events | rewrite queries onto approved ledgers; reuse formula/text code |
| `services/impact_service.py`, `routers/impact.py`, `schedule.py` | 628/153/395 | schedule_dependencies | reuse algorithm, feed v2 dependencies |
| `services/quality_service.py`, `routers/quality.py` | 467/214 | quality tables (legacy) | **new additive tables (D8)** |
| `services/reopen_service.py`, `routers/reopen.py` | 245/195 | approved_actuals | **append-only correction model (D7)** |
| `agents/*`, `routers/agent.py` | 393/135 | views over legacy data | read-only; feed v2 data; no new model |
| `dossier/*`, `routers/dossier.py` | 148 | audit_logs | v2 audit chain |
| `memory/*`, `routers/memory.py` | 118 | institutional_memory | v2 table exists; add search |
| `services/batch_intake_service.py`, `routers/batches.py` | 444/66 | upload_batches | v2 table exists; port |
| `routers/graph.py`, `claim_graph.py`, `investigation.py` | 326/278/272 | dependencies, claims | port read-only |
| `routers/mock_p6.py` | 78 | — | excluded (mock push target) |
| stages / work_packages / contractors | — | legacy stage tables | **R15 deferred** — proposal only |

## 3. Dependencies and infrastructure

* Embeddings: sentence-transformers + FAISS present, model cached locally (matching already uses it).
* LLM: optional. `extract_claim_fields` uses the configured provider when present, otherwise the deterministic rule fallback (`EXTRACTION_FALLBACK=rules`).
  Live provider calls are never made by tests (opt-in `SETUAI_ALLOW_LIVE_LLM=1`). No new model is introduced.
* OCR / vision for scanned images: legacy path needs the configured vision model; without it the legacy code raises its own "OCR unavailable" error — preserved, and
  documented as an environment dependency.
* Voice-to-text: the browser's Web Speech API (client-side). Works wherever the original works; audio-file upload is stored as evidence.

## 4. Conflicts that need a decision (only genuine ones)

1. **Time Agent files claims as a reviewer.** In the legacy demo a REVIEW_CLAIM holder (supervisor/planner/PM/owner) could file claims through the Time Agent chat.
   In v2 only a Site Engineer files a claim, and the person who decides a claim must not be its author (database guard). Proposal that keeps the capability:
   the Supervisor's Time Agent conversation drafts the claim and lets them **hand it to a Site Engineer of the project** (a notification + a pre-filled report),
   or lets them record **issues** directly. The Supervisor never files a claim they decide. (Will be confirmed when the Time Agent page is read in full.)
2. **UNMATCHED status.** v2 has no UNMATCHED claim status; legacy pages expect it. Adapter reports `UNMATCHED` for an EXTRACTED claim that has no activity and a
   recorded `NO_AUTOMATIC_MATCH`; the database status stays EXTRACTED.
3. **Percent-based approved actuals vs the quantity ledger.** Legacy decisions carry `approved_pct`/`approved_qty`; v2 approves per measured assignment. The
   adapter maps the legacy decision form onto the v2 methods (`PERCENT_AS_CLAIMED`, `QUANTITIES_AS_CLAIMED`, edit with explicit quantities) without changing v2 math.
