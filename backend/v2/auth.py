"""Authentication (Supabase-style JWT) and project-scoped authorization for the v2 API.

Identity comes from the verified token only. Project authority comes from an ACTIVE row in project_memberships, looked up on every
request; a project id in the URL is never proof of anything by itself. Being a PROJECT_MANAGER on project A grants nothing on project B."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional, Set

from fastapi import Depends, Path
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import jwt_verify
from .db import tx
from .errors import ApiError, forbidden
from .permissions import ROLE_PERMISSIONS

bearer = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    id: uuid.UUID
    email: str
    full_name: str
    capabilities: Set[str] = field(default_factory=set)       # platform grants: CREATE_PROJECT, PLATFORM_ADMIN

    def can(self, cap: str) -> bool:
        return cap in self.capabilities


@dataclass
class ProjectAccess:
    user: CurrentUser
    project_id: uuid.UUID
    role: str
    record_status: str

    def require(self, permission: str) -> "ProjectAccess":
        if permission not in ROLE_PERMISSIONS.get(self.role, set()):
            raise forbidden(f"role {self.role} may not {permission.lower().replace('_', ' ')}", "PERMISSION_DENIED")
        return self

    def require_writable(self) -> None:
        if self.record_status != "ACTIVE":
            raise ApiError(409, "PROJECT_ARCHIVED", "The project is archived and read-only")


def get_current_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer)) -> CurrentUser:
    if creds is None or not creds.credentials:
        raise ApiError(401, "UNAUTHENTICATED", "Missing bearer token")
    try:
        claims = jwt_verify.get_verifier().verify(creds.credentials)
    except jwt_verify.TokenError as e:
        raise ApiError(401, e.code, e.message)
    except jwt_verify.JwksUnavailable:
        raise ApiError(503, "AUTH_UNAVAILABLE", "Sign-in keys are temporarily unavailable; try again shortly")
    uid = claims.sub
    with tx() as c:
        p = c.execute("select id, email, full_name, is_active, tokens_valid_after from profiles where id = %s", (uid,)).fetchone()
        if p is None:
            raise ApiError(401, "NO_PROFILE", "No profile exists for this identity")
        if not p["is_active"]:
            raise forbidden("This account is deactivated", "ACCOUNT_DISABLED")
        if p["tokens_valid_after"] is not None and claims.iat < p["tokens_valid_after"].timestamp():
            raise ApiError(401, "TOKEN_REVOKED", "This session was revoked; sign in again")
        caps = {r["capability"] for r in c.execute(
            "select capability from platform_grants where user_id = %s and revoked_at is null", (uid,)).fetchall()}
    return CurrentUser(id=uid, email=p["email"], full_name=p["full_name"], capabilities=caps)


def project_access(project_id: uuid.UUID = Path(...), user: CurrentUser = Depends(get_current_user)) -> ProjectAccess:
    with tx() as c:
        row = c.execute(
            "select m.role, p.record_status from project_memberships m join projects p on p.project_id = m.project_id "
            "where m.project_id = %s and m.user_id = %s and m.status = 'ACTIVE'", (project_id, user.id)).fetchone()
    if row is None:
        raise forbidden("You are not a member of this project", "NOT_A_MEMBER")
    return ProjectAccess(user=user, project_id=project_id, role=row["role"], record_status=row["record_status"])


def require(permission: str, writable: bool = False):
    """Dependency factory: membership + permission (+ project not archived for writes)."""
    def dep(access: ProjectAccess = Depends(project_access)) -> ProjectAccess:
        access.require(permission)
        if writable:
            access.require_writable()
        return access
    return dep
