"""
Project- and Schedule-Scoped Progress Repository for SETUAI V7 Phase 8.
Enforces project isolation, single bulk queries (zero N+1), and authoritative approved actuals access.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Union

from backend.context.project import ProjectContext
from backend.context.schedule import ScheduleContext
from backend.repositories.base import BaseRepository
from backend.shared.workflow_flags import with_workflow_flags


class ProjectProgressRepository(BaseRepository):
    """
    Project- and schedule-scoped repository for progress rollups.
    """

    @classmethod
    def get_activity_with_actual(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        activity_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves a single activity joined with its current authoritative approved actual.
        """
        project_id = context.project_id
        schedule_id = getattr(context, "schedule_id", None)

        query = """
            SELECT sa.activity_id, sa.schedule_id, sa.project_id, sa.stage_id,
                   sa.activity_name, sa.wbs_code, sa.discipline, sa.location,
                   sa.planned_start, sa.planned_finish, sa.planned_quantity,
                   sa.baseline_pct_complete, sa.weight_factor, sa.quality_gate_required,
                   aa.actual_id, aa.actual_start, aa.actual_finish,
                   aa.actual_pct_complete, aa.actual_quantity, aa.is_reopened,
                   aa.reopened_at, aa.rework_notes,
                   (
                       SELECT ee.reopen_status FROM execution_events ee
                       WHERE ee.matched_activity_id = sa.activity_id AND ee.schedule_id = sa.schedule_id
                       ORDER BY ee.event_date DESC LIMIT 1
                   ) AS reopen_status
            FROM schedule_activities sa
            LEFT JOIN approved_actuals aa
                   ON sa.schedule_id = aa.schedule_id AND sa.activity_id = aa.activity_id
            WHERE sa.project_id = %(project_id)s
              AND sa.activity_id = %(activity_id)s
        """
        params: Dict[str, Any] = {"project_id": project_id, "activity_id": activity_id}
        if schedule_id:
            query += " AND sa.schedule_id = %(schedule_id)s"
            params["schedule_id"] = schedule_id
        query = with_workflow_flags(query)

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                return dict(row) if row else None

    @classmethod
    def get_schedule_activities_with_actuals(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        stage_id: Optional[Union[str, uuid.UUID]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Bulk-loads all activities for the scoped project and schedule version,
        joined with their current authoritative approved actuals in a single query.
        """
        project_id = context.project_id
        schedule_id = getattr(context, "schedule_id", None)

        query = """
            SELECT sa.activity_id, sa.schedule_id, sa.project_id, sa.stage_id,
                   sa.activity_name, sa.wbs_code, sa.discipline, sa.location,
                   sa.planned_start, sa.planned_finish, sa.planned_quantity,
                   sa.baseline_pct_complete, sa.weight_factor, sa.quality_gate_required,
                   aa.actual_id, aa.actual_start, aa.actual_finish,
                   aa.actual_pct_complete, aa.actual_quantity, aa.is_reopened,
                   aa.reopened_at, aa.rework_notes,
                   (
                       SELECT ee.reopen_status FROM execution_events ee
                       WHERE ee.matched_activity_id = sa.activity_id AND ee.schedule_id = sa.schedule_id
                       ORDER BY ee.event_date DESC LIMIT 1
                   ) AS reopen_status
            FROM schedule_activities sa
            LEFT JOIN approved_actuals aa
                   ON sa.schedule_id = aa.schedule_id AND sa.activity_id = aa.activity_id
            WHERE sa.project_id = %(project_id)s
        """
        params: Dict[str, Any] = {"project_id": project_id}

        if schedule_id:
            query += " AND sa.schedule_id = %(schedule_id)s"
            params["schedule_id"] = schedule_id

        if stage_id:
            query += " AND sa.stage_id = %(stage_id)s"
            params["stage_id"] = str(stage_id)

        query += " ORDER BY sa.activity_id ASC;"
        query = with_workflow_flags(query)

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return [dict(r) for r in cur.fetchall()]

    @classmethod
    def get_schedule_stages(
        cls,
        context: Union[ScheduleContext, ProjectContext],
    ) -> List[Dict[str, Any]]:
        """
        Bulk-loads all stages for the scoped project and schedule version.
        """
        project_id = context.project_id
        schedule_id = getattr(context, "schedule_id", None)

        query = """
            SELECT stage_id, project_id, schedule_id, stage_code, stage_name,
                   parent_stage_id, sequence_order, weight_pct, status,
                   planned_start, planned_finish, contract_milestone_date
            FROM stages
            WHERE project_id = %(project_id)s
        """
        params: Dict[str, Any] = {"project_id": project_id}

        if schedule_id:
            query += " AND schedule_id = %(schedule_id)s"
            params["schedule_id"] = schedule_id

        query += " ORDER BY sequence_order ASC, stage_name ASC;"

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return [dict(r) for r in cur.fetchall()]

    @classmethod
    def get_project_schedules(
        cls,
        context: ProjectContext,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves all schedule versions belonging to the project.
        """
        query = """
            SELECT schedule_id, project_id, project_name, version_code,
                   active, created_at
            FROM schedules
            WHERE project_id = %s
            ORDER BY active DESC, created_at DESC;
        """
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (context.project_id,))
                return [dict(r) for r in cur.fetchall()]
