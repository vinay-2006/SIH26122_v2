"""
Read-Only Activity State Tool for Supervising Agent.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.shared.workflow_flags import with_workflow_flags
from backend.context.project import ProjectContext
from backend.services.stage_service import StageService
from backend.shared.db import get_connection

logger = logging.getLogger(__name__)


def get_critical_and_blocked_activities(
    context: ProjectContext, schedule_id: Optional[str] = None
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Identifies activities requiring supervisory attention:
    - Blocked activities (quality hold, dependency block, or BLOCKED condition)
    - Reopened or rework activities
    - Critical / delayed activities
    """
    blocked_activities: List[Dict[str, Any]] = []
    rework_activities: List[Dict[str, Any]] = []
    delayed_activities: List[Dict[str, Any]] = []

    try:
        with get_connection() as conn:
            query = """
                SELECT sa.activity_id, sa.activity_name, sa.stage_id, sa.schedule_id,
                       sa.weight_factor, sa.quality_gate_required, sa.planned_start, sa.planned_finish,
                       aa.actual_pct_complete, aa.actual_start, aa.actual_finish, aa.is_reopened,
                       (SELECT ee.reopen_status FROM execution_events ee
                         WHERE ee.matched_activity_id = sa.activity_id AND ee.schedule_id = sa.schedule_id
                           AND ee.project_id = sa.project_id
                         ORDER BY ee.event_date DESC, ee.created_at DESC LIMIT 1) AS reopen_status,
                       st.stage_name
                FROM schedule_activities sa
                LEFT JOIN stages st ON sa.stage_id = st.stage_id
                LEFT JOIN approved_actuals aa ON aa.schedule_id = sa.schedule_id AND aa.activity_id = sa.activity_id
                WHERE sa.project_id = %(project_id)s
            """
            params: Dict[str, Any] = {"project_id": context.project_id}
            if schedule_id:
                query += " AND sa.schedule_id = %(schedule_id)s"
                params["schedule_id"] = schedule_id

            rows = conn.execute(with_workflow_flags(query), params).fetchall()

            for r in rows:
                item = dict(r)
                exec_state = StageService.get_execution_state(item)
                workflow_cond = StageService.get_workflow_condition(item)

                act_summary = {
                    "activity_id": item.get("activity_id"),
                    "activity_name": item.get("activity_name"),
                    "stage_id": str(item.get("stage_id")) if item.get("stage_id") else None,
                    "stage_name": item.get("stage_name"),
                    "execution_state": exec_state,
                    "workflow_condition": workflow_cond,
                    "actual_pct_complete": item.get("actual_pct_complete"),
                    "is_reopened": bool(item.get("is_reopened")),
                    "reopen_status": item.get("reopen_status"),
                    "planned_finish": str(item.get("planned_finish")) if item.get("planned_finish") else None,
                    "actual_finish": str(item.get("actual_finish")) if item.get("actual_finish") else None,
                }

                # Check if blocked
                if workflow_cond in ("BLOCKED", "QUALITY_HOLD"):
                    blocked_activities.append(act_summary)

                # Check if rework
                if workflow_cond in ("REOPEN_REQUESTED", "REWORK_IN_PROGRESS") or item.get("is_reopened"):
                    rework_activities.append(act_summary)

                # Check if delayed
                if item.get("planned_finish") and item.get("actual_finish"):
                    if item["actual_finish"] > item["planned_finish"]:
                        act_summary["delay_reason"] = "Actual finish exceeded planned finish"
                        delayed_activities.append(act_summary)
                elif item.get("planned_finish") and exec_state != "COMPLETED":
                    # Potentially delayed if today > planned_finish
                    pass

        return {
            "blocked": blocked_activities,
            "rework": rework_activities,
            "delayed": delayed_activities,
        }
    except Exception as e:
        logger.error(f"[activity_state_tool] Error getting critical activities: {e}")
        return {"blocked": [], "rework": [], "delayed": []}


def get_activity_state(
    context: ProjectContext, activity_id: str, schedule_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Retrieves execution context and approved actual for an individual activity."""
    try:
        with get_connection() as conn:
            query = """
                SELECT sa.*, aa.actual_pct_complete, aa.actual_start, aa.actual_finish,
                       aa.is_reopened, aa.created_at AS approved_at,
                       (SELECT ee.reopen_status FROM execution_events ee
                         WHERE ee.matched_activity_id = sa.activity_id AND ee.schedule_id = sa.schedule_id
                           AND ee.project_id = sa.project_id
                         ORDER BY ee.event_date DESC, ee.created_at DESC LIMIT 1) AS reopen_status,
                       st.stage_name
                FROM schedule_activities sa
                LEFT JOIN stages st ON sa.stage_id = st.stage_id
                LEFT JOIN approved_actuals aa ON aa.schedule_id = sa.schedule_id AND aa.activity_id = sa.activity_id
                WHERE sa.project_id = %(project_id)s AND sa.activity_id = %(activity_id)s
            """
            params: Dict[str, Any] = {"project_id": context.project_id, "activity_id": activity_id}
            if schedule_id:
                query += " AND sa.schedule_id = %(schedule_id)s"
                params["schedule_id"] = schedule_id

            row = conn.execute(with_workflow_flags(query), params).fetchone()
            if not row:
                return None
            item = dict(row)
            item["canonical_state"] = StageService.get_execution_state(item)
            item["workflow_condition"] = StageService.get_workflow_condition(item)
            return item
    except Exception as e:
        logger.error(f"[activity_state_tool] Error getting activity {activity_id}: {e}")
        return None
