"""Project dashboard router (V7): stage-wise and discipline-wise progress from the database."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path

from backend.context.schedule import ScheduleContext, require_schedule_context
from backend.services.dashboard_service import DashboardService

router = APIRouter(tags=["project-dashboard"])


@router.get("/api/v1/projects/{project_id}/schedules/{schedule_id}/dashboard")
def project_dashboard(
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    context: ScheduleContext = Depends(require_schedule_context),
):
    """
    Overall, stage-wise and discipline-wise actual vs planned progress for one schedule version, plus issue totals.
    Actual progress comes only from approved_actuals; planned progress is time-phased from the planned dates.
    """
    return DashboardService.get_dashboard(context)
