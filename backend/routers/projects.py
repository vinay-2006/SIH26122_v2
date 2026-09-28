"""
Project Domain Router for SETUAI V7.
Provides endpoints for project lifecycle and schedule version management.
"""

from __future__ import annotations

import uuid
from typing import List
from fastapi import APIRouter, Depends, Query, status

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
