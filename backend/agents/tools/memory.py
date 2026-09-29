"""
Read-Only Institutional Memory Tool for Supervising Agent.
Integrates with Phase 11 Institutional Memory Retrieval Infrastructure.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.context.project import ProjectContext
from backend.memory.retrieval_service import InstitutionalMemoryRetrievalService
from backend.memory.schemas import MemoryFilter
from backend.shared.db import get_connection

logger = logging.getLogger(__name__)


def search_institutional_memory(
    context: ProjectContext,
    query: str,
    stage_id: Optional[str] = None,
    activity_id: Optional[str] = None,
    contractor_id: Optional[str] = None,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    Retrieves relevant historical lessons and incidents using Phase 11 retrieval engine.
    Applies exact metadata filtering and explainable deterministic ranking.
    """
    try:
        mem_filter = MemoryFilter(
            project_id=context.project_id,
            stage_id=stage_id,
            activity_id=activity_id,
            contractor_id=contractor_id,
        )
        response = InstitutionalMemoryRetrievalService.search(
            context=context,
            query=query,
            filters=mem_filter,
            top_k=top_k,
        )
        results = []
        for r in response.results:
            results.append({
                "memory_id": r.record.memory_id,
                "title": r.record.title,
                "summary": r.record.summary,
                "score": r.score,
                "match_reasons": r.match_reasons,
                "source_type": r.record.source_type,
                "stage_id": r.record.stage_id,
                "activity_id": r.record.activity_id,
                "contractor_id": r.record.contractor_id,
            })
        return results
    except Exception as e:
        logger.error(f"[memory_tool] Error searching institutional memory: {e}")
        return []


def get_recent_incidents(context: ProjectContext, limit: int = 10) -> List[Dict[str, Any]]:
    """Retrieves active incidents recorded for the current project."""
    try:
        with get_connection() as conn:
            query = """
                SELECT incident_id, title, incident_type, status, delay_days, cost_impact,
                       root_cause, corrective_action, lessons_learned, recorded_at,
                       stage_id, activity_id, contractor_id
                FROM institutional_incidents
                WHERE project_id = %(project_id)s
                ORDER BY recorded_at DESC
                LIMIT %(limit)s
            """
            rows = conn.execute(query, {"project_id": context.project_id, "limit": limit}).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[memory_tool] Error getting recent incidents: {e}")
        return []
