"""Governed reopen of a completed activity (decision D7), as an append-only correction.

request (Site Engineer or Supervisor, with a reason) -> decide (Supervisor, notes mandatory) -> if APPROVED the activity is in REWORK: its approved progress is unchanged until a
Supervisor approves a fresh claim for it, whose ledger entries SUPERSEDE the latest ones (decisions.py does the writing; the ledger refuses a superseding entry unless a reopen is
APPROVED). Nothing is deleted or edited; every step is audited."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from .. import audit
from ..errors import ApiError
from .common import PM, SE, SUP, ProjectActor, actor_tx, active_version, notify, require_role, require_writable

REASONS = ("INCORRECT_COMPLETION", "CONTRADICTORY_FIELD_REPORT", "QUALITY_FAILURE", "QUANTITY_CORRECTION", "DATE_CORRECTION", "SUPERVISOR_CORRECTION", "OTHER")


def snapshot(c, version_id, activity_uid) -> Dict[str, Any]:
    r = c.execute("select physical_pct, actual_start, actual_finish, execution_state from activity_progress_as_of(%s, current_date) where activity_uid = %s", (version_id, activity_uid)).fetchone()
    if r is None:
        raise ApiError(409, "ACTIVITY_NOT_IN_ACTIVE_SCHEDULE", "That activity is not part of the project's active schedule version")
    return {"physical_pct": str(r["physical_pct"]), "actual_start": str(r["actual_start"]) if r["actual_start"] else None,
            "actual_finish": str(r["actual_finish"]) if r["actual_finish"] else None, "execution_state": r["execution_state"]}


def is_completed(snap: Dict[str, Any]) -> bool:
    return snap["execution_state"] == "COMPLETED" or snap["actual_finish"] is not None or float(snap["physical_pct"]) >= 100


def open_rework(c, project_id, activity_uid) -> Optional[dict]:
    return c.execute("select * from activity_reopens where project_id = %s and activity_uid = %s and status = 'APPROVED'", (project_id, activity_uid)).fetchone()


def request_reopen(actor: ProjectActor, activity_uid, *, reason_code: str, justification: str, evidence_event_ids: Optional[List[uuid.UUID]] = None) -> Dict[str, Any]:
    require_role(actor, SE, SUP, what="requesting a reopen")
    require_writable(actor)
    if reason_code not in REASONS:
        raise ApiError(422, "BAD_REASON", f"reason must be one of {', '.join(REASONS)}")
    if len((justification or "").strip()) < 3:
        raise ApiError(422, "JUSTIFICATION_REQUIRED", "A reopen request needs a justification")
    with actor_tx(actor, write=True) as c:
        ver = active_version(c, actor.project_id)
        snap = snapshot(c, ver["version_id"], activity_uid)
        if not is_completed(snap):
            raise ApiError(422, "NOT_COMPLETED", "Only a completed activity can be reopened: this one is still in progress or not started")
        if c.execute("select 1 from activity_reopens where project_id = %s and activity_uid = %s and status in ('REQUESTED','APPROVED')", (actor.project_id, activity_uid)).fetchone():
            raise ApiError(409, "REOPEN_ALREADY_OPEN", "A reopen of this activity is already waiting for a decision or in rework")
        ev_ids = list(dict.fromkeys(evidence_event_ids or []))
        if ev_ids:
            found = c.execute("select event_id, filed_by from execution_events where project_id = %s and event_id = any(%s)", (actor.project_id, ev_ids)).fetchall()
            if len(found) != len(ev_ids) or (actor.role == SE and any(f["filed_by"] != actor.user_id for f in found)):
                raise ApiError(404, "CLAIM_NOT_FOUND", "An evidence claim was not found")
        import json
        r = c.execute("insert into activity_reopens (project_id, activity_uid, reason_code, justification, evidence_event_ids, completed_snapshot, requested_by) "
                      "values (%s,%s,%s,%s,%s,%s::jsonb,%s) returning *", (actor.project_id, activity_uid, reason_code, justification.strip(), ev_ids, json.dumps(snap), actor.user_id)).fetchone()
        from .common import supervisors_of
        for sup in supervisors_of(c, actor.project_id):
            if sup != actor.user_id:
                notify(c, project_id=actor.project_id, recipient_id=sup, ntype="CLAIM_SUBMITTED", event_id=ev_ids[0] if ev_ids else None, created_by=actor.user_id,
                       title="Reopen requested", body=justification.strip()[:300]) if ev_ids else None
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=actor.role, action="REOPEN_REQUESTED", entity_type="ACTIVITY_REOPEN", entity_id=r["reopen_id"],
                  version_id=ver["version_id"], after={"activity_uid": str(activity_uid), "reason": reason_code, "completed_snapshot": snap})
    return r


def decide_reopen(actor: ProjectActor, activity_uid, *, decision: str, notes: str, rework_instructions: Optional[str] = None) -> Dict[str, Any]:
    require_role(actor, SUP, what="deciding a reopen")
    require_writable(actor)
    if decision not in ("APPROVED", "REJECTED"):
        raise ApiError(422, "BAD_DECISION", "decision must be APPROVED or REJECTED")
    if len((notes or "").strip()) < 3:
        raise ApiError(422, "REASON_REQUIRED", "A reopen decision needs notes: say why")
    with actor_tx(actor, write=True) as c:
        r = c.execute("select * from activity_reopens where project_id = %s and activity_uid = %s and status = 'REQUESTED' for update", (actor.project_id, activity_uid)).fetchone()
        if r is None:
            raise ApiError(400, "NO_PENDING_REOPEN", "There is no pending reopen request for this activity")
        ver = active_version(c, actor.project_id)
        row = c.execute("update activity_reopens set status = %s, decided_by = %s, decided_at = now(), decision_notes = %s, rework_instructions = %s where reopen_id = %s returning *",
                        (decision, actor.user_id, notes.strip(), rework_instructions, r["reopen_id"])).fetchone()
        notify_ev = r["evidence_event_ids"][0] if r["evidence_event_ids"] else None
        if notify_ev is not None:
            notify(c, project_id=actor.project_id, recipient_id=r["requested_by"], ntype="CLAIM_CLARIFICATION", event_id=notify_ev, created_by=actor.user_id,
                   title=f"Reopen {decision.lower()}", body=notes.strip()[:300])
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=SUP, action="REOPEN_APPROVED" if decision == "APPROVED" else "REOPEN_REJECTED", entity_type="ACTIVITY_REOPEN",
                  entity_id=r["reopen_id"], version_id=ver["version_id"], before={"status": "REQUESTED"}, after={"status": decision, "notes": notes.strip(), "rework_instructions": rework_instructions})
    return row


def close_after_rework(c, actor: ProjectActor, reopen_id, decision_id) -> None:
    c.execute("update activity_reopens set status = 'CLOSED', closed_at = now(), closing_decision_id = %s where reopen_id = %s and status = 'APPROVED'", (decision_id, reopen_id))
    audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=SUP, action="REOPEN_CLOSED", entity_type="ACTIVITY_REOPEN", entity_id=reopen_id,
              before={"status": "APPROVED"}, after={"status": "CLOSED", "closing_decision_id": str(decision_id)})


def list_reopens(actor: ProjectActor, activity_uid=None) -> List[dict]:
    with actor_tx(actor, readonly=True) as c:
        return c.execute("select r.*, rb.full_name as requested_by_name from activity_reopens r left join profiles rb on rb.id = r.requested_by where r.project_id = %s "
                         "and (%s::uuid is null or r.activity_uid = %s) order by r.requested_at desc", (actor.project_id, activity_uid, activity_uid)).fetchall()
