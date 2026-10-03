"""v2 rows -> the dict shapes the existing matching engine consumes. Pure translation; nothing is scored or converted here.

Planned quantity / unit (input adapter only): the activity's quantity is the baseline quantity and unit of its ONE progress-measuring assignment
(`measures_progress`). With zero or several such assignments there is no single planned quantity, so both stay None -- the uncertainty is preserved,
never summed, converted or guessed."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Dict, List, Optional

# One query = one project + one schedule version. Nothing else can reach the engine.
_ACTIVITIES_SQL = """
select ba.activity_uid, ba.external_activity_id, ba.activity_name, ba.description, ba.discipline_code, ba.location, ba.asset_tag,
       ba.baseline_start, ba.baseline_finish, ba.total_float, ba.is_critical, sw.wbs_code,
       (select st.wbs_name from schedule_wbs st where st.version_id = ba.version_id and st.node_type = 'STAGE'
           and sw.wbs_path like st.wbs_path || '%%' order by length(st.wbs_path) desc limit 1) as stage_name,
       (select st.wbs_uid from schedule_wbs st where st.version_id = ba.version_id and st.node_type = 'STAGE'
           and sw.wbs_path like st.wbs_path || '%%' order by length(st.wbs_path) desc limit 1) as stage_uid,
       (select count(*) from baseline_resources br where br.version_id = ba.version_id and br.activity_uid = ba.activity_uid and br.measures_progress) as n_measured,
       (select br.baseline_qty from baseline_resources br where br.version_id = ba.version_id and br.activity_uid = ba.activity_uid and br.measures_progress limit 1) as m_qty,
       (select br.unit_of_measure from baseline_resources br where br.version_id = ba.version_id and br.activity_uid = ba.activity_uid and br.measures_progress limit 1) as m_uom,
       p.physical_pct, p.actual_start, p.actual_finish
  from baseline_activities ba
  join schedule_wbs sw on sw.wbs_id = ba.wbs_id
  left join activity_progress_as_of(%(version)s, %(asof)s) p on p.activity_uid = ba.activity_uid
 where ba.project_id = %(project)s and ba.version_id = %(version)s
 order by ba.external_activity_id
"""


def _f(v) -> Optional[float]:
    return None if v is None else float(v)


def load_activities(conn, project_id, version_id, as_of: Optional[date] = None) -> List[Dict[str, Any]]:
    """Activities of one version as legacy `schedule_activities` dicts. activity_id is the external id (unique within a version);
    `activity_uid` rides along so results can be mapped back to the stable identity."""
    from ..domain.workflow import workflow_flags
    flags = workflow_flags(conn, project_id, version_id)
    rows = conn.execute(_ACTIVITIES_SQL, {"project": project_id, "version": version_id, "asof": as_of or date.today()}).fetchall()
    out = []
    for r in rows:
        single = r["n_measured"] == 1
        out.append({
            "schedule_id": str(version_id), "project_id": str(project_id),
            "activity_id": r["external_activity_id"], "activity_uid": r["activity_uid"],
            "activity_name": r["activity_name"], "description": r["description"], "wbs_code": r["wbs_code"],
            "discipline": r["discipline_code"], "location": r["location"], "asset_tag": r["asset_tag"], "stage_name": r["stage_name"], "stage_id": str(r["stage_uid"]) if r["stage_uid"] else None,
            "planned_start": r["baseline_start"], "planned_finish": r["baseline_finish"],
            "planned_quantity": _f(r["m_qty"]) if single else None, "uom": r["m_uom"] if single else None,
            "baseline_pct_complete": 0.0, "total_float": _f(r["total_float"]), "is_critical": r["is_critical"],
            # canonical execution state inputs (read by the engine's eligibility rule: >= 100% => completed, never offered)
            "actual_pct_complete": _f(r["physical_pct"]) if r["physical_pct"] is not None else 0.0,
            "actual_start": r["actual_start"], "actual_finish": r["actual_finish"],
            # derived workflow facts for the original eligibility rules: quality hold (D8), blockers, governed reopen (D7)
            **flags.get(r["activity_uid"], {}),
        })
    return out


def claim_for_engine(claim: Dict[str, Any], quantities: List[Dict[str, Any]], version_id) -> Dict[str, Any]:
    """A v2 claim row -> ExecutionClaim fields. The legacy claim carries ONE quantity: it is passed only when the claim reports exactly one;
    several reported quantities are not collapsed into a guess."""
    one = quantities[0] if len(quantities) == 1 else None
    return {
        "event_id": str(claim["event_id"]), "schedule_id": str(version_id), "event_date": claim["event_date"], "raw_claim_text": claim["raw_claim_text"],
        "input_channel": claim["input_channel"], "language_detected": claim.get("language_detected"),
        "reported_activity_id": claim.get("reported_activity_ref"), "discipline": claim.get("discipline_code"),
        "event_type": claim.get("event_type"), "claim_mode": claim.get("claim_mode") or "CUMULATIVE_PCT",
        "asset_tag": claim.get("asset_tag"), "location": claim.get("location"),
        "claimed_quantity": _f(one["reported_qty"]) if one else None, "claimed_uom": one["reported_uom"] if one else None,
        "claimed_pct": _f(claim.get("claimed_pct")), "delay_reason": claim.get("delay_reason"), "supervisor_id": None,
    }


def to_uuid(v) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        return None
