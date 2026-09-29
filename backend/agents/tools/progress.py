"""
Read-Only Progress Tool for Supervising Agent.
Exposes authoritative numbers from the Phase 8 Weighted Progress Engine.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict

from backend.context.project import ProjectContext
from backend.services.progress_service import ProgressService

logger = logging.getLogger(__name__)


def get_project_progress(context: ProjectContext) -> Dict[str, Any]:
    """Retrieves authoritative multi-level project progress rollup."""
    try:
        res = ProgressService.get_project_progress(context)
        return res.model_dump() if hasattr(res, "model_dump") else dict(res)
    except Exception as e:
        logger.error(f"[progress_tool] Error getting project progress: {e}")
        return {"project_id": str(context.project_id), "progress_pct": 0.0, "error": str(e)}


def get_stage_progress(context: ProjectContext, stage_id: uuid.UUID) -> Dict[str, Any]:
    """Retrieves authoritative progress for a specific stage."""
    try:
        res = ProgressService.get_stage_progress(context, stage_id)
        return res.model_dump() if hasattr(res, "model_dump") else dict(res)
    except Exception as e:
        logger.error(f"[progress_tool] Error getting stage progress {stage_id}: {e}")
        return {"stage_id": str(stage_id), "progress_pct": 0.0, "error": str(e)}


def get_activity_progress(context: ProjectContext, activity_id: str) -> Dict[str, Any]:
    """Retrieves authoritative progress for a specific activity."""
    try:
        res = ProgressService.get_activity_progress(context, activity_id)
        return res.model_dump() if hasattr(res, "model_dump") else dict(res)
    except Exception as e:
        logger.error(f"[progress_tool] Error getting activity progress {activity_id}: {e}")
        return {"activity_id": activity_id, "progress_pct": 0.0, "error": str(e)}
