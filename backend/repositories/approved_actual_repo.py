"""
Project-Scoped Approved Actuals Repository for SETUAI V7.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.context.project import ProjectContext
from backend.repositories.base import BaseRepository


class ProjectApprovedActualRepository(BaseRepository):
    """
    Project-scoped repository for approved actuals.
    Enforces project_id isolation.
    """

    @classmethod
    def get(cls, context: ProjectContext, actual_id: str) -> Optional[Dict[str, Any]]:
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT actual_id, schedule_id, activity_id, project_id, stage_id,
                           actual_start, actual_finish, actual_quantity, actual_pct_complete,
                           decision_id, is_reopened, created_at
                    FROM approved_actuals
                    WHERE actual_id = %s AND project_id = %s;
                    """,
                    (actual_id, context.project_id),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    @classmethod
    def list(
        cls,
        context: ProjectContext,
        schedule_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        query = """
            SELECT actual_id, schedule_id, activity_id, project_id, stage_id,
                   actual_start, actual_finish, actual_quantity, actual_pct_complete,
                   decision_id, is_reopened, created_at
            FROM approved_actuals
            WHERE project_id = %(project_id)s
        """
        params: Dict[str, Any] = {"project_id": context.project_id, "limit": limit, "offset": offset}

        if schedule_id:
            query += " AND schedule_id = %(schedule_id)s"
            params["schedule_id"] = schedule_id

        query += " ORDER BY created_at DESC LIMIT %(limit)s OFFSET %(offset)s;"

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return [dict(r) for r in cur.fetchall()]
