"""
Work Package Domain Service for SETUAI V7.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional
from fastapi import HTTPException, status

from backend.context.errors import raise_permission_denied, raise_resource_not_found
from backend.context.project import ProjectContext
from backend.repositories.audit_repo import ProjectAuditRepository
from backend.repositories.contractor_repo import ContractorNotFoundError
from backend.repositories.work_package_repo import (
    ProjectWorkPackageRepository,
    WorkPackageAlreadyExistsError,
    WorkPackageNotFoundError,
)
from backend.schemas.work_package import WorkPackageCreate, WorkPackageUpdate


class WorkPackageService:
    """Service layer managing the Work Package Domain."""

    @classmethod
    def create_work_package(
        cls,
        context: ProjectContext,
        payload: WorkPackageCreate,
    ) -> Dict[str, Any]:
        try:
            created = ProjectWorkPackageRepository.create(
                context=context,
                package_name=payload.package_name,
                contractor_id=payload.contractor_id,
                stage_id=payload.stage_id,
                package_code=payload.package_code,
                discipline=payload.discipline,
                description=payload.description,
                planned_start=payload.planned_start,
                planned_finish=payload.planned_finish,
                status=payload.status or "NOT_STARTED",
            )
        except WorkPackageAlreadyExistsError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            ) from e
        except ContractorNotFoundError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            ) from e

        ProjectAuditRepository.log(
            context=context,
            action="WORK_PACKAGE_CREATED",
            entity_type="WORK_PACKAGE",
            entity_id=str(created["work_package_id"]),
            new_state={
                "package_code": created.get("package_code"),
                "package_name": created.get("package_name"),
            },
        )
        return created

    @classmethod
    def get_work_package(
        cls,
        context: ProjectContext,
        work_package_id: uuid.UUID | str,
    ) -> Dict[str, Any]:
        package = ProjectWorkPackageRepository.get(context, work_package_id)
        if not package:
            raise_resource_not_found("WorkPackage", str(work_package_id))
        return package

    @classmethod
    def list_work_packages(
        cls,
        context: ProjectContext,
        contractor_id: Optional[uuid.UUID | str] = None,
    ) -> List[Dict[str, Any]]:
        return ProjectWorkPackageRepository.list(context, contractor_id=contractor_id)

    @classmethod
    def update_work_package(
        cls,
        context: ProjectContext,
        work_package_id: uuid.UUID | str,
        payload: WorkPackageUpdate,
    ) -> Dict[str, Any]:
        try:
            updated = ProjectWorkPackageRepository.update(
                context=context,
                work_package_id=work_package_id,
                package_name=payload.package_name,
                contractor_id=payload.contractor_id,
                stage_id=payload.stage_id,
                discipline=payload.discipline,
                description=payload.description,
                planned_start=payload.planned_start,
                planned_finish=payload.planned_finish,
                status=payload.status,
            )
        except WorkPackageNotFoundError as e:
            raise_resource_not_found("WorkPackage", str(work_package_id))
        except ContractorNotFoundError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            ) from e

        ProjectAuditRepository.log(
            context=context,
            action="WORK_PACKAGE_UPDATED",
            entity_type="WORK_PACKAGE",
            entity_id=str(work_package_id),
            new_state={"package_name": updated.get("package_name"), "status": updated.get("status")},
        )
        return updated
