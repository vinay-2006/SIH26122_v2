"""
Authentication and Authorization module for SETUAI (V6 compatibility bridge & V7 foundation).
"""

from __future__ import annotations

from typing import Optional, Set

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.auth.dependencies import (
    get_current_user as _v7_get_current_user,
    require_authenticated_user as _v7_require_authenticated_user,
    security,
)
from backend.auth.jwt import decode_supabase_jwt, get_jwks_client
from backend.auth.models import CurrentUser, UserProfile
from backend.rbac.roles import VALID_PROJECT_ROLES

VALID_ROLES: Set[str] = {"SITE_ENGINEER", "SUPERVISOR"}

# Canonical dependencies
get_current_user = _v7_get_current_user
require_authenticated_user = _v7_require_authenticated_user


def require_role(*required_roles: str):
    """
    FastAPI dependency factory enforcing that the authenticated caller
    possesses one of the required roles.
    Returns HTTP 403 Forbidden if the caller's role does not match any of them.
    """
    for required_role in required_roles:
        if required_role not in VALID_ROLES:
            raise ValueError(
                f"Invalid role '{required_role}'. "
                f"Allowed roles are: {sorted(VALID_ROLES)}"
            )

    def role_dependency(
        current_user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        if current_user.role not in required_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Operation requires role in {sorted(required_roles)}, "
                    f"but caller has role '{current_user.role}'"
                ),
            )

        return current_user

    return role_dependency