"""
Project-Scoped Execution Event Repository for SETUAI V7.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.context.project import ProjectContext
from backend.repositories.base import BaseRepository


class ProjectExecutionEventRepository(BaseRepository):
    """
    Project-scoped repository for execution events (claims).
    Enforces strict project_id scoping on read and write.
    """

    @classmethod
    def get(cls, context: ProjectContext, event_id: str) -> Optional[Dict[str, Any]]:
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT event_id, schedule_id, project_id, stage_id, contractor_id,
                           work_package_id, event_date, raw_claim_text, input_channel,
                           event_type, status, created_at
                    FROM execution_events
                    WHERE event_id = %s AND project_id = %s;
                    """,
                    (event_id, context.project_id),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    @classmethod
    def list(
        cls,
        context: ProjectContext,
        schedule_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        query = """
            SELECT event_id, schedule_id, project_id, stage_id, contractor_id,
                   work_package_id, event_date, raw_claim_text, input_channel,
                   event_type, status, created_at
            FROM execution_events
            WHERE project_id = %(project_id)s
        """
        params: Dict[str, Any] = {"project_id": context.project_id, "limit": limit, "offset": offset}

        if schedule_id:
            query += " AND schedule_id = %(schedule_id)s"
            params["schedule_id"] = schedule_id
        if status:
            query += " AND status = %(status)s"
            params["status"] = status

        query += " ORDER BY event_date DESC, created_at DESC LIMIT %(limit)s OFFSET %(offset)s;"

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return [dict(r) for r in cur.fetchall()]

    @classmethod
    def create(cls, context: ProjectContext, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Insert execution event enforcing project context.
        Overrides any caller-supplied project_id with context.project_id.
        """
        insert_data = dict(data)
        insert_data["project_id"] = context.project_id

        cols = [
            "event_id", "schedule_id", "project_id", "event_date",
            "raw_claim_text", "input_channel", "event_type"
        ]
        # Include optional fields if provided
        for opt in ["stage_id", "contractor_id", "work_package_id", "quality_gate_id", "discipline", "location"]:
            if opt in insert_data:
                cols.append(opt)

        col_str = ", ".join(cols)
        val_str = ", ".join([f"%({c})s" for c in cols])

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO execution_events ({col_str}) VALUES ({val_str}) RETURNING event_id;",
                    insert_data,
                )
                conn.commit()
                return cls.get(context, insert_data["event_id"]) or insert_data
