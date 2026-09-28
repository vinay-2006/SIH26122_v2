"""
Permission definitions and Role-Permission mapping for SETUAI V7.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, Set

from backend.rbac.roles import ProjectRole


class Permission(str, Enum):
    """Granular permissions for project-scoped operations in V7."""
    VIEW_PROJECT = "VIEW_PROJECT"
    VIEW_SCHEDULE = "VIEW_SCHEDULE"
    MANAGE_SCHEDULE = "MANAGE_SCHEDULE"
    CREATE_EXECUTION_EVENT = "CREATE_EXECUTION_EVENT"
    VIEW_EXECUTION_EVENTS = "VIEW_EXECUTION_EVENTS"
    REVIEW_CLAIM = "REVIEW_CLAIM"
    APPROVE_ACTUAL = "APPROVE_ACTUAL"
    REQUEST_REOPEN = "REQUEST_REOPEN"
    APPROVE_REOPEN = "APPROVE_REOPEN"
    MANAGE_QUALITY = "MANAGE_QUALITY"
    VIEW_AUDIT = "VIEW_AUDIT"


ROLE_PERMISSIONS: Dict[str, Set[Permission]] = {
    ProjectRole.OWNER.value: set(Permission),
    ProjectRole.PROJECT_MANAGER.value: set(Permission),
    ProjectRole.PLANNER.value: {
        Permission.VIEW_PROJECT,
        Permission.VIEW_SCHEDULE,
        Permission.MANAGE_SCHEDULE,
        Permission.VIEW_EXECUTION_EVENTS,
        Permission.REVIEW_CLAIM,
        Permission.VIEW_AUDIT,
    },
    ProjectRole.SUPERVISOR.value: {
        Permission.VIEW_PROJECT,
        Permission.VIEW_SCHEDULE,
        Permission.VIEW_EXECUTION_EVENTS,
        Permission.REVIEW_CLAIM,
        Permission.APPROVE_ACTUAL,
        Permission.REQUEST_REOPEN,
        Permission.APPROVE_REOPEN,
        Permission.MANAGE_QUALITY,
        Permission.VIEW_AUDIT,
    },
    ProjectRole.SITE_ENGINEER.value: {
        Permission.VIEW_PROJECT,
        Permission.VIEW_SCHEDULE,
        Permission.CREATE_EXECUTION_EVENT,
        Permission.VIEW_EXECUTION_EVENTS,
        Permission.REQUEST_REOPEN,
    },
    ProjectRole.QUALITY_INSPECTOR.value: {
        Permission.VIEW_PROJECT,
        Permission.VIEW_SCHEDULE,
        Permission.VIEW_EXECUTION_EVENTS,
        Permission.MANAGE_QUALITY,
        Permission.VIEW_AUDIT,
    },
    ProjectRole.AUDITOR.value: {
        Permission.VIEW_PROJECT,
        Permission.VIEW_SCHEDULE,
        Permission.VIEW_EXECUTION_EVENTS,
        Permission.VIEW_AUDIT,
    },
}


def has_permission(role: str, permission: Permission) -> bool:
    """Check if a project role possesses the specified permission."""
    perms = ROLE_PERMISSIONS.get(role, set())
    return permission in perms
