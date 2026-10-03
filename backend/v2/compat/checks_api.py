"""The original claim CHECK pipeline (backend/routers/checks.py: check_claim) on v2 data.

The evaluators (cumulative conflicts, incremental-quantity anomalies, sequence/dependency validation, evidence fusion, smart review priority) are the ORIGINAL functions,
imported unchanged. They read the legacy read model (migration 0016: views in the demo's vocabulary over the v2 tables) while the connection's search_path points at it;
everything is persisted afterwards, with the search_path restored, through v2's own tables. Nothing here approves a claim or touches the approved ledgers."""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Dict, List

from .. import audit
from ..domain import claims as dc
from ..domain.common import FINAL_STATUSES, PENDING_STATUSES, SE, actor_tx, active_version
from ..errors import ApiError
from . import shapes
from .context import Ctx

SYSTEM_ACTOR = "SYSTEM:M4 (deterministic claim checks, v2 adapter)"
# validation rows written by the v2 domain itself (quantity binding, matching, intake): a check never removes them
DOMAIN_CODES = {"UNKNOWN_UNIT", "NO_MEASURED_ASSIGNMENT", "UNIT_DIMENSION_MISMATCH", "AMBIGUOUS_QUANTITY", "DUPLICATE_QUANTITY", "REPORTED_ABOVE_BASELINE", "PERCENT_ONLY_NEEDS_METHOD",
                "NO_AUTOMATIC_MATCH", "AUTOMATIC_MATCH_UNAVAILABLE", "INCOMPLETE_REPORT"}
MARKER = "REVIEW_REQUIRED"           # "the checks flagged this claim for a person" -- the demo's REVIEW_REQUIRED state, which v2's status machine has no value for


@contextmanager
def legacy_view_mode(c, project_id, version_id):
    """Let the original read-only code see the project's data in the demo's vocabulary: scope the views to this project/version and put the lrm schema first on the
    search_path -- for evaluation only. The path is restored before anything is written."""
    prev = c.execute("show search_path").fetchone()["search_path"]
    c.execute("select set_config('app.lrm_project', %s, true), set_config('app.lrm_version', %s, true)", (str(project_id), str(version_id)))
    c.execute("select set_config('search_path', 'lrm, public', true)")
    try:
        yield c
    finally:
        c.execute("select set_config('search_path', %s, true)", (prev,))


def _evidence_checks(c, claim: Dict[str, Any], event_date) -> List[Dict[str, Any]]:
    """photo evidence vs the claim: EXIF date and GPS against the project site (the original VAL_EVIDENCE_MISMATCH rules, on the stored document metadata)"""
    import os
    from backend.routers.checks import haversine_distance_km
    issues = []
    docs = c.execute("select d.captured_at, d.gps_lat, d.gps_lon from source_documents d where d.mime_type like 'image/%%' and (d.document_id = %s or d.document_id in "
                     "(select document_id from claim_evidence where event_id = %s))", (claim["document_id"], claim["event_id"])).fetchall()
    site_lat, site_lon = float(os.getenv("PROJECT_SITE_LAT", "27.4728")), float(os.getenv("PROJECT_SITE_LON", "95.3547"))
    radius = float(os.getenv("PROJECT_SITE_RADIUS_KM", "5.0"))
    for d in docs:
        if d["captured_at"] is not None and event_date and abs((d["captured_at"].date() - event_date).days) > 2:
            issues.append({"issue_id": str(uuid.uuid4()), "event_id": str(claim["event_id"]), "rule_code": "VAL_EVIDENCE_MISMATCH", "severity": "WARNING",
                           "description": f"Photo EXIF date ({d['captured_at'].date()}) differs from event date ({event_date}) by > 2 days."})
        if d["gps_lat"] is not None and d["gps_lon"] is not None:
            dist = haversine_distance_km(float(d["gps_lat"]), float(d["gps_lon"]), site_lat, site_lon)
            if dist > radius:
                issues.append({"issue_id": str(uuid.uuid4()), "event_id": str(claim["event_id"]), "rule_code": "VAL_EVIDENCE_MISMATCH", "severity": "WARNING",
                               "description": f"Photo GPS location is {round(dist, 2)}km from project site (exceeds {radius}km radius)."})
    return issues


def run_check(ctx: Ctx, claim_id) -> Dict[str, Any]:
    from backend.routers import checks as lc
    from backend.shared.workflow_flags import with_workflow_flags  # noqa: F401  (imported by the original module; keeps import order identical)
    cid = str(claim_id)
    with actor_tx(ctx.actor, write=True) as c:
        claim = dc._claim_row(c, ctx.project_id, claim_id, lock=True)
        if ctx.access.role == SE:
            dc._own_or_404(ctx.actor, claim)
        if claim["status"] == "REPORTED":
            raise ApiError(400, "CLARIFICATION_PENDING", "Cannot check a claim while clarification is pending. Engineer must submit clarification first.")
        if claim["status"] in FINAL_STATUSES:
            raise ApiError(409, "CLAIM_ALREADY_FINAL", f"The claim is {claim['status']} and can no longer be checked")
        version_id = claim["filed_in_version_id"]
        issues: List[Dict[str, Any]] = []
        conflicts: List[Dict[str, Any]] = []
        links: List[Dict[str, Any]] = []
        issues += _evidence_checks(c, claim, claim["event_date"])
        with legacy_view_mode(c, ctx.project_id, version_id):
            row = c.execute("select * from execution_events where event_id = %s", (cid,)).fetchone()
            before = {k: (str(v) if isinstance(v, (uuid.UUID, date, datetime)) else v) for k, v in row.items() if k != "field_provenance"}
            incoming = row["status"]
            sid, matched, ev_date = row["schedule_id"], row["matched_activity_id"], row["event_date"]
            claimed_pct, claimed_qty, claimed_uom = row["claimed_pct"], row["claimed_quantity"], row["claimed_uom"]
            mode, etype = row["claim_mode"] or "CUMULATIVE_PCT", row["event_type"]
            act = c.execute("select * from schedule_activities where schedule_id = %s and activity_id = %s", (sid, matched)).fetchone() if matched else None
            splits = lc.load_claim_activity_splits(c, cid)
            has_matched = matched is not None and str(matched).strip() != ""

            if not has_matched and not splits and incoming in ("MATCHED", "VALIDATED", "REVIEW_REQUIRED", "APPROVED", "EDITED"):
                issues.append({"issue_id": str(uuid.uuid4()), "event_id": cid, "rule_code": "VAL_SPLIT_INCONSISTENCY", "severity": "ERROR",
                               "description": f"Inconsistent matching state: Event '{cid}' has status '{incoming}' but has neither a matched_activity_id nor WBS split rows."})
            if incoming == "UNMATCHED":
                issues.append({"issue_id": str(uuid.uuid4()), "event_id": cid, "rule_code": "VAL_LOW_MATCH_CONFIDENCE", "severity": "WARNING",
                               "description": "Match confidence fell below matching threshold."})
            if mode == "INCREMENTAL_QUANTITY":
                i, cf, derived = lc.evaluate_incremental_quantity_anomalies(
                    conn=c, schedule_id=sid, matched_activity_id=matched, event_id=cid, event_date=ev_date, claimed_qty=claimed_qty, claimed_uom=claimed_uom,
                    activity_row=dict(act) if act else None, raw_claim_text=row["raw_claim_text"], current_claim=dict(row))
                issues += i
                conflicts += cf
                if derived is not None:
                    claimed_pct = derived
            else:
                if claimed_pct is not None and claimed_pct > 100.0:
                    issues.append({"issue_id": str(uuid.uuid4()), "event_id": cid, "rule_code": "VAL_OVER_100", "severity": "ERROR",
                                   "description": f"Claimed progress percentage ({claimed_pct}%) exceeds 100%."})
                if matched and ev_date and claimed_pct is not None:
                    i, cf = lc.evaluate_cumulative_conflicts(conn=c, schedule_id=sid, matched_activity_id=matched, event_id=cid, event_date=ev_date,
                                                             claimed_pct=claimed_pct, current_claim=dict(row))
                    issues += i
                    conflicts += cf
            if claimed_pct is not None and claimed_pct < 0.0 and not any(x["rule_code"] == "VAL_NEGATIVE" for x in issues):
                issues.append({"issue_id": str(uuid.uuid4()), "event_id": cid, "rule_code": "VAL_NEGATIVE", "severity": "ERROR",
                               "description": f"Claimed progress percentage ({claimed_pct}%) cannot be negative."})
            issues += lc.evaluate_sequence_validation(conn=c, schedule_id=sid, matched_activity_id=matched, event_id=cid, event_type=etype, event_date=ev_date,
                                                      claimed_pct=claimed_pct, claimed_qty=claimed_qty)
            if mode == "CUMULATIVE_PCT" and claimed_pct is not None and claimed_pct < 100.0 and matched:
                done = c.execute("select actual_finish from approved_actuals where schedule_id = %s and activity_id = %s", (sid, matched)).fetchone()
                if done and done.get("actual_finish") is not None:
                    issues.append({"issue_id": str(uuid.uuid4()), "event_id": cid, "rule_code": "VAL_REOPENED_COMPLETED_ACTIVITY", "severity": "ERROR",
                                   "description": "Claim reported for an activity that was already marked as completed."})
            top = c.execute("select match_tier from candidate_matches where event_id = %s and rank_order = 1", (cid,)).fetchone()
            unmatched_tier = bool(top and top.get("match_tier") in ("UNMATCHED", "HARD_MISMATCH"))
            fusion_row = dict(row)
            fusion_row["claimed_pct"] = claimed_pct
            links = lc.evaluate_evidence_fusion(conn=c, event_id=cid, schedule_id=sid, event_row=fusion_row, splits=splits, tolerance_pct=lc.CONFLICT_TOLERANCE_PCT,
                                                window_days=lc.EVIDENCE_COMPARISON_WINDOW_DAYS, persist=False)
            prio = lc.evaluate_smart_review_priority(event_row=fusion_row, validation_issues=issues, conflicts=conflicts, evidence_links=links, splits=splits, conn=c)

        flagged = bool(issues or conflicts) or incoming == "UNMATCHED" or unmatched_tier
        # ---- persist (search_path restored): only the validations the check itself produces are replaced
        c.execute("delete from claim_validations where event_id = %s and rule_code <> all(%s)", (claim_id, sorted(DOMAIN_CODES)))
        for x in issues:
            c.execute("insert into claim_validations (project_id, event_id, rule_code, severity, description) values (%s,%s,%s,%s,%s)",
                      (ctx.project_id, claim_id, x["rule_code"], x["severity"] if x["severity"] in ("INFO", "WARNING", "ERROR") else "WARNING", x["description"]))
        if flagged:
            c.execute("insert into claim_validations (project_id, event_id, rule_code, severity, description) values (%s,%s,%s,'INFO','The checks flagged this claim for a Supervisor')",
                      (ctx.project_id, claim_id, MARKER))
        c.execute("delete from conflict_records where project_id = %s and (event_id_a = %s or event_id_b = %s) and status = 'OPEN'", (ctx.project_id, claim_id, claim_id))
        a_uid = claim["matched_activity_uid"]
        for cf in conflicts:
            other = cf["event_id_b"]
            try:
                other_id = uuid.UUID(str(other))
            except ValueError:
                continue                                   # a conflict against an approved-actual marker (no second claim) is carried by the validation issue
            if a_uid is None or other_id == claim_id:
                continue
            c.execute("insert into conflict_records (project_id, activity_uid, reporting_period, event_id_a, event_id_b, value_a, value_b, variance_pct, status) "
                      "values (%s,%s,%s,%s,%s,%s,%s,%s,'OPEN')", (ctx.project_id, a_uid, cf["reporting_period"], claim_id, other_id, cf["value_a"], cf["value_b"], cf["variance_pct"]))
        c.execute("delete from evidence_links where project_id = %s and (event_id_a = %s or event_id_b = %s)", (ctx.project_id, claim_id, claim_id))
        for ln in links:
            try:
                other = uuid.UUID(str(ln["event_id_b"] if str(ln["event_id_a"]) == cid else ln["event_id_a"]))
            except ValueError:
                continue
            c.execute("insert into evidence_links (project_id, event_id_a, event_id_b, relation_type, confidence, rationale) values (%s,%s,%s,%s,%s,%s)",
                      (ctx.project_id, claim_id, other, ln["relation_type"], ln.get("confidence"), ln.get("rationale") or ln["relation_type"]))
        new_status = claim["status"]
        if not flagged and claim["status"] == "MATCHED":
            c.execute("update execution_events set status = 'VALIDATED' where project_id = %s and event_id = %s", (ctx.project_id, claim_id))
            new_status = "VALIDATED"
        c.execute("update execution_events set priority_score = %s, priority_reasons = %s where project_id = %s and event_id = %s",
                  (prio["priority_score"], prio["priority_reasons"], ctx.project_id, claim_id))
        audit.log(c, project_id=ctx.project_id, actor_id=ctx.user.id, role=ctx.access.role, action="CLAIM_CHECKED", entity_type="CLAIM", entity_id=claim_id, version_id=version_id,
                  before={"status": claim["status"]}, after={"status": new_status, "flagged": flagged, "issues": len(issues), "conflicts": len(conflicts), "evidence_links": len(links),
                                                            "priority_score": prio["priority_score"], "performed_by": SYSTEM_ACTOR})
        from .claims_api import one_claim
        legacy = one_claim(c, ctx, claim_id)["status"]
    return {"event_id": cid, "status": legacy, "claimed_pct": claimed_pct, "validation_issues": issues,
            "conflicts": [{**cf, "reporting_period": shapes.iso(cf["reporting_period"])} for cf in conflicts], "evidence_links": links,
            "priority_score": prio["priority_score"], "priority_reasons": prio["priority_reasons"]}


