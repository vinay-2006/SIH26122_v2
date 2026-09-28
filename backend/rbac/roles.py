"""
Role definitions for SETUAI V7.
"""

from __future__ import annotations

from enum import Enum
from typing import Set


class ProjectRole(str, Enum):
    """Authoritative project roles established in V7 schema & PRD."""
    OWNER = "OWNER"
    PROJECT_MANAGER = "PROJECT_MANAGER"
    PLANNER = "PLANNER"
    SUPERVISOR = "SUPERVISOR"
    SITE_ENGINEER = "SITE_ENGINEER"
    QUALITY_INSPECTOR = "QUALITY_INSPECTOR"
    AUDITOR = "AUDITOR"


VALID_PROJECT_ROLES: Set[str] = {role.value for role in ProjectRole}
