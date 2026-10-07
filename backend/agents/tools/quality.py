"""
Read-Only Quality Integration Tool for Supervising Agent.
Exposes quality gates, holds, inspections, and evidence.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from backend.context.project import ProjectContext
from backend.shared.db import get_connection

logger = logging.getLogger(__name__)


def get_quality_holds(context: ProjectContext) -> List[Dict[str, Any]]:
    """Retrieves all active quality holds (PENDING or FAILED required gates) for the project."""
    try:
        with get_connection() as conn:
            query = """
                SELECT qg.quality_gate_id, qg.gate_type, qg.gate_name, qg.status,
                       qg.required, qg.due_date, qg.remarks, qg.stage_id, qg.activity_id,
                       st.stage_name, sa.activity_name
                FROM quality_gates qg
                LEFT JOIN stages st ON qg.stage_id = st.stage_id
                LEFT JOIN schedule_activities sa ON qg.activity_id = sa.activity_id
                     AND sa.project_id = qg.project_id AND sa.schedule_id = qg.schedule_id
                WHERE qg.project_id = %(project_id)s
                  AND qg.required = TRUE
                  AND qg.status IN ('PENDING', 'SUBMITTED', 'FAILED')
                  -- a "hold" is a HOLD-category point not yet released, or any failed required gate;
                  -- an ordinary pending inspection is not a hold
                  AND (qg.status = 'FAILED' OR qg.checkpoint_category = 'HOLD'
                       OR qg.gate_type IN ('INTERMEDIATE_HOLD', 'PRE_COMMENCEMENT'))
                ORDER BY qg.created_at DESC
            """
            rows = conn.execute(query, {"project_id": context.project_id}).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[quality_tool] Error getting quality holds: {e}")
        return []


def get_activity_quality_status(context: ProjectContext, activity_id: str) -> List[Dict[str, Any]]:
    """Retrieves all quality gates and associated evidence for a specific activity."""
    try:
        with get_connection() as conn:
            query = """
                SELECT qg.quality_gate_id, qg.gate_type, qg.gate_name, qg.status,
                       qg.required, qg.due_date, qg.passed_at, qg.remarks
                FROM quality_gates qg
                WHERE qg.project_id = %(project_id)s AND qg.activity_id = %(activity_id)s
                ORDER BY qg.created_at ASC
            """
            rows = conn.execute(
                query, {"project_id": context.project_id, "activity_id": activity_id}
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[quality_tool] Error getting quality status for activity {activity_id}: {e}")
        return []
