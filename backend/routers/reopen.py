"""
Reopen, Rework, and Actuals Revision Router for SETUAI V7 Phase 7.
Provides RESTful endpoints for challenge/reopen requests, human authorization decisions,
rework actual revisions, and reconstructible audit timeline retrieval.
"""

from __future__ import annotations

import uuid
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, Path, Query, status

from backend.context.project import ProjectContext, require_project_context
from backend.context.schedule import ScheduleContext, require_schedule_context
from backend.schemas.reopen import (
    ActualRevisionApprovalRequest,
    ActualRevisionResponse,
    ActivityExecutionHistoryResponse,
    ReopenDecisionRequest,
    ReopenRequestCreate,
    ReopenStatusResponse,
)
from backend.services.reopen_service import ReopenService

router = APIRouter(tags=["reopen"])


# ============================================================================
# 1. CHALLENGE & REOPEN REQUEST
# ============================================================================

@router.post(
    "/api/v1/projects/{project_id}/schedules/{schedule_id}/activities/{activity_id}/reopen",
    response_model=ReopenStatusResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Challenge and request reopening of a completed activity",
)
@router.post(
    "/api/v1/projects/{project_id}/activities/{activity_id}/reopen",
    response_model=ReopenStatusResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Challenge and request reopening of a completed activity (scoped via schedule header/query)",
)
def request_reopen(
    project_id: uuid.UUID,
    activity_id: str,
    payload: ReopenRequestCreate,
    schedule_id: Optional[str] = None,
    context: ScheduleContext = Depends(require_schedule_context),
) -> ReopenStatusResponse:
    """
    Challenges a completed activity and creates a formal reopen request.
    Requires Permission.REQUEST_REOPEN (Owner, PM, Supervisor, Site Engineer).
    """
    res = ReopenService.request_reopen(context, activity_id, payload)
    return ReopenStatusResponse(**res)


# ============================================================================
# 2. REOPEN DECISION (AUTHORIZE / REJECT)
# ============================================================================

@router.post(
    "/api/v1/projects/{project_id}/schedules/{schedule_id}/activities/{activity_id}/reopen/decide",
    response_model=ReopenStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Human-authorize or reject a pending reopen request",
)
@router.post(
    "/api/v1/projects/{project_id}/activities/{activity_id}/reopen/decide",
    response_model=ReopenStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Human-authorize or reject a pending reopen request (scoped via schedule header/query)",
)
def decide_reopen(
    project_id: uuid.UUID,
    activity_id: str,
    payload: ReopenDecisionRequest,
    schedule_id: Optional[str] = None,
    context: ScheduleContext = Depends(require_schedule_context),
) -> ReopenStatusResponse:
    """
    Decides on a pending reopen request.
    Requires Permission.APPROVE_REOPEN (Owner, PM, Supervisor). Site Engineer is excluded.
    """
    res = ReopenService.decide_reopen(context, activity_id, payload)
    return ReopenStatusResponse(**res)


# ============================================================================
# 3. ACTUALS REVISION APPROVAL
# ============================================================================

@router.post(
    "/api/v1/projects/{project_id}/schedules/{schedule_id}/activities/{activity_id}/actuals/revision",
    response_model=ActualRevisionResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve corrected actuals following authorized rework",
)
@router.post(
    "/api/v1/projects/{project_id}/activities/{activity_id}/actuals/revision",
    response_model=ActualRevisionResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve corrected actuals following authorized rework (scoped via schedule header/query)",
)
def approve_actual_revision(
    project_id: uuid.UUID,
    activity_id: str,
    payload: ActualRevisionApprovalRequest,
    schedule_id: Optional[str] = None,
    context: ScheduleContext = Depends(require_schedule_context),
) -> ActualRevisionResponse:
    """
    Approves a revised actual following authorized rework.
    Requires Permission.APPROVE_ACTUAL (Owner, PM, Supervisor).
    Preserves full prior actual in the immutable audit ledger.
    """
    res = ReopenService.approve_actual_revision(context, activity_id, payload)
    return ActualRevisionResponse(**res)


# ============================================================================
# 4. REOPEN STATUS
# ============================================================================

@router.get(
    "/api/v1/projects/{project_id}/schedules/{schedule_id}/activities/{activity_id}/reopen",
    response_model=ReopenStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Get current reopen lifecycle status for an activity",
)
@router.get(
    "/api/v1/projects/{project_id}/activities/{activity_id}/reopen",
    response_model=ReopenStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Get current reopen lifecycle status for an activity (scoped via schedule header/query)",
)
def get_reopen_status(
    project_id: uuid.UUID,
    activity_id: str,
    schedule_id: Optional[str] = None,
    context: ScheduleContext = Depends(require_schedule_context),
) -> ReopenStatusResponse:
    """
    Retrieves the current reopen lifecycle status and context.
    Requires Permission.VIEW_SCHEDULE or Permission.VIEW_AUDIT.
    """
    res = ReopenService.get_reopen_status(context, activity_id)
    return ReopenStatusResponse(**res)


# ============================================================================
# 5. EXECUTION & REVISION HISTORY
# ============================================================================

@router.get(
    "/api/v1/projects/{project_id}/schedules/{schedule_id}/activities/{activity_id}/history",
    response_model=ActivityExecutionHistoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Reconstruct complete chronological execution and revision history",
)
@router.get(
    "/api/v1/projects/{project_id}/activities/{activity_id}/history",
    response_model=ActivityExecutionHistoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Reconstruct complete chronological execution and revision history (scoped via schedule header/query)",
)
def get_activity_history(
    project_id: uuid.UUID,
    activity_id: str,
    schedule_id: Optional[str] = None,
    context: ScheduleContext = Depends(require_schedule_context),
) -> ActivityExecutionHistoryResponse:
    """
    Returns full chronological history: initial actual, reopen requests,
    approvals, rework events, and actual revisions with tamper-evident audit links.
    Requires Permission.VIEW_SCHEDULE or Permission.VIEW_AUDIT.
    """
    res = ReopenService.get_actual_history(context, activity_id)
    return ActivityExecutionHistoryResponse(**res)
