"""
Reopen, Rework, and Actuals Revision Service for SETUAI V7 Phase 7.
Enforces RBAC permissions, deterministic state machine constraints, and stable Phase 8 contracts.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Union
from fastapi import HTTPException, status

from backend.context.errors import raise_permission_denied, raise_resource_not_found
from backend.context.project import ProjectContext
from backend.context.schedule import ScheduleContext
from backend.rbac.permissions import Permission, has_permission
from backend.repositories.reopen_repo import (
    ActivityAlreadyReopenedError,
    ActivityNotEligibleForReopenError,
    ActivityNotFoundError,
    ActivityNotInReworkError,
    NoPendingReopenRequestError,
    ProjectReopenRepository,
    ReopenAlreadyPendingError,
)
from backend.schemas.reopen import (
    ActualRevisionApprovalRequest,
    ActualRevisionResponse,
    ActivityExecutionHistoryResponse,
    ReopenDecisionRequest,
    ReopenRequestCreate,
    ReopenStatusResponse,
)
from backend.schemas.stage import CanonicalExecutionState, WorkflowCondition
from backend.services.stage_service import StageService

logger = logging.getLogger(__name__)


class ReopenService:
    """
    Authoritative service implementing Phase 7 Reopen, Rework, and Actuals Revision engine.
    """

    # =========================================================================
    # 1. REOPEN WORKFLOW (REQUEST & DECISION)
    # =========================================================================

    @classmethod
    def request_reopen(
        cls,
        context: ScheduleContext,
        activity_id: str,
        payload: ReopenRequestCreate,
    ) -> Dict[str, Any]:
        """
        Challenges a completed activity and creates a formal reopen request.
        Requires Permission.REQUEST_REOPEN (Owner, PM, Supervisor, Site Engineer).
        """
        if not has_permission(context.role, Permission.REQUEST_REOPEN):
            raise_permission_denied(Permission.REQUEST_REOPEN.value, context.role)

        try:
            return ProjectReopenRepository.create_reopen_request(
                context=context,
                activity_id=activity_id,
                reason=payload.reason.value,
                justification=payload.justification,
                evidence_event_ids=payload.evidence_event_ids,
            )
        except ActivityNotFoundError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
        except ActivityNotEligibleForReopenError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
        except (ReopenAlreadyPendingError, ActivityAlreadyReopenedError) as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

    @classmethod
    def decide_reopen(
        cls,
        context: ScheduleContext,
        activity_id: str,
        payload: ReopenDecisionRequest,
    ) -> Dict[str, Any]:
        """
        Decides (APPROVE or REJECT) on a pending reopen request.
        Requires Permission.APPROVE_REOPEN (Owner, PM, Supervisor).
        Explicitly excludes Site Engineer.
        """
        if not has_permission(context.role, Permission.APPROVE_REOPEN):
            raise_permission_denied(Permission.APPROVE_REOPEN.value, context.role)

        try:
            return ProjectReopenRepository.decide_reopen_request(
                context=context,
                activity_id=activity_id,
                decision=payload.decision.value,
                notes=payload.notes,
                rework_instructions=payload.rework_instructions,
            )
        except ActivityNotFoundError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
        except NoPendingReopenRequestError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    # =========================================================================
    # 2. ACTUALS REVISION APPROVAL
    # =========================================================================

    @classmethod
    def approve_actual_revision(
        cls,
        context: ScheduleContext,
        activity_id: str,
        payload: ActualRevisionApprovalRequest,
    ) -> Dict[str, Any]:
        """
        Approves a corrected actual following authorized rework.
        Requires Permission.APPROVE_ACTUAL (Owner, PM, Supervisor).
        Atomically records the new actual while preserving the historical record in audit logs.
        """
        if not has_permission(context.role, Permission.APPROVE_ACTUAL):
            raise_permission_denied(Permission.APPROVE_ACTUAL.value, context.role)

        try:
            return ProjectReopenRepository.record_actual_revision(
                context=context,
                activity_id=activity_id,
                actual_start=payload.actual_start,
                actual_finish=payload.actual_finish,
                actual_quantity=payload.actual_quantity,
                actual_pct_complete=payload.actual_pct_complete,
                revision_notes=payload.revision_notes,
                claim_event_id=payload.claim_event_id,
            )
        except ActivityNotFoundError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
        except ActivityNotInReworkError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e

    # =========================================================================
    # 3. STATUS & HISTORY (STABLE PHASE 8 CONTRACTS)
    # =========================================================================

    @classmethod
    def get_reopen_status(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        activity_id: str,
    ) -> Dict[str, Any]:
        """
        Retrieves the current reopen and workflow status for an activity.
        Requires VIEW_SCHEDULE or VIEW_AUDIT.
        """
        if not (has_permission(context.role, Permission.VIEW_SCHEDULE) or has_permission(context.role, Permission.VIEW_AUDIT)):
            raise_permission_denied(Permission.VIEW_SCHEDULE.value, context.role)

        res = ProjectReopenRepository.get_reopen_status(context, activity_id)
        if not res:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Activity '{activity_id}' not found in current context.",
            )
        return res

    @classmethod
    def list_reopen_requests(cls, context: ScheduleContext, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Reopen lifecycle of every activity of this schedule that has ever had a reopen request (latest state each)."""
        if not (has_permission(context.role, Permission.VIEW_SCHEDULE) or has_permission(context.role, Permission.VIEW_AUDIT)):
            raise_permission_denied(Permission.VIEW_SCHEDULE.value, context.role)
        out: List[Dict[str, Any]] = []
        for activity_id in ProjectReopenRepository.list_reopen_activity_ids(context):
            res = ProjectReopenRepository.get_reopen_status(context, activity_id)
            if res and res.get("reopen_status") not in (None, "NONE") and (not status_filter or res["reopen_status"] == status_filter):
                out.append(res)
        return out

    @classmethod
    def get_actual_history(
        cls,
        context: ScheduleContext,
        activity_id: str,
    ) -> Dict[str, Any]:
        """
        Reconstructs the full tamper-evident execution and revision history.
        Requires VIEW_SCHEDULE or VIEW_AUDIT.
        """
        if not (has_permission(context.role, Permission.VIEW_SCHEDULE) or has_permission(context.role, Permission.VIEW_AUDIT)):
            raise_permission_denied(Permission.VIEW_SCHEDULE.value, context.role)

        try:
            return ProjectReopenRepository.get_actual_history(context, activity_id)
        except ActivityNotFoundError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    @classmethod
    def get_current_approved_actual(
        cls,
        context: ScheduleContext,
        activity_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Stable Phase 8 contract: Returns the single authoritative approved actual record.
        """
        status_info = cls.get_reopen_status(context, activity_id)
        history = cls.get_actual_history(context, activity_id)
        return history.get("current_approved_actual")

    @classmethod
    def is_activity_reopened(cls, activity_data: Dict[str, Any]) -> bool:
        """
        Stable contract: Checks if an activity is currently reopened for rework.
        """
        cond = StageService.get_workflow_condition(activity_data)
        return cond == WorkflowCondition.REWORK_IN_PROGRESS.value

    @classmethod
    def get_workflow_condition(cls, activity_data: Dict[str, Any]) -> str:
        """
        Stable contract: Returns the operational workflow condition.
        """
        return StageService.get_workflow_condition(activity_data)

    @classmethod
    def get_activity_execution_context(
        cls,
        context: ScheduleContext,
        activity_id: str,
    ) -> Dict[str, Any]:
        """
        Stable Phase 8 contract: Returns full contextual state for downstream engine consume.
        """
        status_info = cls.get_reopen_status(context, activity_id)
        history = cls.get_actual_history(context, activity_id)
        return {
            "activity_id": activity_id,
            "schedule_id": context.schedule_id,
            "project_id": context.project_id,
            "canonical_state": status_info["canonical_state"],
            "workflow_condition": status_info["workflow_condition"],
            "reopen_status": status_info["reopen_status"],
            "is_reopened": status_info["workflow_condition"] == WorkflowCondition.REWORK_IN_PROGRESS.value,
            "current_approved_actual": history.get("current_approved_actual"),
            "historical_actuals_count": len(history.get("historical_approved_actuals", [])),
        }
