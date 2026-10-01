"""
Member 5's backend surface — the human Approve/Edit/Reject/Hold review
action. This is the pipeline's core principle in code: a field report is a
claim, not a fact, and nothing reaches approved_actuals/export/P6 without
passing through this endpoint.

Every decision creates exactly one planner_decisions row (never from
viewing/matching/checking a claim). Only APPROVE and EDIT additionally
write to approved_actuals, via M6's upsert_approved_actual(). REJECT and
HOLD still get their planner_decisions row (the record a decision was
made) but never touch approved_actuals. A single event_id can accumulate
several planner_decisions rows over time (the HOLD-reopen loop) --
execution_events.status always reflects the most recently submitted
action.
"""

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.context import gates
from backend.context.errors import SecurityErrorCode, SecurityException, raise_project_denied
from backend.context.project import ProjectContext
from backend.context.schedule import ScheduleContext
from backend.shared.actuals import upsert_approved_actual, _execute_upsert
from backend.services.notification_service import insert_decision_notification
from backend.shared.audit import append_audit_record
from backend.shared.db import get_connection
from backend.shared.schemas import DecisionRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["decisions"])

VALID_ACTIONS = {"APPROVE", "EDIT", "REJECT", "HOLD"}
# execution_events.status may only be entered from these states per the
# shared status-ownership rule -- VALIDATED/REVIEW_REQUIRED (first review)
# or HOLD (reopened, can be re-decided any number of times).
ELIGIBLE_SOURCE_STATUSES = {"VALIDATED", "REVIEW_REQUIRED", "HOLD"}
ACTION_TO_STATUS = {
    "APPROVE": "APPROVED",
    "EDIT": "EDITED",
    "REJECT": "REJECTED",
    "HOLD": "HOLD",
}
BULK_APPROVE_JUSTIFICATION = (
    "Bulk-approved: no open flags, confidence above threshold"
)


@router.get("/decisions/health")
def health():
    return {"router": "decisions", "status": "ok"}


def _fetch_claim(cur, event_id: str) -> Optional[dict]:
    cur.execute(
        "SELECT * FROM execution_events WHERE event_id = %s",
        (event_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _with_derived_pct(actual: dict) -> dict:
    """
    P6 has no quantity field (it is dropped from the payload), so for an activity progressed by
    incremental quantity the write-back would carry no progress at all. Derive PercentComplete
    from approved quantity / planned quantity -- the same derivation GET /activities/{id}/rollup
    uses -- for the P6 payload only; the stored approved actual is not modified.
    """
    if actual.get("actual_pct_complete") is not None or not actual.get("actual_quantity"):
        return actual
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT planned_quantity FROM schedule_activities WHERE schedule_id = %s AND activity_id = %s",
                (actual["schedule_id"], actual["activity_id"]),
            ).fetchone()
        planned = float(row["planned_quantity"]) if row and row["planned_quantity"] else 0.0
        if planned > 0:
            return {**actual, "actual_pct_complete": round(min(100.0, 100.0 * float(actual["actual_quantity"]) / planned), 2)}
    except Exception as e:
        logger.warning("could not derive P6 percent complete: %s", e)
    return actual


def _load_claim_splits(cur, event_id: str) -> List[dict]:
    """WBS split rows of a decomposed claim, largest share first."""
    cur.execute(
        """
        SELECT activity_id, split_pct FROM claim_activity_splits
        WHERE event_id = %s ORDER BY split_pct DESC, activity_id ASC
        """,
        (event_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def _differs(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return a is not b and not (a is None and b is None)
    return abs(float(a) - float(b)) > 1e-9


def _apply_edit_provenance(
    cur,
    claim: dict,
    resolved_activity_id: str,
    approved_pct: Optional[float],
    approved_qty: Optional[float],
) -> Dict[str, str]:
    """
    Feature 33: on a Supervisor EDIT only the fields that actually changed become
    SUPERVISOR_EDITED; untouched fields keep their stored provenance. Returns the
    tags that were set.
    """
    changed: Dict[str, str] = {}
    if approved_pct is not None and _differs(approved_pct, claim.get("claimed_pct")):
        changed["claimed_pct"] = "SUPERVISOR_EDITED"
    if approved_qty is not None and _differs(approved_qty, claim.get("claimed_quantity")):
        changed["claimed_quantity"] = "SUPERVISOR_EDITED"
    matched = claim.get("matched_activity_id")
    if matched and resolved_activity_id != matched:
        changed["activity_id"] = "SUPERVISOR_EDITED"
    if not changed:
        return {}
    prov = claim.get("field_provenance") or {}
    if isinstance(prov, str):
        try:
            prov = json.loads(prov)
        except ValueError:
            prov = {}
    prov = {**prov, **changed}
    cur.execute(
        "UPDATE execution_events SET field_provenance = %s::jsonb WHERE event_id = %s",
        (json.dumps(prov), claim["event_id"]),
    )
    return changed


def _record_decision(
    event_id: str,
    action: str,
    planner_id: str,
    justification: str,
    selected_activity_id: Optional[str],
    approved_pct: Optional[float],
    approved_qty: Optional[float],
    project_context: ProjectContext,
) -> Dict[str, Any]:
    """
    Shared core for POST /decisions and POST /digest/bulk-approve: inserts
    exactly one planner_decisions row, updates execution_events.status,
    appends the audit record (same transaction: no approval without its audit trail), and
    (APPROVE/EDIT only) calls the approved-actual upsert. The claim MUST belong to
    project_context's project. Raises HTTPException on a genuine failure so
    callers can decide how to surface it (bulk-approve catches this
    per-claim; the single-decision endpoint lets it propagate).
    """
    action = action.upper()
    if action not in VALID_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid action '{action}'. Must be one of {sorted(VALID_ACTIONS)}.",
        )

    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                claim = _fetch_claim(cur, event_id)
                if not claim:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Claim '{event_id}' not found.",
                    )
                if claim.get("project_id") is None or str(claim["project_id"]) != str(project_context.project_id):
                    raise_project_denied("The claim does not belong to the authorized project")

                if claim.get("status") not in ELIGIBLE_SOURCE_STATUSES:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Claim '{event_id}' has status "
                            f"'{claim.get('status')}', which is not eligible for "
                            f"a decision (must be one of {sorted(ELIGIBLE_SOURCE_STATUSES)})."
                        ),
                    )

                # Feature 30: a decomposed claim has no matched_activity_id (XOR with its
                # split rows); approval is applied to every split child, and the decision row
                # records the largest-share child as its primary activity.
                splits = [] if claim.get("matched_activity_id") else _load_claim_splits(cur, event_id)
                split_ids = [sp["activity_id"] for sp in splits]

                # M5 pre-fills with matched_activity_id; planner may override
                # with any top-3 candidate or a typed ID.
                if splits:
                    resolved_activity_id = (
                        selected_activity_id if selected_activity_id in split_ids else split_ids[0]
                    )
                else:
                    resolved_activity_id = selected_activity_id or claim.get("matched_activity_id")
                if not resolved_activity_id:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"Claim '{event_id}' has no matched_activity_id and "
                            "no selected_activity_id was provided."
                        ),
                    )

                # The target activity must exist in the CLAIM's schedule of THIS project, and a completed
                # activity can only be changed through the governed reopen/revision workflow, never by
                # overriding the target of an ordinary approval.
                target_ids = split_ids if splits else [resolved_activity_id]
                cur.execute(
                    "SELECT sa.activity_id, sa.stage_id, aa.actual_pct_complete, aa.is_reopened "
                    "FROM schedule_activities sa LEFT JOIN approved_actuals aa "
                    "  ON aa.schedule_id = sa.schedule_id AND aa.activity_id = sa.activity_id "
                    "WHERE sa.schedule_id = %s AND sa.project_id = %s AND sa.activity_id = ANY(%s)",
                    (claim["schedule_id"], str(project_context.project_id), target_ids),
                )
                target_rows = {r["activity_id"]: r for r in cur.fetchall()}
                missing = [a for a in target_ids if a not in target_rows]
                if missing:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Activity {missing} does not exist in the claim's schedule for this project.",
                    )
                if action in ("APPROVE", "EDIT"):
                    for a, tr in target_rows.items():
                        if (tr["actual_pct_complete"] or 0) >= 100 and not tr["is_reopened"]:
                            raise SecurityException(
                                status_code=409,
                                error_code="REOPEN_NOT_ALLOWED",
                                message=(
                                    f"Activity '{a}' is COMPLETED. A completed activity cannot be changed by an "
                                    "ordinary approval; request a governed reopen first."
                                ),
                            )

                decision_id = str(uuid.uuid4())
                before_state = dict(claim)

                cur.execute(
                    """
                    INSERT INTO planner_decisions (
                        decision_id, event_id, selected_activity_id, action,
                        approved_pct, approved_qty, planner_id, justification
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        decision_id,
                        event_id,
                        resolved_activity_id,
                        action,
                        approved_pct,
                        approved_qty,
                        planner_id,
                        justification,
                    ),
                )

                new_status = ACTION_TO_STATUS[action]
                cur.execute(
                    "UPDATE execution_events SET status = %s WHERE event_id = %s",
                    (new_status, event_id),
                )
                # Notify the site engineer who reported the claim, in the SAME transaction: the decision and its
                # notice commit together or not at all. (execution_events.supervisor_id is the reporting engineer.)
                cur.execute(
                    "SELECT activity_name FROM schedule_activities WHERE schedule_id = %s AND activity_id = %s",
                    (claim["schedule_id"], resolved_activity_id),
                )
                _act = cur.fetchone()
                insert_decision_notification(
                    cur,
                    project_id=str(project_context.project_id),
                    decision_id=decision_id,
                    event_id=event_id,
                    recipient_id=claim.get("supervisor_id"),
                    created_by=planner_id,
                    action=action,
                    activity_name=_act["activity_name"] if _act else None,
                    claimed_pct=claim.get("claimed_pct"),
                    approved_pct=approved_pct,
                    comment=justification,
                )

                approved_actual = None
                approved_actuals: List[dict] = []
                edited_fields: Dict[str, str] = {}
                if action in ("APPROVE", "EDIT"):
                    # Atomic upsert(s) inside the SAME transaction
                    if splits:
                        for sp in splits:
                            child = _execute_upsert(
                                conn=conn,
                                schedule_id=claim["schedule_id"],
                                activity_id=sp["activity_id"],
                                event_id=event_id,
                                decision_id=decision_id,
                                approved_pct=approved_pct,
                                approved_qty=approved_qty,
                                split_pct=float(sp["split_pct"]),
                                project_id=str(project_context.project_id),
                                stage_id=target_rows[sp["activity_id"]]["stage_id"],
                            )
                            if child:
                                approved_actuals.append(child)
                        approved_actual = approved_actuals[0] if approved_actuals else None
                    else:
                        approved_actual = _execute_upsert(
                            conn=conn,
                            schedule_id=claim["schedule_id"],
                            activity_id=resolved_activity_id,
                            event_id=event_id,
                            decision_id=decision_id,
                            approved_pct=approved_pct,
                            approved_qty=approved_qty,
                            project_id=str(project_context.project_id),
                            stage_id=target_rows[resolved_activity_id]["stage_id"],
                        )
                        approved_actuals = [approved_actual] if approved_actual else []
                    if action == "EDIT":
                        edited_fields = _apply_edit_provenance(
                            cur, claim, resolved_activity_id, approved_pct, approved_qty
                        )

                # Audit record in the SAME transaction: the decision, the approved actual and its audit
                # entry commit together or not at all (project-scoped hash chain).
                append_audit_record(
                    conn,
                    entity_type="execution_event",
                    entity_id=event_id,
                    action=action,
                    actor_id=planner_id,
                    before_state=before_state,
                    after_state={
                        "status": new_status,
                        "selected_activity_id": resolved_activity_id,
                        "decision_id": decision_id,
                        "split_activity_ids": split_ids,
                        "approved_pct": approved_pct,
                        "approved_qty": approved_qty,
                    },
                    project_id=project_context.project_id,
                    schedule_id=claim["schedule_id"],
                    role=project_context.role,
                    entity_context={"justification": justification, "edited_fields": edited_fields},
                )

    # Post-commit downstream adapters (non-blocking)
    if action in ("APPROVE", "EDIT") and approved_actuals:
        try:
            from backend.routers.export import trigger_auto_export
            trigger_auto_export(schedule_id=claim["schedule_id"])
        except Exception as e:
            logger.warning("Auto-triggered CSV export error (non-blocking): %s", e)

        for pushed in approved_actuals:
            try:
                from backend.shared.p6 import trigger_p6_actual_push
                trigger_p6_actual_push(actual=_with_derived_pct(pushed))
            except Exception as e:
                logger.warning("Auto-triggered P6 push error (non-blocking): %s", e)

    return {
        "decision_id": decision_id,
        "event_id": event_id,
        "action": action,
        "status": new_status,
        "selected_activity_id": resolved_activity_id,
        "approved_actual": approved_actual,
        "approved_actuals": approved_actuals,
        "edited_fields": edited_fields,
    }


@router.post("/decisions")
def create_decision(
    request: DecisionRequest,
    project_context: ProjectContext = Depends(gates.project_approve),
):
    """
    POST /api/v1/decisions
    Record Approve/Edit/Reject/Hold with justification. Requires an explicit project (X-Project-ID) and the
    APPROVE_ACTUAL permission (supervisor / project manager / owner); the claim must belong to that project.
    Human decision only: nothing in the system approves a claim on its own.
    """
    return _record_decision(
        event_id=request.event_id,
        action=request.action,
        planner_id=str(project_context.user.id),
        justification=request.justification,
        selected_activity_id=request.selected_activity_id,
        approved_pct=request.approved_pct,
        approved_qty=request.approved_qty,
        project_context=project_context,
    )


@router.get("/decisions")
def list_decisions(
    limit: int = 10,
    project_context: ProjectContext = Depends(gates.project_review),
):
    """
    GET /api/v1/decisions?limit=N
    Most recent planner_decisions of THIS PROJECT (via the decided event), newest first.
    """
    safe_limit = max(1, min(limit, 100))
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pd.* FROM planner_decisions pd
                JOIN execution_events ee ON ee.event_id = pd.event_id
                WHERE ee.project_id = %s
                ORDER BY pd.decided_at DESC, pd.decision_id DESC
                LIMIT %s
                """,
                (str(project_context.project_id), safe_limit),
            )
            rows = [dict(r) for r in cur.fetchall()]
    return rows


@router.get("/digest")
def get_digest(
    date: Optional[str] = None,
    schedule_id: Optional[str] = None,  # validated via schedule_context
    schedule_context: ScheduleContext = Depends(gates.claim_review_schedule),
):
    """
    GET /api/v1/digest?date=...&schedule_id=...
    Claims for a given day of an EXPLICIT schedule of the caller's project (no active/latest fallback),
    flat and unfiltered by status -- the frontend groups by discipline and derives its own per-status counts.
    """
    resolved_schedule_id = schedule_context.schedule_id
    project_id = str(schedule_context.project_id)
    with get_connection() as conn:
        with conn.cursor() as cur:
            if date:
                cur.execute(
                    """
                    SELECT * FROM execution_events
                    WHERE schedule_id = %s AND project_id = %s AND event_date = %s
                    ORDER BY discipline ASC, priority_score DESC NULLS LAST, created_at ASC
                    """,
                    (resolved_schedule_id, project_id, date),
                )
            else:
                cur.execute(
                    """
                    SELECT * FROM execution_events
                    WHERE schedule_id = %s AND project_id = %s
                    ORDER BY discipline ASC, priority_score DESC NULLS LAST, created_at ASC
                    """,
                    (resolved_schedule_id, project_id),
                )
            rows = [dict(r) for r in cur.fetchall()]

    return rows


class BulkApproveRequest(BaseModel):
    event_ids: Optional[List[str]] = None
    schedule_id: Optional[str] = None


@router.post("/digest/bulk-approve")
def bulk_approve(
    request: BulkApproveRequest = BulkApproveRequest(),
    schedule_context: ScheduleContext = Depends(gates.approve_schedule),
):
    """
    POST /api/v1/digest/bulk-approve
    Approve multiple eligible claims at once (supervisor-initiated; APPROVE_ACTUAL). Only VALIDATED claims of
    the EXPLICIT schedule/project are eligible. If event_ids is omitted, every currently-VALIDATED claim of
    that schedule is attempted -- never another schedule's or project's.

    Failure behavior: independent per-claim commits, not atomic. One bad
    claim does not roll back the others.
    """
    resolved_schedule_id = schedule_context.schedule_id
    project_context = schedule_context.project_context
    project_id = str(schedule_context.project_id)
    with get_connection() as conn:
        with conn.cursor() as cur:
            if request.event_ids is not None:
                cur.execute(
                    """
                    SELECT event_id FROM execution_events
                    WHERE status = 'VALIDATED' AND schedule_id = %s AND project_id = %s AND event_id = ANY(%s)
                    """,
                    (resolved_schedule_id, project_id, request.event_ids),
                )
            else:
                cur.execute(
                    "SELECT event_id FROM execution_events WHERE status = 'VALIDATED' AND schedule_id = %s AND project_id = %s",
                    (resolved_schedule_id, project_id),
                )
            candidate_ids = [r["event_id"] for r in cur.fetchall()]

    # Report any explicitly-requested ids that weren't eligible, alongside
    # per-claim failures encountered while processing eligible ones.
    approved: List[str] = []
    failed: List[Dict[str, str]] = []

    if request.event_ids is not None:
        ineligible = set(request.event_ids) - set(candidate_ids)
        for event_id in ineligible:
            failed.append({
                "event_id": event_id,
                "error": "Not eligible for bulk-approve (status is not VALIDATED, or not in this project's schedule).",
            })

    for event_id in candidate_ids:
        try:
            _record_decision(
                event_id=event_id,
                action="APPROVE",
                planner_id=str(project_context.user.id),
                justification=BULK_APPROVE_JUSTIFICATION,
                selected_activity_id=None,
                approved_pct=None,
                approved_qty=None,
                project_context=project_context,
            )
            approved.append(event_id)
        except HTTPException as e:
            failed.append({"event_id": event_id, "error": str(e.detail)})
        except Exception as e:
            failed.append({"event_id": event_id, "error": str(e)})

    return {"approved": approved, "failed": failed}
