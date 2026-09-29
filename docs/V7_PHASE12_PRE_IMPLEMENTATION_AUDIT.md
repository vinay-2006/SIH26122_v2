# SETUAI V7 — Phase 12 Pre-Implementation Audit Report
**Supervising Agent / Project Intelligence Orchestrator**

**Date:** 2026-09-29  
**Branch:** `feat/v7-phase12-supervising-agent`  
**Author:** Antigravity (AI Assistant)

---

## 1. Existing Agent / Intelligence Code
- **`backend/agents/`**: Does not exist prior to Phase 12.
- **`backend/shared/llm_extraction.py`**: Handles unstructured field report extraction into claims (Member 2).
- **`backend/shared/rule_extraction.py`**: Deterministic fallback extraction when LLMs are unavailable.
- **`backend/shared/wbs_split.py`**: Heuristic and LLM-assisted schedule and WBS tree decomposition.
- **`backend/models/migrations/009_v7_intelligence_foundation.sql`**: Contains schema definitions for `institutional_incidents`, `contractor_disputes`, `impact_scenarios`, and `agent_briefings`.

## 2. Existing LLM Infrastructure
- **`backend/shared/llm_client.py`**:
  - The centralized singleton client implementing Team Hard Restriction #16: *"Nobody makes a Groq/Gemini call outside shared/llm_client.py. If a feature needs an LLM call, it imports the shared function."*
  - Supports Groq (`openai/gpt-oss-20b`) and Gemini (`gemini-3.6-flash`), configurable via `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`.
  - Exposes `call_llm(messages, temperature=0.0, response_format=None, model=None) -> str`.
  - Includes retry backoff on transient errors (`RateLimitError`, `APIConnectionError`, `APITimeoutError`, `InternalServerError`) and fail-fast behavior on daily quota exhaustion.
  - Phase 12 **must** import and reuse `call_llm` from `backend.shared.llm_client` without introducing secondary clients.

## 3. Existing Project State APIs
- **Router**: `backend/routers/projects.py` (`POST /api/v1/projects`, `GET /api/v1/projects`, `GET /api/v1/projects/{project_id}`).
- **Service**: `backend/services/project_service.py` (`ProjectService.get_project(context)`).
- **Repository**: `backend/repositories/project_repo.py` (`ProjectRepository.get_project(project_id)`).

## 4. Existing Stage APIs
- **Router**: `backend/routers/stages.py` (`GET /api/v1/stages`, `GET /api/v1/stages/{stage_id}`, `GET /api/v1/stages/{stage_id}/state`, `GET /api/v1/stages/{stage_id}/progress`, `GET /api/v1/stages/{stage_id}/completion-check`).
- **Service**: `backend/services/stage_service.py`:
  - `StageService.get_stage(context, stage_id)`
  - `StageService.list_stages(context)`
  - `StageService.get_stage_state(context, stage_id)`
  - `StageService.calculate_stage_progress(context, stage_id)`
  - `StageService.check_stage_dependencies(context, stage_id)`
  - `StageService.check_stage_gates(context, stage_id)`
  - `StageService.is_stage_complete(context, stage_id)`
- **Repository**: `backend/repositories/stage_repo.py` (`ProjectStageRepository`).

## 5. Existing Activity APIs
- **Router**: `backend/routers/activities.py` (`GET /api/v1/activities`, `GET /api/v1/activities/{activity_id}/execution-context`).
- **Service Rules**: `StageService.get_execution_state` and `StageService.get_workflow_condition`.
- **Repository**: `backend/repositories/activity_repo.py` (`ScheduleActivityRepository`).

## 6. Existing Progress APIs
- **Router**: `backend/routers/progress.py` (`GET /api/v1/projects/{project_id}/progress`, `GET .../schedules/{schedule_id}/progress`, `GET .../stages/{stage_id}/progress`, `GET .../activities/{activity_id}/progress`, `GET .../progress/breakdown`).
- **Service**: `backend/services/progress_service.py`:
  - Authoritative Phase 8 weighted progress rollup engine.
  - `ProgressService.get_project_progress(context)`
  - `ProgressService.get_schedule_progress(context, schedule_id)`
  - `ProgressService.get_stage_progress(context, stage_id)`
  - `ProgressService.get_activity_progress(context, activity_id)`
  - `ProgressService.get_progress_breakdown(context)`
- **Repository**: `backend/repositories/progress_repo.py` (`ProjectProgressRepository`).

## 7. Existing Matching APIs
- **Router**: `backend/routers/matching.py`, `backend/routers/checks.py`.
- **Service**: `backend/services/matching_eligibility_service.py` (`MatchingEligibilityService.evaluate_activity`, `get_eligible_activities_for_stage`).
- **Repositories**: `backend/repositories/execution_event_repo.py`, `candidate_matches` table.

## 8. Existing Quality APIs
- **Database Schema**: `006_v7_quality.sql` defines `quality_gates` and `quality_evidence`.
- **Service**: `StageService.check_stage_gates(context, stage_id)` resolves quality gates via `ProjectStageRepository.get_stage_gates`.
- **Read Integration**: Read-only queries directly accessing `quality_gates` scoped to `project_id`.

## 9. Existing Contractor APIs
- **Database Schema**: `005_v7_contractors_work_packages.sql` defines `contractors` and `work_packages`.
- **Database Schema**: `009_v7_intelligence_foundation.sql` defines `contractor_disputes`.
- **Read Integration**: Read-only queries against `contractors` and `work_packages` scoped to `project_id`.

## 10. Existing Institutional Memory APIs
- **Package**: `backend/memory/` (Phase 11).
- **Service**: `backend/memory/retrieval_service.py` (`InstitutionalMemoryRetrievalService` accessed via `get_memory_retrieval_service()`).
- **Endpoints**: `POST /api/v7/projects/{project_id}/memory/search`.
- **Capabilities**: Exact metadata filtering, CPU-pinned `SentenceTransformers` semantic search with deterministic lexical fallback, and explainable score breakdowns.

## 11. Existing Dossier APIs
- **Package**: `backend/dossier/` (Phase 13).
- **Service**: `backend/dossier/service.py` (`DossierService` accessed via `get_dossier_service()`).
- **Endpoints**: `GET /api/v7/projects/{project_id}/dossier`, `GET .../schedules/{schedule_id}/dossier`, `GET .../activities/{activity_id}/dossier`, `GET .../dossier/audit-verification`.
- **Capabilities**: $O(N + E)$ bulk collection, completeness evaluator, and audit chain verification.

## 12. Existing Audit APIs
- **Repository**: `backend/repositories/audit_repo.py` (`ProjectAuditRepository.log`, `ProjectAuditRepository.list`).
- **Verification**: `backend/shared/audit.py` (`write_audit_log`, `verify_audit_chain`).

## 13. Existing RBAC Permissions
- **Definitions**: `backend/rbac/permissions.py` (`Permission.VIEW_PROJECT`, `VIEW_SCHEDULE`, `MANAGE_SCHEDULE`, `CREATE_EXECUTION_EVENT`, `VIEW_EXECUTION_EVENTS`, `REVIEW_CLAIM`, `APPROVE_ACTUAL`, `REQUEST_REOPEN`, `APPROVE_REOPEN`, `MANAGE_QUALITY`, `VIEW_AUDIT`).
- **Role Map**: All authenticated project members (`OWNER`, `PROJECT_MANAGER`, `PLANNER`, `SUPERVISOR`, `SITE_ENGINEER`, `QUALITY_INSPECTOR`, `AUDITOR`) possess `Permission.VIEW_PROJECT`.
- **Agent Policy**: Read-only agent briefing and query endpoints require `Permission.VIEW_PROJECT` under strict `ProjectContext`. Review-queue intelligence checks `Permission.VIEW_EXECUTION_EVENTS` or `Permission.REVIEW_CLAIM`.

## 14. Existing Database Tables
- `projects`, `profiles`, `project_memberships`, `schedules`, `stages`, `schedule_activities`, `activity_relationships`, `execution_events`, `source_documents`, `candidate_matches`, `planner_decisions`, `approved_actuals`, `actual_revisions`, `reopen_requests`, `rework_items`, `audit_logs`, `contractors`, `work_packages`, `quality_gates`, `quality_evidence`, `institutional_incidents`, `incident_evidence`, `contractor_disputes`, `impact_scenarios`, `agent_briefings`.

## 15. Existing Agent-Related Tables
- `agent_briefings` (Migration 009):
  ```sql
  CREATE TABLE IF NOT EXISTS agent_briefings (
    briefing_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    trigger_type TEXT,
    severity TEXT,
    title TEXT NOT NULL,
    briefing_text TEXT NOT NULL,
    evidence_refs JSONB DEFAULT '[]',
    recommended_action TEXT,
    generated_at TIMESTAMPTZ DEFAULT now(),
    status TEXT DEFAULT 'ACTIVE'
  );
  ```

## 16. Existing Tests
- Phase 11: `tests/test_phase11_memory_retrieval.py` (14 passed).
- Phase 13: `tests/test_phase13_audit_dossier.py` (14 passed).
- Phase 5–10 + Audit Chain: `tests/test_phase5_execution_state.py`, `tests/test_phase5_stage_domain.py`, `tests/test_phase6_matching_eligibility.py`, `tests/test_phase7_actual_revisions.py`, `tests/test_phase8_progress_engine.py`, `tests/test_phase10_compound_impact.py`, `tests/test_priority1_audit_chain_tamper.py` (41 passed).

## 17. Reusable Components
- `backend.shared.llm_client.call_llm`: Reused for all LLM reasoning.
- `backend.services.progress_service.ProgressService`: Reused for authoritative progress numbers.
- `backend.services.stage_service.StageService`: Reused for canonical execution state, stage tree, dependencies, and gates.
- `backend.services.impact_service.ImpactService`: Reused for compound impact simulations.
- `backend.services.project_service.ProjectService`: Reused for project metadata.
- `backend.memory.get_memory_retrieval_service`: Reused for institutional memory retrieval.
- `backend.dossier.get_dossier_service`: Reused for audit dossier integration.
- `backend.shared.audit.write_audit_log`: Reused for cryptographic audit logging of agent invocations.
- `backend.context.project.require_project_context`: Reused for strict multi-tenant boundary enforcement.

## 18. Missing Components
- `backend/agents/`:
  - `__init__.py`
  - `schemas.py`: Typed Pydantic schemas for findings, briefing, queries, tool outputs, and degraded status.
  - `prompts.py`: System prompt, supervisory reasoning guidelines, injection defense delimiters.
  - `tools/`: Modular read-only domain tools (`project_state.py`, `stage_state.py`, `activity_state.py`, `progress.py`, `matching.py`, `quality.py`, `contractor.py`, `memory.py`, `impact.py`, `review_queue.py`, `dossier.py`).
  - `context_builder.py`: Targeted context assembly within token budget.
  - `supervising_agent.py`: Agent reasoning orchestrator interfacing with `llm_client`.
  - `orchestrator.py`: High-level entrypoint for briefings, queries, and review queue evaluation.
- `backend/routers/agent.py`: Read-only REST endpoints.
- `tests/test_phase12_supervising_agent.py`: Dedicated Phase 12 test suite.

## 19. Required Phase 12 Changes
1. Create `backend/agents/` package with all domain tools, context builder, prompt templates, schemas, and orchestrator.
2. Ensure every domain tool is strictly **read-only** and accepts `ProjectContext`.
3. Wrap LLM calls in structured validation with deterministic fallback when the LLM is offline or malformed.
4. Implement prompt injection defenses by enclosing all project/claim/incident data within XML-style untrusted delimiters.
5. Create `backend/routers/agent.py` and register it in `backend/main.py`.
6. Write comprehensive tests in `tests/test_phase12_supervising_agent.py`.

## 20. Identified Risks & Mitigations
- **Risk 1: LLM hallucination of numerical project state.**
  - *Mitigation:* The prompt strictly forbids inventing numbers. Deterministic values computed by ProgressService, StageService, and ImpactService are passed into the prompt, and the agent's task is strictly synthesis and explanation.
- **Risk 2: Prompt injection via field report or note text.**
  - *Mitigation:* All dynamic project data is sanitized and wrapped in `<untrusted_project_evidence>` tags with explicit system instructions to ignore commands within that block.
- **Risk 3: LLM service outages or rate limits.**
  - *Mitigation:* When `call_llm` fails or returns unparseable content, the orchestrator returns a valid `SupervisoryBriefing` with `agent_status = "DEGRADED"` populated with full deterministic engine findings, ensuring zero application crashes.
- **Risk 4: Cross-project data leakage.**
  - *Mitigation:* Every domain tool takes `ProjectContext` and executes queries filtered by `project_id = context.project_id`. Attempts to pass foreign project IDs trigger permission denial.
- **Risk 5: Performance degradation from N+1 tool queries.**
  - *Mitigation:* The `ContextBuilder` aggregates data via bulk queries, reusing existing cached/bulk repository methods.
