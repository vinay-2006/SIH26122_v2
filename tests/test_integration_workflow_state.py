"""
Workflow condition integration (DB_WRITE + INTEGRATION, isolated DB): QUALITY_HOLD and BLOCKED are derived
from persisted quality gates / blockers, in bulk, per project and per schedule version, and are exposed by the
same engines the UI/agent/dossier use (progress breakdown).

Canonical execution state stays separate: 100% is COMPLETED whatever the workflow condition is.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

import backend.main  # noqa: F401  (import order: avoids a pre-existing repositories<->services circular import)
from backend.auth.dependencies import get_current_user
from backend.auth.models import CurrentUser
from backend.main import app
from backend.shared.db import get_connection

pytestmark = [pytest.mark.integration]


@pytest.fixture(scope="module")
def w():
    su = get_connection()
    su.autocommit = True
    tag = uuid.uuid4().hex[:6]
    d = {"su": su, "tag": tag}
    d["P"], d["P2"] = str(uuid.uuid4()), str(uuid.uuid4())
    d["pm"], d["sup"], d["eng"], d["other"] = (str(uuid.uuid4()) for _ in range(4))
    d["V1"], d["V2"], d["X2"] = f"WF-V1-{tag}", f"WF-V2-{tag}", f"WF-OTHER-{tag}"
    d["stage"], d["stage2"] = str(uuid.uuid4()), str(uuid.uuid4())
    for u, name, role in ((d["pm"], "pm", "SUPERVISOR"), (d["sup"], "sup", "SUPERVISOR"), (d["eng"], "eng", "SITE_ENGINEER"), (d["other"], "other", "SUPERVISOR")):
        su.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (u, f"{name}-{tag}@wf.test"))
        su.execute("INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s)", (u, f"WF {name}", role))
    for p, code in ((d["P"], f"V7-INTEG-WF-{tag}"), (d["P2"], f"V7-INTEG-WF2-{tag}")):
        su.execute("INSERT INTO projects (project_id, project_code, project_name, status) VALUES (%s, %s, 'WF', 'ACTIVE')", (p, code))
    for u, p, role in ((d["pm"], d["P"], "PROJECT_MANAGER"), (d["sup"], d["P"], "SUPERVISOR"), (d["eng"], d["P"], "SITE_ENGINEER"), (d["other"], d["P2"], "PROJECT_MANAGER")):
        su.execute("INSERT INTO project_memberships (user_id, project_id, assigned_role, active, status) VALUES (%s, %s, %s, TRUE, 'ACTIVE')", (u, p, role))
    for sid, p, code, act in ((d["V1"], d["P"], "V1", False), (d["V2"], d["P"], "V2", True), (d["X2"], d["P2"], "V1", True)):
        su.execute("INSERT INTO schedules (schedule_id, project_name, project_id, version_code, active) VALUES (%s, 'WF', %s, %s, %s)", (sid, p, code, act))
    su.execute("INSERT INTO stages (stage_id, project_id, schedule_id, stage_code, stage_name, sequence_order, weight_pct, status) VALUES (%s, %s, %s, 'S1', 'Stage 1', 1, 60, 'IN_PROGRESS')", (d["stage"], d["P"], d["V2"]))
    su.execute("INSERT INTO stages (stage_id, project_id, schedule_id, stage_code, stage_name, sequence_order, weight_pct, status) VALUES (%s, %s, %s, 'S2', 'Stage 2', 2, 40, 'NOT_STARTED')", (d["stage2"], d["P"], d["V2"]))

    def act(aid, sid=None, project=None, stage=None, qreq=False, pct=None):
        sid, project = sid or d["V2"], project or d["P"]
        su.execute(
            "INSERT INTO schedule_activities (activity_id, schedule_id, project_id, activity_name, discipline, location, planned_start, planned_finish, stage_id, weight_factor, quality_gate_required) "
            "VALUES (%s, %s, %s, %s, 'CIVIL', 'x', '2026-01-01', '2026-02-01', %s, 1.0, %s)", (aid, sid, project, aid, stage, qreq))
        if pct is not None:
            ev, dec = f"EV-{uuid.uuid4().hex[:8]}", f"DEC-{uuid.uuid4().hex[:8]}"
            su.execute("INSERT INTO execution_events (event_id, schedule_id, project_id, event_date, raw_claim_text, input_channel, status) VALUES (%s, %s, %s, '2026-01-10', 's', 'TYPED_TEXT', 'APPROVED')", (ev, sid, project))
            su.execute("INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, approved_pct, planner_id, justification) VALUES (%s, %s, %s, 'APPROVE', %s, %s, 's')", (dec, ev, aid, pct, d["sup"]))
            su.execute("INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, project_id, actual_pct_complete, actual_start) VALUES (%s, %s, %s, %s, %s, %s, %s, '2026-01-02')", (str(uuid.uuid4()), dec, ev, sid, aid, project, pct))

    def gate(aid, status, category="QUALITY_CHECK", required=True, sid=None, project=None, gtype="INSPECTION"):
        su.execute(
            "INSERT INTO quality_gates (quality_gate_id, project_id, schedule_id, activity_id, gate_type, gate_name, required, status, checkpoint_category) "
            "VALUES (%s, %s, %s, %s, %s, 'g', %s, %s, %s)", (str(uuid.uuid4()), project or d["P"], sid or d["V2"], aid, gtype, required, status, category))

    def dep(pred, succ, rel):
        su.execute("INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id, relationship_type, lag_days) VALUES (%s, %s, %s, %s, %s, 0)",
                   (f"D-{uuid.uuid4().hex[:8]}", d["V2"], pred, succ, rel))

    d["act"], d["gate"] = act, gate
    act("NS-CLEAN", stage=d["stage"])                              # nothing wrong
    act("DONE-PENDING", stage=d["stage"], qreq=True, pct=100)      # 100% + gate PENDING -> hold
    gate("DONE-PENDING", "PENDING")
    act("DONE-PASSED", stage=d["stage"], qreq=True, pct=100)       # 100% + gate PASSED -> clean
    gate("DONE-PASSED", "PASSED")
    act("DONE-WAIVED", stage=d["stage"], qreq=True, pct=100)
    gate("DONE-WAIVED", "WAIVED")
    act("DONE-NOGATE", stage=d["stage"], qreq=True, pct=100)       # quality required, no gate recorded -> hold
    act("PROG-FAILED", stage=d["stage"], qreq=True, pct=50)        # failed gate at 50% -> hold
    gate("PROG-FAILED", "FAILED")
    act("PROG-PENDING", stage=d["stage"], qreq=True, pct=50)       # pending gate while in progress -> NOT a hold
    gate("PROG-PENDING", "PENDING")
    act("NS-HOLDPOINT", stage=d["stage"], qreq=True)               # unreleased PRE_COMMENCEMENT hold point -> hold
    gate("NS-HOLDPOINT", "PENDING", category="HOLD", gtype="PRE_COMMENCEMENT")
    act("NS-INSPHOLD", stage=d["stage"], qreq=True)                # ordinary HOLD gate on a not-started activity -> no hold on itself
    gate("NS-INSPHOLD", "PENDING", category="HOLD")
    act("REBAR", stage=d["stage"], qreq=True, pct=100)             # finished, its HOLD inspection not released -> holds ITSELF (R3) ...
    gate("REBAR", "SUBMITTED", category="HOLD")
    act("POUR", stage=d["stage"], qreq=False)                      # ... and the FS successor (R4): "no pour before the rebar release"
    act("POUR-SS", stage=d["stage"], qreq=False)                   # SS successor is NOT held by a predecessor hold point
    act("POUR-DONE", stage=d["stage"], qreq=False, pct=100)        # completed work is never un-completed by a later hold
    dep("REBAR", "POUR", "FS"); dep("REBAR", "POUR-SS", "SS"); dep("REBAR", "POUR-DONE", "FS")
    act("NS-NOTREQ", stage=d["stage"], qreq=False)                 # NOT_REQUIRED gate ignored
    gate("NS-NOTREQ", "NOT_REQUIRED")
    act("NS-OPTIONAL", stage=d["stage"])                           # required=false gate ignored
    gate("NS-OPTIONAL", "FAILED", required=False)
    act("BLK-ACT", stage=d["stage"], pct=30)                       # activity-level blocker (created via API in tests)
    act("BLK-STAGE-A", stage=d["stage2"])                          # stage-level blocker target
    act("BLK-STAGE-B", stage=d["stage2"], pct=100)
    act("ISO", sid=d["V1"])                                        # same id in another version
    act("ISO", stage=d["stage"])
    gate("ISO", "FAILED", sid=d["V1"])                             # failed gate on V1 must not touch V2
    act("OTHER-PROJECT-ACT", sid=d["X2"], project=d["P2"])
    yield d
    for sql in (
        "DELETE FROM audit_logs WHERE project_id IN (%s, %s)",
        "DELETE FROM notifications WHERE project_id IN (%s, %s)",
        "DELETE FROM institutional_incidents WHERE project_id IN (%s, %s)",
        "DELETE FROM issues WHERE project_id IN (%s, %s)",
        "DELETE FROM quality_gates WHERE project_id IN (%s, %s)",
        "DELETE FROM approved_actuals WHERE project_id IN (%s, %s)",
        "DELETE FROM planner_decisions WHERE planner_id = ANY(ARRAY[%s::uuid, %s::uuid])",
    ):
        args = (d["P"], d["P2"]) if "planner_decisions" not in sql else (d["sup"], d["pm"])
        su.execute(sql, args)
    su.execute("DELETE FROM execution_events WHERE project_id IN (%s, %s)", (d["P"], d["P2"]))
    su.execute("DELETE FROM schedule_activities WHERE project_id IN (%s, %s)", (d["P"], d["P2"]))
    su.execute("DELETE FROM stages WHERE project_id IN (%s, %s)", (d["P"], d["P2"]))
    su.execute("DELETE FROM schedules WHERE project_id IN (%s, %s)", (d["P"], d["P2"]))
    su.execute("DELETE FROM project_memberships WHERE project_id IN (%s, %s)", (d["P"], d["P2"]))
    su.execute("DELETE FROM projects WHERE project_id IN (%s, %s)", (d["P"], d["P2"]))
    su.execute("DELETE FROM profiles WHERE id = ANY(ARRAY[%s::uuid, %s::uuid, %s::uuid, %s::uuid])", (d["pm"], d["sup"], d["eng"], d["other"]))
    su.execute("DELETE FROM auth.users WHERE id = ANY(ARRAY[%s::uuid, %s::uuid, %s::uuid, %s::uuid])", (d["pm"], d["sup"], d["eng"], d["other"]))
    su.close()


def _client(w, who, project="P", schedule="V2"):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=w[who], full_name=who, role="SUPERVISOR")
    c = TestClient(app, raise_server_exceptions=False)
    c.headers.update({"X-Project-ID": w[project]})
    return c


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _items(w, who="sup", schedule="V2", project="P"):
    c = _client(w, who, project)
    r = c.get(f"/api/v1/projects/{w[project]}/schedules/{w[schedule]}/progress/breakdown")
    assert r.status_code == 200, r.text
    body = r.json()
    out = {}
    for st in body["stages"]:
        for a in st["activities"]:
            out[a["activity_id"]] = a
    for a in body["unassigned_activities"]:
        out[a["activity_id"]] = a
    return out


def test_quality_hold_is_derived_from_persisted_gates(w):
    it = _items(w)
    cond = lambda a: (it[a]["canonical_state"], it[a]["workflow_condition"])
    assert cond("NS-CLEAN") == ("NOT_STARTED", "NONE")
    assert cond("DONE-PENDING") == ("COMPLETED", "QUALITY_HOLD"), "100% but the required gate is not released"
    assert cond("DONE-PASSED") == ("COMPLETED", "NONE")
    assert cond("DONE-WAIVED") == ("COMPLETED", "NONE")
    assert cond("DONE-NOGATE") == ("COMPLETED", "QUALITY_HOLD"), "quality declared required but nothing recorded"
    assert cond("PROG-FAILED") == ("IN_PROGRESS", "QUALITY_HOLD"), "a FAILED required gate holds at any progress"
    assert cond("PROG-PENDING") == ("IN_PROGRESS", "NONE"), "a pending gate only holds COMPLETION, not work in progress"
    assert cond("NS-HOLDPOINT") == ("NOT_STARTED", "QUALITY_HOLD"), "an unreleased PRE_COMMENCEMENT hold point blocks the start"
    assert cond("NS-INSPHOLD") == ("NOT_STARTED", "NONE"), "an ordinary hold gate does not block the activity it inspects"
    assert cond("REBAR") == ("COMPLETED", "QUALITY_HOLD"), "finished work whose hold inspection is not released"
    assert cond("POUR") == ("NOT_STARTED", "QUALITY_HOLD"), "no pour before the rebar inspection is released"
    assert cond("POUR-SS") == ("NOT_STARTED", "NONE"), "only finish-to-start successors are held"
    assert cond("POUR-DONE") == ("COMPLETED", "NONE"), "completed work is never un-completed by a later hold"
    assert cond("NS-NOTREQ")[1] == "NONE" and cond("NS-OPTIONAL")[1] == "NONE"


def test_gate_transitions_change_the_condition_deterministically(w):
    su = w["su"]
    gid = su.execute("SELECT quality_gate_id FROM quality_gates WHERE activity_id = 'DONE-PENDING' AND project_id = %s", (w["P"],)).fetchone()["quality_gate_id"]
    try:
        su.execute("UPDATE quality_gates SET status = 'FAILED' WHERE quality_gate_id = %s", (gid,))
        assert _items(w)["DONE-PENDING"]["workflow_condition"] == "QUALITY_HOLD"
        su.execute("UPDATE quality_gates SET status = 'PASSED' WHERE quality_gate_id = %s", (gid,))
        assert _items(w)["DONE-PENDING"]["workflow_condition"] == "NONE"
        su.execute("UPDATE quality_gates SET status = 'SUBMITTED' WHERE quality_gate_id = %s", (gid,))
        assert _items(w)["DONE-PENDING"]["workflow_condition"] == "QUALITY_HOLD"
    finally:
        su.execute("UPDATE quality_gates SET status = 'PENDING' WHERE quality_gate_id = %s", (gid,))


def test_quality_on_another_schedule_version_never_leaks(w):
    assert _items(w, schedule="V2")["ISO"]["workflow_condition"] == "NONE"
    assert _items(w, schedule="V1")["ISO"]["workflow_condition"] == "QUALITY_HOLD"


def test_blocker_lifecycle_activity_and_stage_level(w):
    sup = _client(w, "sup")
    base = f"/api/v1/projects/{w['P']}/schedules/{w['V2']}/issues"
    r = sup.post(base, json={"activity_id": "BLK-ACT", "category_code": "MATERIAL_DELIVERY_DELAY", "title": "Steel delivery delayed",
                             "description": "Steel delivery delayed", "blocks_work": True})
    assert r.status_code == 201, r.text
    b1 = r.json()
    assert b1["status"] == "ACTIVE" and b1["reported_by"] == w["sup"] and b1["resolved_by"] is None
    assert _items(w)["BLK-ACT"]["workflow_condition"] == "BLOCKED"
    assert _items(w)["BLK-ACT"]["canonical_state"] == "IN_PROGRESS", "BLOCKED is a condition, not an execution state"

    r = sup.post(base, json={"stage_id": w["stage2"], "category_code": "PERMIT_APPROVAL", "title": "Work permit not issued",
                             "description": "Work permit not issued for the stage", "blocks_work": True})
    assert r.status_code == 201, r.text
    b2 = r.json()
    it = _items(w)
    assert it["BLK-STAGE-A"]["workflow_condition"] == "BLOCKED" and it["BLK-STAGE-B"]["workflow_condition"] == "BLOCKED"
    assert it["NS-CLEAN"]["workflow_condition"] == "NONE", "a stage blocker only affects that stage"

    assert len(sup.get(base).json()) == 2
    r = sup.post(f"{base}/{b1['issue_id']}/resolve", json={"resolution_notes": "Steel arrived"})
    assert r.status_code == 200 and r.json()["status"] == "RESOLVED" and r.json()["resolved_by"] == w["sup"]
    assert _items(w)["BLK-ACT"]["workflow_condition"] == "NONE"
    assert sup.post(f"{base}/{b1['issue_id']}/resolve", json={"resolution_notes": "again"}).status_code == 409
    assert {b["status"] for b in sup.get(base + "?status=ALL").json()} == {"ACTIVE", "RESOLVED"}
    sup.post(f"{base}/{b2['issue_id']}/resolve", json={"resolution_notes": "Permit issued"})
    assert _items(w)["BLK-STAGE-A"]["workflow_condition"] == "NONE"


def test_blocker_governance_and_isolation(w):
    base = f"/api/v1/projects/{w['P']}/schedules/{w['V2']}/issues"
    body = {"activity_id": "NS-CLEAN", "category_code": "SITE_ACCESS", "title": "Gate locked", "description": "Gate locked"}
    # a site engineer reports issues from the field (REPORT_ISSUE) but cannot resolve them (MANAGE_BLOCKERS)
    eng = _client(w, "eng")
    reported = eng.post(base, json=body)
    assert reported.status_code == 201, reported.text
    assert eng.post(f"{base}/{reported.json()['issue_id']}/resolve", json={"resolution_notes": "self-resolve"}).status_code == 403
    assert _client(w, "sup").post(f"{base}/{reported.json()['issue_id']}/resolve", json={"resolution_notes": "gate unlocked"}).status_code == 200
    assert _client(w, "other", project="P2").post(base.replace(w["P"], w["P2"]), json=body).status_code in (403, 404)
    other = _client(w, "other", project="P2")
    assert other.get(base.replace(w["P"], w["P2"])).status_code == 403, "schedule belongs to project P, not P2"
    assert _client(w, "other", project="P2").get(base).status_code == 400, "path project (P) conflicts with header (P2)"
    bad = {"activity_id": "NO-SUCH", "category_code": "SITE_ACCESS", "title": "x y z", "description": "x y z"}
    assert _client(w, "sup").post(base, json=bad).status_code == 404
    assert _client(w, "sup").post(base, json={"category_code": "SITE_ACCESS", "title": "no target", "description": "no target"}).status_code == 422
    assert _client(w, "sup").post(base, json={"activity_id": "OTHER-PROJECT-ACT", "category_code": "SITE_ACCESS", "title": "x y z", "description": "x y z"}).status_code == 404


def test_blockers_are_audited_in_the_project_chain_and_never_deleted(w):
    from backend.shared.audit import verify_audit_chain

    su = w["su"]
    logs = [dict(r) for r in su.execute("SELECT * FROM audit_logs WHERE project_id = %s ORDER BY log_id", (w["P"],)).fetchall()]
    actions = [l["action"] for l in logs]
    assert actions.count("ISSUE_REPORTED") >= 2 and actions.count("ISSUE_RESOLVED") >= 2
    assert verify_audit_chain(logs, allow_subchain=False) == (True, None)
    # history is kept: no DELETE policy exists for authenticated users
    n = su.execute("SELECT count(*) AS n FROM pg_policies WHERE tablename = 'issues' AND cmd = 'DELETE'").fetchone()["n"]
    assert n == 0


def test_condition_priority_reopen_beats_quality_beats_blocked():
    from backend.services.stage_service import StageService

    base = {"actual_pct_complete": 100.0, "wf_q_failed": True, "wf_blockers": 2}
    assert StageService.get_workflow_condition({**base, "is_reopened": True}) == "REWORK_IN_PROGRESS"
    assert StageService.get_workflow_condition({**base, "reopen_status": "REQUESTED"}) == "REOPEN_REQUESTED"
    assert StageService.get_workflow_condition(base) == "QUALITY_HOLD"
    assert StageService.get_workflow_condition({"wf_blockers": 1}) == "BLOCKED"
    assert StageService.get_workflow_condition({}) == "NONE"


def test_releasing_the_predecessor_hold_point_releases_the_successor(w):
    su = w["su"]
    gid = su.execute("SELECT quality_gate_id FROM quality_gates WHERE activity_id = 'REBAR' AND project_id = %s", (w["P"],)).fetchone()["quality_gate_id"]
    try:
        su.execute("UPDATE quality_gates SET status = 'PASSED' WHERE quality_gate_id = %s", (gid,))
        it = _items(w)
        assert it["REBAR"]["workflow_condition"] == "NONE" and it["POUR"]["workflow_condition"] == "NONE"
        su.execute("UPDATE quality_gates SET status = 'FAILED' WHERE quality_gate_id = %s", (gid,))
        assert _items(w)["REBAR"]["workflow_condition"] == "QUALITY_HOLD" and _items(w)["POUR"]["workflow_condition"] == "QUALITY_HOLD"
    finally:
        su.execute("UPDATE quality_gates SET status = 'SUBMITTED' WHERE quality_gate_id = %s", (gid,))
