"""
Read-Only Impact Engine Tool for Supervising Agent.
Exposes compound schedule delays and critical-path impacts from Phase 10.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.context.project import ProjectContext
from backend.shared.db import get_connection

logger = logging.getLogger(__name__)


def get_impact_summary(context: ProjectContext, schedule_id: Optional[str] = None) -> Dict[str, Any]:
    """Retrieves recent compound impact scenarios and delay forecasts for the project."""
    try:
        with get_connection() as conn:
            query = """
                SELECT scenario_id, schedule_id, name, created_at, results
                FROM impact_scenarios
                WHERE project_id = %(project_id)s
            """
            params: Dict[str, Any] = {"project_id": context.project_id}
            if schedule_id:
                query += " AND schedule_id = %(schedule_id)s"
                params["schedule_id"] = schedule_id
            query += " ORDER BY created_at DESC LIMIT 5"

            rows = conn.execute(query, params).fetchall()
            scenarios = []
            for r in rows:
                item = dict(r)
                scenarios.append({
                    "scenario_id": str(item.get("scenario_id")),
                    "schedule_id": str(item.get("schedule_id")),
                    "name": item.get("name"),
                    "created_at": str(item.get("created_at")),
                    "results_summary": item.get("results"),
                })
            return {
                "scenario_count": len(scenarios),
                "recent_scenarios": scenarios,
            }
    except Exception as e:
        logger.error(f"[impact_tool] Error getting impact summary: {e}")
        return {"scenario_count": 0, "recent_scenarios": [], "error": str(e)}
