"""
Read-Only Audit Dossier Tool for Supervising Agent.
Integrates with Phase 13 Audit Dossier Foundation.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from backend.context.project import ProjectContext
from backend.dossier.service import DossierService

logger = logging.getLogger(__name__)


def get_audit_verification(context: ProjectContext) -> Dict[str, Any]:
    """Retrieves cryptographic hash-chain verification status from Phase 13."""
    try:
        report = DossierService.verify_audit_chain(context)
        return report.model_dump() if hasattr(report, "model_dump") else dict(report)
    except Exception as e:
        logger.error(f"[dossier_tool] Error verifying audit chain: {e}")
        return {
            "status": "ERROR",
            "is_valid": False,
            "error": str(e),
        }


def get_activity_dossier_summary(
    context: ProjectContext, schedule_id: str, activity_id: str
) -> Optional[Dict[str, Any]]:
    """Retrieves deep decision and evidence history for a specific activity."""
    try:
        dossier = DossierService.build_activity_dossier(
            context=context, schedule_id=schedule_id, activity_id=activity_id
        )
        return dossier.model_dump() if hasattr(dossier, "model_dump") else dict(dossier)
    except Exception as e:
        logger.error(f"[dossier_tool] Error building activity dossier {activity_id}: {e}")
        return None
