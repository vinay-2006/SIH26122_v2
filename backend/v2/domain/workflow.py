"""Derived workflow facts of every activity of one schedule version, in the exact shape the ORIGINAL state engine consumes (backend/shared/workflow_flags.py):
quality-gate flags (failed / open / pre-commencement hold / predecessor hold / gate count), active work-blocking issues and the governed-reopen state. Nothing is stored on the
activity: it is all derived from gates, issues and reopens, so the engine's explainable QUALITY_HOLD / BLOCKED / REOPEN_REQUESTED / REWORK_IN_PROGRESS conditions work on v2 data."""
from __future__ import annotations

from typing import Any, Dict

_SQL = """
select ba.activity_uid,
       coalesce((select bool_or(g.status = 'FAILED') from quality_gates g where g.project_id = ba.project_id and g.activity_uid = ba.activity_uid and g.required), false) as wf_q_failed,
       coalesce((select bool_or(g.status in ('PENDING','SUBMITTED')) from quality_gates g where g.project_id = ba.project_id and g.activity_uid = ba.activity_uid and g.required), false) as wf_q_open,
       coalesce((select bool_or(g.gate_type = 'PRE_COMMENCEMENT' and g.checkpoint_category = 'HOLD' and g.status not in ('PASSED','WAIVED')) from quality_gates g
                  where g.project_id = ba.project_id and g.activity_uid = ba.activity_uid and g.required), false) as wf_q_precomm_open,
       exists (select 1 from schedule_dependencies d join quality_gates pg on pg.project_id = d.project_id and pg.activity_uid = d.predecessor_uid
                where d.version_id = ba.version_id and d.successor_uid = ba.activity_uid and d.relationship_type = 'FS'
                  and pg.required and pg.checkpoint_category = 'HOLD' and pg.status not in ('PASSED','WAIVED')) as wf_pred_hold,
       (select count(*) from quality_gates g where g.project_id = ba.project_id and g.activity_uid = ba.activity_uid and g.required)::int as wf_q_gate_count,
       (select count(*) from issues b where b.project_id = ba.project_id and b.status = 'ACTIVE' and b.blocks_work
           and (b.activity_uid = ba.activity_uid or (b.activity_uid is null and b.stage_wbs_uid is not null and b.stage_wbs_uid = (
                select st.wbs_uid from schedule_wbs st join schedule_wbs sw on sw.wbs_id = ba.wbs_id where st.version_id = ba.version_id and st.node_type = 'STAGE'
                   and sw.wbs_path like st.wbs_path || '%%' order by length(st.wbs_path) desc limit 1))))::int as wf_blockers,
       (select ar.status from activity_reopens ar where ar.project_id = ba.project_id and ar.activity_uid = ba.activity_uid and ar.status in ('REQUESTED','APPROVED')) as reopen_state
  from baseline_activities ba where ba.project_id = %(project)s and ba.version_id = %(version)s
"""


def workflow_flags(conn, project_id, version_id) -> Dict[Any, Dict[str, Any]]:
    out = {}
    for r in conn.execute(_SQL, {"project": project_id, "version": version_id}).fetchall():
        out[r["activity_uid"]] = {
            "wf_q_failed": r["wf_q_failed"], "wf_q_open": r["wf_q_open"], "wf_q_precomm_open": r["wf_q_precomm_open"], "wf_pred_hold": r["wf_pred_hold"],
            "wf_q_gate_count": r["wf_q_gate_count"], "wf_blockers": r["wf_blockers"], "quality_gate_required": False,
            "reopen_status": {"REQUESTED": "REQUESTED", "APPROVED": "APPROVED"}.get(r["reopen_state"]), "is_reopened": r["reopen_state"] == "APPROVED",
        }
    return out
