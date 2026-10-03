"""Quality gates, inspection & test plans and inspection evidence (decision D8).

Inspection status is independent of progress. A REQUIRED gate must be PASSED or WAIVED before a Supervisor can approve progress on its activity (checked here for a clear
error and again by the database); gates that are not required are informational. Supervisors configure gates and pass / fail / waive them; Site Engineers (and Supervisors)
submit evidence; Project Managers only read gate status. Every change is audited."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from .. import audit
from ..errors import ApiError
from .common import PM, SE, SUP, ProjectActor, actor_tx, active_version, require_role, require_writable

HOLD_PASS_NEEDS = "A required hold point can only be released with a PASS inspection record and no FAIL record. Record the evidence first, or waive the gate with a justification."


def assert_releasable(conn, project_id, activity_uid) -> None:
    """raise QUALITY_HOLD if a REQUIRED gate of the activity is not satisfied (called before progress is approved)"""
    rows = conn.execute("select gate_name, status from quality_unsatisfied_gates(%s, %s)", (project_id, activity_uid)).fetchall()
    if rows:
        raise ApiError(409, "QUALITY_HOLD", f"{len(rows)} mandatory quality gate(s) of this activity are not satisfied yet: " + ", ".join(f"{r['gate_name']} ({r['status']})" for r in rows),
                       {"gates": [{"gate_name": r["gate_name"], "status": r["status"]} for r in rows]})


def _gate(c, project_id, gate_id, lock=False) -> dict:
    r = c.execute("select * from quality_gates where project_id = %s and quality_gate_id = %s" + (" for update" if lock else ""), (project_id, gate_id)).fetchone()
    if r is None:
        raise ApiError(404, "QUALITY_GATE_NOT_FOUND", "No such quality gate")
    return r


def create_itp(actor: ProjectActor, *, title: str, description: Optional[str] = None, discipline_code: Optional[str] = None, responsible_party: Optional[str] = None,
               status: str = "DRAFT") -> Dict[str, Any]:
    require_role(actor, SUP, what="managing inspection and test plans")
    require_writable(actor)
    with actor_tx(actor, write=True) as c:
        r = c.execute("insert into inspection_test_plans (project_id, title, description, discipline_code, responsible_party, status, created_by) values (%s,%s,%s,%s,%s,%s,%s) returning *",
                      (actor.project_id, title.strip(), description, discipline_code, responsible_party, status, actor.user_id)).fetchone()
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=SUP, action="ITP_CREATED", entity_type="ITP", entity_id=r["itp_id"], after={"title": title.strip(), "status": status})
    return r


def list_itps(actor: ProjectActor) -> List[dict]:
    with actor_tx(actor, readonly=True) as c:
        return c.execute("select * from inspection_test_plans where project_id = %s order by created_at desc", (actor.project_id,)).fetchall()


def get_itp(actor: ProjectActor, itp_id) -> dict:
    with actor_tx(actor, readonly=True) as c:
        r = c.execute("select * from inspection_test_plans where project_id = %s and itp_id = %s", (actor.project_id, itp_id)).fetchone()
    if r is None:
        raise ApiError(404, "ITP_NOT_FOUND", "No such inspection and test plan")
    return r


def create_gate(actor: ProjectActor, *, gate_name: str, gate_type: str, checkpoint_category: str = "QUALITY_CHECK", activity_uid=None, stage_wbs_uid=None, itp_id=None,
                required: bool = True, due_date: Optional[date] = None, remarks: Optional[str] = None) -> Dict[str, Any]:
    require_role(actor, SUP, what="configuring quality gates")
    require_writable(actor)
    if activity_uid is None and stage_wbs_uid is None:
        raise ApiError(422, "TARGET_REQUIRED", "A quality gate must name an activity or a stage")
    with actor_tx(actor, write=True) as c:
        ver = active_version(c, actor.project_id)
        if activity_uid is not None and c.execute("select 1 from baseline_activities where version_id = %s and activity_uid = %s", (ver["version_id"], activity_uid)).fetchone() is None:
            raise ApiError(409, "ACTIVITY_NOT_IN_ACTIVE_SCHEDULE", "That activity is not part of the project's active schedule version")
        if stage_wbs_uid is not None and c.execute("select 1 from schedule_wbs where version_id = %s and wbs_uid = %s and node_type = 'STAGE'", (ver["version_id"], stage_wbs_uid)).fetchone() is None:
            raise ApiError(409, "STAGE_NOT_IN_ACTIVE_SCHEDULE", "That is not a stage of the active schedule version")
        if itp_id is not None and c.execute("select 1 from inspection_test_plans where project_id = %s and itp_id = %s", (actor.project_id, itp_id)).fetchone() is None:
            raise ApiError(404, "ITP_NOT_FOUND", "No such inspection and test plan in this project")
        r = c.execute("insert into quality_gates (project_id, itp_id, activity_uid, stage_wbs_uid, gate_name, gate_type, checkpoint_category, required, due_date, remarks, created_by) "
                      "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *",
                      (actor.project_id, itp_id, activity_uid, stage_wbs_uid, gate_name.strip(), gate_type, checkpoint_category, required, due_date, remarks, actor.user_id)).fetchone()
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=SUP, action="QUALITY_GATE_CREATED", entity_type="QUALITY_GATE", entity_id=r["quality_gate_id"],
                  version_id=ver["version_id"], after={"gate_name": gate_name.strip(), "type": gate_type, "category": checkpoint_category, "required": required,
                                                       "activity_uid": str(activity_uid) if activity_uid else None, "stage_wbs_uid": str(stage_wbs_uid) if stage_wbs_uid else None})
    return r


def submit_evidence(actor: ProjectActor, gate_id, *, evidence_type: str, result: str = "PASS", document_id=None, inspector_name: Optional[str] = None,
                    inspection_date: Optional[date] = None, evidence_hash: Optional[str] = None, metadata: Optional[dict] = None) -> Dict[str, Any]:
    require_role(actor, SE, SUP, what="submitting inspection evidence")
    require_writable(actor)
    import json
    with actor_tx(actor, write=True) as c:
        gate = _gate(c, actor.project_id, gate_id, lock=True)
        if document_id is not None and c.execute("select 1 from source_documents where project_id = %s and document_id = %s", (actor.project_id, document_id)).fetchone() is None:
            raise ApiError(422, "DOCUMENT_NOT_FOUND", "That document does not belong to this project")
        ev = c.execute("insert into quality_evidence (project_id, quality_gate_id, document_id, evidence_type, result, inspector_name, inspection_date, evidence_hash, metadata, submitted_by) "
                       "values (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) returning *",
                       (actor.project_id, gate_id, document_id, evidence_type, result, inspector_name, inspection_date, evidence_hash, json.dumps(metadata or {}), actor.user_id)).fetchone()
        if gate["status"] == "PENDING" or gate["status"] == "FAILED":
            c.execute("update quality_gates set status = 'SUBMITTED' where project_id = %s and quality_gate_id = %s", (actor.project_id, gate_id))
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=actor.role, action="QUALITY_EVIDENCE_SUBMITTED", entity_type="QUALITY_EVIDENCE",
                  entity_id=ev["quality_evidence_id"], before={"gate_status": gate["status"]}, after={"gate": str(gate_id), "evidence_type": evidence_type, "result": result})
    return ev


def _transition(actor: ProjectActor, gate_id, status: str, action: str, remarks: Optional[str], extra: Optional[dict] = None) -> Dict[str, Any]:
    require_role(actor, SUP, what=f"{action.lower()} a quality gate")
    require_writable(actor)
    with actor_tx(actor, write=True) as c:
        gate = _gate(c, actor.project_id, gate_id, lock=True)
        if gate["status"] in ("PASSED", "WAIVED"):
            raise ApiError(409, "GATE_ALREADY_FINAL", f"The quality gate is already {gate['status']}")
        if status == "PASSED" and gate["required"] and gate["checkpoint_category"] == "HOLD":
            recs = c.execute("select result from quality_evidence where quality_gate_id = %s", (gate_id,)).fetchall()
            if not any(r["result"] == "PASS" for r in recs) or any(r["result"] == "FAIL" for r in recs):
                raise ApiError(409, "EVIDENCE_REQUIRED", HOLD_PASS_NEEDS)
        sets, params = ["status = %s", "remarks = coalesce(%s, remarks)"], [status, remarks]
        if status == "PASSED":
            sets += ["passed_at = now()", "passed_by = %s"]; params.append(actor.user_id)
        elif status == "FAILED":
            sets += ["failed_at = now()", "failed_by = %s"]; params.append(actor.user_id)
        elif status == "WAIVED":
            sets += ["waived_at = now()", "waived_by = %s", "waiver_reason = %s"]; params += [actor.user_id, (extra or {}).get("waiver_reason")]
        r = c.execute(f"update quality_gates set {', '.join(sets)} where project_id = %s and quality_gate_id = %s returning *", (*params, actor.project_id, gate_id)).fetchone()
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=SUP, action=action, entity_type="QUALITY_GATE", entity_id=gate_id,
                  before={"status": gate["status"]}, after={"status": status, "remarks": remarks, **(extra or {})})
    return r


def pass_gate(actor: ProjectActor, gate_id, remarks: Optional[str] = None):
    return _transition(actor, gate_id, "PASSED", "QUALITY_GATE_PASSED", remarks)


def fail_gate(actor: ProjectActor, gate_id, remarks: Optional[str] = None):
    return _transition(actor, gate_id, "FAILED", "QUALITY_GATE_FAILED", remarks)


def waive_gate(actor: ProjectActor, gate_id, waiver_reason: str, remarks: Optional[str] = None):
    if len((waiver_reason or "").strip()) < 3:
        raise ApiError(422, "WAIVER_REASON_REQUIRED", "A waiver needs a justification")
    return _transition(actor, gate_id, "WAIVED", "QUALITY_GATE_WAIVED", remarks, {"waiver_reason": waiver_reason.strip()})


def activity_status(c, project_id, activity_uid) -> Dict[str, Any]:
    gates = c.execute("select * from quality_gates where project_id = %s and activity_uid = %s order by created_at", (project_id, activity_uid)).fetchall()
    req = [g for g in gates if g["required"]]
    total = len(gates)
    passed = sum(1 for g in gates if g["status"] == "PASSED")
    pending = sum(1 for g in req if g["status"] in ("PENDING", "SUBMITTED"))
    failed = sum(1 for g in req if g["status"] == "FAILED")
    waived = sum(1 for g in gates if g["status"] == "WAIVED")
    hold = any(g["checkpoint_category"] == "HOLD" and g["required"] and g["status"] not in ("PASSED", "WAIVED") for g in gates)
    required = len(req) > 0
    if not required:
        ok, st, why = True, "NOT_REQUIRED", None
    elif failed:
        ok, st, why = False, "FAILED", f"Activity has {failed} failed quality gate(s)"
    elif hold:
        ok, st, why = False, "PENDING", "Activity has 1 or more active hold point(s) pending clearance"
    elif pending:
        ok, st, why = False, ("SUBMITTED" if any(g["status"] == "SUBMITTED" for g in req) else "PENDING"), f"Activity has {pending} pending/submitted quality gate(s)"
    else:
        ok, st, why = True, ("PASSED" if any(g["status"] == "PASSED" for g in req) else "WAIVED"), None
    return {"quality_gate_required": required, "is_eligible": ok, "status": st, "total_gates": total, "passed_gates": passed, "pending_gates": pending, "failed_gates": failed,
            "waived_gates": waived, "has_active_hold_point": hold, "blocking_reason": why, "gates": gates}
