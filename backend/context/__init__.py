"""
SETUAI V7 Context Package.
"""

from backend.context.errors import (
    SecurityErrorCode,
    SecurityException,
    raise_auth_required,
    raise_invalid_project_context,
    raise_invalid_schedule_context,
    raise_invalid_token,
    raise_permission_denied,
    raise_project_denied,
    raise_resource_not_found,
    raise_role_required,
    raise_schedule_denied,
)
from backend.context.project import ProjectContext, require_project_context
from backend.context.schedule import ScheduleContext, require_schedule_context

__all__ = [
    "SecurityErrorCode",
    "SecurityException",
    "ProjectContext",
    "ScheduleContext",
    "require_project_context",
    "require_schedule_context",
    "raise_auth_required",
    "raise_invalid_token",
    "raise_project_denied",
    "raise_schedule_denied",
    "raise_permission_denied",
    "raise_role_required",
    "raise_invalid_project_context",
    "raise_invalid_schedule_context",
    "raise_resource_not_found",
]
