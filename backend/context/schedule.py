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
    """Extracts raw schedule_id from path, header or query.

    A path parameter is authoritative: it is what the route handler will actually read. If a header
    or query value is also supplied it MUST agree, otherwise the caller could authorize schedule X
    via the header while the handler operates on schedule Y from the path (context substitution).
    """
    supplied = [v.strip() for v in (x_schedule_id, query_schedule_id) if v and v.strip()]
    if "schedule_id" in request.path_params:
        path_value = str(request.path_params["schedule_id"]).strip()
        if any(v != path_value for v in supplied):
            raise_invalid_schedule_context(
                "schedule_id in the path conflicts with X-Schedule-ID / schedule_id query parameter"
            )
        return path_value
    if len(set(supplied)) > 1:
        raise_invalid_schedule_context("X-Schedule-ID conflicts with the schedule_id query parameter")
    return supplied[0] if supplied else None


def validate_schedule_for_project(project_context: ProjectContext, schedule_id: str) -> ScheduleContext:
    """The schedule must exist AND belong to the already-authorized project. Never authorize a schedule
    merely because the caller knows its ID; never fall back to an active/latest schedule."""
    target = (schedule_id or "").strip()
    if not target:
        raise_invalid_schedule_context(
            "Explicit schedule_id is required for schedule-scoped operations; implicit fallback is disallowed."
        )
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT schedule_id, project_id FROM schedules WHERE schedule_id = %s;", (target,))
                row = cur.fetchone()
    except Exception as e:
        raise_schedule_denied(f"Database error verifying schedule: {str(e)}")

    if not row:
        raise_resource_not_found("Schedule", target)

    row_proj_id = row["project_id"] if isinstance(row, dict) else row[1]
    if row_proj_id is None or str(row_proj_id) != str(project_context.project_id):
        raise_schedule_denied(
            f"Schedule '{target}' does not belong to authorized project context '{project_context.project_id}'"
        )
    return ScheduleContext(project_context=project_context, schedule_id=target)


def resolve_explicit_schedule(project_context: ProjectContext, *candidates: Optional[str]) -> ScheduleContext:
    """For handlers whose schedule_id may arrive in several places (body, form, header, query): at least
    one must be present and all present values must agree."""
    supplied = [c.strip() for c in candidates if c and c.strip()]
    if not supplied:
        raise_invalid_schedule_context(
            "Explicit schedule_id is required (X-Schedule-ID header, schedule_id parameter or request body); "
            "implicit active/latest fallback is disallowed."
        )
    if len(set(supplied)) > 1:
        raise_invalid_schedule_context("Conflicting schedule_id values supplied in the request")
    return validate_schedule_for_project(project_context, supplied[0])


def require_schedule_context(
    request: Request,
    project_context: ProjectContext = Depends(require_project_context),
    raw_schedule_id: Optional[str] = Depends(resolve_raw_schedule_id),
) -> ScheduleContext:
    """
    FastAPI dependency validating that the requested schedule exists AND belongs
    to the authorized ProjectContext (explicit project, explicit schedule; no fallback).
    """
    return validate_schedule_for_project(project_context, raw_schedule_id or "")
