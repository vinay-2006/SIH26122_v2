"""D8: quality gates and ITP on v2 -- mandatory gates block approval, non-blocking ones do not, roles are enforced by the API and the database."""
import uuid

import pytest

from domainkit import connect, claims, decisions
from backend.v2.errors import ApiError

pytestmark = pytest.mark.db_write


def Q(kit, tail):
    return f"/api/v1/projects/{kit.project}{tail}"


@pytest.fixture
def ver(kit):
    with connect() as c:
        return c.execute("select version_id from schedule_versions where project_id = %s and status = 'ACTIVE'", (kit.project,)).fetchone()["version_id"]


def gate(lg, kit, **kw):
    body = {"gate_name": "Root pass inspection", "gate_type": "WELD_INSPECTION", "checkpoint_category": "HOLD", "activity_id": "A2010", "required": True, **kw}
    r = lg.post(Q(kit, "/quality-gates"), kit.world.sup, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def claim(kit):
    return kit.submit("A2010", qty=100)["claim_id"]


def test_existing_projects_have_no_gates_and_nothing_is_imposed(kit, lg, ver):
    assert lg.get(Q(kit, f"/schedules/{ver}/quality-gates"), kit.world.se).json() == []
    st = lg.get(Q(kit, "/activities/A2010/quality-status"), kit.world.sup).json()
    assert st["quality_gate_required"] is False and st["is_eligible"] is True and st["status"] == "NOT_REQUIRED"
    kit.approve(claim(kit))                                                       # approval works exactly as before
    assert kit.count("approved_resource_progress") == 1


def test_a_mandatory_gate_blocks_approval_until_passed_and_inspection_is_not_progress(kit, lg, ver):
    g = gate(lg, kit)
    cid = claim(kit)
    with pytest.raises(ApiError) as e:
        kit.approve(cid)
    assert e.value.code == "QUALITY_HOLD" and e.value.status == 409
    assert kit.count("approved_resource_progress") == 0 and kit.count("approved_activity_progress") == 0
    # the hold point is released on evidence, not assertion
    assert lg.post(Q(kit, f"/quality-gates/{g['quality_gate_id']}/pass"), kit.world.sup, json={}).status_code == 409
    ev = lg.post(Q(kit, f"/quality-gates/{g['quality_gate_id']}/evidence"), kit.world.se, json={"evidence_type": "WELD_INSPECTION", "result": "PASS", "inspector_name": "R. Das"})
    assert ev.status_code == 201, ev.text
    assert lg.get(Q(kit, f"/schedules/{ver}/quality-gates"), kit.world.se).json()[0]["status"] == "SUBMITTED"       # still not released
    with pytest.raises(ApiError):
        kit.approve(cid)
    assert lg.post(Q(kit, f"/quality-gates/{g['quality_gate_id']}/pass"), kit.world.sup, json={"remarks": "witnessed"}).json()["status"] == "PASSED"
    kit.approve(cid)
    assert kit.count("approved_resource_progress") == 1 and kit.pct("A2010") > 0
    # status of inspection and status of progress are separate facts
    st = lg.get(Q(kit, "/activities/A2010/quality-status"), kit.world.sup).json()
    assert st["status"] == "PASSED" and st["passed_gates"] == 1


def test_a_non_blocking_gate_never_blocks(kit, lg, ver):
    gate(lg, kit, required=False, checkpoint_category="REVIEW", gate_type="INSPECTION")
    kit.approve(claim(kit))
    assert kit.count("approved_resource_progress") == 1


def test_a_fail_record_prevents_release_and_a_waiver_is_justified_and_audited(kit, lg, ver):
    g = gate(lg, kit)
    lg.post(Q(kit, f"/quality-gates/{g['quality_gate_id']}/evidence"), kit.world.se, json={"evidence_type": "NDT_RESULT", "result": "FAIL"})
    assert lg.post(Q(kit, f"/quality-gates/{g['quality_gate_id']}/pass"), kit.world.sup, json={}).status_code == 409
    assert lg.post(Q(kit, f"/quality-gates/{g['quality_gate_id']}/waive"), kit.world.sup, json={"waiver_reason": "x"}).status_code == 422
    w = lg.post(Q(kit, f"/quality-gates/{g['quality_gate_id']}/waive"), kit.world.sup, json={"waiver_reason": "Client accepted the weld map in writing"})
    assert w.status_code == 200 and w.json()["status"] == "WAIVED" and w.json()["waiver_reason"].startswith("Client")
    kit.approve(claim(kit))
    with connect() as c:
        assert c.execute("select count(*) n from audit_logs where action in ('QUALITY_GATE_CREATED','QUALITY_EVIDENCE_SUBMITTED','QUALITY_GATE_WAIVED')").fetchone()["n"] == 3
    assert lg.post(Q(kit, f"/quality-gates/{g['quality_gate_id']}/fail"), kit.world.sup, json={}).status_code == 409      # a waived gate is final


def test_roles_are_enforced(kit, lg, ver):
    body = {"gate_name": "x", "gate_type": "INSPECTION", "activity_id": "A2010"}
    for who in (kit.world.se, kit.world.pm):
        assert lg.post(Q(kit, "/quality-gates"), who, json=body).status_code == 403
        assert lg.post(Q(kit, "/itps"), who, json={"title": "ITP"}).status_code == 403
    g = gate(lg, kit)
    gid = g["quality_gate_id"]
    for who in (kit.world.se, kit.world.pm):
        for tail in ("pass", "fail"):
            assert lg.post(Q(kit, f"/quality-gates/{gid}/{tail}"), who, json={}).status_code == 403
        assert lg.post(Q(kit, f"/quality-gates/{gid}/waive"), who, json={"waiver_reason": "because"}).status_code == 403
    assert lg.post(Q(kit, f"/quality-gates/{gid}/evidence"), kit.world.pm, json={"evidence_type": "OTHER"}).status_code == 403          # a PM never submits evidence
    assert lg.get(Q(kit, f"/schedules/{ver}/quality-gates"), kit.world.pm).status_code == 200                                           # but may read gate status
    assert lg.get(Q(kit, f"/schedules/{ver}/quality-gates"), kit.world.outsider).status_code == 403
    itp = lg.post(Q(kit, "/itps"), kit.world.sup, json={"title": "Mainline welding ITP", "discipline": "PIPING", "status": "ACTIVE"})
    assert itp.status_code == 201 and lg.get(Q(kit, "/itps"), kit.world.se).json()[0]["title"] == "Mainline welding ITP"


def test_the_database_enforces_the_hold_even_if_the_api_is_bypassed(kit, lg, ver):
    gate(lg, kit)
    cid = claim(kit)
    from v2api import connect as raw
    with raw() as c:
        c.execute("select set_config('app.actor_id', %s, true)", (str(kit.sup.user_id),))
        act = c.execute("select matched_activity_uid from execution_events where event_id = %s", (cid,)).fetchone()["matched_activity_uid"]
        with pytest.raises(Exception) as e:
            c.execute("insert into planner_decisions (project_id, event_id, selected_activity_uid, action, method, justification, decided_by) values (%s,%s,%s,'APPROVE','QUANTITIES_AS_CLAIMED','sneaky',%s)",
                      (kit.project, cid, act, kit.sup.user_id))
        assert "QUALITY_HOLD" in str(e.value)


def test_gates_are_project_scoped(kit, lg, ver, api):
    from v2api import build_and_activate
    from legacykit import Legacy
    gate(lg, kit)
    build_and_activate(api, kit.world.pm2, kit.world.project2, "csv")
    with connect() as c:
        ver2 = c.execute("select version_id from schedule_versions where project_id = %s and status = 'ACTIVE'", (kit.world.project2,)).fetchone()["version_id"]
    other = Legacy(api, kit.world.project2, ver2)
    assert other.get(f"/api/v1/projects/{kit.world.project2}/schedules/{ver2}/quality-gates", kit.world.pm2).json() == []          # the other project has none
    assert other.get(f"/api/v1/projects/{kit.project}/schedules/{ver}/quality-gates", kit.world.pm2).status_code == 403            # and cannot read this project's
    assert lg.get(f"/api/v1/projects/{kit.world.project2}/schedules/{ver}/quality-gates", kit.world.sup).status_code == 403
