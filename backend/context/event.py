"""
Event-scoped access control for `/claims/{event_id}/...` routes.

Project context is EXPLICIT (X-Project-ID / path / query, validated against active membership), and the
event must belong to that project: a member of project A can never reach an event of project B by knowing
its id. Events with no project (legacy V6 rows) are unreachable through the API.
Errors: 401 unauthenticated, 400 no explicit project, 403 not a member / role lacks the permission /
event belongs to another project (or has none), 404 unknown event.
"""
from __future__ import annotations

from typing import Callable

from fastapi import Depends, Path
from pydantic import BaseModel

from backend.context.errors import (
    raise_permission_denied,
    raise_project_denied,
    raise_resource_not_found,
)
from backend.context.project import ProjectContext, require_project_context
from backend.rbac.permissions import Permission, has_permission
from backend.shared.db import get_connection


class EventContext(BaseModel):
    project_context: ProjectContext
    event_id: str
    schedule_id: str | None = None

    @property
    def project_id(self):
        return self.project_context.project_id

    @property
    def role(self) -> str:
        return self.project_context.role


def load_event_in_project(project_context: ProjectContext, event_id: str) -> EventContext:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT event_id, project_id, schedule_id FROM execution_events WHERE event_id = %s", (event_id,))
            row = cur.fetchone()
    if row is None:
        raise_resource_not_found("Execution claim", event_id)
    if row["project_id"] is None or str(row["project_id"]) != str(project_context.project_id):
        # same response for "other project" and "legacy project-less": nothing about the event is disclosed
        raise_project_denied("The claim does not belong to the authorized project")
    return EventContext(project_context=project_context, event_id=event_id, schedule_id=row["schedule_id"])


def require_event_permission(*permissions: Permission) -> Callable[..., EventContext]:
    """Dependency factory: explicit project + membership, event belongs to it, role holds ANY permission."""
    def dependency(
        event_id: str = Path(...),
        project_context: ProjectContext = Depends(require_project_context),
    ) -> EventContext:
        if not any(has_permission(project_context.role, p) for p in permissions):
            raise_permission_denied(permission=" | ".join(p.value for p in permissions), role=project_context.role)
        return load_event_in_project(project_context, event_id)

    return dependency
