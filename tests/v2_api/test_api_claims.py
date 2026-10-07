"""Claim workflows over HTTP: engineer files, supervisor decides, nothing else creates progress."""
import threading
import uuid
from decimal import Decimal as D

from apikit import TODAY, activity_pct, claim_body, decide, file_claim, jpeg, summary, upload, url
from v2api import connect


def test_activities_list_shows_measured_assignments_and_approved_progress(kit, api):
    r = api.get(url(kit, "/activities"), kit.world.se, params={"q": "weld", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    a = next(x for x in body["items"] if x["external_activity_id"] == "A2010")
    assert body["version"]["version_no"] == 1 and a["physical_pct"] == 0 and a["claim_types"] == ["QUANTITY", "PERCENT"]
    asg = a["measured_assignments"][0]
    assert asg["unit_of_measure"] and asg["baseline_qty"] and asg["approved_cumulative_qty"] is None
    assert "storage" not in r.text and "password" not in r.text.lower()


def test_pagination_envelope_and_limits(kit, api):
    p1 = api.get(url(kit, "/activities"), kit.world.se, params={"limit": 4}).json()
    assert len(p1["items"]) == 4 and p1["next_offset"] == 4
    p2 = api.get(url(kit, "/activities"), kit.world.se, params={"limit": 4, "offset": 4}).json()
    assert {a["activity_uid"] for a in p1["items"]}.isdisjoint({a["activity_uid"] for a in p2["items"]})
    assert api.get(url(kit, "/activities"), kit.world.se, params={"limit": 500}).status_code == 422
    last = api.get(url(kit, "/activities"), kit.world.se, params={"limit": 200}).json()
    assert last["next_offset"] is None or last["next_offset"] == 200


def test_site_engineer_claim_is_pending_and_does_not_move_progress(kit, api):
    doc = upload(kit, api, "photo.jpg", jpeg(), "PHOTO")
    c = file_claim(kit, api, "A2010", 500, evidence_document_ids=[doc["document_id"]])
    assert c["status"] == "MATCHED" and c["quantities"][0]["reported_qty"] == 500
    assert summary(kit, api)["physical_pct"] == 0 and activity_pct(kit, api, "A2010") == 0
    q = api.get(url(kit, "/my-claims"), kit.world.se).json()
    assert [x["event_id"] for x in q["items"]] == [c["claim_id"]] and q["items"][0]["status"] == "MATCHED"
    full = api.get(url(kit, f"/claims/{c['claim_id']}"), kit.world.se).json()
    assert [e["document_id"] for e in full["evidence"]] == [doc["document_id"]]
    assert summary(kit, api)["claims"] == {"scope": "aggregate", **summary(kit, api)["claims"]} and summary(kit, api)["claims"]["pending_total"] == 1
    with connect() as c2:
        assert c2.execute("select count(*) n from approved_resource_progress").fetchone()["n"] == 0


def test_validation_errors_use_the_error_envelope(kit, api):
    r = api.post(url(kit, "/claims"), kit.world.se, json={"event_date": "not-a-date", "raw_text": "x", "bogus": 1})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    fields = {d["field"] for d in r.json()["error"]["details"]}
    assert {"event_date", "raw_text", "bogus"} <= fields
    for body, code in [(claim_body(kit, "A2010", 5, days_ago=-3), "FUTURE_DATE"), (claim_body(kit, "A2010"), "EMPTY_CLAIM"),
                       (claim_body(kit, "A2010", 5, uom="furlongs"), None)]:
        r = api.post(url(kit, "/claims"), kit.world.se, json=body)
        if code:
            assert r.status_code == 422 and r.json()["error"]["code"] == code, r.text
    assert api.post(url(kit, "/claims"), kit.world.se, json=claim_body(kit, "A2010", -5)).status_code == 422
    assert api.post(url(kit, "/claims"), kit.world.se, json=claim_body(kit, "A2010", pct=101)).status_code == 422


def test_idempotency_key_replays_and_never_creates_a_second_claim(kit, api):
    h = {"Idempotency-Key": "claim-key-0001"}
    first = api.post(url(kit, "/claims"), kit.world.se, json=claim_body(kit, "A2010", 100), headers=h)
    again = api.post(url(kit, "/claims"), kit.world.se, json=claim_body(kit, "A2010", 100), headers=h)
    assert first.status_code == again.status_code == 201 and first.json() == again.json() and again.headers["Idempotent-Replay"] == "true"
    assert "Idempotent-Replay" not in first.headers
    other = api.post(url(kit, "/claims"), kit.world.se, json=claim_body(kit, "A2010", 200), headers=h)
    assert other.status_code == 422 and other.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert api.post(url(kit, "/claims"), kit.world.se, json=claim_body(kit, "A2010", 100), headers={"Idempotency-Key": "short"}).json()["error"]["code"] == "BAD_IDEMPOTENCY_KEY"
    assert kit.count("execution_events") == 1
    # a different engineer using the same key is a different request
    third = api.post(url(kit, "/claims"), kit.world.se2, json=claim_body(kit, "A2010", 100, text="second engineer's own report"), headers=h)
    assert third.status_code == 201 and third.json()["claim_id"] != first.json()["claim_id"]


def test_a_failed_request_does_not_poison_its_idempotency_key(kit, api):
    h = {"Idempotency-Key": "claim-key-0002"}
    bad = api.post(url(kit, "/claims"), kit.world.se, json=claim_body(kit, "A2010", 100, days_ago=-1), headers=h)
    assert bad.status_code == 422
    ok = api.post(url(kit, "/claims"), kit.world.se, json=claim_body(kit, "A2010", 100), headers=h)
    assert ok.status_code == 201


def test_concurrent_duplicate_submissions_with_one_key_create_one_claim(kit, api):
    h = {"Idempotency-Key": "claim-key-race1"}
    out = []
    def go():
        out.append(api.post(url(kit, "/claims"), kit.world.se, json=claim_body(kit, "A2010", 100), headers=h))
    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    codes = sorted(r.status_code for r in out)
    assert set(codes) <= {201, 409} and codes.count(201) >= 1
    assert kit.count("execution_events") == 1
    ids = {r.json()["claim_id"] for r in out if r.status_code == 201}
    assert len(ids) == 1


def test_identical_content_is_refused_and_does_not_leak_another_engineers_claim(kit, api):
    mine = file_claim(kit, api, "A2010", 100, text="same words")
    again = api.post(url(kit, "/claims"), kit.world.se, json=claim_body(kit, "A2010", 100, text="same words"))
    assert again.status_code == 409 and again.json()["error"]["code"] == "DUPLICATE_CLAIM" and again.json()["error"]["details"]["claim_id"] == mine["claim_id"]
    theirs = api.post(url(kit, "/claims"), kit.world.se2, json=claim_body(kit, "A2010", 100, text="same words"))
    assert theirs.status_code == 409 and theirs.json()["error"]["details"] is None


def test_supervisor_approval_updates_the_ledger_once_and_keeps_the_reported_figure(kit, api):
    c = file_claim(kit, api, "A2010", 500)
    prev = api.post(url(kit, f"/claims/{c['claim_id']}/decision-preview"), kit.world.sup, json={"action": "APPROVE"})
    assert prev.status_code == 200 and prev.json()["ok"] is True
    assert kit.count("planner_decisions") == 0
    d = decide(kit, api, c["claim_id"])
    assert d["status"] == "APPROVED" and d["method"] == "QUANTITIES_AS_CLAIMED"
    assert activity_pct(kit, api, "A2010") == D("25") and summary(kit, api)["physical_pct"] > 0
    assert kit.count("approved_resource_progress") == 1 and kit.count("notifications", "notification_type = 'CLAIM_DECISION'") == 1
    again = api.post(url(kit, f"/claims/{c['claim_id']}/decision"), kit.world.sup, json={"action": "APPROVE"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "CLAIM_NOT_DECIDABLE"
    assert kit.count("approved_resource_progress") == 1
    full = api.get(url(kit, f"/claims/{c['claim_id']}"), kit.world.sup).json()
    assert full["status"] == "APPROVED" and full["quantities"][0]["reported_qty"] == 500 and full["decisions"][0]["method"] == "QUANTITIES_AS_CLAIMED"


def test_edit_keeps_both_reported_and_approved_values(kit, api):
    c = file_claim(kit, api, "A2010", 800)
    asg = str(kit.asg("A2010", "WELD_JOINTS"))
    d = decide(kit, api, c["claim_id"], action="EDIT", justification="Verified 600 in the joint register", approved_quantities={asg: {"cumulative": 600}})
    assert d["method"] == "MANUAL_QUANTITIES" and activity_pct(kit, api, "A2010") == D("30")
    got = api.get(url(kit, f"/claims/{c['claim_id']}"), kit.world.sup).json()
    assert got["quantities"][0]["reported_qty"] == 800 and kit.ledger()[0][0]["cumulative_qty"] == 600


def test_percent_only_claims_are_never_silently_converted(kit, api):
    c = file_claim(kit, api, "A2010", None, pct=40)
    bad = api.post(url(kit, f"/claims/{c['claim_id']}/decision"), kit.world.sup, json={"action": "APPROVE"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "PERCENT_METHOD_REQUIRED", bad.text
    assert bad.json()["error"]["details"]["choices"] == ["APPLY_PCT_TO_ASSIGNMENTS", "MANUAL_QUANTITIES"]
    assert kit.ledger() == ([], []) and activity_pct(kit, api, "A2010") == 0
    d = decide(kit, api, c["claim_id"], method="APPLY_PCT_TO_ASSIGNMENTS")
    assert d["method"] == "APPLY_PCT_TO_ASSIGNMENTS" and activity_pct(kit, api, "A2010") == D("40")
    assert d["applied"][0]["source"] == "PCT" and d["applied"][0]["approved_cumulative"] == "800.000"


def test_overrun_beyond_tolerance_needs_acknowledgement_and_is_not_clamped(kit, api):
    c = file_claim(kit, api, "A2010", 2600)
    r = api.post(url(kit, f"/claims/{c['claim_id']}/decision"), kit.world.sup, json={"action": "APPROVE"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "OVERRUN_ACK_REQUIRED" and r.json()["error"]["details"]["overruns"][0]["beyond_tolerance"] is True, r.text
    d = decide(kit, api, c["claim_id"], overrun_ack_note="Re-welded joints counted twice, QA agrees")
    assert kit.ledger()[0][0]["cumulative_qty"] == 2600 and activity_pct(kit, api, "A2010") == D("100")
    full = api.get(url(kit, f"/claims/{c['claim_id']}"), kit.world.sup).json()
    assert full["decisions"][0]["overrun_ack"] is True and "twice" in full["decisions"][0]["overrun_ack_note"] and d["status"] == "APPROVED"


def test_clarification_loop_hold_answer_then_approve(kit, api):
    c = file_claim(kit, api, "A2010", 300)
    q = api.post(url(kit, f"/claims/{c['claim_id']}/clarification-request"), kit.world.sup, json={"question": "Which section of the spread?"})
    assert q.status_code == 201 and q.json()["status"] == "DISPUTED"
    assert api.post(url(kit, f"/claims/{c['claim_id']}/withdraw"), kit.world.se2, json={"reason": "not mine"}).status_code == 404
    assert activity_pct(kit, api, "A2010") == 0
    mine = api.get(url(kit, f"/claims/{c['claim_id']}"), kit.world.se).json()
    assert mine["clarification_status"] == "ASKED" and mine["clarification_question"] == "Which section of the spread?"
    a = api.post(url(kit, f"/claims/{c['claim_id']}/clarification-answer"), kit.world.se, json={"answer": "Section 3, km 14-18"})
    assert a.status_code == 200 and a.json()["clarification_status"] == "ANSWERED"
    decide(kit, api, c["claim_id"])
    assert activity_pct(kit, api, "A2010") == D("15")
    assert kit.count("notifications", "notification_type = 'CLAIM_CLARIFICATION'") == 1


def test_withdrawal_is_own_pending_only_and_keeps_the_record(kit, api):
    doc = upload(kit, api, "daily.txt", b"A2010 welded 100 joints cumulative", "EVIDENCE")
    c = file_claim(kit, api, "A2010", 100, evidence_document_ids=[doc["document_id"]])
    assert api.post(url(kit, f"/claims/{c['claim_id']}/withdraw"), kit.world.sup, json={"reason": "supervisors do not withdraw"}).status_code == 403
    assert api.post(url(kit, f"/claims/{c['claim_id']}/withdraw"), kit.world.se, json={"reason": "x"}).status_code == 422
    w = api.post(url(kit, f"/claims/{c['claim_id']}/withdraw"), kit.world.se, json={"reason": "Entered against the wrong activity"})
    assert w.status_code == 200 and w.json()["status"] == "WITHDRAWN"
    full = api.get(url(kit, f"/claims/{c['claim_id']}"), kit.world.se).json()
    assert full["status"] == "WITHDRAWN" and full["quantities"] and [e["document_id"] for e in full["evidence"]] == [doc["document_id"]]
    assert kit.count("audit_logs", "action = 'CLAIM_WITHDRAWN'") == 1
    gone = api.post(url(kit, f"/claims/{c['claim_id']}/decision"), kit.world.sup, json={"action": "APPROVE"})
    assert gone.status_code == 409
    c2 = file_claim(kit, api, "A2010", 150)
    decide(kit, api, c2["claim_id"])
    late = api.post(url(kit, f"/claims/{c2['claim_id']}/withdraw"), kit.world.se, json={"reason": "changed my mind"})
    assert late.status_code == 409 and late.json()["error"]["code"] == "CLAIM_ALREADY_FINAL"
    assert activity_pct(kit, api, "A2010") == D("7.5")


def test_rejection_then_linked_correction(kit, api):
    c = file_claim(kit, api, "A2010", 900, text="first attempt")
    assert api.post(url(kit, f"/claims/{c['claim_id']}/decision"), kit.world.sup, json={"action": "REJECT"}).status_code == 422       # a reason is required
    decide(kit, api, c["claim_id"], action="REJECT", justification="Joint count exceeds the register for that spread")
    assert activity_pct(kit, api, "A2010") == 0
    assert api.get(url(kit, f"/claims/{c['claim_id']}"), kit.world.se).json()["status"] == "REJECTED"
    assert api.post(url(kit, f"/claims/{c['claim_id']}/withdraw"), kit.world.se, json={"reason": "too late"}).status_code == 409
    fix = api.post(url(kit, f"/claims/{c['claim_id']}/correction"), kit.world.se, json=claim_body(kit, "A2010", 700, text="corrected count"), headers={"Idempotency-Key": "fix-key-00001"})
    assert fix.status_code == 201
    assert api.post(url(kit, f"/claims/{c['claim_id']}/correction"), kit.world.se, json=claim_body(kit, "A2010", 650, text="second correction")).status_code == 409
    again = api.post(url(kit, f"/claims/{c['claim_id']}/correction"), kit.world.se, json=claim_body(kit, "A2010", 700, text="corrected count"), headers={"Idempotency-Key": "fix-key-00001"})
    assert again.json() == fix.json()
    with connect() as c2:
        assert c2.execute("select resubmits_event_id from execution_events where event_id = %s", (fix.json()["claim_id"],)).fetchone()["resubmits_event_id"] == uuid.UUID(c["claim_id"])
    decide(kit, api, fix.json()["claim_id"])
    assert activity_pct(kit, api, "A2010") == D("35")
    assert api.get(url(kit, f"/claims/{c['claim_id']}"), kit.world.se).json()["status"] == "REJECTED"
    other = api.post(url(kit, f"/claims/{fix.json()['claim_id']}/correction"), kit.world.se, json=claim_body(kit, "A2010", 10, text="not a rejected one"))
    assert other.status_code == 409 and other.json()["error"]["code"] == "NOT_A_REJECTED_CLAIM"


def test_review_queue_is_supervisor_only_paginated_and_pending_only(kit, api):
    ids = [file_claim(kit, api, "A2010", 100 * i, text=f"report {i}")["claim_id"] for i in range(1, 6)]
    decide(kit, api, ids[0])
    q = api.get(url(kit, "/review-queue"), kit.world.sup, params={"limit": 3}).json()
    assert len(q["items"]) == 3 and q["next_offset"] == 3 and ids[0] not in {x["event_id"] for x in q["items"]}
    rest = api.get(url(kit, "/review-queue"), kit.world.sup, params={"limit": 3, "offset": 3}).json()
    assert len(rest["items"]) == 1 and rest["next_offset"] is None
    for u in (kit.world.se, kit.world.pm):
        assert api.get(url(kit, "/review-queue"), u).status_code == 403


def test_pm_sees_counts_only(kit, api):
    c = file_claim(kit, api, "A2010", 100)
    cnt = api.get(url(kit, "/claim-counts"), kit.world.pm).json()
    assert cnt["pending_total"] == 1 and "event_id" not in str(cnt)
    r = api.get(url(kit, f"/claims/{c['claim_id']}"), kit.world.pm)
    assert r.status_code == 403 and r.json()["error"]["code"] == "CLAIM_CONTENT_FORBIDDEN"
    assert api.get(url(kit, "/claim-counts"), kit.world.se).status_code == 403
    assert api.post(url(kit, "/claims"), kit.world.pm, json=claim_body(kit, "A2010", 5)).status_code == 403
    assert api.post(url(kit, "/claims"), kit.world.sup, json=claim_body(kit, "A2010", 5)).status_code == 403
    assert api.post(url(kit, f"/claims/{c['claim_id']}/decision"), kit.world.pm, json={"action": "APPROVE"}).status_code == 403
    assert api.post(url(kit, f"/claims/{c['claim_id']}/decision"), kit.world.se, json={"action": "APPROVE"}).status_code == 403
    s = summary(kit, api, kit.world.pm)
    assert s["claims"]["scope"] == "aggregate" and "A2010" not in str(s["claims"])


def test_engineers_see_only_their_own_claims(kit, api):
    mine = file_claim(kit, api, "A2010", 100, text="mine")
    theirs = file_claim(kit, api, "A2010", 200, who=kit.world.se2, text="theirs")
    assert api.get(url(kit, f"/claims/{theirs['claim_id']}"), kit.world.se).status_code == 404
    assert [x["event_id"] for x in api.get(url(kit, "/my-claims"), kit.world.se).json()["items"]] == [mine["claim_id"]]
    assert api.post(url(kit, f"/claims/{theirs['claim_id']}/evidence"), kit.world.se, json={"document_id": str(uuid.uuid4())}).status_code == 404


def test_rematch_and_bind_quantities(kit, api):
    c = file_claim(kit, api, None, 100, reported_activity_ref="welding spread 2")
    assert c["status"] == "EXTRACTED"
    r = api.post(url(kit, f"/claims/{c['claim_id']}/rematch"), kit.world.sup, json={"activity_uid": str(kit.uid("A2010"))})
    assert r.status_code == 200
    d = decide(kit, api, c["claim_id"])
    assert d["status"] == "APPROVED" and activity_pct(kit, api, "A2010") == D("5")
    assert api.post(url(kit, f"/claims/{c['claim_id']}/rematch"), kit.world.se, json={"activity_uid": str(kit.uid("A2000"))}).status_code == 403


def test_archived_projects_refuse_writes_but_allow_reads(kit, api):
    c = file_claim(kit, api, "A2010", 100)
    assert api.post(url(kit, "/archive"), kit.world.pm).status_code == 200
    assert api.post(url(kit, "/claims"), kit.world.se, json=claim_body(kit, "A2010", 200, text="after archive")).json()["error"]["code"] == "PROJECT_ARCHIVED"
    assert api.post(url(kit, f"/claims/{c['claim_id']}/decision"), kit.world.sup, json={"action": "APPROVE"}).json()["error"]["code"] == "PROJECT_ARCHIVED"
    assert api.get(url(kit, f"/claims/{c['claim_id']}"), kit.world.sup).status_code == 200
