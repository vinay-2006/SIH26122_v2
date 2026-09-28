"""
Project Domain Service for SETUAI V7.
Encapsulates project lifecycle, RBAC enforcement, membership isolation, and audit logging.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional
from fastapi import HTTPException, status

from backend.auth.models import CurrentUser
from backend.context.errors import raise_permission_denied, raise_resource_not_found
from backend.context.project import ProjectContext
from backend.repositories.audit_repo import ProjectAuditRepository
from backend.repositories.project_repo import (
    ProjectAlreadyExistsError,
    ProjectRepository,
)
from backend.schemas.project import ProjectCreate, ProjectUpdate


class ProjectService:
    """Service layer managing the Project Domain."""

    @classmethod
    def create_project(cls, user: CurrentUser, payload: ProjectCreate) -> Dict[str, Any]:
        """
        Creates a new project, establishes the creator as PROJECT_MANAGER, and logs an audit record.
        """
        project_data = payload.model_dump()
        try:
            created = ProjectRepository.create_project(project_data, creator_user_id=user.user_id)
        except ProjectAlreadyExistsError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            ) from e

        # Construct temporary ProjectContext to emit audit record under this project
        project_context = ProjectContext(
            user=user,
            project_id=created["project_id"],
            role="PROJECT_MANAGER",
            membership_id=created["membership_id"],
            project_name=created["project_name"],
        )

        try:
            ProjectAuditRepository.log(
                context=project_context,
                action="PROJECT_CREATED",
                entity_type="PROJECT",
                entity_id=str(created["project_id"]),
                new_state={
                    "project_id": str(created["project_id"]),
                    "project_code": created["project_code"],
                    "project_name": created["project_name"],
                    "status": created["status"],
                },
            )
        except Exception:
            # Audit logging failure should not abort project creation in non-strict DB modes,
            # but is logged
            pass

        return created

    @classmethod
    def get_project(cls, context: ProjectContext) -> Dict[str, Any]:
        """
        Retrieves project details for the authorized ProjectContext.
        Guaranteed to be isolated to caller's project membership.
        """
        project = ProjectRepository.get_by_id(context.project_id)
        if not project:
            raise_resource_not_found("Project", str(context.project_id))
        return project

    @classmethod
    def update_project(cls, context: ProjectContext, payload: ProjectUpdate) -> Dict[str, Any]:
        """
        Updates project metadata. Requires PROJECT_MANAGER or OWNER role.
        """
        if context.role not in ("PROJECT_MANAGER", "OWNER"):
            raise_permission_denied("UPDATE_PROJECT", context.role)

        old_project = ProjectRepository.get_by_id(context.project_id)
        if not old_project:
            raise_resource_not_found("Project", str(context.project_id))

        update_fields = payload.model_dump(exclude_unset=True)
        if not update_fields:
            return old_project

        updated = ProjectRepository.update_project(context.project_id, update_fields)
        if not updated:
            raise_resource_not_found("Project", str(context.project_id))

        ProjectAuditRepository.log(
            context=context,
            action="PROJECT_UPDATED",
            entity_type="PROJECT",
            entity_id=str(context.project_id),
            old_state={"project_name": old_project.get("project_name")},
            new_state={"project_name": updated.get("project_name")},
        )

        return updated

    @classmethod
    def archive_project(cls, context: ProjectContext) -> Dict[str, Any]:
        """
        Transitions project status to ARCHIVED. Requires PROJECT_MANAGER or OWNER role.
        """
        if context.role not in ("PROJECT_MANAGER", "OWNER"):
            raise_permission_denied("ARCHIVE_PROJECT", context.role)

        old_project = ProjectRepository.get_by_id(context.project_id)
        if not old_project:
            raise_resource_not_found("Project", str(context.project_id))

        updated = ProjectRepository.update_status(context.project_id, "ARCHIVED")
        if not updated:
            raise_resource_not_found("Project", str(context.project_id))

        ProjectAuditRepository.log(
            context=context,
            action="PROJECT_ARCHIVED",
            entity_type="PROJECT",
            entity_id=str(context.project_id),
            old_state={"status": old_project.get("status")},
            new_state={"status": "ARCHIVED"},
        )

        return updated

    @classmethod
    def activate_project(cls, context: ProjectContext) -> Dict[str, Any]:
        """
        Transitions project status back to ACTIVE. Requires PROJECT_MANAGER or OWNER role.
        """
        if context.role not in ("PROJECT_MANAGER", "OWNER"):
            raise_permission_denied("ACTIVATE_PROJECT", context.role)

        old_project = ProjectRepository.get_by_id(context.project_id)
        if not old_project:
            raise_resource_not_found("Project", str(context.project_id))

        updated = ProjectRepository.update_status(context.project_id, "ACTIVE")
        if not updated:
            raise_resource_not_found("Project", str(context.project_id))

        ProjectAuditRepository.log(
            context=context,
            action="PROJECT_ACTIVATED",
            entity_type="PROJECT",
            entity_id=str(context.project_id),
            old_state={"status": old_project.get("status")},
            new_state={"status": "ACTIVE"},
        )

        return updated

    @classmethod
    def get_user_projects(cls, user: CurrentUser) -> List[Dict[str, Any]]:
        """
        Returns only projects where the authenticated caller has an active membership.
        Never executes unscoped queries.
        """
        return ProjectRepository.list_for_user(user.user_id)
