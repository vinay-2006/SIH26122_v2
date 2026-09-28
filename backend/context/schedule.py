"""
Schedule Context resolution and cross-project validation for SETUAI V7.
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import Depends, Header, Path, Query, Request
from pydantic import BaseModel, Field

from backend.auth.models import CurrentUser
from backend.context.errors import (
    raise_invalid_schedule_context,
    raise_resource_not_found,
    raise_schedule_denied,
)
from backend.context.project import ProjectContext, require_project_context
from backend.shared.db import get_connection


class ScheduleContext(BaseModel):
    """
    Authoritative schedule context for schedule-scoped operations.
    Inherits project context and validates schedule-project boundary.
    """
    project_context: ProjectContext
    schedule_id: str = Field(description="Validated schedule version identifier")

    @property
    def user(self) -> CurrentUser:
        return self.project_context.user

    @property
    def user_id(self) -> uuid.UUID:
        return self.project_context.user_id

    @property
    def project_id(self) -> uuid.UUID:
        return self.project_context.project_id

    @property
    def role(self) -> str:
        return self.project_context.role

    @property
    def membership_id(self) -> uuid.UUID:
        return self.project_context.membership_id


def resolve_raw_schedule_id(
    request: Request,
    x_schedule_id: Optional[str] = Header(None, alias="X-Schedule-ID"),
    query_schedule_id: Optional[str] = Query(None, alias="schedule_id"),
) -> Optional[str]:
    """Extracts raw schedule_id from header, query, or path."""
    if x_schedule_id:
        return x_schedule_id.strip()
    if query_schedule_id:
        return query_schedule_id.strip()
    if "schedule_id" in request.path_params:
        return str(request.path_params["schedule_id"]).strip()
    return None


def require_schedule_context(
    request: Request,
    project_context: ProjectContext = Depends(require_project_context),
    raw_schedule_id: Optional[str] = Depends(resolve_raw_schedule_id),
) -> ScheduleContext:
    """
    FastAPI dependency validating that the requested schedule exists AND belongs
    to the authorized ProjectContext.
    
    CRITICAL SECURITY INVARIANT:
    Never authorize a schedule merely because the caller knows its ID.
    If schedule.project_id != project_context.project_id, the request is REJECTED.
    Implicit fallback to an active/latest schedule is forbidden to prevent cross-version contamination.
    """
    if not raw_schedule_id:
        raise_invalid_schedule_context(
            "Explicit schedule_id is required for schedule-scoped operations; implicit fallback is disallowed."
        )

    target_sched = raw_schedule_id.strip()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT schedule_id, project_id FROM schedules WHERE schedule_id = %s;",
                    (target_sched,),
                )
                row = cur.fetchone()
    except Exception as e:
        raise_schedule_denied(f"Database error verifying schedule: {str(e)}")

    if not row:
        raise_resource_not_found("Schedule", target_sched)

    row_proj_id = row["project_id"] if isinstance(row, dict) else row[1]
    
    # Verify schedule ownership matches project context
    if row_proj_id is None or str(row_proj_id) != str(project_context.project_id):
        raise_schedule_denied(
            f"Schedule '{target_sched}' does not belong to authorized project context '{project_context.project_id}'"
        )

    return ScheduleContext(
        project_context=project_context,
        schedule_id=target_sched,
    )
