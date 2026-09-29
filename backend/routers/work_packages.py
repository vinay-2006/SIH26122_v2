"""
Work Package Domain Router for SETUAI V7.
"""

from __future__ import annotations

import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status

from backend.context.project import ProjectContext, require_project_context
from backend.schemas.work_package import WorkPackageCreate, WorkPackageResponse, WorkPackageUpdate
from backend.services.work_package_service import WorkPackageService

router = APIRouter(prefix="/api/v1/projects", tags=["work-packages"])


@router.post(
    "/{project_id}/work-packages",
    response_model=WorkPackageResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_work_package(
    project_id: uuid.UUID,
    payload: WorkPackageCreate,
    context: ProjectContext = Depends(require_project_context),
) -> WorkPackageResponse:
    """
    Creates a new work package under the authorized project context.
    """
    created = WorkPackageService.create_work_package(context, payload)
    return WorkPackageResponse(**created)


@router.get(
    "/{project_id}/work-packages",
    response_model=List[WorkPackageResponse],
)
def list_work_packages(
    project_id: uuid.UUID,
    contractor_id: Optional[uuid.UUID] = Query(None, description="Filter by contractor ID"),
    context: ProjectContext = Depends(require_project_context),
) -> List[WorkPackageResponse]:
    """
    Lists all work packages belonging to the authorized project context.
    """
    packages = WorkPackageService.list_work_packages(context, contractor_id=contractor_id)
    return [WorkPackageResponse(**p) for p in packages]


@router.get(
    "/{project_id}/work-packages/{work_package_id}",
    response_model=WorkPackageResponse,
)
def get_work_package(
    project_id: uuid.UUID,
    work_package_id: uuid.UUID,
    context: ProjectContext = Depends(require_project_context),
) -> WorkPackageResponse:
    """
    Retrieves a work package by ID within the authorized project context.
    """
    package = WorkPackageService.get_work_package(context, work_package_id)
    return WorkPackageResponse(**package)


@router.patch(
    "/{project_id}/work-packages/{work_package_id}",
    response_model=WorkPackageResponse,
)
def update_work_package(
    project_id: uuid.UUID,
    work_package_id: uuid.UUID,
    payload: WorkPackageUpdate,
    context: ProjectContext = Depends(require_project_context),
) -> WorkPackageResponse:
    """
    Updates work package metadata within the authorized project context.
    """
    updated = WorkPackageService.update_work_package(context, work_package_id, payload)
    return WorkPackageResponse(**updated)
