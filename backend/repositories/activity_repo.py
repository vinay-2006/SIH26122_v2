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
        context: ScheduleContext,
        activity_id: str,
        changes: Dict[str, Optional[str]],
    ) -> Optional[Dict[str, Any]]:
        """
        Set stage / contractor / work-package of ONE activity in ONE schedule version.

        `changes` contains only the fields the caller supplied (a None value clears the field). Every referenced
        entity must belong to the caller's project (a stage also to this schedule version); when both a work
        package and a contractor are given the work package's contractor must agree; a work package alone
        derives its contractor. The change is audited in the same transaction.
        """
        from backend.shared.audit import append_audit_record

        cols = "activity_id, schedule_id, project_id, stage_id, contractor_id, work_package_id, activity_name, wbs_code, discipline, location"
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {cols} FROM schedule_activities WHERE activity_id = %s AND schedule_id = %s AND project_id = %s FOR UPDATE",
                    (activity_id, context.schedule_id, context.project_id),
                )
                before = cur.fetchone()
                if before is None:
                    return None
                before = dict(before)
                new = {k: v for k, v in changes.items() if k in ("stage_id", "contractor_id", "work_package_id")}

                def _exists(sql, *args):
                    cur.execute(sql, args)
                    return cur.fetchone()

                if new.get("stage_id") and not _exists(
                    "SELECT 1 FROM stages WHERE stage_id = %s AND project_id = %s AND schedule_id = %s",
                    new["stage_id"], context.project_id, context.schedule_id):
                    raise LookupError("stage_id")
                if new.get("contractor_id") and not _exists(
                    "SELECT 1 FROM contractors WHERE contractor_id = %s AND project_id = %s", new["contractor_id"], context.project_id):
                    raise LookupError("contractor_id")
                if new.get("work_package_id"):
                    wp = _exists("SELECT contractor_id FROM work_packages WHERE work_package_id = %s AND project_id = %s",
                                 new["work_package_id"], context.project_id)
                    if not wp:
                        raise LookupError("work_package_id")
                    wp_con = wp["contractor_id"]
                    if new.get("contractor_id") and wp_con and str(wp_con) != str(new["contractor_id"]):
                        raise ValueError("contractor_id does not match the contractor of the work package")
                    if "contractor_id" not in new and wp_con:
                        new["contractor_id"] = str(wp_con)  # a work package alone derives its contractor

                if not new:
                    return before
                sets = ", ".join(f"{k} = %s" for k in new)
                cur.execute(
                    f"UPDATE schedule_activities SET {sets} WHERE activity_id = %s AND schedule_id = %s AND project_id = %s RETURNING {cols}",
                    (*new.values(), activity_id, context.schedule_id, context.project_id),
                )
                after = dict(cur.fetchone())
                append_audit_record(
                    cur, entity_type="SCHEDULE_ACTIVITY", entity_id=f"{context.schedule_id}:{activity_id}",
                    action="ATTRIBUTION_UPDATED", actor_id=str(context.user_id),
                    before_state={k: (str(v) if v is not None else None) for k, v in before.items()},
                    after_state={k: (str(v) if v is not None else None) for k, v in after.items()},
                    project_id=context.project_id, schedule_id=context.schedule_id, role=context.role,
                )
                conn.commit()
                return after

