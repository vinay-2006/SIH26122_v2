"""Progress rollups for dashboards. Every number is derived from the approved ledgers by the as-of SQL functions (migration 0013); nothing here
adds, converts or estimates a quantity. Planned progress is a documented APPROXIMATION and every payload says so."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from ..db import tx
from ..errors import ApiError
from .claims import claim_counts
from .common import actor_tx, PM, SE, SUP, ProjectActor, active_version

WEIGHT_BASIS_EXPLANATION = {
    "MANHOURS": "Each activity counts in proportion to its baseline man-hours. Every activity in this schedule version has man-hours, so all weights share one scale.",
    "DURATION": "Some activities have no baseline man-hours, so every activity counts in proportion to its baseline duration (working days) instead. One scale is used for the whole version; milestones have zero duration and carry no weight.",
    "UNIT": "No usable man-hours or durations exist, so every activity counts equally.",
}
PLANNED_METHOD_NOTE = ("Planned progress is an APPROXIMATION: each activity is assumed to progress linearly between its baseline start and finish dates (a milestone jumps at its finish), "
                       "combined with the same weights as actual progress. It is not a CPM time-phased plan and not cost-based earned value.")
SPI_NOTE = "spi_approx = actual % divided by approximate planned %. It is a schedule indicator only, not a cost-based earned-value index."
MAX_TIMELINE_POINTS = 400


def _scope(c, actor: ProjectActor, version_id=None, as_of: Optional[date] = None):
    if version_id is None:
        v = active_version(c, actor.project_id)
    else:
        v = c.execute("select version_id, version_no, baseline_name, data_date from schedule_versions where project_id = %s and version_id = %s", (actor.project_id, version_id)).fetchone()
        if v is None:
            raise ApiError(404, "VERSION_NOT_FOUND", "No such schedule version in this project")
    return v, as_of or date.today()


def _own_claim_counts(c, actor: ProjectActor) -> Dict[str, int]:
    rows = c.execute("select status, count(*) as n from execution_events where project_id = %s and filed_by = %s group by status", (actor.project_id, actor.user_id)).fetchall()
    out = {r["status"]: r["n"] for r in rows}
    out["pending_total"] = sum(out.get(s, 0) for s in ("REPORTED", "EXTRACTED", "MATCHED", "VALIDATED", "DISPUTED"))
    return out


def project_summary(actor: ProjectActor, as_of: Optional[date] = None, version_id=None) -> Dict[str, Any]:
    with actor_tx(actor, readonly=True) as c:
        v, asof = _scope(c, actor, version_id, as_of)
        p = c.execute("select * from project_progress_as_of(%s, %s)", (v["version_id"], asof)).fetchone()
        pr = c.execute("select project_name, lifecycle_status from projects where project_id = %s", (actor.project_id,)).fetchone()
        issues = c.execute("select count(*) filter (where status = 'ACTIVE') as active, count(*) filter (where status = 'ACTIVE' and blocks_work) as blocking, count(*) filter (where status = 'RESOLVED') as resolved "
                           "from issues where project_id = %s and (%s <> 'SITE_ENGINEER' or reported_by = %s)", (actor.project_id, actor.role, actor.user_id)).fetchone()
    claims = _own_claim_counts_safe(actor)
    return {
        "project": {"project_id": actor.project_id, "name": pr["project_name"], "lifecycle_status": pr["lifecycle_status"]},
        "version": {"version_id": v["version_id"], "version_no": v["version_no"], "baseline_name": v["baseline_name"], "data_date": v["data_date"]},
        "as_of": asof, "data_date": v["data_date"],
        "physical_pct": p["physical_pct"], "planned_pct": p["planned_pct"], "spi_approx": p["spi_approx"],
        "activities": {"total": p["activities"], "completed": p["completed"], "in_progress": p["in_progress"], "not_started": p["not_started"]},
        "any_overrun": p["any_overrun"],
        "weight_basis": p["weight_basis"], "weight_basis_explanation": WEIGHT_BASIS_EXPLANATION.get(p["weight_basis"], ""),
        "planned_method_note": PLANNED_METHOD_NOTE, "spi_note": SPI_NOTE,
        "issues": {"active": issues["active"], "blocking": issues["blocking"], "resolved": issues["resolved"]},
        "claims": claims,
        "source": "approved ledgers only: pending, rejected, withdrawn and disputed claims are not included",
    }


def _own_claim_counts_safe(actor: ProjectActor) -> Dict[str, int]:
    if actor.role == SE:
        with actor_tx(actor, readonly=True) as c:
            return {"scope": "own", **_own_claim_counts(c, actor)}
    return {"scope": "aggregate", **claim_counts(actor)}                   # supervisors and project managers: counts only, never content


def wbs_progress(actor: ProjectActor, as_of: Optional[date] = None, version_id=None) -> List[dict]:
    with actor_tx(actor, readonly=True) as c:
        v, asof = _scope(c, actor, version_id, as_of)
        return c.execute("select * from wbs_progress_as_of(%s, %s)", (v["version_id"], asof)).fetchall()


def stage_progress(actor: ProjectActor, as_of: Optional[date] = None, version_id=None) -> List[dict]:
    return [r for r in wbs_progress(actor, as_of, version_id) if r["node_type"] == "STAGE"]


def discipline_progress(actor: ProjectActor, as_of: Optional[date] = None, version_id=None) -> List[dict]:
    with actor_tx(actor, readonly=True) as c:
        v, asof = _scope(c, actor, version_id, as_of)
        return c.execute("select * from discipline_progress_as_of(%s, %s)", (v["version_id"], asof)).fetchall()


def activity_progress(actor: ProjectActor, as_of: Optional[date] = None, version_id=None, discipline: Optional[str] = None, state: Optional[str] = None,
                      wbs_prefix: Optional[str] = None, limit: int = 500, offset: int = 0) -> List[dict]:
    with actor_tx(actor, readonly=True) as c:
        v, asof = _scope(c, actor, version_id, as_of)
        return c.execute("select * from activity_progress_as_of(%s, %s) a where (%s::text is null or a.discipline_code = %s) and (%s::text is null or a.execution_state = %s) "
                         "and (%s::text is null or a.wbs_path like %s || '%%') order by a.baseline_start, a.external_activity_id limit %s offset %s",
                         (v["version_id"], asof, discipline, discipline, state, state, wbs_prefix, wbs_prefix, min(limit, 2000), offset)).fetchall()


def timeline(actor: ProjectActor, date_from: Optional[date] = None, date_to: Optional[date] = None, step_days: int = 7, version_id=None) -> Dict[str, Any]:
    with actor_tx(actor, readonly=True) as c:
        v, _ = _scope(c, actor, version_id)
        span = c.execute("select min(baseline_start) as s, max(baseline_finish) as f from baseline_activities where version_id = %s", (v["version_id"],)).fetchone()
        d0 = date_from or span["s"]
        d1 = date_to or (min(span["f"], date.today()) if span["f"] else None)          # default end: today, but not past the last baseline finish
        if d0 is None or d1 is None or d1 < d0:
            raise ApiError(422, "BAD_RANGE", "The timeline range is empty")
        step = max(1, int(step_days))
        if ((d1 - d0).days // step) + 1 > MAX_TIMELINE_POINTS:
            raise ApiError(422, "TOO_MANY_POINTS", f"At most {MAX_TIMELINE_POINTS} points: widen the step")
        pts = c.execute("select * from progress_timeline(%s, %s, %s, %s)", (v["version_id"], d0, d1, step)).fetchall()
    return {"version_id": v["version_id"], "data_date": v["data_date"], "points": pts, "planned_method_note": PLANNED_METHOD_NOTE, "spi_note": SPI_NOTE}


def compare_versions(actor: ProjectActor, old_version_id, new_version_id, as_of: Optional[date] = None) -> Dict[str, Any]:
    """planned vs actual across two schedule versions by STABLE activity identity. Progress is never carried across a split, merge or retirement:
    activities that left the new version are listed as retired scope with the history they keep."""
    with actor_tx(actor, readonly=True) as c:
        old, asof = _scope(c, actor, old_version_id, as_of)
        new, _ = _scope(c, actor, new_version_id, as_of)
        po = {r["activity_uid"]: r for r in c.execute("select * from activity_progress_as_of(%s, %s)", (old["version_id"], asof)).fetchall()}
        pn = {r["activity_uid"]: r for r in c.execute("select * from activity_progress_as_of(%s, %s)", (new["version_id"], asof)).fetchall()}
        so = c.execute("select * from project_progress_as_of(%s, %s)", (old["version_id"], asof)).fetchone()
        sn = c.execute("select * from project_progress_as_of(%s, %s)", (new["version_id"], asof)).fetchone()
        lineage = c.execute("select relation, from_activity_uid, to_activity_uid, fraction from activity_lineage where project_id = %s and version_id = %s", (actor.project_id, new["version_id"])).fetchall()
        has_history = {r["activity_uid"] for r in c.execute("select distinct activity_uid from approved_resource_progress where project_id = %s union select distinct activity_uid from approved_activity_progress where project_id = %s",
                                                          (actor.project_id, actor.project_id)).fetchall()}
    common = [{"activity_uid": u, "old_external_id": po[u]["external_activity_id"], "new_external_id": pn[u]["external_activity_id"],
               "actual_pct_old_scope": po[u]["physical_pct"], "actual_pct_new_scope": pn[u]["physical_pct"],
               "planned_pct_old": po[u]["planned_pct"], "planned_pct_new": pn[u]["planned_pct"],
               "baseline_finish_shift_days": (pn[u]["baseline_finish"] - po[u]["baseline_finish"]).days} for u in po if u in pn]
    retired = [{"activity_uid": u, "external_id": po[u]["external_activity_id"], "last_actual_pct": po[u]["physical_pct"], "has_approved_history": u in has_history,
                "progress_transferred": False} for u in po if u not in pn]
    added = [{"activity_uid": u, "external_id": pn[u]["external_activity_id"], "actual_pct": pn[u]["physical_pct"]} for u in pn if u not in po]
    return {"as_of": asof, "old": {"version_id": old["version_id"], "version_no": old["version_no"], "physical_pct": so["physical_pct"], "planned_pct": so["planned_pct"], "weight_basis": so["weight_basis"]},
            "new": {"version_id": new["version_id"], "version_no": new["version_no"], "physical_pct": sn["physical_pct"], "planned_pct": sn["planned_pct"], "weight_basis": sn["weight_basis"]},
            "activities": common, "retired_scope": retired, "added_scope": added, "lineage": lineage,
            "note": "Actual quantities are the same approved ledger entries in both columns; only the denominators (baseline quantities, weights, scope) differ. "
                    "Nothing is transferred automatically across splits, merges or retirements. " + PLANNED_METHOD_NOTE}
