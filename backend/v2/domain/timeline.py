"""One activity's whole story across schedule versions, and the audit trail. Role-filtered: a Site Engineer sees their own claims; a Supervisor sees all;
a Project Manager sees versions, approved ledger entries, issues and lineage but NO claim content or decision text."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .. import audit
from ..db import tx
from ..errors import ApiError
from .common import actor_tx, PM, SE, SUP, ProjectActor, require_role


def activity_timeline(actor: ProjectActor, activity_uid) -> Dict[str, Any]:
    with actor_tx(actor, readonly=True) as c:
        if c.execute("select 1 from activities where project_id = %s and activity_uid = %s", (actor.project_id, activity_uid)).fetchone() is None:
            raise ApiError(404, "ACTIVITY_NOT_FOUND", "No such activity in this project")
        out: Dict[str, Any] = {"activity_uid": activity_uid}
        out["versions"] = c.execute(
            "select v.version_id, v.version_no, v.kind, v.status, a.external_activity_id, a.activity_name, a.baseline_start, a.baseline_finish, a.baseline_duration, a.total_float "
            "from baseline_activities a join schedule_versions v on v.version_id = a.version_id where a.project_id = %s and a.activity_uid = %s order by v.version_no", (actor.project_id, activity_uid)).fetchall()
        out["quantity_entries"] = c.execute(
            "select r.entry_seq, r.as_of_date, (select pr.resource_code from baseline_resources br join project_resources pr on pr.resource_id = br.resource_id where br.assignment_uid = r.assignment_uid limit 1) as resource_code, "
            "r.assignment_uid, r.cumulative_qty, r.incremental_qty, r.prev_cumulative_qty, r.baseline_qty_at_entry, r.overrun_pct, r.over_baseline, r.overrun_ack_by, r.overrun_ack_note, "
            "r.decision_id, r.claim_quantity_id, r.created_at from approved_resource_progress r where r.project_id = %s and r.activity_uid = %s order by r.entry_seq", (actor.project_id, activity_uid)).fetchall()
        out["activity_entries"] = c.execute("select entry_seq, as_of_date, actual_start, actual_finish, reported_pct, decision_id, created_at from approved_activity_progress "
                                            "where project_id = %s and activity_uid = %s order by entry_seq", (actor.project_id, activity_uid)).fetchall()
        out["issues"] = c.execute("select issue_id, title, severity, status, blocks_work, reported_date, delay_started_on, delay_ended_on, impact_days_estimated, impact_days_actual, reported_by from issues "
                                  "where project_id = %s and activity_uid = %s and (%s <> 'SITE_ENGINEER' or reported_by = %s) order by reported_date", (actor.project_id, activity_uid, actor.role, actor.user_id)).fetchall()
        out["lineage"] = c.execute("select l.version_id, l.relation, l.from_activity_uid, l.to_activity_uid, l.fraction, l.confirmed_at from activity_lineage l "
                                   "where l.project_id = %s and (l.from_activity_uid = %s or l.to_activity_uid = %s) order by l.confirmed_at", (actor.project_id, activity_uid, activity_uid)).fetchall()
        if actor.role == PM:
            out["claims"], out["decisions"] = [], []
            out["claims_note"] = "Project managers see aggregate claim counts only, not claim content."
        else:
            mine = actor.role == SE
            out["claims"] = c.execute("select event_id, event_date, status, claimed_pct, raw_claim_text, filed_by, filed_in_version_id, created_at, withdrawn_at, resubmits_event_id from execution_events "
                                      "where project_id = %s and matched_activity_uid = %s and (not %s or filed_by = %s) order by created_at", (actor.project_id, activity_uid, mine, actor.user_id)).fetchall()
            ids = [r["event_id"] for r in out["claims"]]
            out["decisions"] = c.execute("select decision_id, event_id, action, method, justification, decided_by, decided_at, overrun_ack, overrun_ack_note, applied, result from planner_decisions "
                                         "where project_id = %s and event_id = any(%s) order by decided_at", (actor.project_id, ids)).fetchall()
        return out


def audit_trail(actor: ProjectActor, entity_type: Optional[str] = None, entity_id: Optional[str] = None, limit: int = 200) -> list:
    require_role(actor, SUP, PM, what="reading the audit trail")
    with actor_tx(actor, readonly=True) as c:
        return c.execute("select log_id, occurred_at, actor_id, role, action, entity_type, entity_id, before_state, after_state, current_hash from audit_logs where project_id = %s "
                         "and (%s::text is null or entity_type = %s) and (%s::text is null or entity_id = %s) order by log_id desc limit %s",
                         (actor.project_id, entity_type, entity_type, entity_id, entity_id, min(limit, 1000))).fetchall()


def verify_audit_chain(actor: ProjectActor) -> Dict[str, Any]:
    require_role(actor, SUP, PM, what="verifying the audit chain")
    with actor_tx(actor, readonly=True) as c:
        return audit.verify_chain(c)
