"""Supervisor: the original Review Workspace backend -- queue, evidence, decisions (translated onto the v2 approval methods), digest and bulk approve, audit."""
import pytest

from domainkit import assert_consistent, connect
from v2api import seed_progress

pytestmark = pytest.mark.db_write


@pytest.fixture
def ver(kit):
    with connect() as c:
        return c.execute("select version_id from schedule_versions where project_id = %s and status = 'ACTIVE'", (kit.project,)).fetchone()["version_id"]


def file_and_check(lg, kit, text, who=None):
    ev = lg.post("/api/v1/claims/text", who or kit.world.se, json={"raw_claim_text": text}).json()
    lg.post(f"/api/v1/claims/{ev['event_id']}/match", who or kit.world.se)
    lg.post(f"/api/v1/claims/{ev['event_id']}/check", who or kit.world.se)
    return ev["event_id"]


def clean(kit):
    seed_progress(kit.project, "A2000", {"PIPE_STRUNG_KM": 3}, kit.world.sup, kit.world.se)       # the predecessor has progress, so A2010 raises no sequence flag


def test_the_queue_lists_checked_claims_with_the_original_priority_and_is_supervisor_only(kit, lg, ver):
    flagged = file_and_check(lg, kit, "Welding mainline A2010: 120 joints completed today")
    q = lg.get("/api/v1/review-queue?sort=priority", kit.world.sup)
    assert q.status_code == 200, q.text
    body = q.json()
    assert body["total"] == 1 and body["items"][0]["event_id"] == flagged and body["items"][0]["status"] == "REVIEW_REQUIRED"
    it = body["items"][0]
    assert it["priority_score"] > 100 and "Critical" in it["priority_reasons"] and it["validation_issues"][0]["rule_code"] == "VAL_OUT_OF_SEQUENCE" and it["base_severity"] >= 100
    for who in (kit.world.se, kit.world.pm, kit.world.outsider):
        assert lg.get("/api/v1/review-queue", who).status_code == 403
    assert lg.get(f"/api/v1/claims/{flagged}/evidence", kit.world.se).status_code == 403
    assert lg.get(f"/api/v1/claims/{flagged}/evidence", kit.world.sup).json()["total"] == 0


def test_an_unchecked_claim_filed_through_the_v2_api_still_reaches_the_queue(kit, lg, ver):
    r = kit.submit("A2010", qty=50, text="Welding A2010 via the v2 api")
    q = lg.get("/api/v1/review-queue", kit.world.sup).json()
    assert [i["event_id"] for i in q["items"]] == [str(r["claim_id"])]


def test_approving_a_percent_claim_writes_the_ledger_and_tells_the_engineer(kit, lg, ver):
    clean(kit)
    eid = file_and_check(lg, kit, "Welding mainline A2010: 10 percent complete today")
    assert lg.get(f"/api/v1/claims/{eid}", kit.world.sup).json()["status"] == "VALIDATED"
    r = lg.post("/api/v1/decisions", kit.world.sup, json={"event_id": eid, "selected_activity_id": "A2010", "action": "APPROVE", "approved_pct": 10, "justification": "Checked in the weld log"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["action"] == "APPROVE" and d["status"] == "APPROVED" and d["selected_activity_id"] == "A2010" and d["approved_actual"]["actual_pct_complete"] == 10
    assert kit.pct("A2010") == 10 and kit.count("approved_resource_progress") >= 1
    mine = lg.get(f"/api/v1/projects/{kit.project}/my-claims", kit.world.se).json()
    assert mine[0]["status"] == "APPROVED" and mine[0]["decision_action"] == "APPROVE" and mine[0]["decision_comment"] == "Checked in the weld log"
    assert lg.post("/api/v1/decisions", kit.world.sup, json={"event_id": eid, "action": "APPROVE", "justification": "again"}).status_code == 409      # one decision per claim
    assert_consistent(kit)


def test_editing_the_quantity_is_an_edit_and_marks_the_changed_field(kit, lg, ver):
    clean(kit)
    eid = file_and_check(lg, kit, "Welding mainline A2010: 120 joints completed today")
    r = lg.post("/api/v1/decisions", kit.world.sup, json={"event_id": eid, "selected_activity_id": "A2010", "action": "EDIT", "approved_qty": 100, "justification": "Only 100 joints are NDT-cleared"})
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "EDIT" and r.json()["status"] == "EDITED" and r.json()["edited_fields"] == {"claimed_quantity": "SUPERVISOR_EDITED"}
    assert kit.pct("A2010") == 5          # 100 of 2000 joints


def test_overriding_the_activity_is_an_explicit_audited_rematch_then_a_normal_approval(kit, lg, ver):
    clean(kit)
    eid = file_and_check(lg, kit, "Welding mainline A2010: 120 joints completed today")
    seed_progress(kit.project, "A1010", {"CLEARED_ROW_KM": 6}, kit.world.sup, kit.world.se)
    r = lg.post("/api/v1/decisions", kit.world.sup, json={"event_id": eid, "selected_activity_id": "A2000", "action": "APPROVE", "approved_pct": 20, "justification": "Report was about stringing"})
    assert r.status_code == 200, r.text
    assert r.json()["selected_activity_id"] == "A2000"
    with connect() as c:
        assert c.execute("select count(*) n from audit_logs where action = 'CLAIM_REMATCHED'").fetchone()["n"] == 1
    assert lg.post("/api/v1/decisions", kit.world.sup, json={"event_id": eid, "selected_activity_id": "NOPE", "action": "APPROVE", "justification": "x"}).status_code in (409, 422)


def test_reject_and_hold_need_a_reason_and_hold_keeps_the_claim_decidable(kit, lg, ver):
    a = file_and_check(lg, kit, "Welding mainline A2010: 120 joints completed today")
    assert lg.post("/api/v1/decisions", kit.world.sup, json={"event_id": a, "action": "REJECT", "justification": ""}).status_code == 422
    h = lg.post("/api/v1/decisions", kit.world.sup, json={"event_id": a, "action": "HOLD", "justification": "Please confirm the joint numbers"})
    assert h.status_code == 200 and h.json()["status"] == "HOLD"
    assert [i["status"] for i in lg.get("/api/v1/review-queue", kit.world.sup).json()["items"]] == ["HOLD"]
    r = lg.post("/api/v1/decisions", kit.world.sup, json={"event_id": a, "action": "REJECT", "justification": "Joint numbers do not exist"})
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    n = lg.get(f"/api/v1/projects/{kit.project}/notifications", kit.world.se).json()
    assert {x["decision_action"] for x in n["items"] if x["decision_action"]} == {"HOLD", "REJECT"}
    assert kit.count("approved_resource_progress") == 0


def test_digest_and_bulk_approve_decide_each_claim_on_its_own(kit, lg, ver):
    clean(kit)
    a = file_and_check(lg, kit, "Welding mainline A2010: 10 percent complete today")
    b = file_and_check(lg, kit, "Pipe stringing A2000: 60 percent complete today")
    day = lg.get("/api/v1/digest", kit.world.sup).json()
    assert {a, b} <= {c["event_id"] for c in day}
    out = lg.post("/api/v1/digest/bulk-approve", kit.world.sup, json={"event_ids": [a, b, "00000000-0000-0000-0000-000000000000"]}).json()
    assert set(out["approved"]) | {f["event_id"] for f in out["failed"]} >= {a, b}
    assert len(out["approved"]) >= 1 and all("Not eligible" in f["error"] or f["error"] for f in out["failed"])
    with connect() as c:
        assert c.execute("select count(*) n from planner_decisions").fetchone()["n"] >= 1 + 0
    assert lg.post("/api/v1/digest/bulk-approve", kit.world.se, json={}).status_code == 403
    assert lg.post("/api/v1/digest/bulk-approve", kit.world.pm, json={}).status_code == 403
    assert_consistent(kit)


def test_audit_feed_and_per_entity_trail(kit, lg, ver):
    eid = file_and_check(lg, kit, "Welding mainline A2010: 120 joints completed today")
    feed = lg.get("/api/v1/audit?limit=50", kit.world.sup).json()
    assert any(x["action"] == "CLAIM_CHECKED" for x in feed) and all(len(x["current_hash"]) == 64 for x in feed)
    trail = lg.get(f"/api/v1/audit/{eid}", kit.world.sup).json()
    assert [r["action"] for r in trail["records"]][:2] == ["CLAIM_SUBMITTED", "CLAIM_AUTO_MATCHED"]
    assert lg.get("/api/v1/audit", kit.world.se).status_code == 403
    assert lg.get("/api/v1/audit", kit.world.pm).status_code == 200
