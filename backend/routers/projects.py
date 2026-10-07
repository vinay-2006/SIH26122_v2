"""
Project Domain Router for SETUAI V7.
Provides endpoints for project lifecycle and schedule version management.
"""

from __future__ import annotations

from datetime import date
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel

from backend.context import gates
from backend.auth.dependencies import get_current_user
from backend.auth.models import CurrentUser
from backend.context.project import ProjectContext, require_project_context
from backend.context.schedule import ScheduleContext, require_schedule_context
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


class ProjectMeResponse(BaseModel):
    project_id: uuid.UUID
    project_name: Optional[str] = None
    user_id: str
    full_name: Optional[str] = None
    role: str
    permissions: List[str]


@router.get("/{project_id}/me", response_model=ProjectMeResponse)
def get_my_project_role(
    project_id: uuid.UUID,
    context: ProjectContext = Depends(require_project_context),
) -> ProjectMeResponse:
    """
    The caller's project role and the permissions it grants (server-authoritative, from the same
    RBAC table every route enforces). The frontend gates screens and actions by these permissions.
    """
    from backend.rbac.permissions import ROLE_PERMISSIONS

    return ProjectMeResponse(
        project_id=context.project_id,
        project_name=context.project_name,
        user_id=str(context.user.id),
        full_name=context.user.full_name,
        role=context.role,
        permissions=sorted(p.value for p in ROLE_PERMISSIONS.get(context.role, set())),
    )


@router.get("/{project_id}/schedules/{schedule_id}/export")
def export_schedule_version(
    project_id: uuid.UUID,
    schedule_id: str,
    format: str = Query("csv", pattern="^(csv|xer)$"),
    context: ScheduleContext = Depends(gates.schedule_view),
) -> Response:
    """
    Exports ONE schedule version (activities + dependencies exactly as stored) as canonical CSV or P6-style XER.
    Same source of truth as import: re-importing the file reproduces the activities and dependency logic.
    Explicit project + schedule required; VIEW_SCHEDULE permission (every project role).
    """
    from backend.shared import schedule_repository as repo
    from backend.shared.schedule_export import export_schedule_csv, export_schedule_xer

    meta = repo.get_schedule(context.schedule_id)
    acts = [a.model_dump() for a in repo.list_schedule_activities(context.schedule_id)]
    deps = [d.model_dump() for d in repo.list_schedule_dependencies(context.schedule_id)]
    if format == "xer":
        body = export_schedule_xer(acts, deps, project_short_name=((meta.project_name if meta else None) or "PROJECT").replace(" ", "_")[:40],
                                   data_date=str(meta.data_date) if meta and meta.data_date else date.today().isoformat())
        media = "application/octet-stream"
    else:
        body, media = export_schedule_csv(acts, deps), "text/csv"
    return Response(content=body, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="schedule_{context.schedule_id}.{format}"'})


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
    "/{project_id}/schedules/{schedule_id}/activities/{activity_id}/attribution",
)
def update_activity_attribution(
    project_id: uuid.UUID,
    schedule_id: str,
    activity_id: str,
    payload: ActivityAttributionRequest,
    context: ScheduleContext = Depends(require_schedule_context),
):
    """
    Sets stage / contractor / work package of ONE activity in ONE schedule version (MANAGE_SCHEDULE).
    Referenced stage, contractor and work package must belong to the caller's project (stage: to this schedule
    version). Audited. Fields omitted from the body are left unchanged; an explicit null clears the field.
    """
    from fastapi import HTTPException
    from backend.context.errors import raise_permission_denied, raise_resource_not_found
    from backend.rbac.permissions import Permission, has_permission
    from backend.repositories.activity_repo import ProjectActivityRepository

    if not has_permission(context.role, Permission.MANAGE_SCHEDULE):
        raise_permission_denied(Permission.MANAGE_SCHEDULE.value, context.role)
    changes = {k: (str(v) if v else None) for k, v in payload.model_dump(exclude_unset=True).items()}
    try:
        updated = ProjectActivityRepository.update_attribution(context, activity_id, changes)
    except LookupError as exc:
        raise_resource_not_found(str(exc).strip("'\""))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if not updated:
        raise_resource_not_found("Activity", activity_id)
    return updated

