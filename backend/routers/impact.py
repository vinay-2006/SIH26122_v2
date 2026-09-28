"""
Compound Impact Router for SETUAI V7 Phase 10.
Exposes project- and schedule-scoped RESTful endpoints for deterministic delay simulation,
compound multi-hop propagation, scenario persistence, and float analysis.
"""

from __future__ import annotations

import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, Path, Query, status

from backend.context.schedule import ScheduleContext, require_schedule_context
from backend.schemas.impact import (
    CreateScenarioRequest,
    ImpactScenarioResult,
    ImpactScenarioSummary,
    PreviewScenarioRequest,
)
from backend.services.impact_service import ImpactService

router = APIRouter(tags=["impact"])


# ============================================================================
# 1. COMPOUND IMPACT PREVIEW (EPHEMERAL)
# ============================================================================

@router.post(
    "/api/v1/projects/{project_id}/schedules/{schedule_id}/impact/preview",
    response_model=ImpactScenarioResult,
    summary="Preview compound downstream schedule impact without persistence",
)
def preview_compound_impact(
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    payload: PreviewScenarioRequest = ...,
    context: ScheduleContext = Depends(require_schedule_context),
) -> ImpactScenarioResult:
    """
    Evaluates multi-hop compound downstream impact for one or more seed activities.
    Determines controlling predecessor constraints, float absorption, and stage/project consequences.
    """
    return ImpactService.evaluate_compound_impact(
        context,
        seed_activities=payload.seed_activities,
        scenario_name=payload.scenario_name or "Compound Impact Simulation",
    )


# ============================================================================
# 2. CREATE & PERSIST IMPACT SCENARIO
# ============================================================================

@router.post(
    "/api/v1/projects/{project_id}/schedules/{schedule_id}/impact/scenarios",
    response_model=ImpactScenarioResult,
    status_code=status.HTTP_201_CREATED,
    summary="Create and persist an authoritative impact scenario",
)
def create_impact_scenario(
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    payload: CreateScenarioRequest = ...,
    context: ScheduleContext = Depends(require_schedule_context),
) -> ImpactScenarioResult:
    """
    Evaluates compound impact and records the scenario permanently in the database.
    Requires MANAGE_SCHEDULE permission.
    """
    return ImpactService.create_and_save_scenario(context, payload)


# ============================================================================
# 3. LIST PERSISTED SCENARIOS
# ============================================================================

@router.get(
    "/api/v1/projects/{project_id}/schedules/{schedule_id}/impact/scenarios",
    response_model=List[ImpactScenarioSummary],
    summary="List persisted impact scenarios for schedule version",
)
def list_impact_scenarios(
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    context: ScheduleContext = Depends(require_schedule_context),
) -> List[ImpactScenarioSummary]:
    """
    Lists all saved reproducible scenarios belonging strictly to this project and schedule version.
    """
    return ImpactService.list_scenarios(context)


# ============================================================================
# 4. GET PERSISTED SCENARIO
# ============================================================================

@router.get(
    "/api/v1/projects/{project_id}/schedules/{schedule_id}/impact/scenarios/{scenario_id}",
    response_model=ImpactScenarioResult,
    summary="Retrieve a specific persisted impact scenario",
)
def get_impact_scenario(
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    scenario_id: uuid.UUID = Path(...),
    context: ScheduleContext = Depends(require_schedule_context),
) -> ImpactScenarioResult:
    """
    Retrieves full deterministic simulation results for a specific scenario ID.
    """
    return ImpactService.get_scenario(context, scenario_id)


# ============================================================================
# 5. SINGLE ACTIVITY PREVIEW (SCOPED)
# ============================================================================

@router.get(
    "/api/v1/projects/{project_id}/schedules/{schedule_id}/activities/{activity_id}/impact-preview",
    response_model=ImpactScenarioResult,
    summary="Single-activity impact preview with float analysis",
)
def get_single_activity_impact_preview(
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    activity_id: str = Path(...),
    delay_days: int = Query(0, ge=0, description="Hypothetical delay days"),
    context: ScheduleContext = Depends(require_schedule_context),
) -> ImpactScenarioResult:
    """
    Evaluates downstream schedule impact preview for a single activity.
    Enforces project and schedule isolation.
    """
    res_dict = ImpactService.get_single_activity_preview(context, activity_id=activity_id, delay_days=delay_days)
    return ImpactScenarioResult(**res_dict)
