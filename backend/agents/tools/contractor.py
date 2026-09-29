"""
Read-Only Contractor and Work Package Tool for Supervising Agent.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from backend.context.project import ProjectContext
from backend.shared.db import get_connection

logger = logging.getLogger(__name__)


def get_contractor_summaries(context: ProjectContext) -> List[Dict[str, Any]]:
    """Retrieves all active contractors and dispute counts for the project."""
    try:
        with get_connection() as conn:
            query = """
                SELECT c.contractor_id, c.contractor_code, c.company_name,
                       c.type_or_category, c.status,
                       COUNT(DISTINCT wp.work_package_id) AS package_count,
                       COUNT(DISTINCT cd.dispute_id) AS dispute_count
                FROM contractors c
                LEFT JOIN work_packages wp ON c.contractor_id = wp.contractor_id
                LEFT JOIN contractor_disputes cd ON c.contractor_id = cd.contractor_id
                WHERE c.project_id = %(project_id)s
                GROUP BY c.contractor_id, c.contractor_code, c.company_name, c.type_or_category, c.status
                ORDER BY c.company_name ASC
            """
            rows = conn.execute(query, {"project_id": context.project_id}).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[contractor_tool] Error getting contractor summaries: {e}")
        return []


def get_work_package_summaries(context: ProjectContext) -> List[Dict[str, Any]]:
    """Retrieves all work packages for the project with stage attribution."""
    try:
        with get_connection() as conn:
            query = """
                SELECT wp.work_package_id, wp.package_code, wp.package_name,
                       wp.discipline, wp.status, wp.planned_start, wp.planned_finish,
                       c.company_name AS contractor_name, st.stage_name
                FROM work_packages wp
                LEFT JOIN contractors c ON wp.contractor_id = c.contractor_id
                LEFT JOIN stages st ON wp.stage_id = st.stage_id
                WHERE wp.project_id = %(project_id)s
                ORDER BY wp.planned_start ASC NULLS LAST
            """
            rows = conn.execute(query, {"project_id": context.project_id}).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[contractor_tool] Error getting work packages: {e}")
        return []
