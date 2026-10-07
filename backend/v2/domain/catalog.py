"""What a Site Engineer needs to file a claim: the ACTIVE schedule's activities with their progress-measuring assignments (resource, unit,
baseline quantity) and what has already been APPROVED against them. Read-only; approved figures come from the ledgers via the as-of functions."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from .common import actor_tx, ProjectActor, active_version


def active_activities(actor: ProjectActor, q: Optional[str] = None, wbs_prefix: Optional[str] = None, discipline: Optional[str] = None,
                      limit: int = 50, offset: int = 0, as_of: Optional[date] = None) -> Dict[str, Any]:
    with actor_tx(actor, readonly=True) as c:
        v = active_version(c, actor.project_id)
        like = f"%{q.strip().lower()}%" if q and q.strip() else None
        rows = c.execute(
            "select a.activity_uid, a.external_activity_id, a.activity_name, a.wbs_path, a.discipline_code, a.activity_type, a.baseline_start, a.baseline_finish, a.baseline_duration, "
            "a.physical_pct, a.execution_state, a.actual_start, a.actual_finish, a.any_overrun, a.progress_basis "
            "from activity_progress_as_of(%s, %s) a where (%s::text is null or lower(a.external_activity_id) like %s or lower(a.activity_name) like %s) "
            "and (%s::text is null or a.wbs_path like %s || '%%') and (%s::text is null or a.discipline_code = %s) "
            "order by a.baseline_start, a.external_activity_id limit %s offset %s",
            (v["version_id"], as_of or date.today(), like, like, like, wbs_prefix, wbs_prefix, discipline, discipline, limit, offset)).fetchall()
        uids = [r["activity_uid"] for r in rows]
        meas = c.execute(
            "select br.activity_uid, br.assignment_uid, pr.resource_code, pr.resource_name, br.unit_of_measure, br.baseline_qty, "
            "(select r.cumulative_qty from approved_resource_progress r where r.assignment_uid = br.assignment_uid and r.as_of_date <= %s order by r.entry_seq desc limit 1) as approved_cumulative_qty "
            "from baseline_resources br join project_resources pr on pr.resource_id = br.resource_id and pr.project_id = br.project_id "
            "where br.version_id = %s and br.activity_uid = any(%s) and br.measures_progress order by pr.resource_code",
            (as_of or date.today(), v["version_id"], uids)).fetchall()
    by: Dict[Any, List[dict]] = {}
    for m in meas:
        by.setdefault(m["activity_uid"], []).append({k: m[k] for k in m if k != "activity_uid"})
    return {"version": {"version_id": v["version_id"], "version_no": v["version_no"], "baseline_name": v["baseline_name"], "data_date": v["data_date"]},
            "items": [{**r, "measured_assignments": by.get(r["activity_uid"], []), "claim_types": (["QUANTITY", "PERCENT"] if by.get(r["activity_uid"]) else ["PERCENT", "DATES"])} for r in rows]}
