"""
Single source of truth for the persisted inputs of an activity's WORKFLOW CONDITION.

QUALITY_HOLD and BLOCKED used to be unreachable from real data (nothing selected quality or blocker facts
alongside an activity). Every bulk activity query now appends WORKFLOW_FLAGS_COLUMNS and WORKFLOW_FLAGS_JOIN
(alias `sa` = schedule_activities), and StageService.get_workflow_condition() turns the flags into a condition.

One LATERAL join per query: no per-activity round trips (no N+1). Scoped by project AND schedule version, so
V1 and V2 never mix and another project's gates/blockers can never leak in.

Flags (all derived, nothing is stored on the activity):
  wf_q_failed     a REQUIRED quality gate of this activity is FAILED
  wf_q_open       a REQUIRED gate is PENDING / SUBMITTED (not yet released)
  wf_q_precomm_open   a REQUIRED PRE_COMMENCEMENT hold-point gate of this activity is not PASSED/WAIVED
  wf_pred_hold    a finish-to-start PREDECESSOR has a REQUIRED HOLD-category gate not PASSED/WAIVED
  wf_q_gate_count number of REQUIRED gates of this activity (NOT_REQUIRED gates are ignored)
  wf_blockers     ACTIVE work-blocking issues (issues.blocks_work) targeting the activity, or its stage when the blocker has no activity
"""

WORKFLOW_FLAGS_COLUMNS = """
                   COALESCE(wf.wf_q_failed, FALSE) AS wf_q_failed,
                   COALESCE(wf.wf_q_open, FALSE) AS wf_q_open,
                   COALESCE(wf.wf_q_precomm_open, FALSE) AS wf_q_precomm_open,
                   COALESCE(wf.wf_pred_hold, FALSE) AS wf_pred_hold,
                   COALESCE(wf.wf_q_gate_count, 0) AS wf_q_gate_count,
                   COALESCE(wf.wf_blockers, 0) AS wf_blockers"""

WORKFLOW_FLAGS_JOIN = """
            LEFT JOIN LATERAL (
                SELECT
                    (SELECT bool_or(g.status = 'FAILED')
                       FROM quality_gates g
                      WHERE g.project_id = sa.project_id AND g.schedule_id = sa.schedule_id
                        AND g.activity_id = sa.activity_id AND g.required AND g.status <> 'NOT_REQUIRED') AS wf_q_failed,
                    (SELECT bool_or(g.status IN ('PENDING', 'SUBMITTED'))
                       FROM quality_gates g
                      WHERE g.project_id = sa.project_id AND g.schedule_id = sa.schedule_id
                        AND g.activity_id = sa.activity_id AND g.required AND g.status <> 'NOT_REQUIRED') AS wf_q_open,
                    (SELECT bool_or(g.gate_type = 'PRE_COMMENCEMENT' AND g.checkpoint_category = 'HOLD' AND g.status NOT IN ('PASSED', 'WAIVED'))
                       FROM quality_gates g
                      WHERE g.project_id = sa.project_id AND g.schedule_id = sa.schedule_id
                        AND g.activity_id = sa.activity_id AND g.required AND g.status <> 'NOT_REQUIRED') AS wf_q_precomm_open,
                    EXISTS (SELECT 1
                              FROM schedule_dependencies d
                              JOIN quality_gates pg
                                ON pg.project_id = sa.project_id AND pg.schedule_id = d.schedule_id
                               AND pg.activity_id = d.predecessor_activity_id
                             WHERE d.schedule_id = sa.schedule_id AND d.successor_activity_id = sa.activity_id
                               AND d.relationship_type = 'FS'
                               AND pg.required AND pg.checkpoint_category = 'HOLD'
                               AND pg.status NOT IN ('PASSED', 'WAIVED', 'NOT_REQUIRED')) AS wf_pred_hold,
                    (SELECT count(*)
                       FROM quality_gates g
                      WHERE g.project_id = sa.project_id AND g.schedule_id = sa.schedule_id
                        AND g.activity_id = sa.activity_id AND g.required AND g.status <> 'NOT_REQUIRED') AS wf_q_gate_count,
                    (SELECT count(*)
                       FROM issues b
                      WHERE b.project_id = sa.project_id AND b.schedule_id = sa.schedule_id AND b.status = 'ACTIVE'
                        AND b.blocks_work
                        AND (b.activity_id = sa.activity_id
                             OR (b.activity_id IS NULL AND sa.stage_id IS NOT NULL AND b.stage_id = sa.stage_id))) AS wf_blockers
            ) wf ON TRUE"""


def quality_hold_reason(activity: dict):
    """The explainable reason an activity is in QUALITY_HOLD, or None. Pure function of the flags above and the
    activity's approved-actual progress; used by the state engine and by every explanation surface.

    R1 non-conformance  any required gate FAILED                               -> hold at any progress
    R2 pre-commencement a PRE_COMMENCEMENT hold point is not released          -> hold until released (work not completed)
    R3 completion       progress >= 100% and a required gate is PENDING/SUBMITTED, or quality is declared
                        required (quality_gate_required) but no gate has been recorded at all
                                                                                -> completed work awaits quality release
    R4 predecessor hold a finish-to-start predecessor has an unreleased HOLD-category gate
                                                                                -> the successor cannot proceed
                                                                                   (e.g. no concrete pour before the
                                                                                   rebar inspection is released)
    An inspection that follows the work (a pending non-hold gate on work in progress) does not hold that work.
    """
    if activity.get("wf_q_failed"):
        return "FAILED_GATE"
    pct = activity.get("actual_pct_complete")
    completed = pct is not None and float(pct) >= 100.0
    if activity.get("wf_q_precomm_open") and not completed:
        return "PRE_COMMENCEMENT_HOLD_POINT_OPEN"
    if completed:
        if activity.get("wf_q_open"):
            return "COMPLETION_PENDING_QUALITY_RELEASE"
        if activity.get("quality_gate_required") and not activity.get("wf_q_gate_count"):
            return "QUALITY_REQUIRED_BUT_NO_GATE_RECORDED"
    elif activity.get("wf_pred_hold"):
        return "PREDECESSOR_HOLD_POINT_OPEN"
    return None


def with_workflow_flags(sql: str) -> str:
    """Append the workflow flag columns and the LATERAL join to a query whose main FROM is
    `FROM schedule_activities sa`. Only the FIRST such FROM is rewritten (subqueries are left alone)."""
    marker = "FROM schedule_activities sa"
    i = sql.find(marker)
    if i < 0:
        raise ValueError("query has no 'FROM schedule_activities sa'")
    head = sql[:i].rstrip()
    j = i + len(marker)
    return head + "," + WORKFLOW_FLAGS_COLUMNS + "\n            " + marker + WORKFLOW_FLAGS_JOIN + sql[j:]
