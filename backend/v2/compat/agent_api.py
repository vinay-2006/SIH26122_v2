"""Project Intelligence: the supervising agent on v2 data.

The ORIGINAL reasoning layer runs unchanged (backend/agents/supervising_agent.py: prompts, the optional language-model synthesis, JSON validation and the deterministic
degraded briefing that reflects engine facts only). What is replaced is the DATA GATHERING: the original context builder reads the demo's tables, so a context-local seam
swaps in a v2 builder that produces the same context dictionary from v2 data (ledger-derived progress, gates, issues, reopens, claims counts, institutional memory).
The agent is advisory and read-only: it cannot approve, reject or change anything. Content is scoped by role: only a Supervisor's context carries claim samples; a Site
Engineer or Project Manager gets counts and project facts, never other people's claim content."""
from __future__ import annotations

import contextvars
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends

from .. import audit, permissions as P
from ..domain.common import PM, SE, SUP, actor_tx
from ..errors import ApiError
from . import issues_api, schedule_api
from ..domain import knowledge as dk
from . import grounding
from .context import Ctx, path_ctx

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["legacy-contract: agent"])
_BUILDER: contextvars.ContextVar = contextvars.ContextVar("v2_agent_builder", default=None)
_PATCHED = False


class _Shim:
    """what the agent reads from a ProjectContext"""
    def __init__(self, ctx: Ctx):
        self.project_id, self.user_id, self.role = ctx.project_id, ctx.user.id, ctx.access.role


def _install_seam() -> None:
    global _PATCHED
    if _PATCHED:
        return
    from backend.agents.context_builder import ContextBuilder
    orig_brief, orig_query = ContextBuilder.build_briefing_context.__func__, ContextBuilder.build_query_context.__func__

    def brief(cls, context):
        b = _BUILDER.get()
        return b["briefing"]() if b else orig_brief(cls, context)

    def query(cls, context, query, mode=None, stage_id=None, activity_id=None, contractor_id=None):
        b = _BUILDER.get()
        return b["query"](query, mode, stage_id, activity_id) if b else orig_query(cls, context, query, mode, stage_id, activity_id, contractor_id)
    ContextBuilder.build_briefing_context = classmethod(brief)
    ContextBuilder.build_query_context = classmethod(query)
    _PATCHED = True


# ---------------------------------------------------------------------------------------------------------------------------------- v2 context
def _audit_verification(c) -> Dict[str, Any]:
    v = audit.verify_chain(c)
    return {"status": "EMPTY" if v["entries"] == 0 else ("VALID" if v["valid"] else "BROKEN"), "records_checked": v["entries"], "legacy_records": 0, "v7_records": v["entries"],
            "failure_type": None if v["valid"] else "TAMPERED_CONTENT", "broken_at_log_id": (v["broken_at"] or [None])[0],
            "reason": None if v["valid"] else "An audit record does not match its hash link", "verified_at": datetime.now(timezone.utc).isoformat()}


def _memory(ctx: Ctx, c, query: str, activity_id: Optional[str] = None, k: int = 3) -> List[Dict[str, Any]]:
    from backend.memory.schemas import MemoryFilter
    recs = issues_api._memory_records(c, ctx)
    ranked, _mode = issues_api._rank(recs, query, MemoryFilter(activity_id=activity_id) if activity_id else None, k)
    return [r.model_dump(mode="json") for r in ranked]


def build_context(ctx: Ctx) -> Dict[str, Callable]:
    version = ctx.active_version_id
    sup = ctx.access.role == SUP

    def gather():
        with actor_tx(ctx.actor, readonly=True) as c:
            proj = c.execute("select project_id, project_code, project_name, lifecycle_status from projects where project_id = %s", (ctx.project_id,)).fetchone()
            acts = schedule_api.activities(c, ctx, version) if version else []
            stages = schedule_api.stages(c, ctx, version) if version else []
            overall = c.execute("select physical_pct from project_progress_as_of(%s, current_date)", (version,)).fetchone() if version else None
            gates = c.execute("select g.quality_gate_id, g.gate_name, g.gate_type, g.status, g.stage_wbs_uid, ba.external_activity_id from quality_gates g left join baseline_activities ba "
                              "on ba.activity_uid = g.activity_uid and ba.version_id = %s where g.project_id = %s and g.required and g.status not in ('PASSED','WAIVED')", (version, ctx.project_id)).fetchall()
            n_claims = c.execute("select count(*) n from execution_events where project_id = %s and status in ('EXTRACTED','MATCHED','VALIDATED','DISPUTED')", (ctx.project_id,)).fetchone()["n"]
            n_reopens = c.execute("select count(*) n from activity_reopens where project_id = %s and status = 'REQUESTED'", (ctx.project_id,)).fetchone()["n"]
            sample = []
            if sup:
                sample = [{"event_id": str(r["event_id"]), "status": r["status"], "claimed_pct": shapes_num(r["claimed_pct"]), "raw_claim_text": (r["raw_claim_text"] or "")[:200]}
                          for r in c.execute("select event_id, status, claimed_pct, raw_claim_text from execution_events where project_id = %s and status in ('EXTRACTED','MATCHED','VALIDATED','DISPUTED') "
                                             "order by priority_score desc nulls last, created_at limit 5", (ctx.project_id,)).fetchall()]
            incidents = [{"issue_id": str(r["issue_id"]), "title": r["title"], "category_code": r["category_code"], "severity": r["severity"], "status": r["status"]}
                         for r in c.execute("select issue_id, title, category_code, severity, status from issues where project_id = %s order by created_at desc limit 5", (ctx.project_id,)).fetchall()]
            memories = _memory(ctx, c, "quality hold delay inspection dispute") if (gates or any(a["_blocked"] for a in acts)) else []
            verification = _audit_verification(c) if ctx.access.role in (SUP, PM) else None
        know = [{"section": e["section"], "title": e["title"], "provenance": e["provenance"], "excerpt": " ".join(e["body"].split())[:240]} for e in dk.list_entries(ctx.actor)
                if e["section"] in ("OVERVIEW", "SCOPE", "MILESTONES")][:8]
        brief = lambda a: {"activity_id": a["activity_id"], "activity_name": a["activity_name"], "stage_id": a["stage_id"], "workflow_condition":                      # noqa: E731
                           "REOPEN_REQUESTED" if a["_reopen"] == "REQUESTED" else ("REWORK_IN_PROGRESS" if a["_reopen"] == "APPROVED" else ("QUALITY_HOLD" if a["_qhold"] else "BLOCKED")),
                           "canonical_state": a["execution_state"], "actual_pct_complete": a["_pct"], "reopen_status": a["_reopen"]}
        blocked = [brief(a) for a in acts if a["_blocked"] or a["_qhold"] or a["_reopen"] == "REQUESTED"]
        rework = [brief(a) for a in acts if a["_reopen"] == "APPROVED"]
        delayed = [{"activity_id": a["activity_id"], "activity_name": a["activity_name"], "is_critical": a["is_critical"], "total_float": a["total_float"], "actual_pct_complete": a["_pct"]}
                   for a in acts if a["is_critical"] and a["execution_state"] != "COMPLETED"][:10]
        holds = [{"quality_gate_id": str(g["quality_gate_id"]), "gate_name": g["gate_name"], "gate_type": g["gate_type"], "status": g["status"], "stage_id": str(g["stage_wbs_uid"]) if g["stage_wbs_uid"] else None,
                  "activity_id": g["external_activity_id"]} for g in gates]
        return {
            "project": {"project_id": str(proj["project_id"]), "project_code": proj["project_code"], "project_name": proj["project_name"], "status": proj["lifecycle_status"]},
            "active_schedule": {"schedule_id": str(version)} if version else None,
            "overall_progress": {"overall_progress_pct": float(overall["physical_pct"]) if overall else 0.0, "progress_pct": float(overall["physical_pct"]) if overall else 0.0},
            "stages": [{"stage_id": s["stage_id"], "stage_name": s["stage_name"], "sequence_order": s["sequence_order"], "planned_start": s["planned_start"], "planned_finish": s["planned_finish"]} for s in stages][:15],
            "counts": {"quality_holds": len(holds), "blocked_activities": sum(1 for x in blocked if x["workflow_condition"] == "BLOCKED"),
                       "quality_hold_activities": sum(1 for x in blocked if x["workflow_condition"] == "QUALITY_HOLD"), "rework_activities": len(rework)},
            "critical_activities": delayed, "blocked_activities": blocked[:10], "rework_activities": rework[:10], "quality_holds": holds[:10], "contractors": [],
            "review_queue": {"total_claims": n_claims, "total_reopens": n_reopens, "pending_claims_sample": sample, "pending_reopens_sample": []},
            "recent_incidents": incidents, "relevant_historical_incidents": memories, "schedule_impact": {}, "audit_verification": verification,
            "project_knowledge": know,
        }

    def query(text: str, mode, stage_id: Optional[str], activity_id: Optional[str]):
        base = gather()
        out: Dict[str, Any] = {"project": base["project"], "query": text, "mode": mode.value if mode else "ON_DEMAND_BRIEFING"}
        if activity_id:
            with actor_tx(ctx.actor, readonly=True) as c:
                hit = next((a for a in schedule_api.activities(c, ctx, version) if a["activity_id"] == activity_id), None) if version else None
                out["activity_details"] = None if hit is None else {"activity_id": hit["activity_id"], "activity_name": hit["activity_name"], "canonical_state": hit["execution_state"],
                                                                    "workflow_condition": "QUALITY_HOLD" if hit["_qhold"] else ("BLOCKED" if hit["_blocked"] else "NONE"),
                                                                    "actual_pct_complete": hit["_pct"], "planned_start": hit["planned_start"], "planned_finish": hit["planned_finish"]}
                out["historical_references"] = _memory(ctx, c, text, activity_id)
            return out
        if mode is not None and mode.value == "INCIDENT_INTELLIGENCE":
            with actor_tx(ctx.actor, readonly=True) as c:
                out["current_project_incidents"] = base["recent_incidents"]
                out["historical_institutional_lessons"] = _memory(ctx, c, text, None, 5)
            return out
        if mode is not None and mode.value == "REVIEW_QUEUE":
            if not sup:
                raise ApiError(403, "PERMISSION_DENIED", "Only a Supervisor may query the review queue")
            out["review_queue"] = base["review_queue"]
            return out
        return base
    return {"briefing": gather, "query": query}


def shapes_num(v):
    return None if v is None else float(v)


def _log(ctx: Ctx, action: str, after: Dict[str, Any]) -> None:
    with actor_tx(ctx.actor, write=True) as c:
        audit.log(c, project_id=ctx.project_id, actor_id=ctx.user.id, role=ctx.access.role, action=action, entity_type="SUPERVISING_AGENT", entity_id=ctx.project_id, after=after)


def _run(ctx: Ctx, fn):
    _install_seam()
    token = _BUILDER.set(build_context(ctx))
    try:
        return fn()
    finally:
        _BUILDER.reset(token)


@router.get("/agent/briefing")
def briefing(ctx: Ctx = Depends(path_ctx(P.VIEW_PROJECT_INTELLIGENCE))):
    """an executive interpretation of the current execution state; the language model (when configured) only phrases engine facts, otherwise the deterministic briefing is returned.
    The authored project context is returned beside it (with where each entry comes from) so the reader can tell context from live data."""
    from backend.agents.supervising_agent import SupervisingAgent
    b = _run(ctx, lambda: SupervisingAgent.generate_briefing(_Shim(ctx)))
    _log(ctx, "AGENT_BRIEFING_GENERATED", {"briefing_id": b.briefing_id, "agent_status": b.agent_status.value, "findings_count": len(b.findings)})
    out = b.model_dump(mode="json")
    entries = dk.list_entries(ctx.actor)
    out["project_context"] = [dict(dk.cite(e), excerpt=" ".join(e["body"].split())[:360]) for e in entries if e["section"] in ("OVERVIEW", "SCOPE", "MILESTONES")][:6]
    out["project_context_available"] = bool(entries)
    out["sources"] = [grounding._src("briefing", "Live project state: progress, blocked work, review queue, incidents")] + [dk.cite(e) for e in entries if e["section"] in ("OVERVIEW", "SCOPE", "MILESTONES")][:6]
    return out


@router.post("/agent/query")
def query(body: Dict[str, Any], ctx: Ctx = Depends(path_ctx(P.VIEW_PROJECT_INTELLIGENCE))):
    """An answer built only from the project's live data and its authored knowledge, each statement tied to its source. Deterministic: no figure is generated.
    Claim wording is never included (the roles that reach this page may not read it); counts are."""
    from backend.agents.schemas import AgentQueryRequest, AgentQueryResponse, AgentStatus, EvidenceReference
    req = AgentQueryRequest(**body)
    g = grounding.compose(ctx, req.query, allow_claims=False, activity_id=req.activity_id)
    evidence = [EvidenceReference(entity_type=s["kind"], entity_id=str(s.get("knowledge_id") or s.get("ref")), source_type=s.get("provenance") or "LIVE", reference_code=s.get("section_label") or s.get("label"),
                                  details=s.get("title") or (f"computed {s['as_of']}" if s.get("as_of") else None)) for s in g["sources"]]
    r = AgentQueryResponse(project_id=str(ctx.project_id), query=req.query, generated_at=datetime.now(timezone.utc).isoformat(), agent_status=AgentStatus.DEGRADED, answer=g["answer"],
                           findings=[], evidence=evidence, recommendations=[], context_used={"keys": ["live_data", "project_knowledge"], "intents": g["intents"], "answered": g["answered"]})
    _log(ctx, "AGENT_QUERY_EXECUTED", {"query": req.query[:300], "agent_status": r.agent_status.value, "answered": g["answered"], "sources": len(g["sources"])})
    out = r.model_dump(mode="json")
    out["sources"] = g["sources"]
    out["answer_source"] = "DETERMINISTIC_GROUNDED"
    return out


@router.get("/agent/findings")
def findings(ctx: Ctx = Depends(path_ctx(P.VIEW_PROJECT_INTELLIGENCE))):
    from backend.agents.supervising_agent import SupervisingAgent
    return [f.model_dump(mode="json") for f in _run(ctx, lambda: SupervisingAgent.generate_briefing(_Shim(ctx))).findings]


@router.get("/agent/review-queue")
def review_queue_intelligence(ctx: Ctx = Depends(path_ctx(P.REVIEW_CLAIMS))):
    """unreviewed claims and reopen requests currently awaiting a Supervisor (Supervisor only: it carries claim samples)"""
    ctxd = build_context(ctx)["briefing"]()
    q = ctxd["review_queue"]
    return {"total_pending_claims": q["total_claims"], "total_pending_reopens": q["total_reopens"], "claims": q["pending_claims_sample"], "reopens": []}
