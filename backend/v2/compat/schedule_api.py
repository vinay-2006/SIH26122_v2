"""Legacy schedule / WBS / stage / progress reads on v2 data. Stages are the schedule's STAGE nodes (v2 WBS); progress is the ledger-derived physical progress
(activity_progress_as_of), never a number computed in the browser."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends

from .. import permissions as P
from ..domain.common import actor_tx
from ..errors import ApiError, forbidden
from . import shapes
from .context import Ctx, legacy_ctx, path_ctx

router = APIRouter(prefix="/api/v1", tags=["legacy-contract: schedule"])

STATE = {"NOT_STARTED": "NOT_STARTED", "IN_PROGRESS": "IN_PROGRESS", "COMPLETED": "COMPLETED"}


def _ver(ctx: Ctx, schedule_id: Optional[str] = None) -> uuid.UUID:
    if schedule_id:
        try:
            sid = uuid.UUID(schedule_id)
        except ValueError:
            raise ApiError(404, "RESOURCE_NOT_FOUND", f"Schedule '{schedule_id}' not found.")
        return sid
    if ctx.version_id is None:
        raise ApiError(409, "NO_ACTIVE_SCHEDULE", "The project has no active schedule: a Project Manager must activate one first")
    return ctx.version_id


def _check_version(c, ctx: Ctx, version) -> None:
    if c.execute("select 1 from schedule_versions where project_id = %s and version_id = %s", (ctx.project_id, version)).fetchone() is None:
        raise forbidden("That schedule version does not belong to the selected project", "SCHEDULE_ACCESS_DENIED")


_ACTIVITY_SQL = """
select p.activity_uid, p.external_activity_id, p.activity_name, p.discipline_code, p.location, p.baseline_start, p.baseline_finish, p.total_float, p.physical_pct, p.actual_start,
       p.actual_finish, p.execution_state, p.weight, ba.asset_tag, ba.description, ba.is_critical, sw.wbs_code, stg.wbs_uid as stage_uid, stg.wbs_name as stage_name,
       q.n as n_measured, q.qty as m_qty, q.uom as m_uom,
       (select sum(r.cumulative_qty)::float8 from (select distinct on (a.assignment_uid) a.cumulative_qty from approved_resource_progress a where a.activity_uid = p.activity_uid
                                                    order by a.assignment_uid, a.entry_seq desc) r) as actual_qty,
       exists(select 1 from issues i where i.project_id = ba.project_id and i.activity_uid = p.activity_uid and i.status = 'ACTIVE' and i.blocks_work) as blocked
  from activity_progress_as_of(%(ver)s, current_date) p
  join baseline_activities ba on ba.version_id = %(ver)s and ba.activity_uid = p.activity_uid
  join schedule_wbs sw on sw.wbs_id = ba.wbs_id
  left join lateral (select st.wbs_uid, st.wbs_name from schedule_wbs st where st.version_id = %(ver)s and st.node_type = 'STAGE' and sw.wbs_path like st.wbs_path || '%%'
                      order by length(st.wbs_path) desc limit 1) stg on true
  left join lateral (select count(*) n, min(br.baseline_qty) qty, min(br.unit_of_measure) uom from baseline_resources br
                      where br.version_id = %(ver)s and br.activity_uid = p.activity_uid and br.measures_progress) q on true
 where p.project_id = %(project)s order by ba.sequence, p.external_activity_id
"""


def activities(c, ctx: Ctx, version) -> List[Dict[str, Any]]:
    _check_version(c, ctx, version)
    rows = c.execute(_ACTIVITY_SQL, {"ver": version, "project": ctx.project_id}).fetchall()
    out = []
    for r in rows:
        single = r["n_measured"] == 1
        pct = float(r["physical_pct"] or 0)
        state = r["execution_state"] if r["execution_state"] in STATE else ("COMPLETED" if pct >= 100 else ("IN_PROGRESS" if (pct > 0 or r["actual_start"]) else "NOT_STARTED"))
        out.append({"activity_id": r["external_activity_id"], "schedule_id": str(version), "activity_name": r["activity_name"], "wbs_code": r["wbs_code"], "discipline": r["discipline_code"],
                    "location": r["location"] or "", "asset_tag": r["asset_tag"], "planned_start": shapes.iso(r["baseline_start"]), "planned_finish": shapes.iso(r["baseline_finish"]),
                    "planned_quantity": shapes.num(r["m_qty"]) if single else None, "uom": r["m_uom"] if single else None, "baseline_pct_complete": 0.0,
                    "total_float": shapes.num(r["total_float"]), "is_critical": r["is_critical"], "execution_state": state, "actual_start": shapes.iso(r["actual_start"]),
                    "actual_finish": shapes.iso(r["actual_finish"]), "actual_pct_complete": pct, "stage_id": str(r["stage_uid"]) if r["stage_uid"] else None, "stage_name": r["stage_name"],
                    "is_stage_completed": False, "weight": shapes.num(r["weight"]), "contractor_id": None, "contractor_name": None, "work_package_id": None, "work_package_code": None,
                    "work_package_name": None, "_uid": r["activity_uid"], "_actual_qty": r["actual_qty"], "_blocked": r["blocked"], "_pct": pct})
    return out


def public(a: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in a.items() if not k.startswith("_")}


@router.get("/schedules/active")
def active_schedule(ctx: Ctx = Depends(legacy_ctx(P.VIEW_SCHEDULE))):
    if ctx.active_version_id is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "The project has no active schedule")
    return {"schedule_id": str(ctx.active_version_id), "project_id": str(ctx.project_id), "active": True}


@router.get("/schedules/{schedule_id}/activities")
def schedule_activities(schedule_id: str, ctx: Ctx = Depends(legacy_ctx(P.VIEW_SCHEDULE))):
    with actor_tx(ctx.actor, readonly=True) as c:
        return [public(a) for a in activities(c, ctx, _ver(ctx, schedule_id))]


@router.get("/schedules/{schedule_id}/activities/{activity_id}")
def schedule_activity(schedule_id: str, activity_id: str, ctx: Ctx = Depends(legacy_ctx(P.VIEW_SCHEDULE))):
    with actor_tx(ctx.actor, readonly=True) as c:
        for a in activities(c, ctx, _ver(ctx, schedule_id)):
            if a["activity_id"] == activity_id:
                return public(a)
    raise ApiError(404, "RESOURCE_NOT_FOUND", f"Activity '{activity_id}' not found.")


@router.get("/schedules/{schedule_id}/dependencies")
def schedule_dependencies(schedule_id: str, ctx: Ctx = Depends(legacy_ctx(P.VIEW_SCHEDULE))):
    ver = _ver(ctx, schedule_id)
    with actor_tx(ctx.actor, readonly=True) as c:
        _check_version(c, ctx, ver)
        rows = c.execute("select d.dependency_id, p.external_activity_id pred, s.external_activity_id succ, d.relationship_type, d.lag_days from schedule_dependencies d "
                         "join baseline_activities p on p.version_id = d.version_id and p.activity_uid = d.predecessor_uid join baseline_activities s on s.version_id = d.version_id and s.activity_uid = d.successor_uid "
                         "where d.project_id = %s and d.version_id = %s order by p.external_activity_id, s.external_activity_id", (ctx.project_id, ver)).fetchall()
    return [{"dependency_id": str(r["dependency_id"]), "schedule_id": str(ver), "predecessor_activity_id": r["pred"], "successor_activity_id": r["succ"],
             "relationship_type": r["relationship_type"], "lag_days": float(r["lag_days"])} for r in rows]


@router.get("/schedules/{schedule_id}/wbs-tree")
def wbs_tree(schedule_id: str, ctx: Ctx = Depends(legacy_ctx(P.VIEW_SCHEDULE))):
    """the flat WBS grouping (wbs_code -> activities) the original explorer shows"""
    ver = _ver(ctx, schedule_id)
    with actor_tx(ctx.actor, readonly=True) as c:
        acts = activities(c, ctx, ver)
    groups: Dict[str, list] = {}
    for a in acts:
        groups.setdefault(a["wbs_code"] or "UNASSIGNED", []).append({"activity_id": a["activity_id"], "planned_quantity": a["planned_quantity"]})
    return {"schedule_id": str(ver), "wbs_groups": [{"wbs_code": k, "activities": v} for k, v in groups.items()]}


# ---------------------------------------------------------------------------------------------------------------------------------- stages and progress
def stages(c, ctx: Ctx, version) -> List[Dict[str, Any]]:
    rows = c.execute(
        "select st.wbs_uid, st.wbs_code, st.wbs_name, st.sequence, min(ba.baseline_start) as ps, max(ba.baseline_finish) as pf from schedule_wbs st "
        "left join schedule_wbs sw on sw.version_id = st.version_id and sw.wbs_path like st.wbs_path || '%%' left join baseline_activities ba on ba.wbs_id = sw.wbs_id "
        "where st.version_id = %s and st.node_type = 'STAGE' group by st.wbs_uid, st.wbs_code, st.wbs_name, st.sequence order by st.sequence, st.wbs_name", (version,)).fetchall()
    out = []
    for i, r in enumerate(rows, 1):
        out.append({"stage_id": str(r["wbs_uid"]), "project_id": str(ctx.project_id), "schedule_id": str(version), "parent_stage_id": None, "stage_code": r["wbs_code"],
                    "stage_name": r["wbs_name"], "sequence_order": i, "weight_pct": None, "status": "NOT_STARTED", "planned_start": shapes.iso(r["ps"]),
                    "planned_finish": shapes.iso(r["pf"]), "gating_predecessor_stage_id": None})
    return out


@router.get("/projects/{project_id}/stages")
def list_stages(schedule_id: Optional[str] = None, ctx: Ctx = Depends(path_ctx(P.VIEW_SCHEDULE))):
    ver = _ver(ctx, schedule_id)
    with actor_tx(ctx.actor, readonly=True) as c:
        _check_version(c, ctx, ver)
        return stages(c, ctx, ver)


def _item(a: Dict[str, Any], total_w: float) -> Dict[str, Any]:
    w = a["weight"] or 0.0
    return {"activity_id": a["activity_id"], "activity_name": a["activity_name"], "stage_id": a["stage_id"], "canonical_state": a["execution_state"],
            "workflow_condition": "BLOCKED" if a["_blocked"] else "NONE", "is_reopened": False, "progress_pct": round(a["_pct"], 3), "actual_pct_complete": a["_pct"],
            "actual_quantity": a["_actual_qty"], "planned_quantity": a["planned_quantity"], "weight_factor": w,
            "weighted_contribution": round((w / total_w * a["_pct"]) if total_w else 0.0, 4)}


@router.get("/projects/{project_id}/schedules/{schedule_id}/progress/breakdown")
def progress_breakdown(ctx: Ctx = Depends(path_ctx(P.VIEW_SCHEDULE))):
    ver = _ver(ctx)
    with actor_tx(ctx.actor, readonly=True) as c:
        acts = activities(c, ctx, ver)
        sts = stages(c, ctx, ver)
        proj = c.execute("select project_name from projects where project_id = %s", (ctx.project_id,)).fetchone()
        overall = c.execute("select physical_pct from project_progress_as_of(%s, current_date)", (ver,)).fetchone()
        basis = c.execute("select weight_basis from version_activity_weights(%s) limit 1", (ver,)).fetchone()
    total_w = sum(a["weight"] or 0.0 for a in acts)
    by_stage: Dict[Optional[str], list] = {}
    for a in acts:
        by_stage.setdefault(a["stage_id"], []).append(a)
    out_stages = []
    for s in sts:
        members = by_stage.get(s["stage_id"], [])
        w = sum(m["weight"] or 0.0 for m in members)
        pct = (sum((m["weight"] or 0.0) * m["_pct"] for m in members) / w) if w else 0.0
        out_stages.append({"stage_id": s["stage_id"], "stage_name": s["stage_name"], "sequence_order": s["sequence_order"], "weight_pct": round(w / total_w * 100, 3) if total_w else None,
                           "progress_pct": round(pct, 3), "weighted_contribution": round((w / total_w * pct) if total_w else 0.0, 4), "activity_count": len(members),
                           "completed_count": sum(1 for m in members if m["execution_state"] == "COMPLETED"), "activities": [_item(m, total_w) for m in members]})
    unassigned = [_item(a, total_w) for a in by_stage.get(None, [])]
    return {"project_id": str(ctx.project_id), "project_name": proj["project_name"], "schedule_id": str(ver), "overall_progress_pct": float(overall["physical_pct"] or 0) if overall else 0.0,
            "calculation_basis": (basis or {}).get("weight_basis") or "UNIT", "stages": out_stages, "unassigned_activities": unassigned}
