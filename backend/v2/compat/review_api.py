"""The Supervisor's Review Workspace on v2 data: the ORIGINAL review queue (smart priority recomputed on read), evidence links, decisions, Daily Digest and bulk operations,
and the audit feed.

Decisions: the original form sends `approved_pct` / `approved_qty`. They are translated onto v2's explicit approval methods (as claimed, apply a percentage to the measured
quantities, or enter an approved quantity) and executed by the v2 decision service, which writes the append-only ledgers and keeps every guard (overrun acknowledgement,
quality gates, governed reopen, one decision per claim). The API never approves anything by itself: only a Supervisor's request reaches `decide`."""
from __future__ import annotations

import json
import uuid
from datetime import date as _date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import permissions as P
from ..domain import claims as dc, decisions as dd
from ..domain.common import DECIDABLE_STATUSES, PM, SE, SUP, actor_tx, active_version
from ..errors import ApiError
from . import shapes
from .checks_api import legacy_view_mode, run_check
from .claims_api import candidates_for, load_claims, one_claim
from .context import Ctx, legacy_ctx

router = APIRouter(prefix="/api/v1", tags=["legacy-contract: review"])
BULK_JUSTIFICATION = "Bulk-approved from the Daily Digest: validated, no flags"


def _cid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        raise ApiError(404, "RESOURCE_NOT_FOUND", f"Claim '{raw}' not found.")


# ----------------------------------------------------------------------------------------------------------------------- review queue
QUEUE_STATUSES = ("VALIDATED", "REVIEW_REQUIRED", "HOLD")


def _ensure_checked(ctx: Ctx) -> None:
    """open claims that have not been through the checks yet (e.g. filed through the v2 API) are checked now, so no claim is invisible to the queue"""
    with actor_tx(ctx.actor, readonly=True) as c:
        todo = c.execute("select e.event_id from execution_events e where e.project_id = %s and e.status in ('EXTRACTED','MATCHED') and not exists "
                         "(select 1 from audit_logs a where a.entity_type = 'CLAIM' and a.entity_id = e.event_id::text and a.action = 'CLAIM_CHECKED') limit 200", (ctx.project_id,)).fetchall()
    for r in todo:
        try:
            run_check(ctx, r["event_id"])
        except ApiError:
            pass


@router.get("/review-queue")
def review_queue(sort: Optional[str] = "priority", status: Optional[str] = None, ctx: Ctx = Depends(legacy_ctx(P.REVIEW_CLAIMS))):
    """claims waiting for a decision (VALIDATED, REVIEW_REQUIRED, HOLD), the smart priority recomputed fresh on every read with the ORIGINAL scoring"""
    from backend.routers import checks as lc
    _ensure_checked(ctx)
    now = datetime.now(timezone.utc)
    wanted = (status.upper(),) if status and status.upper() in QUEUE_STATUSES else QUEUE_STATUSES
    items: List[Dict[str, Any]] = []
    with actor_tx(ctx.actor, write=True) as c:
        claims = [x for x in load_claims(c, ctx, "e.status in ('EXTRACTED','MATCHED','VALIDATED','DISPUTED')", limit=1000) if x["status"] in wanted]
        by_version: Dict[str, List[Dict[str, Any]]] = {}
        for cl in claims:
            by_version.setdefault(cl["schedule_id"], []).append(cl)
        for version, group in by_version.items():
            with legacy_view_mode(c, ctx.project_id, version):
                for cl in group:
                    eid = cl["event_id"]
                    issues = [dict(r) for r in c.execute("select * from validation_issues where event_id = %s", (eid,)).fetchall()]
                    confl = [dict(r) for r in c.execute("select * from conflict_records where (event_id_a = %s or event_id_b = %s) and status = 'OPEN'", (eid, eid)).fetchall()]
                    links = [dict(r) for r in c.execute("select * from evidence_links where event_id_a = %s or event_id_b = %s", (eid, eid)).fetchall()]
                    cands = [dict(r) for r in c.execute("select * from candidate_matches where event_id = %s order by rank_order", (eid,)).fetchall()]
                    row = c.execute("select * from execution_events where event_id = %s", (eid,)).fetchone()
                    prio = lc.evaluate_smart_review_priority(event_row=dict(row), validation_issues=issues, conflicts=confl, evidence_links=links, splits=[], candidate_matches=cands, conn=c, current_time=now)
                    items.append({**{k: cl[k] for k in ("event_id", "schedule_id", "status", "event_date", "raw_claim_text", "input_channel", "discipline", "action", "event_type", "claim_mode",
                                                        "asset_tag", "location", "matched_activity_id", "claimed_pct", "claimed_quantity", "claimed_uom", "created_at", "field_provenance")},
                                  "priority_score": prio["priority_score"], "priority_reasons": prio["priority_reasons"], "base_severity": prio["base_severity"],
                                  "criticality_multiplier": prio["criticality_multiplier"], "aging": prio["aging"], "hours_in_queue": prio["hours_in_queue"],
                                  "validation_issues": [{"issue_id": str(i["issue_id"]), "event_id": str(i["event_id"]), "rule_code": i["rule_code"], "severity": i["severity"], "description": i["description"]} for i in issues],
                                  "conflicts": [{k: (shapes.iso(v) if isinstance(v, (_date, datetime)) else (str(v) if isinstance(v, uuid.UUID) else v)) for k, v in x.items()} for x in confl],
                                  "evidence_links": [{k: (shapes.iso(v) if isinstance(v, (_date, datetime)) else (str(v) if isinstance(v, uuid.UUID) else v)) for k, v in x.items()} for x in links],
                                  "reasons_breakdown": prio.get("reasons_breakdown", {})})
                    c.execute("select set_config('search_path', 'public', true)")
                    c.execute("update execution_events set priority_score = %s, priority_reasons = %s where project_id = %s and event_id = %s and status in ('EXTRACTED','MATCHED','VALIDATED','DISPUTED')",
                              (prio["priority_score"], prio["priority_reasons"], ctx.project_id, uuid.UUID(eid)))
                    c.execute("select set_config('search_path', 'lrm, public', true)")

    def key(it):
        return (-float(it.get("priority_score") or 0.0), it.get("created_at") or "", it["event_id"])
    if not sort or sort.lower() == "priority":
        items.sort(key=key)
    elif sort.lower() == "created_at":
        items.sort(key=lambda it: (it.get("created_at") or "", it["event_id"]))
    return {"total": len(items), "items": items, "review_queue": items, "sort": sort, "migration_blockers": []}


@router.get("/claims/{event_id}/evidence")
def claim_evidence(event_id: str, ctx: Ctx = Depends(legacy_ctx(P.REVIEW_CLAIMS))):
    """Evidence Fusion: corroborating / contradicting links to other claims, with the other claim's channel and document for context (Supervisor only)"""
    cid = _cid(event_id)
    with actor_tx(ctx.actor, readonly=True) as c:
        one_claim(c, ctx, cid)
        rows = c.execute(
            "select l.*, o.event_id as o_id, o.event_date as o_date, o.input_channel as o_channel, o.claimed_pct as o_pct, o.raw_claim_text as o_text, d.kind as o_kind, d.file_name as o_file, "
            "(select reported_qty from claim_quantities q where q.event_id = o.event_id order by claim_quantity_id limit 1) as o_qty, "
            "(select reported_uom from claim_quantities q where q.event_id = o.event_id order by claim_quantity_id limit 1) as o_uom "
            "from evidence_links l join execution_events o on o.event_id = case when l.event_id_a = %s then l.event_id_b else l.event_id_a end "
            "left join source_documents d on d.document_id = o.document_id where l.project_id = %s and (l.event_id_a = %s or l.event_id_b = %s) order by l.created_at desc", (cid, ctx.project_id, cid, cid)).fetchall()
    out = []
    for r in rows:
        ctxt = {"event_id": str(r["o_id"]), "event_date": shapes.iso(r["o_date"]), "input_channel": shapes.CHANNEL_OUT.get(r["o_channel"], "TYPED_TEXT"), "claimed_pct": shapes.num(r["o_pct"]),
                "claimed_quantity": shapes.num(r["o_qty"]), "claimed_uom": r["o_uom"], "raw_claim_text": r["o_text"], "document_type": r["o_kind"], "file_name": r["o_file"]}
        out.append({"link_id": str(r["link_id"]), "event_id_a": str(r["event_id_a"]), "event_id_b": str(r["event_id_b"]), "relation_type": r["relation_type"], "confidence": shapes.num(r["confidence"]),
                    "rationale": r["rationale"], "created_at": shapes.iso(r["created_at"]), "opposite_event_id": str(r["o_id"]), "opposite_channel": ctxt["input_channel"],
                    "opposite_document_type": r["o_kind"], "opposite_context": ctxt})
    return {"event_id": str(cid), "evidence_links": out, "evidence": out, "total": len(out)}


# ----------------------------------------------------------------------------------------------------------------------- decisions
class DecisionIn(BaseModel):
    event_id: str
    selected_activity_id: Optional[str] = None
    action: str
    approved_pct: Optional[float] = None
    approved_qty: Optional[float] = None
    justification: str = ""
    overrun_ack_note: Optional[str] = None            # only when the server asked for an explicit overrun acknowledgement
    short_close_note: Optional[str] = None


def _approval_params(c, ctx: Ctx, claim: dict, uid, body: DecisionIn) -> Dict[str, Any]:
    """the original form's approved_pct / approved_qty -> a v2 approval method (no scoring, no math of its own: the v2 plan computes and validates everything)"""
    ver = active_version(c, ctx.project_id)
    assigns = dc.measured_assignments(c, ver["version_id"], uid)
    qrows = c.execute("select * from claim_quantities where event_id = %s order by claim_quantity_id", (claim["event_id"],)).fetchall()
    bound = [q for q in qrows if q["assignment_uid"] is not None]
    claimed_pct = float(claim["claimed_pct"]) if claim["claimed_pct"] is not None else None
    if body.approved_qty is not None:
        target = bound[0]["assignment_uid"] if len(bound) == 1 else (assigns[0]["assignment_uid"] if len(assigns) == 1 else None)
        if target is None:
            raise ApiError(422, "QUANTITY_TARGET_AMBIGUOUS", "This activity measures several quantities; the original form cannot say which one the approved quantity is for. Bind the claim's quantities first.")
        if len(bound) == 1 and abs(float(bound[0]["reported_qty"]) - body.approved_qty) < 1e-9:
            return {"method": "QUANTITIES_AS_CLAIMED"}
        return {"method": "MANUAL_QUANTITIES", "approved_quantities": {str(target): {"increment": str(body.approved_qty)}}}
    if body.approved_pct is not None:
        if not assigns:
            return {"method": "PCT_ONLY_ACTIVITY", "apply_pct": str(body.approved_pct)}
        return {"method": "APPLY_PCT_TO_ASSIGNMENTS", "apply_pct": str(body.approved_pct)}
    if bound or (assigns and claimed_pct is None):
        return {"method": "QUANTITIES_AS_CLAIMED"}
    return {"method": "APPLY_PCT_TO_ASSIGNMENTS" if assigns else "PCT_ONLY_ACTIVITY"}


def _decide(ctx: Ctx, body: DecisionIn, *, bulk: bool = False) -> Dict[str, Any]:
    cid = _cid(body.event_id)
    action = body.action.upper()
    if action not in ("APPROVE", "EDIT", "REJECT", "HOLD"):
        raise ApiError(422, "BAD_ACTION", f"Invalid action '{action}'. Must be one of ['APPROVE', 'EDIT', 'HOLD', 'REJECT'].")
    with actor_tx(ctx.actor, readonly=True) as c:
        claim = dc._claim_row(c, ctx.project_id, cid)
        ver = active_version(c, ctx.project_id)
        sel_uid = None
        if body.selected_activity_id:
            r = c.execute("select activity_uid from baseline_activities where version_id = %s and external_activity_id = %s", (ver["version_id"], body.selected_activity_id)).fetchone()
            if r is None:
                raise ApiError(422, "ACTIVITY_NOT_FOUND", f"Activity ['{body.selected_activity_id}'] does not exist in the claim's schedule for this project.")
            sel_uid = r["activity_uid"]
    if claim["status"] not in DECIDABLE_STATUSES:
        raise ApiError(409, "CLAIM_NOT_DECIDABLE", f"Claim '{body.event_id}' has status '{claim['status']}', which is not eligible for a decision.")
    just = (body.justification or "").strip()
    if action in ("REJECT", "HOLD"):
        res = dd.decide(ctx.actor, cid, action=action, justification=just, clarification_question=just if action == "HOLD" else None)
        approved = []
        edited: Dict[str, str] = {}
    else:
        if sel_uid is not None and sel_uid != claim["matched_activity_uid"]:          # the Supervisor's explicit override of the engine's (or the engineer's) activity
            dc.rematch_claim(ctx.actor, cid, sel_uid)
            with actor_tx(ctx.actor, readonly=True) as c:
                claim = dc._claim_row(c, ctx.project_id, cid)
        uid = claim["matched_activity_uid"] if sel_uid is None else sel_uid
        if uid is None:
            raise ApiError(422, "NO_ACTIVITY_MATCHED", f"Claim '{body.event_id}' has no matched_activity_id and no selected_activity_id was provided.")
        with actor_tx(ctx.actor, readonly=True) as c:
            params = _approval_params(c, ctx, claim, uid, body)
        # what the values amount to decides APPROVE vs EDIT (the original form lets the Supervisor change them; v2 states it explicitly)
        extra = {k: v for k, v in (("overrun_ack_note", body.overrun_ack_note), ("short_close_note", body.short_close_note)) if v}
        prev = dd.preview_decision(ctx.actor, cid, **{**params, "action": "APPROVE"})
        derived = "APPROVE" if prev.get("ok") else ("EDIT" if (prev.get("error") or {}).get("details", {}).get("expected_action") == "EDIT" else "APPROVE")
        if not prev.get("ok") and (prev["error"]["code"] == "ACTION_MISMATCH"):
            derived = "EDIT"
        res = dd.decide(ctx.actor, cid, action=derived, justification=just or ("Approved as reported" if derived == "APPROVE" else ""), **params, **extra)
        approved = res.get("applied", [])
        edited = {}
        if derived == "EDIT":
            if body.approved_pct is not None and (claim["claimed_pct"] is None or abs(float(claim["claimed_pct"]) - body.approved_pct) > 1e-9):
                edited["claimed_pct"] = "SUPERVISOR_EDITED"
            if body.approved_qty is not None:
                edited["claimed_quantity"] = "SUPERVISOR_EDITED"
    with actor_tx(ctx.actor, readonly=True) as c:
        legacy = one_claim(c, ctx, cid)
        pct_row = c.execute("select physical_pct, actual_start, actual_finish from activity_progress_as_of(%s, current_date) where activity_uid = %s", (ver["version_id"], res.get("activity_uid") or claim["matched_activity_uid"] or sel_uid)).fetchone() \
            if action in ("APPROVE", "EDIT") else None
    actual = None
    if pct_row is not None:
        actual = {"schedule_id": str(ver["version_id"]), "activity_id": legacy["matched_activity_id"], "actual_pct_complete": float(pct_row["physical_pct"]), "actual_start": shapes.iso(pct_row["actual_start"]),
                  "actual_finish": shapes.iso(pct_row["actual_finish"]), "event_id": str(cid), "decision_id": str(res["decision_id"])}
    return {"decision_id": str(res["decision_id"]), "event_id": str(cid), "action": res["action"], "status": legacy["status"], "selected_activity_id": legacy["matched_activity_id"],
            "approved_actual": actual, "approved_actuals": [actual] if actual else [], "edited_fields": edited, "method": res.get("method"), "applied": approved, "result": res.get("result", {})}


@router.post("/decisions")
def create_decision(body: DecisionIn, ctx: Ctx = Depends(legacy_ctx(P.REVIEW_CLAIMS, writable=True))):
    return _decide(ctx, body)


@router.get("/decisions")
def list_decisions(limit: int = 10, ctx: Ctx = Depends(legacy_ctx(P.REVIEW_CLAIMS))):
    with actor_tx(ctx.actor, readonly=True) as c:
        rows = c.execute("select d.decision_id, d.event_id, ba.external_activity_id as sel, d.action, d.approved_pct, d.decided_by, d.justification, d.decided_at, "
                         "(select sum(a.incremental_qty) from approved_resource_progress a where a.decision_id = d.decision_id) as qty "
                         "from planner_decisions d join execution_events e on e.event_id = d.event_id left join baseline_activities ba on ba.version_id = e.filed_in_version_id and ba.activity_uid = d.selected_activity_uid "
                         "where d.project_id = %s order by d.decided_at desc, d.decision_id desc limit %s", (ctx.project_id, max(1, min(limit, 100)))).fetchall()
    return [{"decision_id": str(r["decision_id"]), "event_id": str(r["event_id"]), "selected_activity_id": r["sel"], "action": r["action"], "approved_pct": shapes.num(r["approved_pct"]),
             "approved_qty": shapes.num(r["qty"]), "planner_id": str(r["decided_by"]), "justification": r["justification"], "decided_at": shapes.iso(r["decided_at"])} for r in rows]


# ----------------------------------------------------------------------------------------------------------------------- daily digest
@router.get("/digest")
def digest(date: Optional[str] = None, ctx: Ctx = Depends(legacy_ctx(P.REVIEW_CLAIMS))):
    """the project's claims of one day (or all), flat and unfiltered by status -- the page groups them by discipline"""
    where, params = "true", {}
    if date:
        where, params = "e.event_date = %(d)s", {"d": date}
    with actor_tx(ctx.actor, readonly=True) as c:
        rows = load_claims(c, ctx, where, params, order="e.discipline_code asc nulls last, e.priority_score desc nulls last, e.created_at asc", limit=2000)
    return rows


class BulkIn(BaseModel):
    event_ids: Optional[List[str]] = None


@router.post("/digest/bulk-approve")
def bulk_approve(body: BulkIn = BulkIn(), ctx: Ctx = Depends(legacy_ctx(P.REVIEW_CLAIMS, writable=True))):
    """Approve many VALIDATED claims at once. Each is its own decision through the v2 decision service (own transaction, own audit entry, every guard re-verified);
    one failing claim never rolls back the others."""
    with actor_tx(ctx.actor, readonly=True) as c:
        valid = [x["event_id"] for x in load_claims(c, ctx, "e.status = 'VALIDATED'", limit=2000) if x["status"] == "VALIDATED"]
    wanted = body.event_ids if body.event_ids is not None else valid
    approved: List[str] = []
    failed: List[Dict[str, str]] = []
    if body.event_ids is not None:
        for e in set(body.event_ids) - set(valid):
            failed.append({"event_id": e, "error": "Not eligible for bulk-approve (status is not VALIDATED, or not in this project's schedule)."})
    for eid in [e for e in wanted if e in valid]:
        try:
            _decide(ctx, DecisionIn(event_id=eid, action="APPROVE", justification=BULK_JUSTIFICATION), bulk=True)
            approved.append(eid)
        except ApiError as e:
            failed.append({"event_id": eid, "error": e.message})
        except Exception as e:                                         # noqa: BLE001
            failed.append({"event_id": eid, "error": str(e)})
    return {"approved": approved, "failed": failed}


# ----------------------------------------------------------------------------------------------------------------------- audit feed
def _audit_row(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"log_id": r["log_id"], "entity_type": r["entity_type"], "entity_id": r["entity_id"], "action": r["action"], "actor_id": str(r["actor_id"]) if r["actor_id"] else None,
            "before_state": r["before_state"], "after_state": r["after_state"], "payload_hash": r["payload_hash"], "previous_hash": r["previous_hash"], "current_hash": r["current_hash"],
            "timestamp": shapes.iso(r["occurred_at"]), "role": r["role"]}


@router.get("/audit")
def recent_audit(limit: int = 20, ctx: Ctx = Depends(legacy_ctx(P.VIEW_AUDIT))):
    with actor_tx(ctx.actor, readonly=True) as c:
        rows = c.execute("select * from audit_logs where project_id = %s order by log_id desc limit %s", (ctx.project_id, max(1, min(limit, 200)))).fetchall()
    return [_audit_row(r) for r in rows]


@router.get("/audit/{entity_id}")
def audit_for_entity(entity_id: str, ctx: Ctx = Depends(legacy_ctx(P.VIEW_AUDIT))):
    with actor_tx(ctx.actor, readonly=True) as c:
        rows = c.execute("select * from audit_logs where project_id = %s and entity_id = %s order by log_id", (ctx.project_id, entity_id)).fetchall()
    return {"entity_id": entity_id, "records": [_audit_row(r) for r in rows], "total": len(rows)}
