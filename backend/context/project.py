"""
Project Context resolution and validation for SETUAI V7.
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import Depends, Header, Path, Query, Request
from pydantic import BaseModel, Field

from backend.auth.dependencies import get_current_user
from backend.auth.models import CurrentUser
from backend.context.errors import (
    raise_invalid_project_context,
    raise_project_denied,
)
from backend.shared.db import get_connection


class ProjectContext(BaseModel):
    """
    Authoritative project context for the current request.
    Encapsulates user identity, project identity, and the user's project-specific role.
    """
    user: CurrentUser
    project_id: uuid.UUID = Field(description="Authorized Project UUID")
    role: str = Field(description="Role assigned specifically within this project")
    membership_id: uuid.UUID = Field(description="Project membership identifier")
    project_name: Optional[str] = Field(default=None, description="Project name")

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.user_id


def resolve_raw_project_id(
    request: Request,
    x_project_id: Optional[str] = Header(None, alias="X-Project-ID"),
    query_project_id: Optional[str] = Query(None, alias="project_id"),
) -> Optional[str]:
    """
    Extracts raw project_id string from HTTP header, query params, or path parameters.
    Header X-Project-ID takes precedence.
    """
    if x_project_id:
        return x_project_id.strip()
    if query_project_id:
        return query_project_id.strip()
    if "project_id" in request.path_params:
        return str(request.path_params["project_id"]).strip()
    return None


def require_project_context(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    raw_project_id: Optional[str] = Depends(resolve_raw_project_id),
) -> ProjectContext:
    """
    FastAPI dependency that resolves and validates ProjectContext.
    
    CRITICAL SECURITY INVARIANT:
    Project ID supplied in request is NEVER trusted as proof of authorization.
    This dependency looks up `project_memberships` in PostgreSQL:
      - Validates user has a membership in the requested project
      - Validates membership is ACTIVE (active = TRUE)
      - Resolves the project-specific role (assigned_role)
    """
    user_uuid = current_user.user_id

    # If project_id was explicitly requested
    if raw_project_id:
        try:
            target_project_uuid = uuid.UUID(raw_project_id)
        except ValueError:
            raise_invalid_project_context(f"Invalid project_id format: '{raw_project_id}'")

        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT pm.membership_id, pm.project_id, pm.assigned_role, pm.active, p.project_name
                        FROM project_memberships pm
                        JOIN projects p ON p.project_id = pm.project_id
                        WHERE pm.user_id = %s AND pm.project_id = %s;
                        """,
                        (user_uuid, target_project_uuid),
                    )
                    row = cur.fetchone()
        except Exception as e:
            raise_project_denied(f"Database error during membership verification: {str(e)}")

        if not row:
            raise_project_denied(f"Caller '{user_uuid}' is not a member of project '{target_project_uuid}'")

        is_active = row["active"] if isinstance(row, dict) else row[3]
        if not is_active:
            raise_project_denied("Project membership is suspended or inactive")

        return ProjectContext(
            user=current_user,
            project_id=row["project_id"] if isinstance(row, dict) else row[1],
            role=row["assigned_role"] if isinstance(row, dict) else row[2],
            membership_id=row["membership_id"] if isinstance(row, dict) else row[0],
            project_name=row["project_name"] if isinstance(row, dict) else row[4],
        )

    # If no project_id was explicitly specified, attempt to resolve user's single active project
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT pm.membership_id, pm.project_id, pm.assigned_role, pm.active, p.project_name
                    FROM project_memberships pm
                    JOIN projects p ON p.project_id = pm.project_id
                    WHERE pm.user_id = %s AND pm.active = TRUE
                    ORDER BY pm.created_at ASC;
                    """,
                    (user_uuid,),
                )
                rows = cur.fetchall()
    except Exception as e:
        raise_project_denied(f"Database error resolving project memberships: {str(e)}")

    if not rows:
        raise_project_denied(f"Caller '{user_uuid}' has no active project memberships")

    if len(rows) > 1:
        raise_invalid_project_context(
            "User is a member of multiple active projects; explicit X-Project-ID header or project_id parameter is required"
        )

    row = rows[0]
    return ProjectContext(
        user=current_user,
        project_id=row["project_id"] if isinstance(row, dict) else row[1],
        role=row["assigned_role"] if isinstance(row, dict) else row[2],
        membership_id=row["membership_id"] if isinstance(row, dict) else row[0],
        project_name=row["project_name"] if isinstance(row, dict) else row[4],
    )
