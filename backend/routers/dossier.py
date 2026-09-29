"""
Audit Dossier Router for SETUAI V7 Phase 13.
Provides read-only, project- and schedule-scoped endpoints for structured audit dossiers
and cryptographic audit-chain verification.
"""

from __future__ import annotations

import uuid
from typing import Optional
from fastapi import APIRouter, Depends, Path, Query, status

from backend.context.errors import raise_permission_denied, raise_schedule_denied
from backend.context.project import ProjectContext, require_project_context
from backend.context.schedule import ScheduleContext, require_schedule_context
from backend.dossier.schemas import AuditChainVerificationResult, AuditDossier
from backend.dossier.service import DossierService
from backend.rbac.permissions import Permission, has_permission
from backend.shared.db import get_connection

router = APIRouter(tags=["audit-dossier"])


# ============================================================================
# 1. PROJECT DOSSIER
# ============================================================================

@router.get(
    "/api/v7/projects/{project_id}/dossier",
    response_model=AuditDossier,
    summary="Get comprehensive project audit dossier (V7)",
)
@router.get(
    "/api/v1/projects/{project_id}/dossier",
    response_model=AuditDossier,
    summary="Get comprehensive project audit dossier (V1 alias)",
)
def get_project_dossier(
    project_id: uuid.UUID = Path(...),
    schedule_id: Optional[str] = Query(default=None, description="Optional schedule version to scope the dossier"),
    context: ProjectContext = Depends(require_project_context),
) -> AuditDossier:
    """
    Assembles complete evidence and decision history across project lifecycle.
    Read-only aggregation over project, schedule, stages, activities, evidence,
    matching, decisions, actuals, progress, impact, and cryptographic audit verification.
    """
    if schedule_id:
        # Validate schedule ownership matches project context
        sched_id = schedule_id.strip()
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT project_id FROM schedules WHERE schedule_id = %s;",
                        (sched_id,),
                    )
                    row = cur.fetchone()
        except Exception as e:
            row = None

        if not row or str(row["project_id"] if isinstance(row, dict) else row[0]) != str(context.project_id):
            raise_schedule_denied(
                f"Schedule '{sched_id}' does not belong to authorized project context '{context.project_id}'"
            )

    return DossierService.build_project_dossier(context=context, schedule_id=schedule_id)


# ============================================================================
# 2. SCHEDULE DOSSIER
# ============================================================================

@router.get(
    "/api/v7/projects/{project_id}/schedules/{schedule_id}/dossier",
    response_model=AuditDossier,
    summary="Get schedule version audit dossier (V7)",
)
@router.get(
    "/api/v1/projects/{project_id}/schedules/{schedule_id}/dossier",
    response_model=AuditDossier,
    summary="Get schedule version audit dossier (V1 alias)",
)
def get_schedule_dossier(
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    context: ScheduleContext = Depends(require_schedule_context),
) -> AuditDossier:
    """
    Assembles audit dossier scoped to a validated schedule version.
    Never merges historical schedules. No implicit active schedule fallback.
    """
    return DossierService.build_schedule_dossier(context=context)


# ============================================================================
# 3. ACTIVITY DOSSIER
# ============================================================================

@router.get(
    "/api/v7/projects/{project_id}/schedules/{schedule_id}/activities/{activity_id}/dossier",
    response_model=AuditDossier,
    summary="Get activity-level deep audit dossier (V7)",
)
@router.get(
    "/api/v1/projects/{project_id}/schedules/{schedule_id}/activities/{activity_id}/dossier",
    response_model=AuditDossier,
    summary="Get activity-level deep audit dossier (V1 alias)",
)
def get_activity_dossier(
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    activity_id: str = Path(...),
    context: ScheduleContext = Depends(require_schedule_context),
) -> AuditDossier:
    """
    Assembles deep provenance and decision dossier for a single activity.
    Traces: Claim Evidence -> Matching -> Validation -> Human Decision ->
            Authoritative Actual -> Reopen History -> Progress -> Impact.
    """
    return DossierService.build_activity_dossier(context=context, activity_id=activity_id)


# ============================================================================
# 4. AUDIT CHAIN VERIFICATION
# ============================================================================

@router.get(
    "/api/v7/projects/{project_id}/dossier/audit-verification",
    response_model=AuditChainVerificationResult,
    summary="Verify cryptographic audit hash chain integrity (V7)",
)
@router.get(
    "/api/v1/projects/{project_id}/dossier/audit-verification",
    response_model=AuditChainVerificationResult,
    summary="Verify cryptographic audit hash chain integrity (V1 alias)",
)
def verify_dossier_audit_chain(
    project_id: uuid.UUID = Path(...),
    schedule_id: Optional[str] = Query(default=None, description="Optional schedule filter for verification"),
    context: ProjectContext = Depends(require_project_context),
) -> AuditChainVerificationResult:
    """
    Cryptographically verifies the SHA-256 tamper-evident audit hash chain for the project.
    Verifies payload_hash, current_hash, and previous_hash sequence linkages.
    Returns diagnostic verification result.
    """
    return DossierService.verify_audit_chain(context=context, schedule_id=schedule_id)
