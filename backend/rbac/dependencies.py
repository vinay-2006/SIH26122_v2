"""
RBAC FastAPI dependencies for SETUAI V7.
"""

from __future__ import annotations

from typing import Callable, Union

from fastapi import Depends

from backend.context.errors import raise_permission_denied, raise_role_required
from backend.context.project import ProjectContext, require_project_context
from backend.rbac.permissions import Permission, has_permission
from backend.rbac.roles import ProjectRole


def require_permission(
    permission: Permission,
) -> Callable[[ProjectContext], ProjectContext]:
    """
    FastAPI dependency factory enforcing that the caller's project-specific role
    possesses the requested permission.
    """
    def permission_dependency(
        project_context: ProjectContext = Depends(require_project_context),
    ) -> ProjectContext:
        if not has_permission(project_context.role, permission):
            raise_permission_denied(
                permission=permission.value,
                role=project_context.role,
            )
        return project_context

    return permission_dependency


def require_project_role(
    *allowed_roles: Union[str, ProjectRole],
) -> Callable[[ProjectContext], ProjectContext]:
    """
    FastAPI dependency factory enforcing that the caller's project-specific role
    matches one of the allowed roles.
    """
    role_strings = [r.value if isinstance(r, ProjectRole) else str(r) for r in allowed_roles]

    def role_dependency(
        project_context: ProjectContext = Depends(require_project_context),
    ) -> ProjectContext:
        if project_context.role not in role_strings:
            raise_role_required(
                required_roles=role_strings,
                role=project_context.role,
            )
        return project_context

    return role_dependency
