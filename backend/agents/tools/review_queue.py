"""
Read-Only Review Queue Tool for Supervising Agent.
Identifies execution claims and reopen requests awaiting supervisor decision.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from backend.context.project import ProjectContext
from backend.shared.db import get_connection

logger = logging.getLogger(__name__)


def get_review_queue(context: ProjectContext, limit: int = 20) -> Dict[str, Any]:
    """Retrieves pending execution claims and reopen requests awaiting human supervisor review."""
    try:
        with get_connection() as conn:
            # 1. Execution claims awaiting decision
            claims_query = """
                SELECT ee.event_id, ee.event_type, ee.status, ee.event_date, ee.created_at,
                       ee.matched_activity_id, ee.raw_claim_text, ee.claimed_pct,
                       sa.activity_name, st.stage_name
                FROM execution_events ee
                LEFT JOIN schedule_activities sa ON ee.matched_activity_id = sa.activity_id
                     AND sa.project_id = ee.project_id AND sa.schedule_id = ee.schedule_id
                LEFT JOIN stages st ON sa.stage_id = st.stage_id
                WHERE ee.project_id = %(project_id)s
                  AND ee.status IN ('VALIDATED', 'REVIEW_REQUIRED', 'HOLD')
                  AND ee.event_type <> 'REOPEN_REQUEST'  -- reopen requests are listed separately below
                ORDER BY ee.created_at ASC
                LIMIT %(limit)s
            """
            claim_rows = conn.execute(
                claims_query, {"project_id": context.project_id, "limit": limit}
            ).fetchall()
            pending_claims = [dict(r) for r in claim_rows]

            # 2. Pending reopen requests
            reopen_query = """
                SELECT ee.event_id AS reopen_id, ee.matched_activity_id AS activity_id,
                       ee.schedule_id, ee.reopen_justification AS reason,
                       ee.reopen_status AS status, ee.created_at AS requested_at,
                       ee.reopen_requested_by AS requested_by, sa.activity_name
                FROM execution_events ee
                LEFT JOIN schedule_activities sa ON ee.matched_activity_id = sa.activity_id
                     AND sa.project_id = ee.project_id AND sa.schedule_id = ee.schedule_id
                WHERE ee.project_id = %(project_id)s
                  AND ee.reopen_status = 'REQUESTED'
                ORDER BY ee.created_at ASC
                LIMIT %(limit)s
            """
            reopen_rows = conn.execute(
                reopen_query, {"project_id": context.project_id, "limit": limit}
            ).fetchall()
            pending_reopens = [dict(r) for r in reopen_rows]

            return {
                "total_pending_claims": len(pending_claims),
                "total_pending_reopens": len(pending_reopens),
                "claims": pending_claims,
                "reopens": pending_reopens,
            }
    except Exception as e:
        logger.error(f"[review_queue_tool] Error getting review queue: {e}")
        return {
            "total_pending_claims": 0,
            "total_pending_reopens": 0,
            "claims": [],
            "reopens": [],
            "error": str(e),
        }
