"""The audit dossier on v2 data: "show me the complete evidence and decision history behind this project's execution state". The section structure is the original dossier's
(project, schedule, stages, activities, execution evidence, matching, validation, human decisions, approved actuals, reopen history, progress, audit chain, extensions,
completeness). Read-only. A Project Manager receives the structural sections and the audit-chain verification; the sections that carry CLAIM CONTENT (execution evidence,
matching, validation findings, decision justifications) are marked RESTRICTED for that role."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends

from .. import audit, permissions as P
from ..domain.common import PM, SUP, actor_tx
from ..errors import ApiError
from . import agent_api, schedule_api, shapes
from .context import Ctx, path_ctx

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["legacy-contract: dossier"])
RESTRICTED = {"status": "RESTRICTED", "note": "claim content is not available to the Project Manager role"}


def _verification(c) -> Dict[str, Any]:
    return agent_api._audit_verification(c)


def build(ctx: Ctx, version, activity_id: Optional[str] = None) -> Dict[str, Any]:
    sup = ctx.access.role == SUP
    with actor_tx(ctx.actor, readonly=True) as c:
        proj = c.execute("select project_id, project_code, project_name, lifecycle_status, created_at from projects where project_id = %s", (ctx.project_id,)).fetchone()
        ver = c.execute("select version_id, version_no, baseline_name, status, parent_version_id from schedule_versions where project_id = %s and version_id = %s", (ctx.project_id, version)).fetchone()
        acts = schedule_api.activities(c, ctx, version)
        if activity_id:
            acts = [a for a in acts if a["activity_id"] == activity_id]
            if not acts:
                raise ApiError(404, "RESOURCE_NOT_FOUND", f"Activity '{activity_id}' not found")
        stages = schedule_api.stages(c, ctx, version)
        scope_uid = acts[0]["_uid"] if activity_id else None
        ext = {a["_uid"]: a["activity_id"] for a in schedule_api.activities(c, ctx, version)}
        ev_where, p = ("e.project_id = %s", [ctx.project_id]) if not scope_uid else ("e.project_id = %s and e.matched_activity_uid = %s", [ctx.project_id, scope_uid])
        claims = c.execute(f"select e.event_id, e.event_type, e.event_date, e.document_id, e.raw_claim_text, e.matched_activity_uid, e.claimed_pct, d.file_name, "
                           f"(select reported_qty from claim_quantities q where q.event_id = e.event_id order by claim_quantity_id limit 1) qty from execution_events e "
                           f"left join source_documents d on d.document_id = e.document_id where {ev_where} order by e.created_at", tuple(p)).fetchall() if sup else []
        cands = c.execute("select cm.* from candidate_matches cm join execution_events e on e.event_id = cm.event_id where cm.project_id = %s" + (" and cm.activity_uid = %s" if scope_uid else "") +
                          " order by cm.event_id, cm.rank_order", tuple([ctx.project_id] + ([scope_uid] if scope_uid else []))).fetchall() if sup else []
        vals = c.execute("select v.validation_id, v.event_id, v.rule_code, v.severity, v.description from claim_validations v join execution_events e on e.event_id = v.event_id where " + ev_where, tuple(p)).fetchall() if sup else []
        n_conf = c.execute("select count(*) n from conflict_records where project_id = %s", (ctx.project_id,)).fetchone()["n"] if sup else 0
        decs = c.execute("select d.decision_id, d.event_id, d.selected_activity_uid, d.action, d.approved_pct, d.decided_by, d.justification, d.decided_at from planner_decisions d join execution_events e on e.event_id = d.event_id where " +
                         ev_where.replace("e.matched_activity_uid", "d.selected_activity_uid"), tuple(p)).fetchall() if sup else []
        reopens = c.execute("select r.* from activity_reopens r where r.project_id = %s" + (" and r.activity_uid = %s" if scope_uid else "") + " order by r.requested_at", tuple([ctx.project_id] + ([scope_uid] if scope_uid else []))).fetchall()
        logs = c.execute("select log_id, entity_type, entity_id, action, actor_id, payload_hash, previous_hash, current_hash, occurred_at from audit_logs where project_id = %s order by log_id desc limit 50", (ctx.project_id,)).fetchall()
        verification = _verification(c)
        overall = c.execute("select physical_pct from project_progress_as_of(%s, current_date)", (version,)).fetchone()
    now = datetime.now(timezone.utc).isoformat()

    def sect(rows_key: str, rows: List[Any], restricted: bool = False) -> Dict[str, Any]:
        return RESTRICTED | {rows_key: []} if restricted else {"status": "AVAILABLE" if rows else "PARTIAL", rows_key: rows}
    activities = [{"activity_id": a["activity_id"], "activity_code": a["wbs_code"], "activity_name": a["activity_name"], "canonical_execution_state": a["execution_state"],
                   "workflow_condition": "REOPEN_REQUESTED" if a["_reopen"] == "REQUESTED" else ("REWORK_IN_PROGRESS" if a["_reopen"] == "APPROVED" else ("QUALITY_HOLD" if a["_qhold"] else ("BLOCKED" if a["_blocked"] else "NONE"))),
                   "progress_pct": a["_pct"], "weight_factor": a["weight"] or 1.0, "stage_id": a["stage_id"], "is_reopened": a["_reopen"] == "APPROVED"} for a in acts]
    sections: Dict[str, Any] = {
        "project": {"status": "AVAILABLE", "project_id": str(proj["project_id"]), "project_code": proj["project_code"], "project_name": proj["project_name"], "project_status": proj["lifecycle_status"],
                    "created_at": shapes.iso(proj["created_at"])},
        "schedule": {"status": "AVAILABLE", "schedule_id": str(ver["version_id"]), "version_code": f"v{ver['version_no']}", "is_active": ver["status"] == "ACTIVE",
                     "supersedes_schedule_id": str(ver["parent_version_id"]) if ver["parent_version_id"] else None, "source_hash": None, "metadata": {"baseline_name": ver["baseline_name"]}},
        "stages": {"status": "AVAILABLE" if stages else "PARTIAL", "stages": [{"stage_id": s["stage_id"], "stage_code": s["stage_code"], "stage_name": s["stage_name"], "status": s["status"],
                                                                              "progress_pct": 0.0, "sequence_order": s["sequence_order"], "parent_stage_id": None} for s in stages]},
        "activities": {"status": "AVAILABLE", "activities": activities},
        "execution_evidence": sect("evidence", [{"event_id": str(r["event_id"]), "event_type": shapes.EVENT_OUT.get(r["event_type"]), "event_date": shapes.iso(r["event_date"]),
                                                 "document_id": str(r["document_id"]) if r["document_id"] else None, "document_name": r["file_name"], "source_reference": None,
                                                 "raw_claim_text": r["raw_claim_text"], "matched_activity_id": ext.get(r["matched_activity_uid"]), "claimed_pct": shapes.num(r["claimed_pct"]),
                                                 "claimed_quantity": shapes.num(r["qty"])} for r in claims], not sup),
        "matching": sect("candidates", [{"candidate_id": str(r["candidate_id"]), "event_id": str(r["event_id"]), "activity_id": ext.get(r["activity_uid"]), "rank_order": r["rank_order"],
                                         "match_tier": r["match_tier"], "composite_confidence": shapes.num(r["composite_confidence"]), "semantic_score": shapes.num(r["semantic_score"]),
                                         "fuzzy_score": shapes.num(r["fuzzy_score"]), "supporting_signals": r["supporting_signals"], "disqualifying_signals": r["disqualifying_signals"]} for r in cands], not sup),
        "validation": (sect("issues", [{"issue_id": str(r["validation_id"]), "event_id": str(r["event_id"]), "rule_code": r["rule_code"], "severity": r["severity"], "description": r["description"]} for r in vals], not sup)
                       | {"conflict_count": n_conf}),
        "human_decisions": sect("decisions", [{"decision_id": str(r["decision_id"]), "event_id": str(r["event_id"]), "activity_id": ext.get(r["selected_activity_uid"]), "action": r["action"],
                                               "approved_pct": shapes.num(r["approved_pct"]), "approved_qty": None, "planner_id": str(r["decided_by"]), "justification": r["justification"],
                                               "decided_at": shapes.iso(r["decided_at"])} for r in decs], not sup),
        "approved_actuals": {"status": "AVAILABLE", "actuals": [{"actual_id": a["_uid"] and str(a["_uid"]), "activity_id": a["activity_id"], "schedule_id": str(version), "actual_start": a["actual_start"],
                                                                  "actual_finish": a["actual_finish"], "actual_pct_complete": a["_pct"], "actual_quantity": a["_actual_qty"], "decision_id": None,
                                                                  "is_reopened": a["_reopen"] == "APPROVED", "created_at": None} for a in acts if a["_pct"] > 0 or a["actual_start"]]},
        "reopen_history": {"status": "AVAILABLE" if reopens else "PARTIAL", "records": [{"activity_id": ext.get(r["activity_uid"]), "reopen_status": r["status"],
                                                                                          "workflow_condition": "REWORK_IN_PROGRESS" if r["status"] == "APPROVED" else "NONE",
                                                                                          "historical_revisions_count": 0, "current_actual": None, "historical_snapshots": [r["completed_snapshot"]]} for r in reopens]},
        "progress": {"status": "AVAILABLE", "project_progress_pct": float(overall["physical_pct"] or 0) if overall else 0.0, "schedule_progress_pct": float(overall["physical_pct"] or 0) if overall else 0.0,
                     "calculation_basis": "LEDGER_WEIGHTED", "breakdowns": {}},
        "impact": {"status": "NOT_AVAILABLE", "scenarios": []},
        "audit_chain": {"status": "AVAILABLE", "verification": verification, "recent_logs": [{"log_id": r["log_id"], "entity_type": r["entity_type"], "entity_id": r["entity_id"], "action": r["action"],
                                                                                                "actor_id": str(r["actor_id"]) if r["actor_id"] else None, "current_hash": r["current_hash"],
                                                                                                "timestamp": shapes.iso(r["occurred_at"])} for r in logs]},
    }
    names = [k for k in sections if k not in ("project", "schedule")]
    complete = [k for k in names if sections[k].get("status") == "AVAILABLE"]
    partial = [k for k in names if sections[k].get("status") in ("PARTIAL", "RESTRICTED")]
    missing = [k for k in names if sections[k].get("status") == "NOT_AVAILABLE"]
    return {"dossier_id": f"dossier-{uuid.uuid4()}", "project_id": str(ctx.project_id), "schedule_id": str(version), "activity_id": activity_id, "generated_at": now,
            "scope": "ACTIVITY" if activity_id else "PROJECT", **sections, "extensions": {},
            "completeness": {"overall_status": "AVAILABLE" if not partial and not missing else "PARTIAL", "complete_sections": complete, "partial_sections": partial, "missing_sections": missing,
                             "total_sections": len(names), "completed_count": len(complete)}}


def _version(ctx: Ctx):
    v = ctx.version_id or ctx.active_version_id
    if v is None:
        raise ApiError(409, "NO_ACTIVE_SCHEDULE", "The project has no active schedule")
    return v


@router.get("/dossier")
def project_dossier(ctx: Ctx = Depends(path_ctx(P.VIEW_AUDIT_TRAIL))):
    return json.loads(json.dumps(build(ctx, _version(ctx)), default=str))


@router.get("/schedules/{schedule_id}/dossier")
def schedule_dossier(ctx: Ctx = Depends(path_ctx(P.VIEW_AUDIT_TRAIL))):
    return json.loads(json.dumps(build(ctx, _version(ctx)), default=str))


@router.get("/schedules/{schedule_id}/activities/{activity_id}/dossier")
def activity_dossier(activity_id: str, ctx: Ctx = Depends(path_ctx(P.VIEW_AUDIT_TRAIL))):
    return json.loads(json.dumps(build(ctx, _version(ctx), activity_id), default=str))


@router.get("/dossier/audit-verification")
def audit_verification(ctx: Ctx = Depends(path_ctx(P.VIEW_AUDIT_TRAIL))):
    with actor_tx(ctx.actor, readonly=True) as c:
        return _verification(c)
