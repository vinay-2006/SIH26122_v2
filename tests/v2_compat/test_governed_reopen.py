"""D7: governed reopen as an append-only correction -- the original decisions and ledger entries stay, the correction is a fresh approval that supersedes them."""
from datetime import date

import pytest

from domainkit import assert_consistent, connect
from backend.v2.domain import decisions, reopen
from backend.v2.errors import ApiError

pytestmark = pytest.mark.db_write
TODAY = date.today()


def Q(kit, tail):
    return f"/api/v1/projects/{kit.project}{tail}"


@pytest.fixture
def ver(kit):
    with connect() as c:
        return c.execute("select version_id from schedule_versions where project_id = %s and status = 'ACTIVE'", (kit.project,)).fetchone()["version_id"]


def finish_a2010(kit, text="Welding A2010 finished"):
    """A2010 finished: 2000 joints approved together with the actual finish the engineer reported"""
    r = kit.submit("A2010", qty=2000, text=text, claimed_finish=TODAY)
    kit.approve(r["claim_id"])
    return r


def test_a_completed_activity_cannot_be_changed_by_an_ordinary_approval(kit, lg, ver):
    finish_a2010(kit)
    assert kit.pct("A2010") == 100
    with pytest.raises(ApiError) as e:
        kit.approve(kit.submit("A2010", qty=1500, text="Welding A2010 again", days_ago=0)["claim_id"])
    assert e.value.code == "REOPEN_NOT_ALLOWED" and e.value.status == 409


def test_request_decide_and_the_fresh_approval_supersede_without_deleting_anything(kit, lg, ver):
    first = finish_a2010(kit)
    ledger_before = kit.ledger()
    # an engineer requests, with a reason; a Supervisor decides, with notes
    r = lg.post(Q(kit, f"/schedules/{ver}/activities/A2010/reopen"), kit.world.se, json={"reason": "QUANTITY_CORRECTION", "justification": "Only 1500 joints are actually welded",
                                                                                          "evidence_event_ids": [str(first["claim_id"])]})
    assert r.status_code == 201, r.text
    assert r.json()["reopen_status"] == "REQUESTED"
    pending = lg.get(Q(kit, f"/schedules/{ver}/reopen-requests?status=REQUESTED"), kit.world.sup).json()
    assert [p["activity_id"] for p in pending] == ["A2010"]
    assert lg.post(Q(kit, f"/schedules/{ver}/activities/A2010/reopen/decide"), kit.world.sup, json={"decision": "APPROVED", "notes": ""}).status_code == 422       # notes are mandatory
    d = lg.post(Q(kit, f"/schedules/{ver}/activities/A2010/reopen/decide"), kit.world.sup, json={"decision": "APPROVED", "notes": "Verified with the foreman", "rework_instructions": "Re-measure joints"})
    assert d.status_code == 200 and d.json()["reopen_status"] == "APPROVED"
    # approval alone changes NOTHING in the ledgers
    assert kit.ledger() == ledger_before and kit.pct("A2010") == 100
    acts = {a["activity_id"]: a for a in lg.get(f"/api/v1/schedules/{ver}/activities", kit.world.sup).json()}
    assert acts["A2010"]["execution_state"] == "REOPENED"
    # the correction is a FRESH claim and a FRESH approval
    fix = kit.submit("A2010", qty=1500, text="Rework: recount of welded joints on A2010", days_ago=0)
    prev = decisions.preview_decision(kit.sup, fix["claim_id"])
    assert prev["ok"] and prev["rework"] is True and prev["applied"][0]["approved_cumulative"] == "1500.000"
    done = kit.approve(fix["claim_id"])
    assert done["status"] == "APPROVED" and kit.pct("A2010") == 75
    res, act = kit.ledger()
    assert len(res) == len(ledger_before[0]) + 1 and len(act) == len(ledger_before[1]) + 1                                    # appended, never rewritten
    assert res[0] == ledger_before[0][0]                                                                                    # the original entry is byte-for-byte the same
    with connect() as c:
        row = c.execute("select * from approved_resource_progress order by entry_seq desc limit 1").fetchone()
        assert row["supersedes_entry_id"] == res[0]["entry_id"] and row["incremental_qty"] == -500
        head = c.execute("select actual_finish from approved_activity_progress order by entry_seq desc limit 1").fetchone()
        assert head["actual_finish"] is None                                                                                # the completion is reopened on the record
        r2 = c.execute("select status, closing_decision_id from activity_reopens").fetchone()
        assert r2["status"] == "CLOSED" and r2["closing_decision_id"] == done["decision_id"]
        assert c.execute("select count(*) n from audit_logs where action in ('REOPEN_REQUESTED','REOPEN_APPROVED','REOPEN_CLOSED')").fetchone()["n"] == 3
    assert_consistent(kit)                                                                                                  # hash chain valid; ledgers consistent; rollup equals the independent recomputation
    # the reopen is spent: ordinary rules apply again
    with pytest.raises(ApiError):
        kit.approve(kit.submit("A2010", qty=1400, text="again", days_ago=0)["claim_id"])


def test_a_rejected_reopen_changes_nothing(kit, lg, ver):
    first = finish_a2010(kit)
    lg.post(Q(kit, f"/schedules/{ver}/activities/A2010/reopen"), kit.world.sup, json={"reason": "OTHER", "justification": "second thoughts"})
    d = lg.post(Q(kit, f"/schedules/{ver}/activities/A2010/reopen/decide"), kit.world.sup, json={"decision": "REJECTED", "notes": "Measurement book confirms 2000"})
    assert d.json()["reopen_status"] == "REJECTED"
    with pytest.raises(ApiError) as e:
        kit.approve(kit.submit("A2010", qty=1500, text="Rework: recount", days_ago=0)["claim_id"])
    assert e.value.code == "REOPEN_NOT_ALLOWED"
    again = lg.post(Q(kit, f"/schedules/{ver}/activities/A2010/reopen"), kit.world.se, json={"reason": "OTHER", "justification": "new evidence"})
    assert again.status_code == 201                                                                                         # a rejected request may be raised again


def test_only_a_completed_activity_can_be_reopened_and_roles_are_enforced(kit, lg, ver):
    j = {"reason": "OTHER", "justification": "please"}
    assert lg.post(Q(kit, f"/schedules/{ver}/activities/A2010/reopen"), kit.world.se, json=j).status_code == 422             # not completed
    finish_a2010(kit)
    assert lg.post(Q(kit, f"/schedules/{ver}/activities/A2010/reopen"), kit.world.pm, json=j).status_code == 403             # a PM never requests
    assert lg.post(Q(kit, f"/schedules/{ver}/activities/A2010/reopen"), kit.world.outsider, json=j).status_code == 403
    assert lg.post(Q(kit, f"/schedules/{ver}/activities/A2010/reopen"), kit.world.se, json=j).status_code == 201
    assert lg.post(Q(kit, f"/schedules/{ver}/activities/A2010/reopen"), kit.world.sup, json=j).status_code == 409           # one open reopen per activity
    for who in (kit.world.se, kit.world.pm):
        assert lg.post(Q(kit, f"/schedules/{ver}/activities/A2010/reopen/decide"), who, json={"decision": "APPROVED", "notes": "ok ok"}).status_code == 403
    pm_view = lg.get(Q(kit, f"/schedules/{ver}/reopen-requests"), kit.world.pm).json()
    assert pm_view and "justification" not in pm_view[0]                                                                      # a PM sees the lifecycle only


def test_the_database_refuses_a_superseding_entry_without_an_approved_reopen(kit, lg, ver):
    finish_a2010(kit)
    from v2api import connect as raw
    with raw() as c:
        c.execute("select set_config('app.actor_id', %s, true)", (str(kit.sup.user_id),))
        head = c.execute("select entry_id, activity_uid, assignment_uid from approved_resource_progress order by entry_seq desc limit 1").fetchone()
        ev = kit.submit("A2010", qty=1500, text="Rework: recount")["claim_id"]
        with pytest.raises(Exception) as e:
            c.execute("insert into approved_resource_progress (project_id, activity_uid, assignment_uid, decision_id, as_of_date, cumulative_qty, supersedes_entry_id) "
                      "values (%s,%s,%s,%s,current_date,1500,%s)", (kit.project, head["activity_uid"], head["assignment_uid"], ev, head["entry_id"]))
        assert "REOPEN_REQUIRED" in str(e.value) or "decision" in str(e.value).lower() or "approved" in str(e.value).lower()
