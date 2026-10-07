# SetuAI v2 — legacy-to-v2 parity matrix (evidence-based)

Status: **restored** = original page/engine runs on v2 data and was exercised end to end · **adapted** = restored with a stated v2 rule change · **deferred** · **excluded**.
Evidence key: BE = backend tests (`tests/v2_*`), E2E = real-browser scenario in `frontend/e2e/scenarios/*.mjs` (28/28 passing at last run).
Reuse: pages are the ORIGINAL components; backend uses the ORIGINAL functions through `backend/v2/compat` (legacy read model `lrm`, migrations 0016/0019). New code is adapters only unless noted.

| Area | Original implementation reused | Status | Evidence | Notes |
|---|---|---|---|---|
| Matching (tiers, scoring, FAISS) | `routers/matching.py`, `schedule_index` unchanged | restored | BE tests/v2_matching (golden outcome + pinned snapshot) | golden files untouched; label/confidence differences in golden are reported in earlier notes |
| Claim Intake (text/voice/file/batch/P6 export, Field Copilot) | `ClaimIntake`, `BatchUploadPanel`, `llm_extraction` | restored | BE v2_compat intake/batch; E2E Engineer ×7 | |
| Issues & Delays, My Updates, WBS Explorer | original pages | restored | BE; E2E Engineer | |
| Review Workspace (candidates, decisions, Ask Why) | `ReviewWorkspace`, `AskWhyPanel`, `claim_graph.explain_activity` | restored | BE test_supervisor_review, test_agent_dossier; E2E Supervisor review | seeded claims have no stored candidates (shows "Unmatched" panel) |
| Overrun / short-close acknowledgement | not in original | adapted (new minimal control) | E2E overrun scenario; BE decisions | appears only when server refuses; audited; never approves by itself |
| Daily Digest + bulk approve | `DailyDigest`, digest routes | restored | BE; E2E digest scenario | bulk approves VALIDATED only (original rule) |
| Dashboard, forecast, silent activities, delay reasons, CSV, Phase-7 AI summary | original queries/`summary.py` | restored | BE test_dashboard_reports; E2E page-load + CSV | |
| Activity History, Impact Preview (+ ripple), Execution Summary | original engines | restored | BE test_impact, dashboard_reports; E2E page loads (no failed calls) | PM also gets Impact read-only |
| Root Cause & Memory | original page/ranking | restored | BE; E2E page load | group/record actions not exercised in browser |
| Project Intelligence agent | `agents/*` context builder via seam | restored | BE test_agent_dossier; E2E | deterministic without `SETUAI_ALLOW_LIVE_LLM=1` |
| Audit Trail + dossier | original page; dossier sections rebuilt on v2 (`dossier_api`) | restored (dossier built on original section structure, not original code) | BE; E2E download | PM gets structure + chain, claim sections RESTRICTED |
| Knowledge graph, activity graph, investigation | original `claim_graph`, `graph`, `investigation` | restored (API); no UI caller in original | BE | |
| Mock P6 | original `mock_p6` handler | restored (dev-only) | BE; E2E push | |
| Time Agent | original page | adapted: Supervisor drafts are handed off to the Site Engineer (migration 0020) | BE; E2E hand-off | supervisor chat answers are canned text in the original — NOT backed by data (needs approval) |
| Governed reopen (D7), quality gates/ITP (D8) | original modals/services | restored as approved models | BE test_governed_reopen, test_quality_gates | |
| Project Manager role | v2 pages + original read-only pages | restored | BE; E2E PM ×6 | no claim content on any PM route (UI + API checked) |
| Isolation, sessions, suspension | — | restored | E2E Isolation ×7; BE v2_hardening | |
| Contractors / work packages (R15) | — | deferred | `docs/V2_R15_PROPOSAL.md` | |
| WBS split allocation | — | excluded (owner decision 3) | | read-only splits endpoint |

Test runs at last check: v2 backend suites 580 passed (+ later additions in v2_compat, 60 passed on re-run); `tests/test_phase2_db_reconstruction.py` (old demo schema) fails by design against v2 DB; frontend `tsc` clean; vitest not runnable here (not installed); legacy demo untouched.
