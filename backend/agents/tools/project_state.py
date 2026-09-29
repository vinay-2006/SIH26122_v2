"""
Read-Only Project State Tool for Supervising Agent.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from backend.context.project import ProjectContext
from backend.shared.db import get_connection

logger = logging.getLogger(__name__)


def get_project_status(context: ProjectContext) -> Dict[str, Any]:
    """Retrieves authoritative project metadata scoped to context.project_id."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT project_id, project_code, project_name, status, created_at
                FROM projects
                WHERE project_id = %(project_id)s
                """,
                {"project_id": context.project_id},
            ).fetchone()
            if not row:
                return {"project_id": str(context.project_id), "status": "UNKNOWN"}
            project = dict(row)
            return {
                "project_id": str(project.get("project_id")),
                "project_code": project.get("project_code"),
                "project_name": project.get("project_name"),
                "status": project.get("status"),
                "created_at": str(project.get("created_at")),
            }
    except Exception as e:
        logger.error(f"[project_state_tool] Error retrieving project: {e}")
        return {"project_id": str(context.project_id), "error": str(e)}


def get_active_schedule(context: ProjectContext) -> Optional[Dict[str, Any]]:
    """Retrieves the active schedule version for the project."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT schedule_id, version_code, active, created_at, version_metadata
                FROM schedules
                WHERE project_id = %(project_id)s AND active = TRUE
                LIMIT 1
                """,
                {"project_id": context.project_id},
            ).fetchone()
            if not row:
                return None
            res = dict(row)
            return {
                "schedule_id": str(res.get("schedule_id")),
                "version_code": res.get("version_code"),
                "is_active": bool(res.get("active")),
                "created_at": str(res.get("created_at")),
            }
    except Exception as e:
        logger.error(f"[project_state_tool] Error retrieving active schedule: {e}")
        return None
