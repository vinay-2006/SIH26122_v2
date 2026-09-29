"""
Read-Only Stage State Tool for Supervising Agent.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from backend.context.project import ProjectContext
from backend.services.stage_service import StageService
from backend.shared.db import get_connection

logger = logging.getLogger(__name__)


def get_stages_summary(context: ProjectContext, schedule_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Retrieves summary status for all stages in the project.
    Determines blocked status, quality gate clearance, and progress.
    """
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT stage_id, project_id, schedule_id, stage_name, stage_code, sequence_order, sequence_order AS sequence, status
                FROM stages
                WHERE project_id = %(project_id)s
                ORDER BY sequence_order ASC NULLS LAST
                """,
                {"project_id": context.project_id},
            ).fetchall()
            stages = [dict(r) for r in rows]
        stage_summaries = []
        for s in stages:
            s_id = s.get("stage_id")
            if isinstance(s_id, str):
                s_id = uuid.UUID(s_id)

            try:
                state_info = StageService.calculate_stage_state(context, s_id)
                comp_check = StageService.is_stage_complete(context, s_id)
                gates_check = StageService.check_stage_gates(context, s_id)
                deps_check = StageService.check_stage_dependencies(context, s_id)

                stage_summaries.append({
                    "stage_id": str(s_id),
                    "stage_code": s.get("stage_code"),
                    "stage_name": s.get("stage_name"),
                    "sequence": s.get("sequence"),
                    "computed_state": state_info.get("computed_state"),
                    "workflow_condition": state_info.get("workflow_condition"),
                    "total_activities": state_info.get("total_activities", 0),
                    "completed_activities": state_info.get("completed_activities", 0),
                    "in_progress_activities": state_info.get("in_progress_activities", 0),
                    "is_complete": comp_check.get("is_complete", False),
                    "is_blocked": not deps_check.get("is_satisfied", True) or not gates_check.get("all_cleared", True),
                    "blocking_reasons": comp_check.get("blocking_conditions", []),
                    "pending_gates": gates_check.get("pending_gates", 0),
                    "failed_gates": gates_check.get("failed_gates", 0),
                })
            except Exception as inner_err:
                logger.warning(f"[stage_state_tool] Error resolving stage {s_id}: {inner_err}")
                stage_summaries.append({
                    "stage_id": str(s_id),
                    "stage_name": s.get("stage_name"),
                    "error": str(inner_err),
                })
        return stage_summaries
    except Exception as e:
        logger.error(f"[stage_state_tool] Error listing stages: {e}")
        return []


def get_stage_details(context: ProjectContext, stage_id: uuid.UUID) -> Optional[Dict[str, Any]]:
    """Retrieves deep execution details for a specific stage."""
    try:
        stage = StageService.get_stage(context, stage_id)
        if not stage:
            return None
        state_info = StageService.calculate_stage_state(context, stage_id)
        progress_info = StageService.calculate_stage_progress(context, stage_id)
        gates_info = StageService.check_stage_gates(context, stage_id)
        deps_info = StageService.check_stage_dependencies(context, stage_id)
        comp_info = StageService.is_stage_complete(context, stage_id)

        return {
            "stage_id": str(stage_id),
            "stage_name": stage.get("stage_name"),
            "stage_code": stage.get("stage_code"),
            "state_info": state_info,
            "progress_info": progress_info,
            "gates_info": gates_info,
            "dependencies_info": deps_info,
            "completion_info": comp_info,
        }
    except Exception as e:
        logger.error(f"[stage_state_tool] Error getting stage details for {stage_id}: {e}")
        return None
