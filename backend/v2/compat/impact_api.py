"""Impact preview / compound impact / watch list on v2 data. The ORIGINAL engines run unchanged:
  * backend/routers/schedule.py query_impact_preview (the A1 single-activity CPM preview) reads the legacy read model through the connection's search_path;
  * backend/services/impact_service.py ImpactService (compound multi-seed propagation, float absorption, severity) gets the schedule graph from v2 through a
    context-local seam (the legacy repository's loader is only replaced for the request that set it).
Read-only: nothing is written and nothing is approved."""
from __future__ import annotations

import contextvars
import uuid
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import permissions as P
from ..domain.common import actor_tx
from ..domain.workflow import workflow_flags
from ..errors import ApiError
from . import schedule_api
from .checks_api import legacy_view_mode
from .context import Ctx, legacy_ctx, path_ctx

router = APIRouter(tags=["legacy-contract: impact"])
_GRAPH: contextvars.ContextVar = contextvars.ContextVar("v2_impact_graph", default=None)
_PATCHED = False


class _Shim:
    """what ImpactService reads from a ScheduleContext"""
    def __init__(self, ctx: Ctx, version):
        self.role, self.project_id, self.schedule_id, self.user_id = ctx.access.role, ctx.project_id, str(version), ctx.user.id


def _install_seam() -> None:
    global _PATCHED
    if _PATCHED:
        return
    from backend.repositories.impact_repo import ProjectImpactRepository
    original = ProjectImpactRepository.get_schedule_graph_data

    def get_schedule_graph_data(context):
        g = _GRAPH.get()
        return g if g is not None else original(context)
    ProjectImpactRepository.get_schedule_graph_data = staticmethod(get_schedule_graph_data) if isinstance(original, staticmethod) else classmethod(lambda cls, context: get_schedule_graph_data(context))
    _PATCHED = True


def _d(v) -> Optional[date]:
    from datetime import datetime
    if isinstance(v, str):
        return datetime.strptime(v[:10], "%Y-%m-%d").date()
    return v


def graph(c, ctx: Ctx, version) -> Tuple[List[dict], List[dict], List[dict]]:
    """(activities, dependencies, stages) in the shape the original loader returns"""
    acts = schedule_api.activities(c, ctx, version)
    flags = workflow_flags(c, ctx.project_id, version)
    seq = {s["stage_id"]: s for s in schedule_api.stages(c, ctx, version)}
    out = []
    for a in acts:
        st = seq.get(a["stage_id"])
        out.append({"activity_id": a["activity_id"], "schedule_id": str(version), "project_id": str(ctx.project_id), "stage_id": a["stage_id"], "activity_name": a["activity_name"],
                    "wbs_code": a["wbs_code"], "discipline": a["discipline"], "location": a["location"], "planned_start": _d(a["planned_start"]), "planned_finish": _d(a["planned_finish"]),
                    "planned_quantity": a["planned_quantity"], "total_float": a["total_float"], "is_critical": a["is_critical"], "weight_factor": a["weight"] or 1.0,
                    "quality_gate_required": False, "stage_name": a["stage_name"], "stage_sequence_order": st["sequence_order"] if st else None,
                    "actual_start": _d(a["actual_start"]), "actual_finish": _d(a["actual_finish"]), "actual_pct_complete": a["_pct"], "actual_quantity": a["_actual_qty"],
                    **flags.get(a["_uid"], {})})
    deps = c.execute("select d.dependency_id, p.external_activity_id pred, s.external_activity_id succ, d.relationship_type, d.lag_days from schedule_dependencies d "
                     "join baseline_activities p on p.version_id = d.version_id and p.activity_uid = d.predecessor_uid join baseline_activities s on s.version_id = d.version_id and s.activity_uid = d.successor_uid "
                     "where d.project_id = %s and d.version_id = %s", (ctx.project_id, version)).fetchall()
    dependencies = [{"dependency_id": str(r["dependency_id"]), "schedule_id": str(version), "predecessor_activity_id": r["pred"], "successor_activity_id": r["succ"],
                     "relationship_type": r["relationship_type"], "lag_days": float(r["lag_days"])} for r in deps]
    stages = [{"stage_id": s["stage_id"], "project_id": s["project_id"], "schedule_id": s["schedule_id"], "stage_code": s["stage_code"], "stage_name": s["stage_name"],
               "sequence_order": s["sequence_order"], "weight_pct": s["weight_pct"], "status": s["status"], "planned_start": _d(s["planned_start"]), "planned_finish": _d(s["planned_finish"])}
              for s in seq.values()]
    return out, dependencies, stages


def _run(ctx: Ctx, fn):
    """run an ImpactService call with the v2 graph supplied through the seam"""
    _install_seam()
    version = ctx.version_id or ctx.active_version_id
    if version is None:
        raise ApiError(409, "NO_ACTIVE_SCHEDULE", "The project has no active schedule: a Project Manager must activate one first")
    with actor_tx(ctx.actor, readonly=True) as c:
        g = graph(c, ctx, version)
    token = _GRAPH.set(g)
    try:
        return fn(_Shim(ctx, version), g)
    except Exception as e:
        from fastapi import HTTPException
        if isinstance(e, HTTPException):                     # the original service speaks HTTP errors; carry them over in the v2 error contract
            detail = e.detail if isinstance(e.detail, str) else str(e.detail)
            raise ApiError(e.status_code, "IMPACT_ERROR", detail)
        raise
    finally:
        _GRAPH.reset(token)


def _clean(obj):
    import json
    return json.loads(json.dumps(obj, default=str))


@router.get("/api/v1/schedule/{activity_id}/impact-preview")
def single_preview(activity_id: str, delay_days: int, schedule_id: Optional[str] = None, ctx: Ctx = Depends(legacy_ctx(P.VIEW_SCHEDULE))):
    """the original A1 single-activity preview (bounded multi-hop CPM propagation) on the read model"""
    from backend.routers.schedule import query_impact_preview
    version = ctx.version_id or ctx.active_version_id
    if version is None:
        raise ApiError(409, "NO_ACTIVE_SCHEDULE", "The project has no active schedule")
    if schedule_id and uuid.UUID(schedule_id) != version:
        with actor_tx(ctx.actor, readonly=True) as c:
            if c.execute("select 1 from schedule_versions where project_id = %s and version_id = %s", (ctx.project_id, schedule_id)).fetchone() is None:
                raise ApiError(403, "SCHEDULE_ACCESS_DENIED", "That schedule version does not belong to the selected project")
        version = uuid.UUID(schedule_id)
    from fastapi import HTTPException
    with actor_tx(ctx.actor, readonly=True) as c:
        with legacy_view_mode(c, ctx.project_id, version):
            try:
                return _clean(query_impact_preview(activity_id, delay_days, str(version), conn=c))
            except HTTPException as e:
                raise ApiError(e.status_code, "IMPACT_ERROR", e.detail if isinstance(e.detail, str) else str(e.detail))


@router.get("/api/v1/projects/{project_id}/schedules/{schedule_id}/activities/{activity_id}/impact-preview")
def compound_single(activity_id: str, delay_days: int, ctx: Ctx = Depends(path_ctx(P.VIEW_SCHEDULE))):
    from backend.services.impact_service import ImpactService
    return _clean(_run(ctx, lambda shim, g: ImpactService.get_single_activity_preview(shim, activity_id, delay_days)))


class SeedIn(BaseModel):
    activity_id: str
    delay_days: int
    reason: Optional[str] = None


class PreviewIn(BaseModel):
    seed_activities: List[SeedIn]
    name: Optional[str] = None


@router.post("/api/v1/projects/{project_id}/schedules/{schedule_id}/impact/preview")
def compound_preview(body: PreviewIn, ctx: Ctx = Depends(path_ctx(P.VIEW_SCHEDULE))):
    from backend.schemas.impact import SeedActivityInput
    from backend.services.impact_service import ImpactService
    seeds = [SeedActivityInput(**s.model_dump(exclude_none=True)) for s in body.seed_activities]
    return _clean(_run(ctx, lambda shim, g: ImpactService.evaluate_compound_impact(shim, seeds, scenario_name=body.name or "Compound Impact Preview")).model_dump())


@router.get("/api/v1/projects/{project_id}/schedules/{schedule_id}/impact/watchlist")
def watchlist(sensitivity_days: int = 1, ctx: Ctx = Depends(path_ctx(P.VIEW_SCHEDULE))):
    """activities in a problem state (blocked / quality hold / reopen / rework), ranked by how far a slip would propagate (the original watch-list logic)"""
    from backend.schemas.impact import SeedActivityInput
    from backend.services.impact_service import ImpactService
    from backend.services.stage_service import StageService

    def go(shim, g):
        rank = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
        out = []
        for a in g[0]:
            cond = StageService.get_workflow_condition(a) or "NONE"
            if cond == "NONE":
                continue
            res = ImpactService.evaluate_compound_impact(shim, [SeedActivityInput(activity_id=a["activity_id"], delay_days=sensitivity_days, reason=cond)], scenario_name=f"Watch {a['activity_id']}")
            stages: Dict[str, Dict[str, Any]] = {}
            for x in res.affected_activities:
                if x.stage_id:
                    st = stages.setdefault(str(x.stage_id), {"stage_id": str(x.stage_id), "stage_name": x.stage_name, "count": 0})
                    st["count"] += 1
            severity = res.severity.value if hasattr(res.severity, "value") else str(res.severity)
            out.append({"activity_id": a["activity_id"], "activity_name": a["activity_name"], "stage_id": str(a["stage_id"]) if a.get("stage_id") else None, "workflow_condition": cond,
                        "severity": severity, "sensitivity_days": sensitivity_days, "direct_successor_count": sum(1 for x in res.affected_activities if x.propagation_depth == 1),
                        "total_downstream_count": len(res.affected_activities), "critical_downstream_count": sum(1 for x in res.affected_activities if x.is_critical),
                        "stages": list(stages.values()), "project_completion_impact_days": res.project_completion_impact_days,
                        "primary_reason": ImpactService.WATCH_REASONS.get(cond, cond)})
        out.sort(key=lambda r: (-rank.get(r["severity"], 0), -r["total_downstream_count"], r["activity_id"]))
        return out
    return _clean(_run(ctx, go))
