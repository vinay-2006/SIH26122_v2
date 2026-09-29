"""
Read-Only Matching and Claim Extraction Tool for Supervising Agent.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from backend.context.project import ProjectContext
from backend.shared.db import get_connection

logger = logging.getLogger(__name__)


def get_recent_matching_events(context: ProjectContext, limit: int = 20) -> List[Dict[str, Any]]:
    """Retrieves recent execution events, extracted claims, and matched activities."""
    try:
        with get_connection() as conn:
            query = """
                SELECT ee.event_id, ee.event_type, ee.status, ee.event_date, ee.created_at,
                       ee.matched_activity_id, ee.raw_claim_text, ee.claimed_pct,
                       sd.document_id, sd.file_name, sd.document_type AS source_type
                FROM execution_events ee
                LEFT JOIN source_documents sd ON ee.document_id = sd.document_id
                WHERE ee.project_id = %(project_id)s
                ORDER BY ee.created_at DESC
                LIMIT %(limit)s
            """
            rows = conn.execute(query, {"project_id": context.project_id, "limit": limit}).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[matching_tool] Error getting recent matching events: {e}")
        return []


def get_matching_conflicts(context: ProjectContext) -> List[Dict[str, Any]]:
    """Retrieves execution events with matching conflicts or unassigned status."""
    try:
        with get_connection() as conn:
            query = """
                SELECT ee.event_id, ee.event_type, ee.status, ee.event_date,
                       ee.matched_activity_id, ee.raw_claim_text, ee.claimed_pct
                FROM execution_events ee
                WHERE ee.project_id = %(project_id)s
                  AND ee.status IN ('CONFLICT', 'UNMATCHED', 'REVIEW_REQUIRED', 'HOLD')
                ORDER BY ee.created_at DESC
                LIMIT 20
            """
            rows = conn.execute(query, {"project_id": context.project_id}).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[matching_tool] Error getting matching conflicts: {e}")
        return []
