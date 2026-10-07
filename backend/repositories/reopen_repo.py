"""
Project- and Schedule-Scoped Reopen, Rework, and Actuals Revision Repository for SETUAI V7 Phase 7.
Provides atomic transactions, row-level locking, and tamper-evident audit logging.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple, Union

import psycopg

from backend.shared.workflow_flags import with_workflow_flags
from backend.context.project import ProjectContext
from backend.context.schedule import ScheduleContext
from backend.repositories.base import BaseRepository
from backend.schemas.stage import CanonicalExecutionState, WorkflowCondition
from backend.services.stage_service import StageService
from backend.shared.audit import append_audit_record
from backend.shared.db import get_connection

logger = logging.getLogger(__name__)


# Domain Exceptions
class ActivityNotFoundError(Exception):
    """Raised when an activity is not found in the scoped schedule/project."""
    pass


class ActivityNotEligibleForReopenError(Exception):
    """Raised when an activity is not eligible for reopen challenge."""
    pass


class ReopenAlreadyPendingError(Exception):
    """Raised when a reopen request is already pending review for the activity."""
    pass


class ActivityAlreadyReopenedError(Exception):
    """Raised when an activity is already in rework."""
    pass


class NoPendingReopenRequestError(Exception):
    """Raised when attempting to decide on an activity with no pending reopen request."""
    pass


class ActivityNotInReworkError(Exception):
    """Raised when attempting an actual revision for an activity not in rework."""
    pass


def _serialize_dict(d: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Helper to ensure all dates/UUIDs in a dictionary are safely serializable."""
    if not d:
        return {}
    res: Dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, (datetime, date, uuid.UUID)):
            res[k] = str(v)
        else:
            res[k] = v
    return res


def _insert_audit_log_tx(
    cur: psycopg.Cursor,
    context: Union[ScheduleContext, ProjectContext],
    action: str,
    entity_type: str,
    entity_id: str,
    before_state: Dict[str, Any],
    after_state: Dict[str, Any],
    entity_context: Optional[Dict[str, Any]] = None,
) -> int:
    """Appends a tamper-evident audit log entry within the caller's transaction (shared chain logic)."""
    ctx_payload = {
        "old_state": _serialize_dict(before_state),
        "new_state": _serialize_dict(after_state),
    }
    full_context = dict(entity_context or {})
    full_context.update(ctx_payload)
    return append_audit_record(
        cur,
        project_id=context.project_id,
        schedule_id=getattr(context, "schedule_id", None),
        actor_id=str(context.user_id),
        role=context.role,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_state=_serialize_dict(before_state),
        after_state=_serialize_dict(after_state),
        entity_context=full_context,
    )


class ProjectReopenRepository(BaseRepository):
    """
    Project- and Schedule-scoped repository for Phase 7 Reopen/Rework lifecycle.
    """

    @classmethod
    def list_reopen_activity_ids(cls, context: ScheduleContext) -> List[str]:
        """Activities of the caller's project + schedule that have at least one reopen event."""
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT matched_activity_id
                    FROM execution_events
                    WHERE project_id = %s AND schedule_id = %s AND reopen_status != 'NONE'
                      AND matched_activity_id IS NOT NULL
                    ORDER BY matched_activity_id;
                    """,
                    (context.project_id, context.schedule_id),
                )
                return [r["matched_activity_id"] for r in cur.fetchall()]

    @classmethod
    def get_reopen_status(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        activity_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Inspects the activity's canonical state, operational condition, and latest reopen metadata.
        """
        project_id = context.project_id
        schedule_id = getattr(context, "schedule_id", None)

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                # 1. Fetch activity
                cur.execute(
                    with_workflow_flags("""
                    SELECT sa.activity_id, sa.schedule_id, sa.project_id, sa.stage_id,
                           sa.activity_name, sa.planned_start, sa.planned_finish,
                           sa.planned_quantity, sa.baseline_pct_complete, sa.quality_gate_required
                    FROM schedule_activities sa
                    WHERE sa.activity_id = %s AND sa.project_id = %s
                      AND (%s::text IS NULL OR sa.schedule_id = %s);
                    """),
                    (activity_id, project_id, schedule_id, schedule_id),
                )
                act_row = cur.fetchone()
                if not act_row:
                    return None
                act_data = dict(act_row)

                # 2. Fetch approved actual
                cur.execute(
                    """
                    SELECT aa.actual_id, aa.decision_id, aa.event_id, aa.actual_start,
                           aa.actual_finish, aa.actual_pct_complete, aa.actual_quantity,
                           aa.is_reopened, aa.reopened_at, aa.reopened_by, aa.rework_notes,
                           aa.created_at
                    FROM approved_actuals aa
                    WHERE aa.activity_id = %s AND aa.project_id = %s
                      AND (%s::text IS NULL OR aa.schedule_id = %s);
                    """,
                    (activity_id, project_id, schedule_id, schedule_id),
                )
                aa_row = cur.fetchone()
                aa_data = dict(aa_row) if aa_row else None

                # 3. Fetch latest reopen execution event
                cur.execute(
                    """
                    SELECT ee.event_id, ee.event_date, ee.reopen_status, ee.reopened_from_actual_id,
                           ee.reopen_justification, ee.reopen_requested_by, ee.reopen_decided_by,
                           ee.created_at
                    FROM execution_events ee
                    WHERE ee.matched_activity_id = %s AND ee.project_id = %s
                      AND (%s::text IS NULL OR ee.schedule_id = %s)
                      AND ee.reopen_status != 'NONE'
                    ORDER BY ee.event_date DESC, ee.created_at DESC
                    LIMIT 1;
                    """,
                    (activity_id, project_id, schedule_id, schedule_id),
                )
                ee_row = cur.fetchone()
                ee_data = dict(ee_row) if ee_row else None

                # 4. Fetch latest reopen audit log for decision notes & rework instructions
                cur.execute(
                    """
                    SELECT al.action, al.after_state, al.timestamp
                    FROM audit_logs al
                    WHERE al.entity_id = %s AND al.project_id = %s
                      AND al.action IN ('REOPEN_REQUESTED', 'REOPEN_APPROVED', 'REOPEN_REJECTED')
                    ORDER BY al.timestamp DESC, al.log_id DESC
                    LIMIT 1;
                    """,
                    (activity_id, project_id),
                )
                al_row = cur.fetchone()
                after_state = {}
                if al_row:
                    raw_after = al_row["after_state"] if isinstance(al_row, dict) else al_row[1]
                    if isinstance(raw_after, str):
                        try:
                            after_state = json.loads(raw_after)
                        except Exception:
                            after_state = {}
                    elif isinstance(raw_after, dict):
                        after_state = raw_after

                # Compute canonical state & workflow condition
                merged_data = dict(act_data)
                if aa_data:
                    merged_data.update(aa_data)
                if ee_data:
                    merged_data["reopen_status"] = ee_data["reopen_status"]

                canonical_state = StageService.get_execution_state(merged_data)
                workflow_condition = StageService.get_workflow_condition(merged_data)

                reopen_status_str = ee_data["reopen_status"] if ee_data else "NONE"
                # If approved actual has is_reopened = True and ee_data is approved, keep APPROVED
                if aa_data and aa_data.get("is_reopened"):
                    reopen_status_str = "APPROVED"

                return {
                    "activity_id": activity_id,
                    "schedule_id": act_data["schedule_id"],
                    "project_id": act_data["project_id"],
                    "canonical_state": canonical_state,
                    "workflow_condition": workflow_condition,
                    "reopen_status": reopen_status_str,
                    "reason": after_state.get("reason"),
                    "justification": ee_data.get("reopen_justification") if ee_data else after_state.get("justification"),
                    "requested_by": ee_data.get("reopen_requested_by") if ee_data else (after_state.get("requested_by") if after_state.get("requested_by") else None),
                    "requested_at": ee_data.get("created_at") if ee_data else None,
                    "decided_by": ee_data.get("reopen_decided_by") if ee_data else (after_state.get("decided_by") if after_state.get("decided_by") else None),
                    "decided_at": al_row["timestamp"] if al_row else None,
                    "decision_notes": after_state.get("notes"),
                    "rework_instructions": after_state.get("rework_instructions") or (aa_data.get("rework_notes") if aa_data else None),
                    "original_actual_id": (ee_data.get("reopened_from_actual_id") if ee_data else None) or (aa_data.get("actual_id") if aa_data else None),
                }

    @classmethod
    def create_reopen_request(
        cls,
        context: ScheduleContext,
        activity_id: str,
        reason: str,
        justification: str,
        evidence_event_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Challenges a completed activity and creates a formal reopen request.
        """
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                # 1. Lock and verify activity
                cur.execute(
                    """
                    SELECT sa.activity_id, sa.schedule_id, sa.project_id, sa.stage_id
                    FROM schedule_activities sa
                    WHERE sa.activity_id = %s AND sa.schedule_id = %s AND sa.project_id = %s
                    FOR UPDATE;
                    """,
                    (activity_id, context.schedule_id, context.project_id),
                )
                act_row = cur.fetchone()
                if not act_row:
                    raise ActivityNotFoundError(f"Activity '{activity_id}' not found in schedule '{context.schedule_id}'.")
                stage_id = act_row["stage_id"] if isinstance(act_row, dict) else act_row[3]

                # 2. Lock and verify approved actual
                cur.execute(
                    """
                    SELECT actual_id, actual_pct_complete, actual_finish, is_reopened, rework_notes
                    FROM approved_actuals
                    WHERE activity_id = %s AND schedule_id = %s AND project_id = %s
                    FOR UPDATE;
                    """,
                    (activity_id, context.schedule_id, context.project_id),
                )
                aa_row = cur.fetchone()
                if not aa_row:
                    raise ActivityNotEligibleForReopenError(
                        f"Activity '{activity_id}' does not have an approved actual. Only completed activities can be challenged for reopening."
                    )
                actual_id = aa_row["actual_id"] if isinstance(aa_row, dict) else aa_row[0]
                pct_complete = aa_row["actual_pct_complete"] if isinstance(aa_row, dict) else aa_row[1]
                actual_finish = aa_row["actual_finish"] if isinstance(aa_row, dict) else aa_row[2]
                is_reopened = aa_row["is_reopened"] if isinstance(aa_row, dict) else aa_row[3]

                # Verify legitimately completed
                is_completed = (pct_complete is not None and float(pct_complete) >= 100.0) or (actual_finish is not None)
                if not is_completed:
                    raise ActivityNotEligibleForReopenError(
                        f"Activity '{activity_id}' is not in COMPLETED state (pct_complete={pct_complete}, finish={actual_finish}). Only completed activities can be challenged."
                    )

                if is_reopened:
                    raise ActivityAlreadyReopenedError(
                        f"Activity '{activity_id}' is already authorized and in active rework."
                    )

                # Check for existing pending request
                cur.execute(
                    """
                    SELECT event_id FROM execution_events
                    WHERE matched_activity_id = %s AND schedule_id = %s AND project_id = %s
                      AND reopen_status = 'REQUESTED'
                    LIMIT 1;
                    """,
                    (activity_id, context.schedule_id, context.project_id),
                )
                if cur.fetchone():
                    raise ReopenAlreadyPendingError(
                        f"A reopen request is already pending review for activity '{activity_id}'."
                    )

                # 3. Create execution event for reopen request
                event_id = f"EV-REOPEN-{uuid.uuid4().hex[:12]}"
                cur.execute(
                    """
                    INSERT INTO execution_events (
                        event_id, schedule_id, project_id, stage_id, event_date,
                        raw_claim_text, input_channel, event_type, matched_activity_id,
                        reopen_status, reopened_from_actual_id, reopen_justification,
                        reopen_requested_by, status
                    ) VALUES (
                        %s, %s, %s, %s, CURRENT_DATE,
                        %s, 'MANUAL_REOPEN', 'REOPEN_REQUEST', %s,
                        'REQUESTED', %s, %s,
                        %s, 'REVIEW_REQUIRED'
                    );
                    """,
                    (
                        event_id,
                        context.schedule_id,
                        context.project_id,
                        stage_id,
                        f"REOPEN REQUEST [{reason}]: {justification}",
                        activity_id,
                        actual_id,
                        justification,
                        context.user_id,
                    ),
                )

                # 4. Write audit log entry
                before_state = {
                    "workflow_condition": WorkflowCondition.NONE.value,
                    "reopen_status": "NONE",
                    "actual_id": actual_id,
                    "actual_pct_complete": pct_complete,
                }
                after_state = {
                    "workflow_condition": WorkflowCondition.REOPEN_REQUESTED.value,
                    "reopen_status": "REQUESTED",
                    "reopen_event_id": event_id,
                    "reason": reason,
                    "justification": justification,
                    "requested_by": str(context.user_id),
                    "evidence_event_ids": evidence_event_ids or [],
                }
                _insert_audit_log_tx(
                    cur=cur,
                    context=context,
                    action="REOPEN_REQUESTED",
                    entity_type="ACTIVITY",
                    entity_id=activity_id,
                    before_state=before_state,
                    after_state=after_state,
                    entity_context={
                        "activity_id": activity_id,
                        "schedule_id": context.schedule_id,
                        "reopen_event_id": event_id,
                    },
                )
                conn.commit()

        status_res = cls.get_reopen_status(context, activity_id)
        if not status_res:
            raise ActivityNotFoundError(f"Activity '{activity_id}' not found after reopen request.")
        return status_res

    @classmethod
    def decide_reopen_request(
        cls,
        context: ScheduleContext,
        activity_id: str,
        decision: str,
        notes: Optional[str] = None,
        rework_instructions: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Authorizes or rejects a pending reopen request for an activity.
        """
        decision_upper = decision.upper()
        if decision_upper not in ("APPROVED", "REJECTED"):
            raise ValueError(f"Invalid decision '{decision}'. Must be APPROVED or REJECTED.")

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                # 1. Lock pending execution event
                cur.execute(
                    """
                    SELECT event_id, reopened_from_actual_id, reopen_justification, reopen_requested_by
                    FROM execution_events
                    WHERE matched_activity_id = %s AND schedule_id = %s AND project_id = %s
                      AND reopen_status = 'REQUESTED'
                    ORDER BY event_date DESC, created_at DESC
                    LIMIT 1
                    FOR UPDATE;
                    """,
                    (activity_id, context.schedule_id, context.project_id),
                )
                ee_row = cur.fetchone()
                if not ee_row:
                    raise NoPendingReopenRequestError(
                        f"No pending reopen request found for activity '{activity_id}' in schedule '{context.schedule_id}'."
                    )
                ee_event_id = ee_row["event_id"] if isinstance(ee_row, dict) else ee_row[0]
                original_actual_id = ee_row["reopened_from_actual_id"] if isinstance(ee_row, dict) else ee_row[1]

                # 2. Lock approved actual
                cur.execute(
                    """
                    SELECT actual_id, actual_pct_complete, is_reopened, rework_notes
                    FROM approved_actuals
                    WHERE activity_id = %s AND schedule_id = %s AND project_id = %s
                    FOR UPDATE;
                    """,
                    (activity_id, context.schedule_id, context.project_id),
                )
                aa_row = cur.fetchone()
                if not aa_row:
                    raise ActivityNotFoundError(f"Approved actual record for activity '{activity_id}' not found.")
                actual_id = aa_row["actual_id"] if isinstance(aa_row, dict) else aa_row[0]
                existing_rework_notes = aa_row["rework_notes"] if isinstance(aa_row, dict) else aa_row[3]

                if decision_upper == "REJECTED":
                    # Update execution event
                    cur.execute(
                        """
                        UPDATE execution_events
                        SET reopen_status = 'REJECTED',
                            status = 'REJECTED',
                            reopen_decided_by = %s
                        WHERE event_id = %s;
                        """,
                        (context.user_id, ee_event_id),
                    )

                    before_state = {
                        "workflow_condition": WorkflowCondition.REOPEN_REQUESTED.value,
                        "reopen_status": "REQUESTED",
                    }
                    after_state = {
                        "workflow_condition": WorkflowCondition.NONE.value,
                        "reopen_status": "REJECTED",
                        "decided_by": str(context.user_id),
                        "notes": notes,
                    }
                    _insert_audit_log_tx(
                        cur=cur,
                        context=context,
                        action="REOPEN_REJECTED",
                        entity_type="ACTIVITY",
                        entity_id=activity_id,
                        before_state=before_state,
                        after_state=after_state,
                        entity_context={"reopen_event_id": ee_event_id},
                    )
                else:
                    # APPROVED: Set activity into rework
                    cur.execute(
                        """
                        UPDATE execution_events
                        SET reopen_status = 'APPROVED',
                            status = 'APPROVED',
                            reopen_decided_by = %s
                        WHERE event_id = %s;
                        """,
                        (context.user_id, ee_event_id),
                    )

                    combined_rework_notes = rework_instructions or notes or "Authorized for rework."
                    if existing_rework_notes:
                        combined_rework_notes = f"{existing_rework_notes} | REWORK: {combined_rework_notes}"

                    cur.execute(
                        """
                        UPDATE approved_actuals
                        SET is_reopened = TRUE,
                            reopened_at = now(),
                            reopened_by = %s,
                            rework_notes = %s
                        WHERE schedule_id = %s AND activity_id = %s AND project_id = %s;
                        """,
                        (context.user_id, combined_rework_notes, context.schedule_id, activity_id, context.project_id),
                    )

                    before_state = {
                        "workflow_condition": WorkflowCondition.REOPEN_REQUESTED.value,
                        "is_reopened": False,
                    }
                    after_state = {
                        "workflow_condition": WorkflowCondition.REWORK_IN_PROGRESS.value,
                        "is_reopened": True,
                        "decided_by": str(context.user_id),
                        "rework_instructions": rework_instructions,
                        "notes": notes,
                    }
                    _insert_audit_log_tx(
                        cur=cur,
                        context=context,
                        action="REOPEN_APPROVED",
                        entity_type="ACTIVITY",
                        entity_id=activity_id,
                        before_state=before_state,
                        after_state=after_state,
                        entity_context={
                            "reopen_event_id": ee_event_id,
                            "actual_id": actual_id,
                        },
                    )

                conn.commit()

        status_res = cls.get_reopen_status(context, activity_id)
        if not status_res:
            raise ActivityNotFoundError(f"Activity '{activity_id}' not found after decision.")
        return status_res

    @classmethod
    def record_actual_revision(
        cls,
        context: ScheduleContext,
        activity_id: str,
        actual_start: Optional[date],
        actual_finish: Optional[date],
        actual_quantity: Optional[float],
        actual_pct_complete: float,
        revision_notes: str,
        claim_event_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Atomically records a corrected approved actual following rework,
        capturing the historical snapshot into the immutable audit ledger.
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                # 1. Lock approved actual
                cur.execute(
                    """
                    SELECT actual_id, decision_id, event_id, schedule_id, activity_id,
                           project_id, stage_id, actual_start, actual_finish,
                           actual_quantity, actual_pct_complete, is_reopened,
                           reopened_at, reopened_by, rework_notes, created_at
                    FROM approved_actuals
                    WHERE activity_id = %s AND schedule_id = %s AND project_id = %s
                    FOR UPDATE;
                    """,
                    (activity_id, context.schedule_id, context.project_id),
                )
                aa_row = cur.fetchone()
                if not aa_row:
                    raise ActivityNotFoundError(f"Approved actual for activity '{activity_id}' not found.")
                existing_actual = dict(aa_row)

                if not existing_actual.get("is_reopened"):
                    raise ActivityNotInReworkError(
                        f"Activity '{activity_id}' is not in authorized rework (is_reopened=False). Actuals revision requires an active rework status."
                    )

                before_actual_snapshot = _serialize_dict(existing_actual)

                # 2. Insert new planner decision for revision
                decision_id = str(uuid.uuid4())
                ref_event_id = claim_event_id or existing_actual.get("event_id") or f"EV-REV-{uuid.uuid4().hex[:8]}"

                # Ensure event exists if referenced
                cur.execute(
                    "SELECT event_id FROM execution_events WHERE event_id = %s;",
                    (ref_event_id,),
                )
                if not cur.fetchone():
                    # Fallback to existing event_id
                    ref_event_id = existing_actual["event_id"]

                cur.execute(
                    """
                    INSERT INTO planner_decisions (
                        decision_id, event_id, selected_activity_id, action,
                        approved_pct, approved_qty, planner_id, justification, decided_at
                    ) VALUES (%s, %s, %s, 'REVISION', %s, %s, %s, %s, now());
                    """,
                    (
                        decision_id,
                        ref_event_id,
                        activity_id,
                        actual_pct_complete,
                        actual_quantity or existing_actual.get("actual_quantity"),
                        context.user_id,
                        revision_notes,
                    ),
                )

                # 3. Update approved actuals table
                new_start = actual_start if actual_start is not None else existing_actual.get("actual_start")
                if actual_pct_complete >= 100.0:
                    new_finish = actual_finish if actual_finish is not None else (existing_actual.get("actual_finish") or date.today())
                else:
                    new_finish = None

                new_quantity = actual_quantity if actual_quantity is not None else existing_actual.get("actual_quantity")
                updated_rework_notes = (existing_actual.get("rework_notes") or "") + f" | REVISION: {revision_notes}"

                cur.execute(
                    """
                    UPDATE approved_actuals
                    SET decision_id = %s,
                        event_id = %s,
                        actual_start = %s,
                        actual_finish = %s,
                        actual_quantity = %s,
                        actual_pct_complete = %s,
                        is_reopened = FALSE,
                        rework_notes = %s
                    WHERE schedule_id = %s AND activity_id = %s AND project_id = %s
                    RETURNING actual_id, decision_id, event_id, schedule_id, activity_id,
                              project_id, stage_id, actual_start, actual_finish,
                              actual_quantity, actual_pct_complete, is_reopened,
                              reopened_at, reopened_by, rework_notes, created_at;
                    """,
                    (
                        decision_id,
                        ref_event_id,
                        new_start,
                        new_finish,
                        new_quantity,
                        actual_pct_complete,
                        updated_rework_notes,
                        context.schedule_id,
                        activity_id,
                        context.project_id,
                    ),
                )
                updated_row = cur.fetchone()
                current_actual_snapshot = _serialize_dict(dict(updated_row))

                # 4. Conclude any approved reopen events for this activity
                cur.execute(
                    """
                    UPDATE execution_events
                    SET reopen_status = 'NONE'
                    WHERE matched_activity_id = %s AND schedule_id = %s AND project_id = %s
                      AND reopen_status = 'APPROVED';
                    """,
                    (activity_id, context.schedule_id, context.project_id),
                )

                # 5. Insert audit log capturing the historical record
                audit_log_id = _insert_audit_log_tx(
                    cur=cur,
                    context=context,
                    action="ACTUAL_REVISION_APPROVED",
                    entity_type="APPROVED_ACTUAL",
                    entity_id=existing_actual["actual_id"],
                    before_state=before_actual_snapshot,
                    after_state=current_actual_snapshot,
                    entity_context={
                        "activity_id": activity_id,
                        "schedule_id": context.schedule_id,
                        "decision_id": decision_id,
                        "revision_notes": revision_notes,
                    },
                )

                conn.commit()

        canonical_state = StageService.get_execution_state(current_actual_snapshot)
        workflow_condition = StageService.get_workflow_condition(current_actual_snapshot)

        return {
            "success": True,
            "activity_id": activity_id,
            "schedule_id": context.schedule_id,
            "project_id": context.project_id,
            "canonical_state": canonical_state,
            "workflow_condition": workflow_condition,
            "previous_actual": before_actual_snapshot,
            "current_actual": current_actual_snapshot,
            "audit_log_id": audit_log_id,
        }

    @classmethod
    def get_actual_history(
        cls,
        context: ScheduleContext,
        activity_id: str,
    ) -> Dict[str, Any]:
        """
        Reconstructs the complete, chronological lifecycle of an activity:
        Initial actual -> Reopen requests -> Authorizations -> Rework events -> Revisions.
        """
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                # 1. Verify activity exists
                cur.execute(
                    """
                    SELECT activity_id, schedule_id, project_id, stage_id, activity_name
                    FROM schedule_activities
                    WHERE activity_id = %s AND schedule_id = %s AND project_id = %s;
                    """,
                    (activity_id, context.schedule_id, context.project_id),
                )
                act_row = cur.fetchone()
                if not act_row:
                    raise ActivityNotFoundError(f"Activity '{activity_id}' not found in schedule '{context.schedule_id}'.")

                # 2. Get current approved actual
                cur.execute(
                    """
                    SELECT actual_id, decision_id, event_id, schedule_id, activity_id,
                           project_id, stage_id, actual_start, actual_finish,
                           actual_quantity, actual_pct_complete, is_reopened,
                           reopened_at, reopened_by, rework_notes, created_at
                    FROM approved_actuals
                    WHERE activity_id = %s AND schedule_id = %s AND project_id = %s;
                    """,
                    (activity_id, context.schedule_id, context.project_id),
                )
                cur_actual_row = cur.fetchone()
                current_approved = _serialize_dict(dict(cur_actual_row)) if cur_actual_row else None

                # 3. Get historical approved actual snapshots from audit logs
                cur.execute(
                    """
                    SELECT log_id, action, before_state, after_state, entity_context, timestamp
                    FROM audit_logs
                    WHERE project_id = %s
                      AND action = 'ACTUAL_REVISION_APPROVED'
                      AND (entity_id = %s OR entity_context->>'activity_id' = %s)
                    ORDER BY timestamp ASC, log_id ASC;
                    """,
                    (context.project_id, (current_approved.get("actual_id") if current_approved else ""), activity_id),
                )
                revision_audit_rows = cur.fetchall()
                historical_actuals: List[Dict[str, Any]] = []
                for row in revision_audit_rows:
                    b_state = row["before_state"] if isinstance(row, dict) else row[2]
                    if isinstance(b_state, str):
                        try:
                            b_state = json.loads(b_state)
                        except Exception:
                            b_state = {}
                    if b_state:
                        historical_actuals.append(b_state)

                # 4. Get reopen history (execution events where event_type='REOPEN_REQUEST' or reopen_status != 'NONE')
                cur.execute(
                    """
                    SELECT event_id, event_date, event_type, reopen_status,
                           reopened_from_actual_id, reopen_justification,
                           reopen_requested_by, reopen_decided_by, created_at
                    FROM execution_events
                    WHERE matched_activity_id = %s AND schedule_id = %s AND project_id = %s
                      AND (event_type = 'REOPEN_REQUEST' OR reopen_status != 'NONE')
                    ORDER BY event_date ASC, created_at ASC;
                    """,
                    (activity_id, context.schedule_id, context.project_id),
                )
                reopen_events = [_serialize_dict(dict(r)) for r in cur.fetchall()]

                # 5. Get rework execution events
                cur.execute(
                    """
                    SELECT event_id, event_date, raw_claim_text, event_type, status,
                           claimed_pct, claimed_quantity, reopened_from_actual_id, created_at
                    FROM execution_events
                    WHERE matched_activity_id = %s AND schedule_id = %s AND project_id = %s
                      AND reopened_from_actual_id IS NOT NULL
                      AND event_type != 'REOPEN_REQUEST'
                    ORDER BY event_date ASC, created_at ASC;
                    """,
                    (activity_id, context.schedule_id, context.project_id),
                )
                rework_events = [_serialize_dict(dict(r)) for r in cur.fetchall()]

                # 6. Complete audit timeline
                cur.execute(
                    """
                    SELECT log_id, action, entity_type, entity_id, actor_id, role,
                           before_state, after_state, timestamp
                    FROM audit_logs
                    WHERE project_id = %s
                      AND (entity_id = %s OR entity_context->>'activity_id' = %s)
                    ORDER BY timestamp ASC, log_id ASC;
                    """,
                    (context.project_id, activity_id, activity_id),
                )
                raw_audit_timeline = cur.fetchall()
                audit_timeline = []
                for r in raw_audit_timeline:
                    d = dict(r)
                    for k in ("before_state", "after_state"):
                        if isinstance(d[k], str):
                            try:
                                d[k] = json.loads(d[k])
                            except Exception:
                                pass
                    audit_timeline.append(_serialize_dict(d))

        merged_state = dict(current_approved or {})
        canonical_state = StageService.get_execution_state(merged_state)
        workflow_condition = StageService.get_workflow_condition(merged_state)

        return {
            "activity_id": activity_id,
            "schedule_id": context.schedule_id,
            "project_id": context.project_id,
            "canonical_state": canonical_state,
            "workflow_condition": workflow_condition,
            "current_approved_actual": current_approved,
            "historical_approved_actuals": historical_actuals,
            "reopen_history": reopen_events,
            "rework_execution_events": rework_events,
            "audit_timeline": audit_timeline,
        }
