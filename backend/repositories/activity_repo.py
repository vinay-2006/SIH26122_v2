"""
Project-Scoped Activity Repository for SETUAI V7.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from backend.context.project import ProjectContext
from backend.context.schedule import ScheduleContext
from backend.repositories.base import BaseRepository


class ProjectActivityRepository(BaseRepository):
    """
    Project- and schedule-scoped repository for schedule activities.
    Enforces both project_id and schedule_id isolation.
    """

    @classmethod
    def get(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        activity_id: str,
    ) -> Optional[Dict[str, Any]]:
        project_id = context.project_id
        schedule_id = getattr(context, "schedule_id", None)

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                if schedule_id:
                    cur.execute(
                        """
                        SELECT activity_id, schedule_id, project_id, stage_id, contractor_id,
                               work_package_id, activity_name, wbs_code, discipline, location,
                               planned_start, planned_finish, weight_factor, quality_gate_required
                        FROM schedule_activities
                        WHERE activity_id = %s AND project_id = %s AND schedule_id = %s;
                        """,
                        (activity_id, project_id, schedule_id),
                    )
                else:
                    cur.execute(
                        """
                        SELECT activity_id, schedule_id, project_id, stage_id, contractor_id,
                               work_package_id, activity_name, wbs_code, discipline, location,
                               planned_start, planned_finish, weight_factor, quality_gate_required
                        FROM schedule_activities
                        WHERE activity_id = %s AND project_id = %s;
                        """,
                        (activity_id, project_id),
                    )
                row = cur.fetchone()
                return dict(row) if row else None

    @classmethod
    def list(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        stage_id: Optional[str] = None,
        contractor_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        project_id = context.project_id
        schedule_id = getattr(context, "schedule_id", None)

        query = """
            SELECT activity_id, schedule_id, project_id, stage_id, contractor_id,
                   work_package_id, activity_name, wbs_code, discipline, location,
                   planned_start, planned_finish, weight_factor, quality_gate_required
            FROM schedule_activities
            WHERE project_id = %(project_id)s
        """
        params: Dict[str, Any] = {"project_id": project_id, "limit": limit, "offset": offset}

        if schedule_id:
            query += " AND schedule_id = %(schedule_id)s"
            params["schedule_id"] = schedule_id
        if stage_id:
            query += " AND stage_id = %(stage_id)s"
            params["stage_id"] = stage_id
        if contractor_id:
            query += " AND contractor_id = %(contractor_id)s"
            params["contractor_id"] = contractor_id

        query += " ORDER BY activity_id ASC LIMIT %(limit)s OFFSET %(offset)s;"

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return [dict(r) for r in cur.fetchall()]

    @classmethod
    def update_attribution(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        activity_id: str,
        contractor_id: Optional[str] = None,
        work_package_id: Optional[str] = None,
        stage_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        existing = cls.get(context, activity_id)
        if not existing:
            return None

        fields = []
        params = []
        if contractor_id is not None:
            fields.append("contractor_id = %s")
            params.append(contractor_id if contractor_id else None)
        if work_package_id is not None:
            fields.append("work_package_id = %s")
            params.append(work_package_id if work_package_id else None)
        if stage_id is not None:
            fields.append("stage_id = %s")
            params.append(stage_id if stage_id else None)

        if not fields:
            return existing

        params.extend([activity_id, context.project_id])
        schedule_id = getattr(context, "schedule_id", None)
        where_clause = "WHERE activity_id = %s AND project_id = %s"
        if schedule_id:
            where_clause += " AND schedule_id = %s"
            params.append(schedule_id)

        query = f"""
            UPDATE schedule_activities
            SET {', '.join(fields)}
            {where_clause}
            RETURNING activity_id, schedule_id, project_id, stage_id, contractor_id,
                      work_package_id, activity_name, wbs_code, discipline, location;
        """

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(params))
                row = cur.fetchone()
                conn.commit()
                return dict(row) if row else None

