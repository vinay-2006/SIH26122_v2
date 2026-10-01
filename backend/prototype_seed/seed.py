"""
Seed the four-project prototype dataset into the ISOLATED local demo database.

    scripts/integration_db.sh is the only supported way to create that database:
        SETUAI_INTEG_DB=setuai_integ_demo scripts/integration_db.sh up && ... migrate

    SETUAI_ALLOW_DB_TESTS=1 SETUAI_TEST_ENV=integration \\
    DATABASE_URL=postgresql://postgres@127.0.0.1:54329/setuai_integ_demo \\
    python -m backend.prototype_seed.seed [--verify]

Refuses to run unless the same guard as the test-suite passes (loopback host, db name contains 'integ'/'test', never the
shared Supabase host, integration marker row present). Idempotent: every id is deterministic (uuid5) and every insert is
ON CONFLICT DO NOTHING, so re-running adds nothing.

Progress is written through the real mechanism, never as a number on the activity:
    field claim (execution_events) -> supervisor decision (planner_decisions) -> approved_actuals -> audit chain
so approved_actuals stays the single authoritative source of actual progress.
"""
from __future__ import annotations

import os
import sys
import uuid
import zlib
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional

import psycopg
import psycopg.rows

from backend.prototype_seed import data
from backend.prototype_seed.data import Act, Project
from backend.services.issue_service import _ISSUE_SQL, promote_issue_to_memory
from backend.services.notification_service import insert_decision_notification
from backend.shared.audit import append_audit_record
from backend.testing import guard

NS = uuid.UUID("5e7a0a11-7b70-4c1a-9f10-0d3a4e5f6a7b")


def _id(name: str) -> str:
    return str(uuid.uuid5(NS, name))


def _ts(day: date, hour: int = 9) -> datetime:
    return datetime.combine(day, time(hour, 0), tzinfo=timezone.utc)


def _j(code: str, mod: int) -> int:
    """deterministic small jitter from a code"""
    return zlib.crc32(code.encode()) % mod


UID = {"engineer": _id("user:engineer"), "supervisor": _id("user:supervisor")}
USERS = {
    "engineer": ("engineer@setuai.demo", "Demo Site Engineer", "SITE_ENGINEER"),
    "supervisor": ("supervisor@setuai.demo", "Demo Supervisor", "SUPERVISOR"),
}
APPROVE_COMMENTS = [
    "Verified against the daily site record and photographs.",
    "Quantity matches the measurement book; approved.",
    "Consistent with the contractor's joint measurement.",
    "Checked on site by the supervisor; approved as reported.",
]


def _connect():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=psycopg.rows.dict_row, autocommit=False)


def _stage_status(acts: List[Act]) -> str:
    if all(a.pct >= 100 for a in acts):
        return "COMPLETED"
    if all(a.pct == 0 for a in acts):
        return "NOT_STARTED"
    return "IN_PROGRESS"


def _decision(cur, *, p: Project, pid: str, sid: Dict[str, str], act: Act, stage_code: str, key: str, action: str,
              claimed: float, approved: Optional[float], day: date, text: str, comment: str,
              actual_start: Optional[date], actual_finish: Optional[date], notify: str) -> None:
    """One field claim + supervisor decision (+ approved actual for APPROVE/EDIT) + audit + notification."""
    status = {"APPROVE": "APPROVED", "EDIT": "EDITED", "REJECT": "REJECTED", "HOLD": "HOLD"}[action]
    ev, dec = _id(f"event:{p.code}:{key}"), _id(f"decision:{p.code}:{key}")
    decided_at = _ts(day, 17)
    cur.execute(
        "INSERT INTO execution_events (event_id, schedule_id, project_id, event_date, raw_claim_text, input_channel, "
        "reported_activity_id, matched_activity_id, discipline, claim_mode, claimed_pct, status, supervisor_id, stage_id, "
        "location, event_type, created_at) "
        "VALUES (%s, %s, %s, %s, %s, 'TYPED_TEXT', %s, %s, %s, 'CUMULATIVE_PCT', %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT DO NOTHING",
        (ev, p.schedule_id, pid, day, text, act.code, act.code, act.discipline, claimed, status, UID["engineer"],
         sid[stage_code], act.location, "ACTUAL_FINISH" if claimed >= 100 else "PROGRESS_UPDATE", _ts(day, 9)),
    )
    cur.execute(
        "INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, approved_pct, planner_id, "
        "justification, decided_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (dec, ev, act.code, action, approved, UID["supervisor"], comment, decided_at),
    )
    if action in ("APPROVE", "EDIT"):
        cur.execute(
            "INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, project_id, stage_id, "
            "actual_start, actual_finish, actual_pct_complete, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (_id(f"actual:{p.code}:{act.code}"), dec, ev, p.schedule_id, act.code, pid, sid[stage_code],
             actual_start, actual_finish, approved, decided_at),
        )
    have = cur.execute("SELECT count(*) AS n FROM audit_logs WHERE entity_id = %s AND project_id = %s", (ev, pid)).fetchone()["n"]
    if not have:
        append_audit_record(
            cur, entity_type="execution_event", entity_id=ev, action=action, actor_id=UID["supervisor"],
            before_state={"status": "VALIDATED"},
            after_state={"status": status, "selected_activity_id": act.code, "decision_id": dec, "approved_pct": approved},
            project_id=pid, schedule_id=p.schedule_id, role="SUPERVISOR", entity_context={"justification": comment, "seed": True},
        )
    if notify != "none":
        insert_decision_notification(
            cur, project_id=pid, decision_id=dec, event_id=ev, recipient_id=UID["engineer"], created_by=UID["supervisor"],
            action=action, activity_name=act.name, claimed_pct=claimed, approved_pct=approved, comment=comment,
        )
        cur.execute("UPDATE notifications SET created_at = %s, read_at = %s WHERE decision_id = %s AND recipient_id = %s",
                    (decided_at, None if notify == "unread" else decided_at + timedelta(hours=2), dec, UID["engineer"]))


def seed_project(cur, p: Project, ordinal: int) -> None:
    pid = _id(f"project:{p.code}")
    cur.execute(
        "INSERT INTO projects (project_id, project_code, project_name, description, project_type, location, planned_start, "
        "planned_finish, contract_finish, status, lifecycle_status, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'ACTIVE', %s, %s) ON CONFLICT DO NOTHING",
        (pid, p.code, p.name, p.description, p.project_type, p.location, p.start, p.finish, p.finish, p.lifecycle, UID["supervisor"]),
    )
    for k, (_, _, role) in USERS.items():
        cur.execute(
            "INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active, status) "
            "VALUES (%s, %s, %s, %s, TRUE, 'ACTIVE') ON CONFLICT DO NOTHING",
            (_id(f"membership:{p.code}:{k}"), UID[k], pid, role),
        )
    cur.execute(
        "INSERT INTO schedules (schedule_id, project_name, project_id, version_code, active, data_date, source_format) "
        "VALUES (%s, %s, %s, 'BL', TRUE, %s, 'SEED') ON CONFLICT DO NOTHING",
        (p.schedule_id, p.name, pid, p.data_date),
    )

    weights = data.stage_weights(p)
    sid: Dict[str, str] = {}
    for i, s in enumerate(p.stages, 1):
        sid[s.code] = _id(f"stage:{p.code}:{s.code}")
        cur.execute(
            "INSERT INTO stages (stage_id, project_id, schedule_id, stage_code, stage_name, sequence_order, weight_pct, status, "
            "planned_start, planned_finish) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (sid[s.code], pid, p.schedule_id, s.code, s.name, i, weights[s.code], _stage_status(s.acts), s.start, s.end),
        )

    by_code: Dict[str, tuple] = {}
    for si, s in enumerate(p.stages, 1):
        for ai, a in enumerate(s.acts, 1):
            by_code[a.code] = (a, s.code)
            cur.execute(
                "INSERT INTO schedule_activities (activity_id, schedule_id, project_id, activity_name, description, wbs_code, "
                "discipline, location, planned_start, planned_finish, planned_quantity, uom, baseline_pct_complete, is_critical, "
                "stage_id, weight_factor) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, FALSE, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (a.code, p.schedule_id, pid, a.name, a.description, f"{si}.{ai:02d}", a.discipline, a.location, a.start,
                 a.finish, a.qty, a.uom, sid[s.code], data.weight_of(a)),
            )

    # dependencies: finish-to-start where the dates allow it, start-to-start where the work overlaps
    n = 0
    prev_stage_first: Optional[Act] = None
    prev_stage_last: Optional[Act] = None
    for s in p.stages:
        for pred, succ in zip(s.acts, s.acts[1:]):
            n += 1
            kind = "FS" if pred.finish <= succ.start else "SS"
            cur.execute(
                "INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id, "
                "relationship_type, lag_days) VALUES (%s, %s, %s, %s, %s, 0) ON CONFLICT DO NOTHING",
                (f"DEP-{p.code}-{n:03d}", p.schedule_id, pred.code, succ.code, kind),
            )
        if prev_stage_last is not None:
            n += 1
            first = s.acts[0]
            pred, kind = (prev_stage_last, "FS") if prev_stage_last.finish <= first.start else (prev_stage_first, "SS")
            cur.execute(
                "INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id, "
                "relationship_type, lag_days) VALUES (%s, %s, %s, %s, %s, 0) ON CONFLICT DO NOTHING",
                (f"DEP-{p.code}-{n:03d}", p.schedule_id, pred.code, first.code, kind),
            )
        prev_stage_first, prev_stage_last = s.acts[0], s.acts[-1]

    # ---- execution: every activity with progress gets a real claim -> decision -> approved actual
    special = {c.activity for c in p.claims if c.action == "EDIT"}   # their authoritative decision is the EDIT claim
    approved_keys: List[tuple] = []
    for s in p.stages:
        for a in s.acts:
            if a.pct <= 0 or a.code in special:
                continue
            start = min(a.start + timedelta(days=_j(a.code, 7)), p.data_date)
            if a.pct >= 100:
                finish = min(a.finish + timedelta(days=_j(a.code + "f", 11) - 3), p.data_date)
                finish = max(finish, start)
                day = finish
            else:
                finish = None
                day = max(start, p.data_date - timedelta(days=1 + _j(a.code + "d", 9)))
            text = f"{a.name} completed" if a.pct >= 100 else f"{a.name} {a.pct:g} percent complete"
            approved_keys.append((day, a, s.code, text, start, finish))
    approved_keys.sort(key=lambda t: (t[0], t[1].code))
    recent = {t[1].code for t in approved_keys[-3:]} if p.lifecycle == "ONGOING" else set()
    for day, a, scode, text, start, finish in approved_keys:
        _decision(cur, p=p, pid=pid, sid=sid, act=a, stage_code=scode, key=f"approved:{a.code}", action="APPROVE",
                  claimed=a.pct, approved=a.pct, day=day, text=text, comment=APPROVE_COMMENTS[_j(a.code, len(APPROVE_COMMENTS))],
                  actual_start=start, actual_finish=finish, notify="unread" if a.code in recent else "none")
    for c in p.claims:
        a, scode = by_code[c.activity]
        start = min(a.start + timedelta(days=_j(a.code, 7)), p.data_date)
        _decision(cur, p=p, pid=pid, sid=sid, act=a, stage_code=scode, key=f"claim:{c.key}", action=c.action,
                  claimed=c.claimed_pct, approved=c.approved_pct, day=c.day, text=c.text, comment=c.comment,
                  actual_start=start, actual_finish=None, notify="unread")

    # ---- root causes, issues, memory
    rc_id = {k: _id(f"rootcause:{p.code}:{k}") for k in p.root_causes}
    for k, (cat, title, summary) in p.root_causes.items():
        cur.execute(
            "INSERT INTO root_causes (root_cause_id, project_id, category_code, title, summary, status, identified_by, identified_at) "
            "VALUES (%s, %s, %s, %s, %s, 'IDENTIFIED', %s, %s) ON CONFLICT DO NOTHING",
            (rc_id[k], pid, cat, title, summary, UID["supervisor"], _ts(p.data_date - timedelta(days=20), 11)),
        )
    for i in p.issues:
        a, scode = by_code[i.activity]
        issue_id = _id(f"issue:{p.code}:{i.key}")
        resolved_at = _ts(i.resolved, 16) if i.resolved else None
        cur.execute(
            "INSERT INTO issues (issue_id, project_id, schedule_id, activity_id, stage_id, category_code, title, description, "
            "severity, reported_date, expected_duration_days, blocks_work, status, reported_by, created_at, resolved_by, "
            "resolved_at, resolution_notes, root_cause_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (issue_id, pid, p.schedule_id, i.activity, sid[scode], i.category, i.title, i.description, i.severity, i.reported,
             i.expected_days, i.blocks_work, "RESOLVED" if i.resolved else "ACTIVE", UID["engineer"], _ts(i.reported, 8),
             UID["supervisor"] if i.resolved else None, resolved_at, i.resolution, rc_id.get(i.root_cause)),
        )
        if i.resolved:
            row = dict(cur.execute(_ISSUE_SQL + " WHERE i.issue_id = %s", (issue_id,)).fetchone())
            cause = None
            if i.root_cause:
                cause = p.root_causes[i.root_cause][2]
            else:
                cause = data.CAUSES.get((p.code, i.key))
            promote_issue_to_memory(
                cur, row, recorded_by=UID["supervisor"], cause=cause, outcome=i.outcome, lessons_learned=None,
                share_with_organisation=i.share_org, source="ISSUE_RESOLUTION",
            )
        have = cur.execute("SELECT count(*) AS n FROM audit_logs WHERE entity_id = %s AND action = 'ISSUE_REPORTED'", (issue_id,)).fetchone()["n"]
        if not have:
            append_audit_record(
                cur, entity_type="ISSUE", entity_id=issue_id, action="ISSUE_REPORTED", actor_id=UID["engineer"], before_state=None,
                after_state={"title": i.title, "category_code": i.category, "activity_id": i.activity, "seed": True},
                project_id=pid, schedule_id=p.schedule_id, role="SITE_ENGINEER",
            )


def seed(conn) -> Dict[str, int]:
    cur = conn.cursor()
    for k, (email, name, role) in USERS.items():
        cur.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s) ON CONFLICT DO NOTHING", (UID[k], email))
        cur.execute("INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (UID[k], name, role))
    for n, p in enumerate(data.PROJECTS, 1):
        seed_project(cur, p, n)
    conn.commit()
    return summary(conn)


def summary(conn) -> Dict[str, int]:
    q = lambda sql: conn.execute(sql).fetchone()["n"]
    return {
        "projects": q("SELECT count(*) n FROM projects"),
        "stages": q("SELECT count(*) n FROM stages"),
        "activities": q("SELECT count(*) n FROM schedule_activities"),
        "dependencies": q("SELECT count(*) n FROM schedule_dependencies"),
        "claims (execution_events)": q("SELECT count(*) n FROM execution_events"),
        "decisions": q("SELECT count(*) n FROM planner_decisions"),
        "approved_actuals": q("SELECT count(*) n FROM approved_actuals"),
        "issues": q("SELECT count(*) n FROM issues"),
        "root_causes": q("SELECT count(*) n FROM root_causes"),
        "memory records": q("SELECT count(*) n FROM institutional_incidents"),
        "notifications": q("SELECT count(*) n FROM notifications"),
        "audit rows": q("SELECT count(*) n FROM audit_logs"),
    }


def main() -> int:
    problems = guard.check_env(dict(os.environ))
    if problems:
        print("REFUSING TO SEED:\n  - " + "\n  - ".join(problems))
        return 2
    errs = data.validate()
    if errs:
        print("DATASET INVALID:\n  - " + "\n  - ".join(errs))
        return 3
    with _connect() as conn:
        problem = guard.check_db_marker(conn)
        if problem:
            print("REFUSING TO SEED: " + problem)
            return 2
        result = summary(conn) if "--verify" in sys.argv else seed(conn)
    for k, v in result.items():
        print(f"{k:28} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
