"""
Schedule Version Domain Service for SETUAI V7.
Encapsulates schedule version lifecycle, transactional activation, supersession,
historical version preservation, metadata comparison, and audit logging.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from fastapi import HTTPException, status

from backend.context.errors import (
    raise_permission_denied,
    raise_resource_not_found,
)
from backend.context.project import ProjectContext
from backend.rbac.permissions import Permission, has_permission
from backend.repositories.audit_repo import ProjectAuditRepository
from backend.repositories.schedule_repo import (
    ProjectScheduleRepository,
    ScheduleVersionAlreadyExistsError,
    ScheduleVersionNotFoundError,
)
from backend.schemas.schedule_version import ScheduleVersionCreate


class ScheduleVersionService:
    """Service layer managing the Schedule Version Domain."""

    @classmethod
    def create_schedule_version(
        cls,
        context: ProjectContext,
        payload: ScheduleVersionCreate,
    ) -> Dict[str, Any]:
        """
        Creates a new schedule version under the authorized ProjectContext.
        Validates MANAGE_SCHEDULE permission and records an audit event.
        """
        if not has_permission(context.role, Permission.MANAGE_SCHEDULE):
            raise_permission_denied(Permission.MANAGE_SCHEDULE.value, context.role)

        try:
            created = ProjectScheduleRepository.create_version(
                context=context,
                version_code=payload.version_code,
                csv_content=payload.csv_content,
                data_date=payload.data_date,
                source_format=payload.source_format or "csv",
                version_metadata=payload.version_metadata,
                supersedes_schedule_id=payload.supersedes_schedule_id,
                activate_immediately=payload.activate_immediately,
            )
        except ScheduleVersionAlreadyExistsError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            ) from e
        except ScheduleVersionNotFoundError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            ) from e
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(e),
            ) from e

        ProjectAuditRepository.log(
            context=context,
            action="SCHEDULE_VERSION_CREATED",
            entity_type="SCHEDULE_VERSION",
            entity_id=created["schedule_id"],
            schedule_id=created["schedule_id"],
            new_state={
                "version_code": created.get("version_code"),
                "source_hash": created.get("source_hash"),
                "active": created.get("active"),
                "activity_count": created.get("activity_count"),
            },
        )

        return created

    @classmethod
    def activate_schedule_version(
        cls,
        context: ProjectContext,
        schedule_id: str,
    ) -> Dict[str, Any]:
        """
        Transactionally activates a schedule version within context.project_id.
        Deactivates any previous active version in the same project.
        CRITICAL INVARIANT: Historical versions remain historical and queryable.
        Historical claims are NOT moved or rematched.
        """
        if not has_permission(context.role, Permission.MANAGE_SCHEDULE):
            raise_permission_denied(Permission.MANAGE_SCHEDULE.value, context.role)

        try:
            activated, prev_active_id = ProjectScheduleRepository.activate_version(context, schedule_id)
        except ScheduleVersionNotFoundError as e:
            raise_resource_not_found("ScheduleVersion", schedule_id)

        ProjectAuditRepository.log(
            context=context,
            action="SCHEDULE_VERSION_ACTIVATED",
            entity_type="SCHEDULE_VERSION",
            entity_id=schedule_id,
            schedule_id=schedule_id,
            old_state={"active_schedule_id": prev_active_id},
            new_state={"active_schedule_id": schedule_id},
        )

        return {
            "schedule_id": activated["schedule_id"],
            "project_id": activated["project_id"],
            "version_code": activated.get("version_code"),
            "active": activated["active"],
            "previous_active_schedule_id": prev_active_id,
            "message": f"Schedule version '{schedule_id}' is now active for project '{context.project_id}'",
        }

    @classmethod
    def supersede_schedule_version(
        cls,
        context: ProjectContext,
        schedule_id: str,
        supersedes_schedule_id: str,
    ) -> Dict[str, Any]:
        """
        Links schedule_id as superseding supersedes_schedule_id within the project.
        Preserves historical version queryability and claim associations.
        """
        if not has_permission(context.role, Permission.MANAGE_SCHEDULE):
            raise_permission_denied(Permission.MANAGE_SCHEDULE.value, context.role)

        try:
            updated = ProjectScheduleRepository.supersede_version(
                context, schedule_id, supersedes_schedule_id
            )
        except ScheduleVersionNotFoundError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            ) from e

        ProjectAuditRepository.log(
            context=context,
            action="SCHEDULE_VERSION_SUPERSEDED",
            entity_type="SCHEDULE_VERSION",
            entity_id=schedule_id,
            schedule_id=schedule_id,
            old_state={},
            new_state={"supersedes_schedule_id": supersedes_schedule_id},
        )

        return updated

    @classmethod
    def get_schedule_versions(cls, context: ProjectContext) -> List[Dict[str, Any]]:
        """
        Lists all schedule versions (active and historical) for the authorized project.
        """
        if not has_permission(context.role, Permission.VIEW_SCHEDULE):
            raise_permission_denied(Permission.VIEW_SCHEDULE.value, context.role)

        return ProjectScheduleRepository.list(context)

    @classmethod
    def get_schedule_version(cls, context: ProjectContext, schedule_id: str) -> Dict[str, Any]:
        """
        Retrieves a specific schedule version belonging to the authorized project.
        Historical versions are fully accessible.
        """
        if not has_permission(context.role, Permission.VIEW_SCHEDULE):
            raise_permission_denied(Permission.VIEW_SCHEDULE.value, context.role)

        schedule = ProjectScheduleRepository.get(context, schedule_id)
        if not schedule:
            raise_resource_not_found("ScheduleVersion", schedule_id)
        return schedule

    @classmethod
    def compare_schedule_metadata(
        cls,
        context: ProjectContext,
        schedule_a_id: str,
        schedule_b_id: str,
    ) -> Dict[str, Any]:
        """
        Compares metadata between two schedule versions within the project.
        Metadata comparison only — no CPM or impact calculations.
        """
        if not has_permission(context.role, Permission.VIEW_SCHEDULE):
            raise_permission_denied(Permission.VIEW_SCHEDULE.value, context.role)

        try:
            return ProjectScheduleRepository.compare_metadata(context, schedule_a_id, schedule_b_id)
        except ScheduleVersionNotFoundError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            ) from e
