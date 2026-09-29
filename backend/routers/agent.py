"""
Supervising Agent REST Router for SETUAI V7 Phase 12.
Provides read-only, project-scoped endpoints for supervisory briefings,
evidence-backed queries, review queue intelligence, and active findings.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List
from fastapi import APIRouter, Depends, Path, status

from backend.agents.orchestrator import AgentOrchestrator, get_agent_orchestrator
from backend.agents.schemas import (
    AgentFinding,
    AgentQueryRequest,
    AgentQueryResponse,
    SupervisoryBriefing,
)
from backend.context.errors import raise_permission_denied
from backend.context.project import ProjectContext, require_project_context
from backend.rbac.permissions import Permission, has_permission

router = APIRouter(tags=["supervising-agent"])


def _verify_agent_access(context: ProjectContext) -> None:
    """Verifies caller possesses VIEW_PROJECT permission within project context."""
    if not has_permission(context.role, Permission.VIEW_PROJECT):
        raise_permission_denied(Permission.VIEW_PROJECT.value, context.role)


# ============================================================================
# 1. SUPERVISORY BRIEFING
# ============================================================================

@router.get(
    "/api/v7/projects/{project_id}/agent/briefing",
    response_model=SupervisoryBriefing,
    summary="Generate comprehensive project supervisory briefing (V7)",
)
@router.get(
    "/api/v1/projects/{project_id}/agent/briefing",
    response_model=SupervisoryBriefing,
    summary="Generate comprehensive project supervisory briefing (V1 alias)",
)
def get_supervisory_briefing(
    project_id: uuid.UUID = Path(..., description="Project UUID"),
    context: ProjectContext = Depends(require_project_context),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
) -> SupervisoryBriefing:
    """
    Produces a continuous executive interpretation of current project execution state.
    Orchestrates deterministic domain engines, extracts key risks/holds,
    and synthesizes an evidence-backed supervisory briefing.
    """
    _verify_agent_access(context)
    return orchestrator.generate_briefing(context)


# ============================================================================
# 2. EVIDENCE-BACKED QUERY
# ============================================================================

@router.post(
    "/api/v7/projects/{project_id}/agent/query",
    response_model=AgentQueryResponse,
    summary="Query supervising agent with evidence-backed reasoning (V7)",
)
@router.post(
    "/api/v1/projects/{project_id}/agent/query",
    response_model=AgentQueryResponse,
    summary="Query supervising agent with evidence-backed reasoning (V1 alias)",
)
def query_supervising_agent(
    payload: AgentQueryRequest,
    project_id: uuid.UUID = Path(..., description="Project UUID"),
    context: ProjectContext = Depends(require_project_context),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
) -> AgentQueryResponse:
    """
    Answers specific supervisory questions regarding stages, activities, delays,
    quality gates, or historical incidents using targeted context and LLM reasoning.
    """
    _verify_agent_access(context)
    return orchestrator.query(context, payload)


# ============================================================================
# 3. REVIEW QUEUE INTELLIGENCE
# ============================================================================

@router.get(
    "/api/v7/projects/{project_id}/agent/review-queue",
    response_model=Dict[str, Any],
    summary="Get supervisor review queue intelligence (V7)",
)
@router.get(
    "/api/v1/projects/{project_id}/agent/review-queue",
    response_model=Dict[str, Any],
    summary="Get supervisor review queue intelligence (V1 alias)",
)
def get_review_queue_intelligence(
    project_id: uuid.UUID = Path(..., description="Project UUID"),
    context: ProjectContext = Depends(require_project_context),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
) -> Dict[str, Any]:
    """
    Identifies unreviewed claims, reopen requests, and matching conflicts
    currently awaiting supervisor review.
    """
    _verify_agent_access(context)
    return orchestrator.get_review_queue_intelligence(context)


# ============================================================================
# 4. ACTIVE FINDINGS
# ============================================================================

@router.get(
    "/api/v7/projects/{project_id}/agent/findings",
    response_model=List[AgentFinding],
    summary="List active supervisory findings (V7)",
)
@router.get(
    "/api/v1/projects/{project_id}/agent/findings",
    response_model=List[AgentFinding],
    summary="List active supervisory findings (V1 alias)",
)
def get_agent_findings(
    project_id: uuid.UUID = Path(..., description="Project UUID"),
    context: ProjectContext = Depends(require_project_context),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
) -> List[AgentFinding]:
    """
    Lists current active findings (risks, delays, holds, blocks) surfaced by the supervising agent.
    """
    _verify_agent_access(context)
    return orchestrator.get_findings(context)
