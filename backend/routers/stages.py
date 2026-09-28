"""
Stage Domain and Execution State Router for SETUAI V7.
Provides RESTful endpoints for stage lifecycle, hierarchical trees, canonical
execution-state evaluation, progress aggregation, dependency/gate checks,
and completion rule validation.
"""

from __future__ import annotations

import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status

from backend.context.project import ProjectContext, require_project_context
from backend.context.schedule import ScheduleContext, require_schedule_context
from backend.schemas.stage import (
    ActivityExecutionContextResponse,
    StageCompletionCheckResponse,
    StageCreate,
    StageDependencyCheckResponse,
    StageGateCheckResponse,
    StageProgressResponse,
    StageResponse,
    StageStateResponse,
    StageTreeNode,
    StageUpdate,
)
from backend.services.stage_service import StageService

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["stages"])


# ============================================================================
# 1. STAGE LIFECYCLE & HIERARCHY ENDPOINTS
# ============================================================================

@router.post(
    "/stages",
    response_model=StageResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_stage(
    project_id: uuid.UUID,
    payload: StageCreate,
    context: ScheduleContext = Depends(require_schedule_context),
) -> StageResponse:
    """
    Creates a new stage scoped strictly under the authorized project and schedule version.
    Requires explicit schedule context (via X-Schedule-ID header or schedule_id query).
    """
    created = StageService.create_stage(context, payload)
    return StageResponse(**created)


@router.get(
    "/stages",
    response_model=List[StageResponse],
)
def list_stages(
    project_id: uuid.UUID,
    parent_stage_id: Optional[uuid.UUID] = Query(None, description="Filter by parent stage"),
    context: ScheduleContext = Depends(require_schedule_context),
) -> List[StageResponse]:
    """
    Lists all stages belonging to the authorized project and schedule version.
    """
    stages = StageService.list_stages(context, parent_stage_id=parent_stage_id)
    return [StageResponse(**s) for s in stages]


@router.get(
    "/stages/tree",
    response_model=List[StageTreeNode],
)
def get_stage_tree(
    project_id: uuid.UUID,
    context: ScheduleContext = Depends(require_schedule_context),
) -> List[StageTreeNode]:
    """
    Retrieves the hierarchical stage tree for the authorized project and schedule version.
    """
    tree = StageService.get_stage_tree(context)
    return [StageTreeNode(**node) for node in tree]


@router.get(
    "/stages/{stage_id}",
    response_model=StageResponse,
)
def get_stage(
    project_id: uuid.UUID,
    stage_id: uuid.UUID,
    context: ProjectContext = Depends(require_project_context),
) -> StageResponse:
    """
    Retrieves stage details by ID. Scoped strictly to the caller's authorized ProjectContext.
    """
    stage = StageService.get_stage(context, stage_id)
    return StageResponse(**stage)


@router.patch(
    "/stages/{stage_id}",
    response_model=StageResponse,
)
def update_stage(
    project_id: uuid.UUID,
    stage_id: uuid.UUID,
    payload: StageUpdate,
    context: ProjectContext = Depends(require_project_context),
) -> StageResponse:
    """
    Updates stage metadata. Requires MANAGE_SCHEDULE permission.
    """
    updated = StageService.update_stage(context, stage_id, payload)
    return StageResponse(**updated)


# ============================================================================
# 2. EXECUTION STATE, PROGRESS, AND COMPLETION ENGINE ENDPOINTS
# ============================================================================

@router.get(
    "/stages/{stage_id}/state",
    response_model=StageStateResponse,
)
def get_stage_state(
    project_id: uuid.UUID,
    stage_id: uuid.UUID,
    context: ProjectContext = Depends(require_project_context),
) -> StageStateResponse:
    """
    Evaluates deterministic canonical execution states for all activities in the stage
    and determines the aggregate stage state and workflow conditions.
    """
    state_res = StageService.calculate_stage_state(context, stage_id)
    return StageStateResponse(**state_res)


@router.get(
    "/stages/{stage_id}/progress",
    response_model=StageProgressResponse,
)
def get_stage_progress(
    project_id: uuid.UUID,
    stage_id: uuid.UUID,
    context: ProjectContext = Depends(require_project_context),
) -> StageProgressResponse:
    """
    Calculates the deterministic weighted progress percentage for the stage.
    """
    prog_res = StageService.calculate_stage_progress(context, stage_id)
    return StageProgressResponse(**prog_res)


@router.get(
    "/stages/{stage_id}/dependencies",
    response_model=StageDependencyCheckResponse,
)
def check_stage_dependencies(
    project_id: uuid.UUID,
    stage_id: uuid.UUID,
    context: ProjectContext = Depends(require_project_context),
) -> StageDependencyCheckResponse:
    """
    Validates predecessor stage requirements for the given stage.
    """
    dep_res = StageService.check_stage_dependencies(context, stage_id)
    return StageDependencyCheckResponse(**dep_res)


@router.get(
    "/stages/{stage_id}/gates",
    response_model=StageGateCheckResponse,
)
def check_stage_gates(
    project_id: uuid.UUID,
    stage_id: uuid.UUID,
    context: ProjectContext = Depends(require_project_context),
) -> StageGateCheckResponse:
    """
    Validates that all required quality gates associated with the stage are cleared.
    """
    gate_res = StageService.check_stage_gates(context, stage_id)
    return StageGateCheckResponse(**gate_res)


@router.get(
    "/stages/{stage_id}/completion-check",
    response_model=StageCompletionCheckResponse,
)
def is_stage_complete(
    project_id: uuid.UUID,
    stage_id: uuid.UUID,
    context: ProjectContext = Depends(require_project_context),
) -> StageCompletionCheckResponse:
    """
    Comprehensive stage completion rule check:
    Returns complete ONLY when all required activities are complete, predecessor stages
    satisfied, quality gates cleared, and zero blocking conditions exist.
    """
    comp_res = StageService.is_stage_complete(context, stage_id)
    return StageCompletionCheckResponse(**comp_res)


# ============================================================================
# 3. ACTIVITY EXECUTION CONTEXT (PHASE 6 STABLE INTERFACE)
# ============================================================================

@router.get(
    "/activities/{activity_id}/execution-context",
    response_model=ActivityExecutionContextResponse,
)
def get_activity_execution_context(
    project_id: uuid.UUID,
    activity_id: str,
    context: ScheduleContext = Depends(require_schedule_context),
) -> ActivityExecutionContextResponse:
    """
    Authoritative activity execution context contract consumed by Phase 6 (State-Aware Matching)
    to determine matching eligibility and candidate filtering.
    """
    act_ctx = StageService.get_activity_execution_context(context, activity_id)
    return ActivityExecutionContextResponse(**act_ctx)
