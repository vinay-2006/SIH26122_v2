"""
Progress Domain Router for SETUAI V7 Phase 8.
Exposes project- and schedule-scoped RESTful endpoints for canonical weighted progress rollups:
Activity Progress -> Stage Progress -> Schedule Progress -> Project Progress.
"""

from __future__ import annotations

import uuid
from typing import Optional
from fastapi import APIRouter, Depends, Path, Query, status

from backend.context.project import ProjectContext, require_project_context
from backend.context.schedule import ScheduleContext, require_schedule_context
from backend.schemas.progress import (
    ActivityProgressResponse,
    ProgressBreakdownResponse,
    ProjectProgressResponse,
    ScheduleProgressResponse,
    StageProgressBreakdown,
)
from backend.services.progress_service import ProgressService

router = APIRouter(tags=["progress"])


# ============================================================================
# 1. SCHEDULE PROGRESS
# ============================================================================

@router.get(
    "/api/v1/projects/{project_id}/schedules/{schedule_id}/progress",
    response_model=ScheduleProgressResponse,
    summary="Get authoritative weighted schedule progress rollup",
)
def get_schedule_progress(
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    context: ScheduleContext = Depends(require_schedule_context),
) -> ScheduleProgressResponse:
    """
    Calculates deterministic weighted progress for the specified schedule version.
    Aggregates stage weights or direct activity weights without N+1 queries.
    """
    res = ProgressService.get_schedule_progress(context)
    return ScheduleProgressResponse(**res)


# ============================================================================
# 2. STAGE PROGRESS
# ============================================================================

@router.get(
    "/api/v1/projects/{project_id}/schedules/{schedule_id}/stages/{stage_id}/progress",
    response_model=StageProgressBreakdown,
    summary="Get authoritative weighted stage progress breakdown",
)
def get_stage_progress(
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    stage_id: uuid.UUID = Path(...),
    context: ScheduleContext = Depends(require_schedule_context),
) -> StageProgressBreakdown:
    """
    Calculates deterministic weighted progress for a specific stage within the schedule version.
    Includes explainable per-activity contributions.
    """
    res = ProgressService.get_stage_progress(context, stage_id)
    return StageProgressBreakdown(**res)


# ============================================================================
# 3. ACTIVITY PROGRESS
# ============================================================================

@router.get(
    "/api/v1/projects/{project_id}/schedules/{schedule_id}/activities/{activity_id}/progress",
    response_model=ActivityProgressResponse,
    summary="Get authoritative activity progress",
)
def get_activity_progress(
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    activity_id: str = Path(...),
    context: ScheduleContext = Depends(require_schedule_context),
) -> ActivityProgressResponse:
    """
    Retrieves the authoritative progress percentage and basis for a single activity.
    Always uses the current authoritative approved actual revision.
    """
    res = ProgressService.get_activity_progress(context, activity_id)
    return ActivityProgressResponse(**res)


# ============================================================================
# 4. PROJECT PROGRESS
# ============================================================================

@router.get(
    "/api/v1/projects/{project_id}/progress",
    response_model=ProjectProgressResponse,
    summary="Get authoritative project progress across schedules",
)
def get_project_progress(
    project_id: uuid.UUID = Path(...),
    target_schedule_id: Optional[str] = Query(None, description="Optional target schedule version ID"),
    context: ProjectContext = Depends(require_project_context),
) -> ProjectProgressResponse:
    """
    Calculates project-level progress rollups across schedule versions.
    Maintains strict version isolation (V1 != V2).
    """
    res = ProgressService.get_project_progress(context, target_schedule_id=target_schedule_id)
    return ProjectProgressResponse(**res)


# ============================================================================
# 5. HIERARCHICAL PROGRESS BREAKDOWN
# ============================================================================

@router.get(
    "/api/v1/projects/{project_id}/schedules/{schedule_id}/progress/breakdown",
    response_model=ProgressBreakdownResponse,
    summary="Get hierarchical progress breakdown from schedule to activities",
)
def get_progress_breakdown(
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    context: ScheduleContext = Depends(require_schedule_context),
) -> ProgressBreakdownResponse:
    """
    Provides a comprehensive explainable breakdown: Schedule -> Stages -> Activities
    including unassigned activities.
    """
    res = ProgressService.get_progress_breakdown(context)
    return ProgressBreakdownResponse(**res)
