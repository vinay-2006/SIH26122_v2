"""Supervisor decisions: the ONLY way approved progress is created.

decide() runs in ONE transaction: claim row lock -> per-assignment advisory locks -> plan from the CURRENT ledger heads -> decision row ->
ledger rows -> consistency check against the SQL rollup -> notification -> audit. The database then refuses to COMMIT a decision that lacks
any of those (deferred constraint trigger), so a half-written decision cannot exist.

Methods (recorded on the decision, never inferred silently):
  QUANTITIES_AS_CLAIMED     the claim's bound quantities (cumulative as stated; incremental added to the current ledger head)
  MANUAL_QUANTITIES         the supervisor enters approved quantities per assignment (always an EDIT)
  APPLY_PCT_TO_ASSIGNMENTS  the supervisor explicitly applies a percentage to each measured assignment (quantity = baseline x pct)
  PCT_ONLY_ACTIVITY         activities with no measured quantity (approvals, milestones): the percentage / dates are the progress
A percentage-only claim on a quantity-based activity is NEVER converted unless one of the first three is chosen explicitly.
Over-baseline quantities are stored unclamped; only the percentage caps at 100. Beyond the project tolerance the DECIDING supervisor must
acknowledge with a note, which is kept on the decision and on the ledger row.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import psycopg.errors as pge

from .. import audit
from ..db import tx
from ..errors import ApiError
from .claims import _claim_row, load_units, measured_assignments
from .common import actor_tx, DECIDABLE_STATUSES, PM, SE, SUP, ProjectActor, active_version, lock_keys, notify, require_role, require_writable
from .progress_math import D, UnitMismatch, apply_percentage, capped_pct, convert, overrun, q3, weighted_pct

METHODS = ("QUANTITIES_AS_CLAIMED", "MANUAL_QUANTITIES", "APPLY_PCT_TO_ASSIGNMENTS", "PCT_ONLY_ACTIVITY")
ACTIONS = ("APPROVE", "EDIT", "REJECT", "HOLD")


def _j(o):
    """JSON-safe copy (Decimal / UUID / date -> str) for the jsonb columns"""
    if isinstance(o, dict):
        return {str(k): _j(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_j(v) for v in o]
    if isinstance(o, (Decimal, uuid.UUID, date, datetime)):
        return str(o)
    return o


@dataclass
class Entry:
    assignment_uid: uuid.UUID
    resource_code: str
    unit: str
    baseline_qty: Decimal
    weight: Decimal
    head: Decimal
    approved: Decimal
    source: str                                   # CLAIM | MANUAL | PCT
    claim_quantity_id: Optional[uuid.UUID] = None
    overrun_pct: Decimal = Decimal(0)
    over_baseline: bool = False
    beyond_tolerance: bool = False

    @property
    def incremental(self) -> Decimal:
        return self.approved - self.head


@dataclass
class Plan:
    action: str
    method: str
    activity_uid: uuid.UUID
    entries: List[Entry]
    activity_row: Optional[dict]                   # values for approved_activity_progress, or None
    pct_before: Decimal
    pct_after: Decimal
    start_inferred: bool
    short_close_ack: bool
    tolerance_pct: Decimal
    threshold_pct: Decimal
    version_id: uuid.UUID
    ack_required: bool = False
    overruns: List[dict] = field(default_factory=list)
    all_assignments: List[dict] = field(default_factory=list)


# ------------------------------------------------------------------------------------------------ planning (no writes)
def _heads(conn, assigns, activity_uid):
    heads = {}
    for a in assigns:
        h = conn.execute("select cumulative_qty, as_of_date from approved_resource_progress where assignment_uid = %s order by entry_seq desc limit 1", (a["assignment_uid"],)).fetchone()
        heads[a["assignment_uid"]] = (h["cumulative_qty"], h["as_of_date"]) if h else (Decimal(0), None)
    act = conn.execute("select actual_start, actual_finish, reported_pct, as_of_date from approved_activity_progress where activity_uid = %s order by entry_seq desc limit 1", (activity_uid,)).fetchone()
    return heads, act


def _plan(conn, actor: ProjectActor, claim: dict, p: Dict[str, Any]) -> Plan:
    ver = active_version(conn, actor.project_id)
    uid = claim["matched_activity_uid"]
    if p.get("activity_uid") is not None and p["activity_uid"] != uid:
        raise ApiError(422, "USE_REMATCH_FIRST", "To approve against a different activity, re-match the claim first (the claim's quantities are re-derived on re-match)")
    if uid is None:
        raise ApiError(422, "NO_ACTIVITY_MATCHED", "The claim is not matched to an activity")
    act = conn.execute("select activity_uid, activity_type, external_activity_id from baseline_activities where version_id = %s and activity_uid = %s", (ver["version_id"], uid)).fetchone()
    if act is None:
        raise ApiError(409, "ACTIVITY_NOT_IN_ACTIVE_SCHEDULE", "The claim's activity is not part of the active schedule version: re-match or reject the claim")
    if p.get("action", "APPROVE") in ("APPROVE", "EDIT"):
        from . import quality
        quality.assert_releasable(conn, actor.project_id, uid)                  # mandatory quality gates must be satisfied before progress is approved (D8)
    settings = conn.execute("select over_baseline_tolerance_pct, completion_threshold_pct from project_settings where project_id = %s", (actor.project_id,)).fetchone()
    tol, thr = settings["over_baseline_tolerance_pct"], settings["completion_threshold_pct"]
    assigns = measured_assignments(conn, ver["version_id"], uid)
    by_uid = {a["assignment_uid"]: a for a in assigns}
    heads, head_act = _heads(conn, assigns, uid)
    units = load_units(conn)
    quantities = conn.execute("select * from claim_quantities where event_id = %s order by claim_quantity_id", (claim["event_id"],)).fetchall()
    bound = [q for q in quantities if q["assignment_uid"] is not None]
    unbound = [q for q in quantities if q["assignment_uid"] is None]

    method, approved_q, pct_param = p.get("method"), p.get("approved_quantities") or {}, p.get("apply_pct")
    pct_param = D(pct_param) if pct_param is not None else None
    claim_pct = claim["claimed_pct"]
    has_dates_only = not quantities and claim_pct is None and (claim["claimed_start"] or claim["claimed_finish"] or p.get("actual_start") or p.get("actual_finish"))

    if method is None:                                                           # infer ONLY when there is exactly one honest reading
        if approved_q:
            method = "MANUAL_QUANTITIES"
        elif not assigns:
            method = "PCT_ONLY_ACTIVITY"
        elif unbound:
            raise ApiError(422, "QUANTITY_UNBOUND", "Some reported quantities are not bound to an assignment: bind them, or enter approved quantities manually",
                           {"unbound": [{"claim_quantity_id": str(q["claim_quantity_id"]), "qty": str(q["reported_qty"]), "uom": q["reported_uom"]} for q in unbound]})
        elif bound or has_dates_only:
            method = "QUANTITIES_AS_CLAIMED"
        elif claim_pct is not None:
            raise ApiError(422, "PERCENT_METHOD_REQUIRED", "This is a percentage-only claim on a quantity-based activity. Choose how to apply it: "
                           "APPLY_PCT_TO_ASSIGNMENTS (the same % on each measured assignment) or MANUAL_QUANTITIES (enter approved quantities)",
                           {"claimed_pct": str(claim_pct), "choices": ["APPLY_PCT_TO_ASSIGNMENTS", "MANUAL_QUANTITIES"], "assignments": [
                               {"assignment_uid": str(a["assignment_uid"]), "resource": a["resource_code"], "unit": a["unit_of_measure"], "baseline_qty": str(a["baseline_qty"])} for a in assigns]})
        else:
            raise ApiError(422, "NOTHING_TO_APPROVE", "The claim reports nothing that can be approved")
    if method not in METHODS:
        raise ApiError(422, "BAD_METHOD", f"method must be one of {', '.join(METHODS)}")
    if method == "PCT_ONLY_ACTIVITY" and assigns:
        raise ApiError(422, "ACTIVITY_IS_QUANTITY_BASED", "This activity has measured quantities; use QUANTITIES_AS_CLAIMED, MANUAL_QUANTITIES or APPLY_PCT_TO_ASSIGNMENTS")
    if method != "PCT_ONLY_ACTIVITY" and not assigns and not has_dates_only:
        raise ApiError(422, "NO_MEASURED_ASSIGNMENTS", "This activity has no progress-measuring quantity; use PCT_ONLY_ACTIVITY")

    entries: List[Entry] = []

    def entry(a, cum, source, cq=None):
        head = heads[a["assignment_uid"]][0]
        cum = q3(cum)
        if cum < 0:
            raise ApiError(422, "NEGATIVE_QUANTITY", f"{a['resource_code']}: a cumulative quantity cannot be negative")
        if cum < head:
            raise ApiError(409, "WOULD_DECREASE", f"{a['resource_code']}: approving {cum} would lower the approved cumulative {head}; a reduction needs a reopen",
                           {"resource": a["resource_code"], "head": str(head), "requested": str(cum)})
        if cum == head:
            return
        head_date = heads[a["assignment_uid"]][1]
        if head_date is not None and claim["event_date"] < head_date:
            raise ApiError(409, "STALE_CLAIM", f"{a['resource_code']}: the claim is dated {claim['event_date']}, before the latest approved entry ({head_date})")
        ov_pct, over, beyond = overrun(cum, a["baseline_qty"], tol)
        entries.append(Entry(a["assignment_uid"], a["resource_code"], a["unit_of_measure"], a["baseline_qty"], a["progress_weight"], head, cum, source, cq, ov_pct, over, beyond))

    if method == "QUANTITIES_AS_CLAIMED":
        if unbound:
            raise ApiError(422, "QUANTITY_UNBOUND", "Some reported quantities are not bound to an assignment", {"unbound": [str(q["claim_quantity_id"]) for q in unbound]})
        if not bound and not has_dates_only:
            raise ApiError(422, "NOTHING_TO_APPROVE", "The claim has no bound quantity to approve")
        for q in bound:
            a = by_uid.get(q["assignment_uid"])
            if a is None:
                raise ApiError(409, "ASSIGNMENT_NOT_IN_ACTIVE_SCHEDULE", "A bound quantity refers to an assignment that is not in the active schedule")
            try:
                norm = q["normalized_qty"] if q["normalized_qty"] is not None else convert(q["reported_qty"], units[q["reported_uom"].upper()], units[a["unit_of_measure"]])
            except (UnitMismatch, KeyError) as e:
                raise ApiError(422, "UNIT_DIMENSION_MISMATCH", str(e)) from e
            entry(a, heads[a["assignment_uid"]][0] + norm if q["qty_basis"] == "INCREMENTAL" else norm, "CLAIM", q["claim_quantity_id"])
    elif method == "MANUAL_QUANTITIES":
        if not approved_q:
            raise ApiError(422, "APPROVED_QUANTITIES_REQUIRED", "MANUAL_QUANTITIES needs approved_quantities: {assignment_uid: {cumulative|increment: value}}")
        for key, spec in approved_q.items():
            a = by_uid.get(uuid.UUID(str(key)))
            if a is None:
                raise ApiError(422, "ASSIGNMENT_NOT_MEASURED", "approved_quantities names an assignment that is not a progress-measuring assignment of this activity")
            if not isinstance(spec, dict) or len(spec) != 1 or next(iter(spec)) not in ("cumulative", "increment"):
                raise ApiError(422, "BAD_APPROVED_QUANTITY", "Each approved quantity is exactly one of {'cumulative': x} or {'increment': x}, in the assignment's unit")
            kind, val = next(iter(spec.items()))
            cq = next((q["claim_quantity_id"] for q in bound if q["assignment_uid"] == a["assignment_uid"]), None)
            entry(a, D(val) if kind == "cumulative" else heads[a["assignment_uid"]][0] + D(val), "MANUAL", cq)
    elif method == "APPLY_PCT_TO_ASSIGNMENTS":
        pct = pct_param if pct_param is not None else claim_pct
        if pct is None:
            raise ApiError(422, "PERCENT_REQUIRED", "APPLY_PCT_TO_ASSIGNMENTS needs a percentage (apply_pct, or the claim's own)")
        if not (0 <= pct <= 100):
            raise ApiError(422, "BAD_PERCENT", "The percentage must be between 0 and 100")
        for a in assigns:
            entry(a, apply_percentage(a["baseline_qty"], pct), "PCT")

    # ---- resulting activity percentage
    cum_after = {a["assignment_uid"]: heads[a["assignment_uid"]][0] for a in assigns}
    for e in entries:
        cum_after[e.assignment_uid] = e.approved
    pct_before = weighted_pct((a["progress_weight"], heads[a["assignment_uid"]][0], a["baseline_qty"]) for a in assigns) if assigns else D(head_act["reported_pct"] if head_act and head_act["reported_pct"] is not None else 0)
    pct_only_new = None
    if method == "PCT_ONLY_ACTIVITY":
        if act["activity_type"] == "MILESTONE":
            pct_only_new = Decimal(100) if (p.get("actual_finish") or claim["claimed_finish"]) else pct_before
        else:
            pct_only_new = pct_param if pct_param is not None else claim_pct
            if pct_only_new is None:
                raise ApiError(422, "PERCENT_REQUIRED", "PCT_ONLY_ACTIVITY needs a percentage (apply_pct, or the claim's own)")
            pct_only_new = q3(pct_only_new)
            if not (0 <= pct_only_new <= 100):
                raise ApiError(422, "BAD_PERCENT", "The percentage must be between 0 and 100")
            if pct_only_new < pct_before:
                raise ApiError(409, "WOULD_DECREASE", f"Approving {pct_only_new}% would lower the approved {pct_before}%; a reduction needs a reopen")
    pct_after = pct_only_new if pct_only_new is not None else (weighted_pct((a["progress_weight"], cum_after[a["assignment_uid"]], a["baseline_qty"]) for a in assigns) if assigns else pct_before)

    # ---- activity dates
    h_start = head_act["actual_start"] if head_act else None
    h_finish = head_act["actual_finish"] if head_act else None
    start_req = p.get("actual_start") or claim["claimed_start"]
    finish_req = p.get("actual_finish") or claim["claimed_finish"]
    if h_start and start_req and start_req != h_start:
        raise ApiError(409, "START_ALREADY_APPROVED", f"The actual start was already approved as {h_start}; changing it needs a reopen")
    if h_finish and finish_req and finish_req != h_finish:
        raise ApiError(409, "FINISH_ALREADY_APPROVED", f"The actual finish was already approved as {h_finish}; changing it needs a reopen")
    progress_made = bool(entries) or (pct_only_new is not None and pct_only_new > pct_before) or bool(finish_req)
    start_eff, inferred = h_start or start_req, False
    if start_eff is None and (progress_made or pct_after > 0):
        start_eff, inferred = claim["event_date"], True                                # recorded on the decision, never silent
    finish_eff = h_finish or finish_req
    short_close = False
    if finish_eff and not h_finish:
        if start_eff and finish_eff < start_eff:
            raise ApiError(422, "FINISH_BEFORE_START", "The actual finish is before the actual start")
        if act["activity_type"] != "MILESTONE" and pct_after < thr:
            if len((p.get("short_close_note") or "").strip()) < 3:
                raise ApiError(409, "FINISH_BELOW_THRESHOLD", f"Finishing at {pct_after}% is below the project's completion threshold ({thr}%): add a short-close note to proceed",
                               {"percent": str(pct_after), "threshold": str(thr)})
            short_close = True
    act_row = None
    new_start = start_eff is not None and h_start is None
    new_finish = finish_eff is not None and h_finish is None
    pct_changed = pct_only_new is not None and pct_only_new != pct_before
    if new_start or new_finish or pct_changed:
        act_row = {"actual_start": start_eff if new_start else None, "actual_finish": finish_eff if new_finish else None,
                   "reported_pct": pct_only_new if method == "PCT_ONLY_ACTIVITY" and act["activity_type"] != "MILESTONE" else None}
    if not entries and act_row is None:
        raise ApiError(422, "NO_PROGRESS_CHANGE", "Approving this claim would not change any approved progress")

    # ---- APPROVE vs EDIT must say what it is
    edited = (method == "MANUAL_QUANTITIES" or (method == "APPLY_PCT_TO_ASSIGNMENTS" and pct_param is not None and pct_param != claim_pct)
              or (method == "PCT_ONLY_ACTIVITY" and pct_param is not None and pct_param != claim_pct)
              or (p.get("actual_start") and p["actual_start"] != claim["claimed_start"]) or (p.get("actual_finish") and p["actual_finish"] != claim["claimed_finish"]))
    derived = "EDIT" if edited else "APPROVE"
    if p["action"] != derived:
        raise ApiError(422, "ACTION_MISMATCH", f"With these values the decision is {derived}, not {p['action']}" + (" (the supervisor changed what was reported: an EDIT needs a justification)" if derived == "EDIT" else ""),
                       {"expected_action": derived})

    ov = [{"assignment_uid": str(e.assignment_uid), "resource": e.resource_code, "unit": e.unit, "baseline_qty": str(e.baseline_qty), "approved_cumulative": str(e.approved),
           "overrun_pct": str(e.overrun_pct), "beyond_tolerance": e.beyond_tolerance} for e in entries if e.over_baseline]
    return Plan(derived, method, uid, entries, act_row, pct_before, pct_after, inferred, short_close, tol, thr, ver["version_id"],
                ack_required=any(e.beyond_tolerance for e in entries), overruns=ov, all_assignments=assigns)


def _plan_summary(pl: Plan) -> Dict[str, Any]:
    return {"action": pl.action, "method": pl.method, "activity_uid": str(pl.activity_uid),
            "applied": [{"assignment_uid": str(e.assignment_uid), "resource": e.resource_code, "unit": e.unit, "baseline_qty": str(e.baseline_qty), "head_cumulative": str(e.head),
                         "approved_cumulative": str(e.approved), "incremental": str(e.incremental), "source": e.source,
                         "claim_quantity_id": str(e.claim_quantity_id) if e.claim_quantity_id else None, "overrun_pct": str(e.overrun_pct), "beyond_tolerance": e.beyond_tolerance} for e in pl.entries],
            "result": {"activity_pct_before": str(pl.pct_before), "activity_pct_after": str(pl.pct_after), "start_inferred": pl.start_inferred,
                       "activity_row": _j(pl.activity_row), "short_close_acknowledged": pl.short_close_ack, "overruns": pl.overruns,
                       "tolerance_pct": str(pl.tolerance_pct), "completion_threshold_pct": str(pl.threshold_pct)},
            "requires_overrun_acknowledgement": pl.ack_required}


def preview_decision(actor: ProjectActor, claim_id, **params) -> Dict[str, Any]:
    """what an APPROVE / EDIT would do right now. No writes, no locks; refusals come back as a structured error, not an exception."""
    require_role(actor, SUP, what="previewing a decision")
    params.setdefault("action", "APPROVE")
    with actor_tx(actor, readonly=True) as c:
        claim = _claim_row(c, actor.project_id, claim_id)
        if claim["status"] not in DECIDABLE_STATUSES:
            return {"ok": False, "error": {"code": "CLAIM_NOT_DECIDABLE", "message": f"The claim is {claim['status']}"}}
        try:
            return {"ok": True, **_plan_summary(_plan(c, actor, claim, params))}
        except ApiError as e:
            return {"ok": False, "error": {"code": e.code, "message": e.message, "details": e.details}}


# ------------------------------------------------------------------------------------------------ the decision
def decide(actor: ProjectActor, claim_id, *, action: str, justification: Optional[str] = None, method: Optional[str] = None, activity_uid=None,
           approved_quantities: Optional[dict] = None, apply_pct=None, actual_start: Optional[date] = None, actual_finish: Optional[date] = None,
           overrun_ack_note: Optional[str] = None, short_close_note: Optional[str] = None, clarification_question: Optional[str] = None) -> Dict[str, Any]:
    require_role(actor, SUP, what="deciding on claims")
    require_writable(actor)
    if action not in ACTIONS:
        raise ApiError(422, "BAD_ACTION", f"action must be one of {', '.join(ACTIONS)}")
    just = (justification or "").strip()
    with actor_tx(actor, write=True) as c:
        claim = _claim_row(c, actor.project_id, claim_id, lock=True)                   # a second decider waits here, then sees the final status
        if claim["status"] not in DECIDABLE_STATUSES:
            raise ApiError(409, "CLAIM_NOT_DECIDABLE", f"The claim is {claim['status']} and cannot be decided", {"status": claim["status"]})
        decision_id = uuid.uuid4()
        if action in ("REJECT", "HOLD"):
            text = just if action == "REJECT" else (clarification_question or "").strip()
            if len(text) < 3:
                raise ApiError(422, "REASON_REQUIRED", "A rejection needs a reason" if action == "REJECT" else "A hold needs the question for the engineer")
            c.execute("insert into planner_decisions (decision_id, project_id, event_id, selected_activity_uid, action, method, justification, decided_by, applied, result) "
                      "values (%s,%s,%s,%s,%s,'NONE',%s,%s,'[]'::jsonb,'{}'::jsonb)", (decision_id, actor.project_id, claim_id, claim["matched_activity_uid"], action, just or text, actor.user_id))
            if action == "HOLD":
                c.execute("update execution_events set clarification_status = 'ASKED', clarification_question = %s, clarification_answer = null where project_id = %s and event_id = %s",
                          (text, actor.project_id, claim_id))
            notify(c, project_id=actor.project_id, recipient_id=claim["filed_by"], ntype="CLAIM_DECISION", decision_id=decision_id, event_id=claim_id, created_by=actor.user_id,
                   title="Claim rejected" if action == "REJECT" else "Question about your claim", body=text[:400])
            audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=SUP, action="CLAIM_REJECTED" if action == "REJECT" else "CLAIM_HELD", entity_type="PLANNER_DECISION",
                      entity_id=decision_id, before={"claim_status": claim["status"]}, after={"claim_id": str(claim_id), "reason": text})
            return {"decision_id": decision_id, "claim_id": claim_id, "action": action, "status": "REJECTED" if action == "REJECT" else "DISPUTED", "method": "NONE", "applied": [], "result": {}}

        # ---- APPROVE / EDIT
        params = dict(action=action, method=method, activity_uid=activity_uid, approved_quantities=approved_quantities, apply_pct=apply_pct,
                      actual_start=actual_start, actual_finish=actual_finish, short_close_note=short_close_note)
        # lock every measured assignment of the activity (and its activity chain) BEFORE planning, in a fixed order: concurrent decisions on the
        # same activity queue here, and each then plans from the ledger heads the previous one committed
        ver = active_version(c, actor.project_id)
        if claim["matched_activity_uid"] is not None:
            lock_keys(c, [f"arp:{x['assignment_uid']}" for x in measured_assignments(c, ver["version_id"], claim["matched_activity_uid"])] + [f"aap:{claim['matched_activity_uid']}"])
        pl = _plan(c, actor, claim, params)
        if action == "EDIT" and len(just) < 3:
            raise ApiError(422, "JUSTIFICATION_REQUIRED", "An edit changes what the engineer reported: say why")
        if pl.ack_required and len((overrun_ack_note or "").strip()) < 3:
            raise ApiError(409, "OVERRUN_ACK_REQUIRED", f"Approved quantity exceeds the baseline by more than the {pl.tolerance_pct}% tolerance: the deciding supervisor must acknowledge it with a note",
                           {"tolerance_pct": str(pl.tolerance_pct), "overruns": [o for o in pl.overruns if o["beyond_tolerance"]]})
        summ = _plan_summary(pl)
        c.execute("insert into planner_decisions (decision_id, project_id, event_id, selected_activity_uid, action, method, approved_pct, justification, decided_by, overrun_ack, overrun_ack_note, applied, result) "
                  "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)",
                  (decision_id, actor.project_id, claim_id, pl.activity_uid, pl.action, pl.method, pl.pct_after, just or "Approved as reported", actor.user_id,
                   pl.ack_required, (overrun_ack_note or "").strip() if pl.ack_required else None, json.dumps(summ["applied"]), json.dumps(summ["result"])))
        entry_ids = []
        for e in pl.entries:
            r = c.execute("insert into approved_resource_progress (project_id, activity_uid, assignment_uid, decision_id, as_of_date, cumulative_qty, claim_quantity_id, overrun_ack_by, overrun_ack_note) "
                          "values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning entry_id, incremental_qty, prev_cumulative_qty",
                          (actor.project_id, pl.activity_uid, e.assignment_uid, decision_id, claim["event_date"], e.approved, e.claim_quantity_id,
                           actor.user_id if e.beyond_tolerance else None, (overrun_ack_note or "").strip() if e.beyond_tolerance else None)).fetchone()
            if r["incremental_qty"] != e.incremental or r["prev_cumulative_qty"] != e.head:
                raise RuntimeError(f"ledger arithmetic disagrees with the plan for {e.resource_code}")                 # internal consistency: roll everything back
            entry_ids.append(("RESOURCE", r["entry_id"]))
        if pl.activity_row is not None:
            r = c.execute("insert into approved_activity_progress (project_id, activity_uid, decision_id, as_of_date, actual_start, actual_finish, reported_pct) values (%s,%s,%s,%s,%s,%s,%s) returning entry_id",
                          (actor.project_id, pl.activity_uid, decision_id, claim["event_date"], pl.activity_row["actual_start"], pl.activity_row["actual_finish"], pl.activity_row["reported_pct"])).fetchone()
            entry_ids.append(("ACTIVITY", r["entry_id"]))
        sql_pct = c.execute("select physical_pct from activity_progress_as_of(%s, current_date) where activity_uid = %s", (pl.version_id, pl.activity_uid)).fetchone()
        if sql_pct is not None and sql_pct["physical_pct"] != pl.pct_after:
            raise RuntimeError(f"recorded result {pl.pct_after}% disagrees with the ledger-derived {sql_pct['physical_pct']}%")
        verb = "approved" if pl.action == "APPROVE" else "approved with changes"
        notify(c, project_id=actor.project_id, recipient_id=claim["filed_by"], ntype="CLAIM_DECISION", decision_id=decision_id, event_id=claim_id, created_by=actor.user_id,
               title=f"Claim {verb}", body=f"Activity now {pl.pct_after}% (method {pl.method})" + (f". {just}" if just else ""))
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=SUP, action="CLAIM_APPROVED" if pl.action == "APPROVE" else "CLAIM_EDITED", entity_type="PLANNER_DECISION",
                  entity_id=decision_id, version_id=pl.version_id, before={"claim_status": claim["status"], "activity_pct": str(pl.pct_before)},
                  after={"claim_id": str(claim_id), "method": pl.method, "activity_pct": str(pl.pct_after), "applied": summ["applied"], "overrun_ack": pl.ack_required})
        for kind, eid in entry_ids:
            audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=SUP, action="PROGRESS_RECORDED", entity_type="PROGRESS_ENTRY", entity_id=eid,
                      version_id=pl.version_id, after={"kind": kind, "decision_id": str(decision_id)})
    return {"decision_id": decision_id, "claim_id": claim_id, "action": pl.action, "status": "APPROVED", "method": pl.method, "applied": summ["applied"], "result": summ["result"],
            "ledger_entries": [str(e) for _, e in entry_ids]}
