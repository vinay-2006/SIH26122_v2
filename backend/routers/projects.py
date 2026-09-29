"""
Project Domain Router for SETUAI V7.
Provides endpoints for project lifecycle and schedule version management.
"""

from __future__ import annotations

from datetime import date
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel

from backend.auth.dependencies import get_current_user
from backend.auth.models import CurrentUser
from backend.context.project import ProjectContext, require_project_context
from backend.schemas.project import (
    ProjectCreate,
    ProjectListItem,
    ProjectResponse,
    ProjectUpdate,
)
from backend.schemas.schedule_version import (
    ScheduleMetadataComparison,
    ScheduleVersionActivateResponse,
    ScheduleVersionCreate,
    ScheduleVersionResponse,
    ScheduleVersionSupersedeRequest,
)
from backend.services.project_service import ProjectService
from backend.services.schedule_version_service import ScheduleVersionService

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


# ============================================================================
# PROJECT DOMAIN ENDPOINTS
# ============================================================================

@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    current_user: CurrentUser = Depends(get_current_user),
) -> ProjectResponse:
    """
    Creates a new project and establishes the authenticated caller as PROJECT_MANAGER.
    """
    created = ProjectService.create_project(current_user, payload)
    return ProjectResponse(**created)


@router.get("", response_model=List[ProjectListItem])
def list_user_projects(
    current_user: CurrentUser = Depends(get_current_user),
) -> List[ProjectListItem]:
    """
    Lists only projects where the authenticated caller possesses an active membership.
    """
    projects = ProjectService.get_user_projects(current_user)
    return [ProjectListItem(**p) for p in projects]


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: uuid.UUID,
    context: ProjectContext = Depends(require_project_context),
) -> ProjectResponse:
    """
    Retrieves project details. Scoped strictly to the caller's authorized ProjectContext.
    """
    project = ProjectService.get_project(context)
    return ProjectResponse(**project)


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    context: ProjectContext = Depends(require_project_context),
) -> ProjectResponse:
    """
    Updates project metadata. Requires PROJECT_MANAGER or OWNER role.
    """
    updated = ProjectService.update_project(context, payload)
    return ProjectResponse(**updated)


@router.post("/{project_id}/archive", response_model=ProjectResponse)
def archive_project(
    project_id: uuid.UUID,
    context: ProjectContext = Depends(require_project_context),
) -> ProjectResponse:
    """
    Transitions project status to ARCHIVED. Requires PROJECT_MANAGER or OWNER role.
    """
    archived = ProjectService.archive_project(context)
    return ProjectResponse(**archived)


@router.post("/{project_id}/activate", response_model=ProjectResponse)
def activate_project(
    project_id: uuid.UUID,
    context: ProjectContext = Depends(require_project_context),
) -> ProjectResponse:
    """
    Transitions project status to ACTIVE. Requires PROJECT_MANAGER or OWNER role.
    """
    activated = ProjectService.activate_project(context)
    return ProjectResponse(**activated)


# ============================================================================
# SCHEDULE VERSION DOMAIN ENDPOINTS
# ============================================================================

@router.post(
    "/{project_id}/schedules",
    response_model=ScheduleVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_schedule_version(
    project_id: uuid.UUID,
    payload: ScheduleVersionCreate,
    context: ProjectContext = Depends(require_project_context),
) -> ScheduleVersionResponse:
    """
    Creates a new schedule version scoped explicitly under the authorized project.
    Validates MANAGE_SCHEDULE permission.
    """
    created = ScheduleVersionService.create_schedule_version(context, payload)
    return ScheduleVersionResponse(**created)


@router.get(
    "/{project_id}/schedules",
    response_model=List[ScheduleVersionResponse],
)
def list_schedule_versions(
    project_id: uuid.UUID,
    context: ProjectContext = Depends(require_project_context),
) -> List[ScheduleVersionResponse]:
    """
    Lists all schedule versions (active and historical) belonging to the authorized project.
    """
    versions = ScheduleVersionService.get_schedule_versions(context)
    return [ScheduleVersionResponse(**v) for v in versions]


@router.get(
    "/{project_id}/schedules/compare",
    response_model=ScheduleMetadataComparison,
)
def compare_schedule_metadata(
    project_id: uuid.UUID,
    schedule_a: str = Query(..., description="First schedule version ID"),
    schedule_b: str = Query(..., description="Second schedule version ID"),
    context: ProjectContext = Depends(require_project_context),
) -> ScheduleMetadataComparison:
    """
    Compares metadata (counts, dates, hashes) between two versions of the project.
    Metadata comparison only — no CPM or impact calculations.
    """
    comparison = ScheduleVersionService.compare_schedule_metadata(context, schedule_a, schedule_b)
    return ScheduleMetadataComparison(**comparison)


@router.get(
    "/{project_id}/schedules/{schedule_id}",
    response_model=ScheduleVersionResponse,
)
def get_schedule_version(
    project_id: uuid.UUID,
    schedule_id: str,
    context: ProjectContext = Depends(require_project_context),
) -> ScheduleVersionResponse:
    """
    Retrieves a specific schedule version. Historical versions are fully queryable.
    """
    version = ScheduleVersionService.get_schedule_version(context, schedule_id)
    return ScheduleVersionResponse(**version)


@router.post(
    "/{project_id}/schedules/{schedule_id}/activate",
    response_model=ScheduleVersionActivateResponse,
)
def activate_schedule_version(
    project_id: uuid.UUID,
    schedule_id: str,
    context: ProjectContext = Depends(require_project_context),
) -> ScheduleVersionActivateResponse:
    """
    Transactionally activates a schedule version within the project.
    Historical versions and claims remain untouched.
    """
    result = ScheduleVersionService.activate_schedule_version(context, schedule_id)
    return ScheduleVersionActivateResponse(**result)


@router.post(
    "/{project_id}/schedules/{schedule_id}/supersede",
    response_model=ScheduleVersionResponse,
)
def supersede_schedule_version(
    project_id: uuid.UUID,
    schedule_id: str,
    payload: ScheduleVersionSupersedeRequest,
    context: ProjectContext = Depends(require_project_context),
) -> ScheduleVersionResponse:
    """
    Links schedule_id as superseding an older version in the same project.
    """
    updated = ScheduleVersionService.supersede_schedule_version(
        context, schedule_id, payload.supersedes_schedule_id
    )
    return ScheduleVersionResponse(**updated)


class XERImportRequest(BaseModel):
    version_code: str
    xer_content: str
    data_date: Optional[date] = None
    supersedes_schedule_id: Optional[str] = None
    activate_immediately: bool = False


@router.post(
    "/{project_id}/schedules/xer",
    response_model=ScheduleVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def import_xer_schedule_version(
    project_id: uuid.UUID,
    payload: XERImportRequest,
    context: ProjectContext = Depends(require_project_context),
) -> ScheduleVersionResponse:
    """
    Imports a Primavera P6 .xer baseline schedule version into the authorized project context.
    """
    sv_payload = ScheduleVersionCreate(
        version_code=payload.version_code,
        csv_content=payload.xer_content,
        data_date=payload.data_date,
        source_format="xer",
        supersedes_schedule_id=payload.supersedes_schedule_id,
        activate_immediately=payload.activate_immediately,
    )
    created = ScheduleVersionService.create_schedule_version(context, sv_payload)
    return ScheduleVersionResponse(**created)


class ActivityAttributionRequest(BaseModel):
    contractor_id: Optional[uuid.UUID] = None
    work_package_id: Optional[uuid.UUID] = None
    stage_id: Optional[uuid.UUID] = None


@router.patch(
    "/{project_id}/activities/{activity_id}/attribution",
)
def update_activity_attribution(
    project_id: uuid.UUID,
    activity_id: str,
    payload: ActivityAttributionRequest,
    context: ProjectContext = Depends(require_project_context),
):
    """
    Associates an activity with contractor_id, work_package_id, and/or stage_id within the project.
    """
    from backend.repositories.activity_repo import ProjectActivityRepository
    from backend.context.errors import raise_resource_not_found

    updated = ProjectActivityRepository.update_attribution(
        context=context,
        activity_id=activity_id,
        contractor_id=str(payload.contractor_id) if payload.contractor_id else None,
        work_package_id=str(payload.work_package_id) if payload.work_package_id else None,
        stage_id=str(payload.stage_id) if payload.stage_id else None,
    )
    if not updated:
        raise_resource_not_found("Activity", activity_id)
    return updated

