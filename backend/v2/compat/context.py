from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from fastapi import Depends, Request

from .. import permissions as P
from ..auth import CurrentUser, ProjectAccess, get_current_user, project_access
from ..db import tx
from ..domain.common import ProjectActor
from ..errors import ApiError, forbidden


@dataclass
class Ctx:
    access: ProjectAccess
    actor: ProjectActor
    version_id: Optional[uuid.UUID]          # the schedule version being viewed (X-Schedule-ID), else the project's ACTIVE one
    active_version_id: Optional[uuid.UUID]

    @property
    def project_id(self) -> uuid.UUID:
        return self.access.project_id

    @property
    def user(self) -> CurrentUser:
        return self.access.user


def _uuid(raw: Optional[str], code: str, what: str) -> Optional[uuid.UUID]:
    if raw is None or not raw.strip():
        return None
    try:
        return uuid.UUID(raw.strip())
    except ValueError:
        raise ApiError(400, code, f"{what} is not a valid identifier")


def resolve(request: Request, user: CurrentUser, permission: Optional[str], writable: bool, project_override: Optional[str] = None, schedule_override: Optional[str] = None) -> Ctx:
    pid = _uuid(project_override or request.headers.get("X-Project-ID"), "INVALID_PROJECT_CONTEXT", "The project")
    if pid is None:
        raise ApiError(400, "INVALID_PROJECT_CONTEXT", "Select a project to continue")
    access = project_access(pid, user)                       # ACTIVE membership of THIS project, looked up now
    if permission:
        access.require(permission)
    if writable:
        access.require_writable()
    sid = _uuid(schedule_override or request.headers.get("X-Schedule-ID"), "INVALID_SCHEDULE_CONTEXT", "The schedule version")
    with tx() as c:
        active = c.execute("select version_id from schedule_versions where project_id = %s and status = 'ACTIVE'", (pid,)).fetchone()
        if sid is not None and c.execute("select 1 from schedule_versions where project_id = %s and version_id = %s", (pid, sid)).fetchone() is None:
            raise forbidden("That schedule version does not belong to the selected project", "SCHEDULE_ACCESS_DENIED")
    active_id = active["version_id"] if active else None
    return Ctx(access=access, actor=ProjectActor(access.user.id, pid, access.role, access.record_status), version_id=sid or active_id, active_version_id=active_id)


def legacy_ctx(permission: Optional[str] = None, writable: bool = False) -> Callable[..., Ctx]:
    def dep(request: Request, user: CurrentUser = Depends(get_current_user)) -> Ctx:
        return resolve(request, user, permission, writable)
    return dep


def require_version(ctx: Ctx) -> uuid.UUID:
    if ctx.version_id is None:
        raise ApiError(409, "NO_ACTIVE_SCHEDULE", "The project has no active schedule: a Project Manager must activate one first")
    return ctx.version_id


def path_ctx(permission: Optional[str] = None, writable: bool = False) -> Callable[..., Ctx]:
    """for the legacy routes that carry the project (and schedule) in the URL: the path is the context; it is checked exactly like the headers"""
    def dep(request: Request, user: CurrentUser = Depends(get_current_user)) -> Ctx:
        return resolve(request, user, permission, writable, project_override=request.path_params.get("project_id"), schedule_override=request.path_params.get("schedule_id"))
    return dep
