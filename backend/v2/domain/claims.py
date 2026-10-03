"""Claim lifecycle for the Site Engineer, plus the role-filtered reads (own claims / review queue / aggregate counts).

A claim is a PROPOSAL: nothing here can create progress. Only decisions.decide() does, through the ledgers.
* submit_claim      Site Engineer. Stores exactly what was reported (quantities as reported; percentage as reported), binds quantities to
                    assignments only when the unit kind and (if needed) the resource hint make it unambiguous, records validations.
* withdraw_claim    the filing engineer only, only while pending; the claim, its quantities and evidence are retained.
* answer_clarification / attach_evidence   the filing engineer, while pending.
* resubmit          a correction after rejection is a NEW claim linked with resubmits_event_id (never an edit).
* rematch_claim / bind_quantities   Supervisor, before a final decision.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

import psycopg.errors as pge

from .. import audit
from ..db import tx
from ..errors import ApiError
from ..schedule_import.mapping import RefData, resolve_uom
from .common import (actor_tx, DECIDABLE_STATUSES, FINAL_STATUSES, PENDING_STATUSES, PM, SE, SUP, ProjectActor, active_version, notify, require_role,
                     require_writable, supervisors_of)
from .progress_math import D, Unit, UnitMismatch, convert, q3

CHANNELS = ("TYPED", "VOICE_TRANSCRIPT", "TXT", "CSV", "XLSX", "PDF", "SCANNED", "IMAGE", "API")
BASES = ("CUMULATIVE", "INCREMENTAL")


# ------------------------------------------------------------------------------------------------ helpers
def load_units(conn) -> Dict[str, Unit]:
    return {r["code"]: Unit(r["code"], r["dimension"], r["to_base_factor"]) for r in conn.execute("select code, dimension, to_base_factor from units_of_measure").fetchall()}


def measured_assignments(conn, version_id, activity_uid) -> List[dict]:
    return conn.execute(
        "select br.assignment_uid, br.activity_uid, br.baseline_qty, br.unit_of_measure, br.progress_weight, pr.resource_code, pr.resource_name "
        "from baseline_resources br join project_resources pr on pr.resource_id = br.resource_id and pr.project_id = br.project_id "
        "where br.version_id = %s and br.activity_uid = %s and br.measures_progress order by pr.resource_code", (version_id, activity_uid)).fetchall()


def _claim_row(conn, project_id, claim_id, lock=False) -> dict:
    r = conn.execute("select * from execution_events where project_id = %s and event_id = %s" + (" for update" if lock else ""), (project_id, claim_id)).fetchone()
    if r is None:
        raise ApiError(404, "CLAIM_NOT_FOUND", "No such claim")
    return r


def _own_or_404(actor: ProjectActor, claim: dict) -> None:
    """an engineer only ever learns about their own claims: someone else's looks exactly like a missing one"""
    if actor.role == SE and claim["filed_by"] != actor.user_id:
        raise ApiError(404, "CLAIM_NOT_FOUND", "No such claim")


def _fingerprint(project_id, activity, ref, event_date, quantities, pct, start, finish, text) -> str:
    key = json.dumps([str(project_id), str(activity or ""), (ref or "").lower().strip(), str(event_date),
                      sorted([str(D(q["qty"])), q["uom"].lower().strip(), q["basis"], (q.get("resource_hint") or "").lower().strip()] for q in quantities),
                      str(pct), str(start), str(finish), " ".join((text or "").lower().split())])
    return hashlib.sha256(key.encode()).hexdigest()


def _bind(units: Dict[str, Unit], ref: RefData, assigns: List[dict], quantities: List[dict]):
    """-> list of (quantity dict + assignment/normalised fields) and validation findings. Never converts across unit kinds, never guesses."""
    out, findings, used = [], [], set()
    for q in quantities:
        code = resolve_uom(q["uom"], None, ref)
        row = dict(q, uom_code=code, assignment=None, normalized_qty=None, normalized_uom=None)
        out.append(row)
        if code is None:
            findings.append(("UNKNOWN_UNIT", "ERROR", f"Unit {q['uom']!r} is not a controlled unit; the quantity is kept as reported but cannot be bound"))
            continue
        same_kind = [a for a in assigns if units[a["unit_of_measure"]].dimension == units[code].dimension]
        hint = (q.get("resource_hint") or "").lower().strip()
        cands = [a for a in same_kind if hint and (hint in a["resource_code"].lower() or hint in (a["resource_name"] or "").lower())] or same_kind
        if not assigns:
            findings.append(("NO_MEASURED_ASSIGNMENT", "WARNING", "The activity has no progress-measuring quantity; the figure is kept as reported only"))
        elif not same_kind:
            findings.append(("UNIT_DIMENSION_MISMATCH", "ERROR", f"{q['uom']} is a different kind of unit from every measured quantity of the activity "
                                                                  f"({', '.join(sorted({a['unit_of_measure'] for a in assigns}))}); units are never converted across kinds"))
        elif len(cands) != 1:
            findings.append(("AMBIGUOUS_QUANTITY", "WARNING", f"{q['qty']} {q['uom']} could belong to {len(cands)} assignments "
                                                              f"({', '.join(a['resource_code'] for a in cands)}); the supervisor must bind it"))
        elif cands[0]["assignment_uid"] in used:
            findings.append(("DUPLICATE_QUANTITY", "WARNING", f"The claim gives two figures for {cands[0]['resource_code']}; the second is kept unbound"))
        else:
            a = cands[0]
            used.add(a["assignment_uid"])
            row["assignment"] = a
            row["normalized_uom"] = a["unit_of_measure"]
            row["normalized_qty"] = convert(q["qty"], units[code], units[a["unit_of_measure"]])
            if q["basis"] == "CUMULATIVE" and row["normalized_qty"] > a["baseline_qty"]:
                findings.append(("REPORTED_ABOVE_BASELINE", "WARNING", f"Reported cumulative {row['normalized_qty']} {a['unit_of_measure']} exceeds the baseline "
                                                                        f"{a['baseline_qty']} for {a['resource_code']}"))
    return out, findings


# ------------------------------------------------------------------------------------------------ submit
def submit_claim(actor: ProjectActor, *, event_date: date, raw_text: str, input_channel: str = "TYPED", activity_uid=None, reported_activity_ref: Optional[str] = None,
                 quantities: Optional[List[dict]] = None, claimed_pct=None, claimed_start: Optional[date] = None, claimed_finish: Optional[date] = None,
                 location: Optional[str] = None, evidence_document_ids: Optional[List[uuid.UUID]] = None, document_id=None, batch_id=None,
                 resubmits_event_id=None, field_provenance: Optional[dict] = None, asset_tag: Optional[str] = None,
                 incomplete_ok: bool = False, clarification_question: Optional[str] = None, language_detected: Optional[str] = None, delay_reason: Optional[str] = None,
                 discipline_code: Optional[str] = None, event_type: Optional[str] = None) -> Dict[str, Any]:
    """File a claim. With `incomplete_ok` and a question, a report that lacks required details (the legacy Field Copilot case: event type, discipline or progress) is kept
    as REPORTED with that question for the engineer (clarification ASKED); it is not matched, not reviewable and not announced to Supervisors until the answer completes it."""
    require_role(actor, SE, what="submitting a claim")
    require_writable(actor)
    quantities = [dict(q) for q in (quantities or [])]
    text = (raw_text or "").strip()
    if len(text) < 3:
        raise ApiError(422, "CLAIM_TEXT_REQUIRED", "A claim needs the report text it came from")
    if input_channel not in CHANNELS:
        raise ApiError(422, "BAD_CHANNEL", f"input_channel must be one of {', '.join(CHANNELS)}")
    if event_date > date.today():
        raise ApiError(422, "FUTURE_DATE", "A claim cannot be dated in the future")
    for q in quantities:
        if q.get("basis") not in BASES or q.get("qty") is None or D(q["qty"]) < 0 or not str(q.get("uom") or "").strip():
            raise ApiError(422, "BAD_QUANTITY", "Each quantity needs qty >= 0, a unit, and basis CUMULATIVE or INCREMENTAL")
        q["qty"] = D(q["qty"])
    if claimed_pct is not None and not (0 <= D(claimed_pct) <= 100):
        raise ApiError(422, "BAD_PERCENT", "claimed_pct must be between 0 and 100")
    if claimed_start and claimed_finish and claimed_finish < claimed_start:
        raise ApiError(422, "BAD_DATES", "claimed_finish is before claimed_start")
    has_content = bool(quantities or claimed_pct is not None or claimed_start or claimed_finish)
    if not has_content and not (incomplete_ok and clarification_question):
        raise ApiError(422, "EMPTY_CLAIM", "A claim must report a quantity, a percentage, or an actual start / finish date")

    with actor_tx(actor, write=True) as c:
        ver = active_version(c, actor.project_id)
        units, ref = load_units(c), RefData(set(), {}, {k: v.dimension for k, v in load_units(c).items()})
        assigns, act_row = [], None
        if activity_uid is not None:
            act_row = c.execute("select activity_uid, external_activity_id, activity_name, total_float, is_critical from baseline_activities where version_id = %s and activity_uid = %s",
                                (ver["version_id"], activity_uid)).fetchone()
            if act_row is None:
                raise ApiError(409, "ACTIVITY_NOT_IN_ACTIVE_SCHEDULE", "That activity is not part of the project's active schedule version")
            assigns = measured_assignments(c, ver["version_id"], activity_uid)
        bound, findings = _bind(units, ref, assigns, quantities)
        if act_row and assigns and not quantities and claimed_pct is not None:
            findings.append(("PERCENT_ONLY_NEEDS_METHOD", "INFO", "Percentage-only claim on a quantity-based activity: the supervisor must choose how to apply it "
                                                                   "(apply to each measured assignment, or enter approved quantities)"))
        for d_id in (evidence_document_ids or []) + ([document_id] if document_id else []):
            if c.execute("select 1 from source_documents where project_id = %s and document_id = %s", (actor.project_id, d_id)).fetchone() is None:
                raise ApiError(422, "DOCUMENT_NOT_FOUND", "An evidence document does not belong to this project")
        if resubmits_event_id is not None:
            old = _claim_row(c, actor.project_id, resubmits_event_id)
            if old["status"] != "REJECTED" or old["filed_by"] != actor.user_id:
                raise ApiError(409, "NOT_A_REJECTED_CLAIM", "A correction must be linked to your own REJECTED claim")
        mode = "CUMULATIVE_PCT" if (claimed_pct is not None and not quantities) else ("INCREMENTAL_QTY" if any(q["basis"] == "INCREMENTAL" for q in quantities) else "CUMULATIVE_QTY")
        etype = "PROGRESS" if (quantities or claimed_pct is not None) else ("FINISH" if claimed_finish else "START")
        if event_type in ("PROGRESS", "START", "FINISH", "DELAY_NOTE"):
            etype = event_type
        pending_question = clarification_question if incomplete_ok else None               # a report missing required details waits for its engineer (legacy Field Copilot)
        errors = sum(1 for _, s, _ in findings if s == "ERROR")
        warns = sum(1 for _, s, _ in findings if s == "WARNING")
        priority = Decimal(10 * errors + 3 * warns) + (Decimal(5) if (act_row and act_row["is_critical"]) else Decimal(0))
        fp = _fingerprint(actor.project_id, activity_uid, reported_activity_ref, event_date, quantities, claimed_pct, claimed_start, claimed_finish, text)
        try:
            c.execute("savepoint ins")
            ev = c.execute(
                "insert into execution_events (project_id, filed_in_version_id, document_id, batch_id, event_date, raw_claim_text, input_channel, reported_activity_ref, "
                "matched_activity_uid, event_type, claim_mode, location, asset_tag, claimed_pct, claimed_start, claimed_finish, filed_by, status, field_provenance, priority_score, "
                "priority_reasons, claim_fingerprint, resubmits_event_id, language_detected, discipline_code, delay_reason, clarification_status, clarification_question) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning event_id, status",
                (actor.project_id, ver["version_id"], document_id, batch_id, event_date, text, input_channel, reported_activity_ref, activity_uid, etype, mode, location,
                 (asset_tag or "").strip() or None, claimed_pct, claimed_start, claimed_finish, actor.user_id,
                 "REPORTED" if pending_question else ("MATCHED" if activity_uid else "EXTRACTED"),
                 json.dumps(field_provenance or {"activity": "ENGINEER" if activity_uid else None}), priority,
                 "; ".join(f"{k}" for k, _, _ in findings) or None, fp, resubmits_event_id, language_detected, discipline_code, delay_reason,
                 "ASKED" if pending_question else "NONE", pending_question)).fetchone()
        except pge.UniqueViolation as e:
            c.execute("rollback to savepoint ins")
            if "uq_event_fingerprint" in str(e):
                dup = c.execute("select event_id, filed_by from execution_events where project_id = %s and claim_fingerprint = %s", (actor.project_id, fp)).fetchone()
                raise ApiError(409, "DUPLICATE_CLAIM", "An identical claim already exists",
                               {"claim_id": str(dup["event_id"])} if dup and dup["filed_by"] == actor.user_id else None) from e      # another engineer's claim id is not disclosed
            if "uq_event_one_correction" in str(e):
                raise ApiError(409, "ALREADY_CORRECTED", "That rejected claim already has a live correction") from e
            raise
        eid = ev["event_id"]
        qrows = []
        for b in bound:
            cq = c.execute("insert into claim_quantities (project_id, event_id, assignment_uid, reported_resource, qty_basis, reported_qty, reported_uom, normalized_uom, normalized_qty) "
                           "values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning claim_quantity_id",
                           (actor.project_id, eid, b["assignment"]["assignment_uid"] if b["assignment"] else None, b.get("resource_hint"), b["basis"], b["qty"], b["uom"],
                            b["normalized_uom"], b["normalized_qty"])).fetchone()
            qrows.append({"claim_quantity_id": cq["claim_quantity_id"], "reported_qty": b["qty"], "reported_uom": b["uom"], "basis": b["basis"],
                          "assignment_uid": b["assignment"]["assignment_uid"] if b["assignment"] else None,
                          "resource_code": b["assignment"]["resource_code"] if b["assignment"] else None, "normalized_qty": b["normalized_qty"], "normalized_uom": b["normalized_uom"]})
        for code, sev, msg in findings:
            c.execute("insert into claim_validations (project_id, event_id, rule_code, severity, description) values (%s,%s,%s,%s,%s)", (actor.project_id, eid, code, sev, msg))
        for d_id in dict.fromkeys((evidence_document_ids or [])):
            c.execute("insert into claim_evidence (project_id, event_id, document_id) values (%s,%s,%s)", (actor.project_id, eid, d_id))
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=SE, action="CLAIM_SUBMITTED", entity_type="CLAIM", entity_id=eid, version_id=ver["version_id"],
                  after={"status": ev["status"], "activity_uid": str(activity_uid) if activity_uid else None, "quantities": len(qrows), "claimed_pct": str(claimed_pct) if claimed_pct is not None else None,
                         "resubmits": str(resubmits_event_id) if resubmits_event_id else None})
        auto = None
        if activity_uid is None and not pending_question:                    # no explicit choice: the existing matching engine proposes (it never decides or approves)
            from ..matching import service as matching
            try:                                                             # a matching failure must never lose the engineer's report: it simply stays for a Supervisor to match
                c.execute("savepoint automatch")
                auto = matching.auto_match(c, actor, eid, ver)
            except Exception as e:                                           # noqa: BLE001
                c.execute("rollback to savepoint automatch")
                import logging
                logging.getLogger(__name__).exception("automatic matching failed for claim %s", eid)
                c.execute("insert into claim_validations (project_id, event_id, rule_code, severity, description) values (%s,%s,'AUTOMATIC_MATCH_UNAVAILABLE','INFO',%s)",
                          (actor.project_id, eid, f"Automatic matching could not run ({type(e).__name__}); a Supervisor will match this claim."))
                findings = list(findings) + [("AUTOMATIC_MATCH_UNAVAILABLE", "INFO", "Automatic matching could not run; a Supervisor will match this claim.")]
                auto = {"matched": False, "error": type(e).__name__}
            if auto.get("matched"):
                act_row = c.execute("select activity_uid, external_activity_id, activity_name, total_float, is_critical from baseline_activities where version_id = %s and activity_uid = %s",
                                    (ver["version_id"], auto["activity_uid"])).fetchone()
                activity_uid = auto["activity_uid"]
                ev = {**ev, "status": "MATCHED"}
                findings = [(f["rule"], f["severity"], f["message"]) for f in auto["findings"]]
                priority = c.execute("select priority_score from execution_events where event_id = %s", (eid,)).fetchone()["priority_score"]
                qrows = c.execute("select cq.claim_quantity_id, cq.reported_qty, cq.reported_uom, cq.qty_basis as basis, cq.assignment_uid, pr.resource_code, cq.normalized_qty, cq.normalized_uom "
                                  "from claim_quantities cq left join baseline_resources br on br.assignment_uid = cq.assignment_uid and br.version_id = %s "
                                  "left join project_resources pr on pr.resource_id = br.resource_id where cq.event_id = %s order by cq.claim_quantity_id", (ver["version_id"], eid)).fetchall()
        if activity_uid:                                                       # another pending claim for the same activity and day with a different headline figure
            for o in c.execute("select e.event_id, e.claimed_pct from execution_events e where e.project_id = %s and e.matched_activity_uid = %s and e.event_date = %s "
                               "and e.event_id <> %s and e.status in ('REPORTED','EXTRACTED','MATCHED','VALIDATED','DISPUTED') and e.claimed_pct is not null",
                               (actor.project_id, activity_uid, event_date, eid)).fetchall():
                if claimed_pct is not None and o["claimed_pct"] != D(claimed_pct):
                    c.execute("insert into conflict_records (project_id, activity_uid, reporting_period, event_id_a, event_id_b, value_a, value_b, variance_pct) values (%s,%s,%s,%s,%s,%s,%s,%s)",
                              (actor.project_id, activity_uid, event_date, o["event_id"], eid, o["claimed_pct"], D(claimed_pct), abs(D(claimed_pct) - o["claimed_pct"])))
        for sup in ([] if pending_question else supervisors_of(c, actor.project_id)):
            notify(c, project_id=actor.project_id, recipient_id=sup, ntype="CLAIM_SUBMITTED", event_id=eid, created_by=actor.user_id,
                   title="New claim to review" + (f": {act_row['external_activity_id']}" if act_row else ""), body=text[:300])
    return {"claim_id": eid, "status": ev["status"], "activity_uid": activity_uid, "quantities": qrows,
            "validations": [{"rule": k, "severity": s, "message": m} for k, s, m in findings], "priority_score": priority, "match": auto}


# ------------------------------------------------------------------------------------------------ engineer actions
def withdraw_claim(actor: ProjectActor, claim_id, reason: str) -> Dict[str, Any]:
    require_role(actor, SE, what="withdrawing a claim")
    require_writable(actor)
    if len((reason or "").strip()) < 3:
        raise ApiError(422, "REASON_REQUIRED", "Say why the claim is withdrawn")
    with actor_tx(actor, write=True) as c:
        claim = _claim_row(c, actor.project_id, claim_id, lock=True)
        _own_or_404(actor, claim)
        if claim["status"] in FINAL_STATUSES:
            raise ApiError(409, "CLAIM_ALREADY_FINAL", f"The claim is {claim['status']} and can no longer be withdrawn")
        c.execute("update execution_events set status = 'WITHDRAWN', withdrawn_at = now(), withdrawn_reason = %s where project_id = %s and event_id = %s",
                  (reason.strip(), actor.project_id, claim_id))
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=SE, action="CLAIM_WITHDRAWN", entity_type="CLAIM", entity_id=claim_id,
                  before={"status": claim["status"]}, after={"status": "WITHDRAWN", "reason": reason.strip()})
    return {"claim_id": claim_id, "status": "WITHDRAWN"}


def complete_reported_claim(actor: ProjectActor, claim_id, *, answer: str, quantities: Optional[List[dict]] = None, claimed_pct=None, claimed_start=None, claimed_finish=None,
                            location=None, asset_tag=None, discipline_code=None, delay_reason=None, event_type=None, reported_activity_ref=None, language_detected=None,
                            field_provenance: Optional[dict] = None) -> Dict[str, Any]:
    """The engineer answers the Field Copilot's question about their own incomplete (REPORTED) report. Fields found in the answer fill the gaps; the original report text
    and date stay as filed (claim provenance is immutable), and the answer is stored beside them. Once the report states progress it becomes a normal EXTRACTED claim:
    it is matched automatically (never approved) and announced to the Supervisors."""
    require_role(actor, SE, what="answering the clarification of a report")
    require_writable(actor)
    if len((answer or "").strip()) < 2:
        raise ApiError(422, "ANSWER_REQUIRED", "An answer is required")
    quantities = [dict(q) for q in (quantities or [])]
    for q in quantities:
        if q.get("basis") not in BASES or q.get("qty") is None or D(q["qty"]) < 0 or not str(q.get("uom") or "").strip():
            raise ApiError(422, "BAD_QUANTITY", "Each quantity needs qty >= 0, a unit, and basis CUMULATIVE or INCREMENTAL")
        q["qty"] = D(q["qty"])
    if claimed_pct is not None and not (0 <= D(claimed_pct) <= 100):
        raise ApiError(422, "BAD_PERCENT", "claimed_pct must be between 0 and 100")
    with actor_tx(actor, write=True) as c:
        claim = _claim_row(c, actor.project_id, claim_id, lock=True)
        _own_or_404(actor, claim)
        if claim["status"] != "REPORTED" or claim["clarification_status"] != "ASKED":
            raise ApiError(409, "NO_CLARIFICATION_PENDING", "This report is not waiting for a clarification from you")
        ver = active_version(c, actor.project_id)
        units, ref = load_units(c), RefData(set(), {}, {k: v.dimension for k, v in load_units(c).items()})
        bound, findings = _bind(units, ref, [], quantities)
        existing_q = c.execute("select count(*) n from claim_quantities where event_id = %s", (claim_id,)).fetchone()["n"]
        has_content = bool(quantities or claimed_pct is not None or claimed_start or claimed_finish or existing_q or claim["claimed_pct"] is not None
                           or claim["claimed_start"] or claim["claimed_finish"])
        if existing_q:                                                       # quantities already recorded with the report are never duplicated by the answer
            bound, quantities = [], []
        prov = dict(claim.get("field_provenance") or {})
        prov.update(field_provenance or {})
        mode = "CUMULATIVE_PCT" if (claimed_pct is not None and not quantities) else ("INCREMENTAL_QTY" if any(q["basis"] == "INCREMENTAL" for q in quantities) else "CUMULATIVE_QTY")
        etype = event_type if event_type in ("PROGRESS", "START", "FINISH", "DELAY_NOTE") else ("PROGRESS" if (quantities or claimed_pct is not None) else claim["event_type"])
        errors = sum(1 for _, s_, _ in findings if s_ == "ERROR")
        warns = sum(1 for _, s_, _ in findings if s_ == "WARNING")
        fp = _fingerprint(actor.project_id, None, reported_activity_ref or claim["reported_activity_ref"], claim["event_date"], quantities, claimed_pct, claimed_start, claimed_finish, claim["raw_claim_text"])
        try:
            c.execute("savepoint cmp")
            c.execute("update execution_events set status = %s, clarification_status = 'ANSWERED', clarification_answer = %s, event_type = %s, claim_mode = %s, "
                      "claimed_pct = coalesce(%s, claimed_pct), claimed_start = coalesce(%s, claimed_start), claimed_finish = coalesce(%s, claimed_finish), "
                      "location = coalesce(%s, location), asset_tag = coalesce(%s, asset_tag), discipline_code = coalesce(%s, discipline_code), delay_reason = coalesce(%s, delay_reason), "
                      "reported_activity_ref = coalesce(%s, reported_activity_ref), language_detected = coalesce(%s, language_detected), field_provenance = %s::jsonb, "
                      "priority_score = %s, priority_reasons = %s, claim_fingerprint = %s where project_id = %s and event_id = %s",
                      ("EXTRACTED" if has_content else "REPORTED", answer.strip(), etype, mode, claimed_pct, claimed_start, claimed_finish, location,
                       (asset_tag or "").strip() or None, discipline_code, delay_reason, reported_activity_ref, language_detected, json.dumps(prov),
                       Decimal(10 * errors + 3 * warns), "; ".join(k for k, _, _ in findings) or None, fp if has_content else claim["claim_fingerprint"], actor.project_id, claim_id))
        except pge.UniqueViolation as e:
            c.execute("rollback to savepoint cmp")
            raise ApiError(409, "DUPLICATE_CLAIM", "An identical claim already exists") from e
        qrows = []
        for b in bound:
            cq = c.execute("insert into claim_quantities (project_id, event_id, assignment_uid, reported_resource, qty_basis, reported_qty, reported_uom, normalized_uom, normalized_qty) "
                           "values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning claim_quantity_id",
                           (actor.project_id, claim_id, None, b.get("resource_hint"), b["basis"], b["qty"], b["uom"], b["normalized_uom"], b["normalized_qty"])).fetchone()
            qrows.append({"claim_quantity_id": cq["claim_quantity_id"], "reported_qty": b["qty"], "reported_uom": b["uom"], "basis": b["basis"]})
        for code, sev, msg in findings:
            c.execute("insert into claim_validations (project_id, event_id, rule_code, severity, description) values (%s,%s,%s,%s,%s)", (actor.project_id, claim_id, code, sev, msg))
        auto = None
        if has_content:
            from ..matching import service as matching
            try:
                c.execute("savepoint automatch")
                auto = matching.auto_match(c, actor, claim_id, ver)
            except Exception as e:                                           # noqa: BLE001  (matching must never lose the answered report)
                c.execute("rollback to savepoint automatch")
                import logging
                logging.getLogger(__name__).exception("automatic matching failed for claim %s", claim_id)
                c.execute("insert into claim_validations (project_id, event_id, rule_code, severity, description) values (%s,%s,'AUTOMATIC_MATCH_UNAVAILABLE','INFO',%s)",
                          (actor.project_id, claim_id, f"Automatic matching could not run ({type(e).__name__}); a Supervisor will match this claim."))
                auto = {"matched": False, "error": type(e).__name__}
            for sup in supervisors_of(c, actor.project_id):
                notify(c, project_id=actor.project_id, recipient_id=sup, ntype="CLAIM_SUBMITTED", event_id=claim_id, created_by=actor.user_id,
                       title="New claim to review", body=claim["raw_claim_text"][:300])
        else:
            c.execute("insert into claim_validations (project_id, event_id, rule_code, severity, description) values (%s,%s,'INCOMPLETE_REPORT','ERROR',%s)",
                      (actor.project_id, claim_id, "The report still does not state progress (a percentage, a quantity or a start / finish)."))
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=SE, action="CLAIM_CLARIFICATION_ANSWERED_BY_ENGINEER", entity_type="CLAIM", entity_id=claim_id,
                  version_id=ver["version_id"], before={"status": "REPORTED"}, after={"status": "EXTRACTED" if has_content else "REPORTED", "completed": has_content})
    return {"claim_id": claim_id, "completed": has_content, "match": auto, "quantities": qrows}


def answer_clarification(actor: ProjectActor, claim_id, answer: str, evidence_document_ids: Optional[List[uuid.UUID]] = None) -> Dict[str, Any]:
    require_role(actor, SE, what="answering a clarification")
    require_writable(actor)
    if len((answer or "").strip()) < 2:
        raise ApiError(422, "ANSWER_REQUIRED", "An answer is required")
    with actor_tx(actor, write=True) as c:
        claim = _claim_row(c, actor.project_id, claim_id, lock=True)
        _own_or_404(actor, claim)
        if claim["status"] != "DISPUTED" or claim["clarification_status"] != "ASKED":
            raise ApiError(409, "NO_OPEN_QUESTION", "There is no open supervisor question on this claim")
        c.execute("update execution_events set clarification_answer = %s, clarification_status = 'ANSWERED' where project_id = %s and event_id = %s", (answer.strip(), actor.project_id, claim_id))
        for d_id in evidence_document_ids or []:
            c.execute("insert into claim_evidence (project_id, event_id, document_id) values (%s,%s,%s) on conflict do nothing", (actor.project_id, claim_id, d_id))
        for sup in supervisors_of(c, actor.project_id):
            notify(c, project_id=actor.project_id, recipient_id=sup, ntype="CLAIM_CLARIFICATION", event_id=claim_id, created_by=actor.user_id,
                   title="Clarification answered", body=answer.strip()[:300])
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=SE, action="CLAIM_CLARIFIED", entity_type="CLAIM", entity_id=claim_id, after={"answered": True})
    return {"claim_id": claim_id, "status": "DISPUTED", "clarification_status": "ANSWERED"}


def attach_evidence(actor: ProjectActor, claim_id, document_id) -> Dict[str, Any]:
    require_role(actor, SE, what="attaching evidence")
    require_writable(actor)
    with actor_tx(actor, write=True) as c:
        claim = _claim_row(c, actor.project_id, claim_id, lock=True)
        _own_or_404(actor, claim)
        if claim["status"] in FINAL_STATUSES:
            raise ApiError(409, "CLAIM_ALREADY_FINAL", "Evidence cannot be added to a final claim")
        if c.execute("select 1 from source_documents where project_id = %s and document_id = %s", (actor.project_id, document_id)).fetchone() is None:
            raise ApiError(422, "DOCUMENT_NOT_FOUND", "That document does not belong to this project")
        c.execute("insert into claim_evidence (project_id, event_id, document_id) values (%s,%s,%s) on conflict do nothing", (actor.project_id, claim_id, document_id))
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=SE, action="CLAIM_EVIDENCE_ATTACHED", entity_type="CLAIM", entity_id=claim_id, after={"document_id": str(document_id)})
    return {"claim_id": claim_id, "document_id": document_id}


# ------------------------------------------------------------------------------------------------ supervisor actions before a decision
def rebind_quantities(c, project_id, claim_id, ver, activity_uid) -> list:
    """Point a claim at `activity_uid` and (re)derive its quantity bindings against that activity's measured assignments in version `ver`; earlier bindings
    are cleared, never carried over. Shared by the Supervisor's manual rematch and by automatic matching. -> findings [(rule, severity, message)]"""
    units = load_units(c)
    ref = RefData(set(), {}, {k: v.dimension for k, v in units.items()})
    c.execute("update claim_quantities set assignment_uid = null, normalized_qty = null, normalized_uom = null where event_id = %s", (claim_id,))
    c.execute("update execution_events set matched_activity_uid = %s, status = case when status = 'EXTRACTED' then 'MATCHED' else status end where project_id = %s and event_id = %s",
              (activity_uid, project_id, claim_id))
    assigns = measured_assignments(c, ver["version_id"], activity_uid)
    rows = c.execute("select claim_quantity_id, reported_qty, reported_uom, qty_basis, reported_resource from claim_quantities where event_id = %s", (claim_id,)).fetchall()
    bound, findings = _bind(units, ref, assigns, [{"qty": r["reported_qty"], "uom": r["reported_uom"], "basis": r["qty_basis"], "resource_hint": r["reported_resource"], "_id": r["claim_quantity_id"]} for r in rows])
    for b in bound:
        if b["assignment"]:
            c.execute("update claim_quantities set assignment_uid = %s, normalized_qty = %s, normalized_uom = %s where claim_quantity_id = %s",
                      (b["assignment"]["assignment_uid"], b["normalized_qty"], b["normalized_uom"], b["_id"]))
    c.execute("delete from claim_validations where event_id = %s and rule_code in ('UNKNOWN_UNIT','NO_MEASURED_ASSIGNMENT','UNIT_DIMENSION_MISMATCH','AMBIGUOUS_QUANTITY','DUPLICATE_QUANTITY','REPORTED_ABOVE_BASELINE')", (claim_id,))
    for code, sev, msg in findings:
        c.execute("insert into claim_validations (project_id, event_id, rule_code, severity, description) values (%s,%s,%s,%s,%s)", (project_id, claim_id, code, sev, msg))
    return findings


def refresh_priority(c, project_id, claim_id) -> None:
    """recompute the review priority of a claim from its CURRENT validations and matched activity (same formula as at submission)"""
    errs = c.execute("select count(*) filter (where severity = 'ERROR') e, count(*) filter (where severity = 'WARNING') w from claim_validations where event_id = %s", (claim_id,)).fetchone()
    crit = c.execute("select coalesce(bool_or(a.is_critical), false) k from execution_events e join baseline_activities a on a.version_id = e.filed_in_version_id and a.activity_uid = e.matched_activity_uid "
                     "where e.event_id = %s", (claim_id,)).fetchone()["k"]
    codes = [r["rule_code"] for r in c.execute("select rule_code from claim_validations where event_id = %s order by rule_code", (claim_id,)).fetchall()]
    c.execute("update execution_events set priority_score = %s, priority_reasons = %s where project_id = %s and event_id = %s",
              (Decimal(10 * errs["e"] + 3 * errs["w"]) + (Decimal(5) if crit else Decimal(0)), "; ".join(codes) or None, project_id, claim_id))


def rematch_claim(actor: ProjectActor, claim_id, activity_uid=None) -> Dict[str, Any]:
    """Supervisor: point a claim at a different activity of the ACTIVE schedule (the explicit manual override), or -- with no activity -- run the automatic
    matching engine again. Existing quantity bindings are cleared and re-derived (never carried over)."""
    require_role(actor, SUP, what="re-matching a claim")
    require_writable(actor)
    with actor_tx(actor, write=True) as c:
        claim = _claim_row(c, actor.project_id, claim_id, lock=True)
        if claim["status"] not in DECIDABLE_STATUSES:
            raise ApiError(409, "CLAIM_NOT_DECIDABLE", f"A {claim['status']} claim cannot be re-matched")
        if activity_uid is None:
            from ..matching import service as matching
            return {"claim_id": claim_id, **matching.auto_match(c, actor, claim_id, active_version(c, actor.project_id), overwrite_pick=True)}
        ver = active_version(c, actor.project_id)
        if c.execute("select 1 from baseline_activities where version_id = %s and activity_uid = %s", (ver["version_id"], activity_uid)).fetchone() is None:
            raise ApiError(409, "ACTIVITY_NOT_IN_ACTIVE_SCHEDULE", "That activity is not part of the project's active schedule version")
        findings = rebind_quantities(c, actor.project_id, claim_id, ver, activity_uid)
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=SUP, action="CLAIM_REMATCHED", entity_type="CLAIM", entity_id=claim_id,
                  before={"activity_uid": str(claim["matched_activity_uid"]) if claim["matched_activity_uid"] else None}, after={"activity_uid": str(activity_uid)})
    return {"claim_id": claim_id, "activity_uid": activity_uid, "findings": [{"rule": k, "severity": s, "message": m} for k, s, m in findings]}


def bind_quantities(actor: ProjectActor, claim_id, bindings: Dict[uuid.UUID, uuid.UUID]) -> Dict[str, Any]:
    """Supervisor: bind reported quantities (claim_quantity_id -> assignment_uid). The unit kind must match; the value is converted only within the kind."""
    require_role(actor, SUP, what="binding claim quantities")
    require_writable(actor)
    with actor_tx(actor, write=True) as c:
        claim = _claim_row(c, actor.project_id, claim_id, lock=True)
        if claim["status"] not in DECIDABLE_STATUSES:
            raise ApiError(409, "CLAIM_NOT_DECIDABLE", f"A {claim['status']} claim cannot be changed")
        ver = active_version(c, actor.project_id)
        units = load_units(c)
        ref = RefData(set(), {}, {k: v.dimension for k, v in units.items()})
        out = []
        for cq_id, asg in bindings.items():
            cq = c.execute("select * from claim_quantities where project_id = %s and event_id = %s and claim_quantity_id = %s", (actor.project_id, claim_id, cq_id)).fetchone()
            if cq is None:
                raise ApiError(404, "QUANTITY_NOT_FOUND", "No such reported quantity on this claim")
            a = next((x for x in measured_assignments(c, ver["version_id"], claim["matched_activity_uid"]) if x["assignment_uid"] == asg), None) if claim["matched_activity_uid"] else None
            if a is None:
                raise ApiError(422, "ASSIGNMENT_NOT_MEASURED", "That is not a progress-measuring assignment of the claim's activity")
            code = resolve_uom(cq["reported_uom"], None, ref)
            try:
                norm = convert(cq["reported_qty"], units[code], units[a["unit_of_measure"]]) if code else None
            except UnitMismatch as e:
                raise ApiError(422, "UNIT_DIMENSION_MISMATCH", str(e)) from e
            if norm is None:
                raise ApiError(422, "UNKNOWN_UNIT", f"Unit {cq['reported_uom']!r} is not a controlled unit")
            if any(r["assignment_uid"] == asg for r in out):
                raise ApiError(422, "DUPLICATE_BINDING", "Two quantities cannot be bound to the same assignment")
            c.execute("update claim_quantities set assignment_uid = %s, normalized_qty = %s, normalized_uom = %s where claim_quantity_id = %s", (asg, norm, a["unit_of_measure"], cq_id))
            out.append({"claim_quantity_id": cq_id, "assignment_uid": asg, "normalized_qty": norm, "unit": a["unit_of_measure"]})
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=SUP, action="CLAIM_QUANTITIES_BOUND", entity_type="CLAIM", entity_id=claim_id, after={"bindings": [{k: str(v) for k, v in o.items()} for o in out]})
    return {"claim_id": claim_id, "bound": out}


# ------------------------------------------------------------------------------------------------ reads (role-filtered)
def get_claim(actor: ProjectActor, claim_id) -> Dict[str, Any]:
    """Site Engineer: own claims only (others look absent). Supervisor: any. Project Manager: never (claim content is not theirs to read)."""
    if actor.role == PM:
        raise ApiError(403, "CLAIM_CONTENT_FORBIDDEN", "Project managers see aggregate claim counts only, not claim content")
    with actor_tx(actor, readonly=True) as c:
        claim = _claim_row(c, actor.project_id, claim_id)
        _own_or_404(actor, claim)
        return {
            **claim,
            "quantities": c.execute("select cq.*, pr.resource_code from claim_quantities cq left join assignments a on a.assignment_uid = cq.assignment_uid "
                                    "left join baseline_resources br on br.assignment_uid = a.assignment_uid and br.version_id = %s left join project_resources pr on pr.resource_id = br.resource_id "
                                    "where cq.event_id = %s order by cq.claim_quantity_id", (claim["filed_in_version_id"], claim_id)).fetchall(),
            "evidence": c.execute("select d.document_id, d.kind, d.file_name, d.sha256 from claim_evidence ce join source_documents d on d.document_id = ce.document_id where ce.event_id = %s", (claim_id,)).fetchall(),
            "validations": c.execute("select rule_code, severity, description from claim_validations where event_id = %s order by severity, rule_code", (claim_id,)).fetchall(),
            "candidates": c.execute("select cm.candidate_id, cm.activity_uid, ba.external_activity_id, ba.activity_name, cm.rank_order, cm.composite_confidence, cm.match_tier, cm.semantic_score, cm.fuzzy_score, "
                                    "cm.location_score, cm.discipline_score, cm.supporting_signals, cm.disqualifying_signals from candidate_matches cm "
                                    "join execution_events e on e.event_id = cm.event_id "
                                    "left join baseline_activities ba on ba.activity_uid = cm.activity_uid and ba.version_id = (select version_id from schedule_versions where project_id = e.project_id and status = 'ACTIVE') "
                                    "where cm.event_id = %s order by cm.rank_order", (claim_id,)).fetchall(),
            "decisions": c.execute("select decision_id, action, method, justification, decided_at, approved_pct, overrun_ack, overrun_ack_note, applied, result from planner_decisions "
                                   "where event_id = %s order by decided_at", (claim_id,)).fetchall(),
        }


def list_my_claims(actor: ProjectActor, status: Optional[str] = None, limit: int = 100, offset: int = 0) -> List[dict]:
    require_role(actor, SE, what="listing own claims")
    with actor_tx(actor, readonly=True) as c:
        return c.execute("select event_id, event_date, status, matched_activity_uid, claimed_pct, clarification_status, clarification_question, created_at, withdrawn_at, resubmits_event_id "
                         "from execution_events where project_id = %s and filed_by = %s and (%s::text is null or status = %s) order by created_at desc limit %s offset %s",
                         (actor.project_id, actor.user_id, status, status, min(limit, 500), offset)).fetchall()


def review_queue(actor: ProjectActor, statuses: Optional[List[str]] = None, limit: int = 100, offset: int = 0) -> List[dict]:
    require_role(actor, SUP, what="reading the review queue")
    statuses = statuses or list(DECIDABLE_STATUSES)
    with actor_tx(actor, readonly=True) as c:
        return c.execute(
            "select e.event_id, e.event_date, e.status, e.matched_activity_uid, a.external_activity_id, e.claimed_pct, e.priority_score, e.priority_reasons, e.clarification_status, "
            "e.created_at, e.filed_by, (select count(*) from claim_validations v where v.event_id = e.event_id and v.severity = 'ERROR') as errors "
            "from execution_events e left join baseline_activities a on a.version_id = e.filed_in_version_id and a.activity_uid = e.matched_activity_uid "
            "where e.project_id = %s and e.status = any(%s) order by e.priority_score desc, e.created_at limit %s offset %s",
            (actor.project_id, statuses, min(limit, 500), offset)).fetchall()


def claim_counts(actor: ProjectActor) -> Dict[str, int]:
    """AGGREGATE counts by status. This is all a Project Manager may see about claims; it carries no claim content."""
    require_role(actor, SUP, PM, what="reading claim counts")
    with actor_tx(actor, readonly=True) as c:
        rows = c.execute("select status, count(*) as n from execution_events where project_id = %s group by status", (actor.project_id,)).fetchall()
    out = {s: 0 for s in ("REPORTED", "EXTRACTED", "MATCHED", "VALIDATED", "DISPUTED", "APPROVED", "REJECTED", "WITHDRAWN")}
    out.update({r["status"]: r["n"] for r in rows})
    out["pending_total"] = sum(out[s] for s in PENDING_STATUSES)
    return out
