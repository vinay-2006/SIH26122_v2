"""Run the existing matching engine for one v2 claim and record the outcome.

Writes ONLY: candidate_matches (top 3), execution_events.matched_activity_uid / status / field_provenance, quantity bindings of that claim, claim
validations/priority, and an audit entry. It never touches planner_decisions or the approved ledgers: matching proposes, a Supervisor decides.

The engine (match_claim and the tiers) is imported unchanged. The outcome rules below are the legacy `run_claim_match` rules, kept in `decide_outcome`
and pinned to the legacy source by tests/v2_matching/test_engine_parity.py (thresholds are read back from the legacy file, not re-chosen here):
  * HYBRID_FALLBACK rank-1 vs rank-2 confidence gap < 0.05  -> ambiguous -> not matched
  * MATCHED  <=>  tier in (EXACT_ID, EXACT_ASSET, HYBRID_FALLBACK) and confidence > 0.40 and not ambiguous
Differences forced by the v2 schema (reported, not hidden): v2 has no UNMATCHED claim status, so an unmatched claim stays EXTRACTED with no activity
(the legacy stored its best guess on the claim; v2 keeps the guess only as a candidate, so a Supervisor must choose explicitly)."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from .. import audit
from . import adapter, index

logger = logging.getLogger(__name__)
SYSTEM_NAME = "SYSTEM:matching cascade (existing engine, v2 adapter)"


def _engine():
    """import order matters on macOS: torch (via schedule_index) before faiss, before the matching module (which imports schedule_index itself)"""
    from backend.shared import schedule_index  # noqa: F401
    from backend.routers import matching
    from backend.shared.schemas import ExecutionClaim
    from backend.services.matching_eligibility_service import MatchingEligibilityService
    return matching, ExecutionClaim, MatchingEligibilityService


def decide_outcome(candidates, *, eligible_empty_reason: Optional[str] = None) -> Dict[str, Any]:
    """candidates (ranked CandidateMatch list from the engine) -> status / activity / tier / confidence / reason, with the legacy rules"""
    m, _, _ = _engine()
    ambiguous, amb_reason = False, None
    if candidates and candidates[0].match_tier == m.HYBRID_FALLBACK and len(candidates) >= 2:
        diff = candidates[0].composite_confidence - candidates[1].composite_confidence
        if diff < 0.05:
            ambiguous = True
            amb_reason = (f"Ambiguous match: confidence difference between rank-1 ({candidates[0].activity_id}) "
                          f"and rank-2 ({candidates[1].activity_id}) is {diff:.4f} < 0.05")
    if candidates and candidates[0].match_tier in (m.EXACT_ID, m.EXACT_ASSET, m.HYBRID_FALLBACK) and candidates[0].composite_confidence > 0.40 and not ambiguous:
        return {"matched": True, "activity_id": candidates[0].activity_id, "tier": candidates[0].match_tier,
                "confidence": candidates[0].composite_confidence, "ambiguous": False, "reason": None}
    if ambiguous:
        reason = amb_reason
    elif candidates and candidates[0].match_tier == m.HARD_MISMATCH:
        reason = candidates[0].disqualifying_signals or "Hard metadata mismatch"
    elif candidates:
        reason = f"Low confidence match ({candidates[0].composite_confidence:.2f})"
    else:
        reason = eligible_empty_reason or "NO_ELIGIBLE_CANDIDATE: No matching schedule activities found"
    return {"matched": False, "activity_id": None, "tier": candidates[0].match_tier if candidates else m.HARD_MISMATCH,
            "confidence": candidates[0].composite_confidence if candidates else 0.0, "ambiguous": ambiguous, "reason": reason}


def rank_claim(project_id, version_id, claim: Dict[str, Any], quantities: List[Dict[str, Any]], activities: List[Dict[str, Any]]) -> Dict[str, Any]:
    """the engine call itself, with no database writes: eligibility -> optional semantic retrieval -> match_claim -> outcome"""
    m, ExecutionClaim, Elig = _engine()
    ec = ExecutionClaim(**adapter.claim_for_engine(claim, quantities, version_id))
    eligible, explanations = Elig.filter_eligible_activities(activities, expected_project_id=str(project_id), expected_schedule_id=str(version_id), is_rework=False)
    semantic, semantic_error = None, None
    if ec.raw_claim_text:
        try:
            semantic = index.search(project_id, version_id, activities, ec.raw_claim_text)
        except Exception as e:                                     # same degradation as the legacy pipeline: no semantic signal, everything else still runs
            semantic_error = f"{type(e).__name__}: {e}"
            logger.warning("semantic retrieval unavailable, matching without it: %s", semantic_error)
    candidates = m.match_claim(ec, eligible, semantic_results=semantic, batch_context=None)
    empty_reason = None
    if not candidates:
        rid = ec.reported_activity_id
        if rid and rid in explanations:
            empty_reason = f"NO_ELIGIBLE_CANDIDATE: Reported activity '{rid}' is {explanations[rid].reason}"
        elif activities and not eligible:
            empty_reason = "NO_ELIGIBLE_CANDIDATE: All activities in schedule are COMPLETED"
    out = decide_outcome(candidates, eligible_empty_reason=empty_reason)
    out.update(candidates=candidates, semantic_available=semantic_error is None, semantic_error=semantic_error, eligible=len(eligible), pool=len(activities))
    return out


def auto_match(c, actor, claim_id, ver, *, overwrite_pick: bool = False) -> Dict[str, Any]:
    """Match one claim of `actor`'s project against that project's ACTIVE version, inside the caller's transaction `c`.
    Without `overwrite_pick`, a claim that already has an activity (an explicit choice) is left exactly as it is."""
    from ..domain import claims as dc
    claim = dc._claim_row(c, actor.project_id, claim_id, lock=True)
    if claim["status"] not in ("EXTRACTED", "MATCHED"):
        from ..errors import ApiError
        raise ApiError(409, "CLAIM_NOT_MATCHABLE", f"A {claim['status']} claim cannot be matched automatically")
    if claim["matched_activity_uid"] is not None and not overwrite_pick:
        return {"skipped": "ACTIVITY_ALREADY_CHOSEN", "matched": False}
    activities = adapter.load_activities(c, actor.project_id, ver["version_id"])
    quantities = c.execute("select reported_qty, reported_uom from claim_quantities where event_id = %s order by claim_quantity_id", (claim_id,)).fetchall()
    res = rank_claim(actor.project_id, ver["version_id"], claim, quantities, activities)
    by_ext = {a["activity_id"]: a["activity_uid"] for a in activities}
    top3 = res["candidates"][:3]

    c.execute("delete from candidate_matches where project_id = %s and event_id = %s", (actor.project_id, claim_id))
    for cand in top3:
        c.execute("insert into candidate_matches (candidate_id, project_id, event_id, activity_uid, rank_order, match_tier, composite_confidence, semantic_score, fuzzy_score, "
                  "location_score, discipline_score, supporting_signals, disqualifying_signals) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (adapter.to_uuid(cand.candidate_id) or uuid.uuid4(), actor.project_id, claim_id, by_ext[cand.activity_id], cand.rank_order, cand.match_tier,
                   round(cand.composite_confidence, 4), cand.semantic_score, cand.fuzzy_score, cand.location_score, cand.discipline_score,
                   cand.supporting_signals, cand.disqualifying_signals))

    c.execute("delete from claim_validations where event_id = %s and rule_code = 'NO_AUTOMATIC_MATCH'", (claim_id,))
    prov = dict(claim.get("field_provenance") or {})
    before = {"activity_uid": str(claim["matched_activity_uid"]) if claim["matched_activity_uid"] else None, "status": claim["status"]}
    if res["matched"]:
        uid = by_ext[res["activity_id"]]
        findings = dc.rebind_quantities(c, actor.project_id, claim_id, ver, uid)
        if claim["claimed_pct"] is not None and not quantities and dc.measured_assignments(c, ver["version_id"], uid):     # same note submit_claim adds for an explicit pick
            note = ("PERCENT_ONLY_NEEDS_METHOD", "INFO", "Percentage-only claim on a quantity-based activity: the supervisor must choose how to apply it "
                                                          "(apply to each measured assignment, or enter approved quantities)")
            findings = list(findings) + [note]
            c.execute("insert into claim_validations (project_id, event_id, rule_code, severity, description) values (%s,%s,%s,%s,%s)", (actor.project_id, claim_id, *note))
        prov["activity"] = "SCHEDULE_AUTO_FILLED"
        c.execute("update execution_events set field_provenance = %s::jsonb where project_id = %s and event_id = %s", (json.dumps(prov), actor.project_id, claim_id))
    else:
        uid, findings = None, []
        # an explicit re-run that finds nothing keeps whatever the claim already points at (a Supervisor's choice is never silently cleared)
        c.execute("insert into claim_validations (project_id, event_id, rule_code, severity, description) values (%s,%s,'NO_AUTOMATIC_MATCH','INFO',%s)",
                  (actor.project_id, claim_id, (res["reason"] or "No automatic match")[:900]))
    dc.refresh_priority(c, actor.project_id, claim_id)
    audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=actor.role, action="CLAIM_AUTO_MATCHED", entity_type="CLAIM", entity_id=claim_id,
              version_id=ver["version_id"], before=before,
              after={"matched": res["matched"], "activity_uid": str(uid) if uid else None, "match_tier": res["tier"], "composite_confidence": round(res["confidence"], 4),
                     "ambiguous": res["ambiguous"], "reason": res["reason"], "candidates": [str(by_ext[x.activity_id]) for x in top3],
                     "semantic_available": res["semantic_available"], "performed_by": SYSTEM_NAME})
    return {"matched": res["matched"], "activity_uid": uid, "match_tier": res["tier"], "composite_confidence": round(res["confidence"], 4), "ambiguous": res["ambiguous"],
            "reason": res["reason"], "semantic_available": res["semantic_available"],
            "candidates": [{"activity_uid": by_ext[x.activity_id], "external_activity_id": x.activity_id, "rank_order": x.rank_order, "match_tier": x.match_tier,
                            "composite_confidence": round(x.composite_confidence, 4)} for x in top3],
            "findings": [{"rule": k, "severity": s, "message": m_} for k, s, m_ in findings]}
