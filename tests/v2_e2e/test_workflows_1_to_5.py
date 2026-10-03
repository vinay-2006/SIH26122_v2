"""Workflows 1-5 of Phase 3D: project setup, claim submission, supervisor approval, clarification and correction, issues and delays."""
import uuid
from decimal import Decimal as D

import pytest

from apikit import jpeg, upload
from v2api import make_user
from v2api import connect, file_bytes

NSP = file_bytes("nsp.csv")
NSP_RES = file_bytes("nsp_resources.csv")


def stage(api, who, pid, acts, res, expect=201, **extra):
    data = {"data_date": "2026-01-05", "planned_start": "2026-01-12", "planned_finish": "2026-07-31", "project_name": "E2E Northern Spur Pipeline", **extra}
    r = api.post(f"/api/v2/projects/{pid}/schedule-imports", who, files={"file": ("plan.csv", acts, "text/csv"), "resources_file": ("res.csv", res, "text/csv")}, data=data)
    assert r.status_code == expect, r.text
    return r.json()


# ======================================================================================================== workflow 1
def test_workflow_1_project_manager_creates_a_project_and_it_starts_with_zero_progress(world, api):
    pm = world.pm
    r = api.post("/api/v2/projects", pm, json={"project_code": "W1-NEW", "project_name": "Workflow One Pipeline", "location": "Assam"})
    assert r.status_code == 201
    pid = r.json()["project_id"]
    assert api.get(f"/api/v2/projects/{pid}", pm).json()["my_role"] == "PROJECT_MANAGER"
    assert api.post("/api/v2/projects", world.se, json={"project_code": "NOPE-1", "project_name": "Not allowed"}).status_code == 403     # creation needs the platform grant

    # a file with a validation ERROR writes nothing at all
    bad = NSP + b"A1000,Duplicate id,Northern Spur Test Pipeline > Civil & ROW Preparation,Civil Works,Task,5.0,12-01-2026,16-01-2026,0,\n"
    e = stage(api, pm, pid, bad, NSP_RES, expect=422)
    assert e["error"]["code"] == "IMPORT_INVALID" and any(x["code"] == "DUPLICATE_ACTIVITY_ID" for x in e["error"]["details"]["errors"])
    with connect() as c:
        assert c.execute("select (select count(*) from schedule_imports) a, (select count(*) from schedule_versions) b, (select count(*) from baseline_activities) c").fetchone() == {"a": 0, "b": 0, "c": 0}

    # a valid file with an unknown discipline label and an unknown unit needs the PM's mapping decisions
    extra_act = b"X1,Radiograph welds,Northern Spur Test Pipeline > Commissioning,Radiography & NDT,Task,10.0,15-06-2026,26-06-2026,2,A2030FS\n"
    imp = stage(api, pm, pid, NSP + extra_act, NSP_RES + b"X1,FILMS,Radiographic films,Material,400,reels\n")
    assert imp["report"]["valid"] and not imp["report"]["ready_to_build"] and imp["report"]["mapping"] == {"unmapped_disciplines": ["radiography & ndt"], "unmapped_units": ["reels"]}
    base = f"/api/v2/projects/{pid}/schedule-imports/{imp['import_id']}"
    refused = api.post(f"{base}/build", pm)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "IMPORT_NOT_READY"
    ok = api.put(f"{base}/decisions", pm, json={"discipline_map": {"Radiography & NDT": "PIPING"}, "uom_map": {"reels": "NOS"}, "header": {"baseline_name": "Contract baseline Rev 0"}})
    assert ok.status_code == 200 and ok.json()["report"]["ready_to_build"]
    built = api.post(f"{base}/build", pm)
    assert built.status_code == 201 and built.json()["status"] == "VALIDATED" and built.json()["activities"] == 13
    act = api.post(f"/api/v2/projects/{pid}/schedule-versions/{built.json()['version_id']}/activate", pm)
    assert act.status_code == 200

    # people: an existing user by email, a new one by invitation
    assert api.post(f"/api/v2/projects/{pid}/members", pm, json={"email": world.sup.email, "role": "SUPERVISOR"}).status_code == 201
    assert api.post(f"/api/v2/projects/{pid}/members", pm, json={"email": world.se.email, "role": "SITE_ENGINEER"}).status_code == 201
    newbie = make_user("newbie")
    inv = api.post(f"/api/v2/projects/{pid}/invitations", pm, json={"email": newbie.email, "role": "SITE_ENGINEER"})
    assert inv.status_code == 201 and api.post("/api/v2/invitations/accept", newbie, json={"token": inv.json()["token"]}).status_code == 200
    roles = sorted(m["role"] for m in api.get(f"/api/v2/projects/{pid}/members", pm).json())
    assert roles == ["PROJECT_MANAGER", "SITE_ENGINEER", "SITE_ENGINEER", "SUPERVISOR"]

    # the new project starts with exactly zero execution progress
    s = api.get(f"/api/v2/projects/{pid}/dashboard/summary", pm, params={"as_of": "2100-01-01"}).json()
    assert s["physical_pct"] == 0 and s["activities"] == {"total": 13, "completed": 0, "in_progress": 0, "not_started": 13} and s["claims"]["pending_total"] == 0 and s["version"]["baseline_name"] == "Contract baseline Rev 0"
    assert s["data_date"] == "2026-01-05" and s["weight_basis"] in ("MANHOURS", "DURATION")
    with connect() as c:
        for t in ("execution_events", "planner_decisions", "approved_resource_progress", "approved_activity_progress", "notifications", "issues"):
            assert c.execute(f"select count(*) n from {t} where project_id = %s", (pid,)).fetchone()["n"] == 0, t
    # the Site Engineer sees the active activities but cannot manage baselines
    acts = api.get(f"/api/v2/projects/{pid}/activities", world.se, params={"limit": 50}).json()["items"]
    assert len(acts) == 13 and all(a["physical_pct"] == 0 for a in acts)
    assert api.post(f"/api/v2/projects/{pid}/schedule-imports", world.se, files={"file": ("p.csv", NSP, "text/csv")}).status_code == 403
    assert api.post(f"/api/v2/projects/{pid}/schedule-versions/{built.json()['version_id']}/activate", world.se).status_code == 403
    assert api.post(f"/api/v2/projects/{pid}/documents", world.se, files={"file": ("plan.csv", NSP, "text/csv")}, data={"kind": "DAILY_REPORT"}).json()["error"]["code"] == "SCHEDULE_FILE_NOT_ALLOWED"
    assert api.get(f"/api/v2/projects/{pid}/audit/verify", pm).json()["valid"]


# ======================================================================================================== workflow 2
def test_workflow_2_site_engineer_submits_measured_progress_with_evidence_and_it_stays_pending(story):
    s, api = story, story.api
    found = api.get(s.url("/activities"), s.se, params={"q": "welding"}).json()["items"]
    welding = next(a for a in found if a["external_activity_id"] == "A2010")
    asg = welding["measured_assignments"][0]
    assert asg["resource_code"] == "WELD_JOINTS" and asg["unit_of_measure"] == "JOINT" and asg["baseline_qty"] == 2000
    photo = upload(s, api, "weld-log.jpg", jpeg(), "PHOTO")
    report = upload(s, api, "daily.csv", b"Activity ID,Date,Cumulative Qty,Unit\nA2010,2026-03-02,500,joints\n", "DAILY_REPORT")
    claim = api.post(s.url("/claims"), s.se, json={"event_date": "2026-03-02", "raw_text": "Welded 500 joints on Spread 1 to date; weld log attached.", "activity_uid": welding["activity_uid"],
                                                  "quantities": [{"qty": 500, "uom": "joints", "basis": "CUMULATIVE", "resource_hint": "WELD_JOINTS"}],
                                                  "evidence_document_ids": [photo["document_id"], report["document_id"]]})
    assert claim.status_code == 201 and claim.json()["status"] == "MATCHED" and claim.json()["quantities"][0]["assignment_uid"] == asg["assignment_uid"]
    cid = claim.json()["claim_id"]
    # it is pending: no ledger row, no progress anywhere
    assert s.count("approved_resource_progress") == 0 and s.pct("A2010") == 0 and s.summary()["physical_pct"] == 0
    mine = api.get(s.url("/my-claims"), s.se).json()["items"]
    assert [m["event_id"] for m in mine] == [cid] and mine[0]["status"] == "MATCHED"
    detail = api.get(s.url(f"/claims/{cid}"), s.se).json()
    assert {e["document_id"] for e in detail["evidence"]} == {photo["document_id"], report["document_id"]} and detail["quantities"][0]["reported_qty"] == 500
    # the supervisor is told; the PM sees a count and nothing else
    sup_n = api.get(s.url("/notifications"), s.sup).json()["items"]
    assert [n["notification_type"] for n in sup_n] == ["CLAIM_SUBMITTED"] and sup_n[0]["claim_id"] == cid
    assert api.get(s.url("/notifications"), s.pm).json()["items"] == []
    cnt = api.get(s.url("/claim-counts"), s.pm).json()
    assert cnt["pending_total"] == 1 and cnt["APPROVED"] == 0 and "A2010" not in str(cnt)
    assert api.get(s.url(f"/claims/{cid}"), s.pm).status_code == 403
    assert api.get(s.url("/review-queue"), s.pm).status_code == 403


# ======================================================================================================== workflow 3
def test_workflow_3_supervisor_reviews_and_approves_exactly_once(story):
    s, api = story, story.api
    c = s.claim("A2010", 500, text="Welded 500 joints to date")
    q = api.get(s.url("/review-queue"), s.sup).json()["items"]
    assert [x["event_id"] for x in q] == [c["claim_id"]] and q[0]["external_activity_id"] == "A2010"
    detail = api.get(s.url(f"/claims/{c['claim_id']}"), s.sup).json()
    assert detail["status"] == "MATCHED" and detail["quantities"][0]["normalized_qty"] == 500
    prev = api.post(s.url(f"/claims/{c['claim_id']}/decision-preview"), s.sup, json={"action": "APPROVE"}).json()
    assert prev["ok"] and prev["method"] == "QUANTITIES_AS_CLAIMED" and prev["result"]["activity_pct_after"] == "25.000"
    assert s.count("planner_decisions") == 0 and s.count("approved_resource_progress") == 0                     # a preview writes nothing
    d = s.decide(c["claim_id"], justification="Verified against the weld log")
    assert d["status"] == "APPROVED" and d["method"] == "QUANTITIES_AS_CLAIMED" and d["result"]["activity_pct_after"] == "25.000"
    # recorded once, ledger once, notification once, audit once
    assert s.count("planner_decisions") == 1 and s.count("approved_resource_progress") == 1 and s.count("approved_activity_progress") == 1
    again = api.post(s.url(f"/claims/{c['claim_id']}/decision"), s.sup, json={"action": "APPROVE"})
    assert again.status_code == 409 and s.count("approved_resource_progress") == 1
    inbox = api.get(s.url("/notifications"), s.se, params={"unread_only": True}).json()["items"]
    assert [n["notification_type"] for n in inbox] == ["CLAIM_DECISION"] and inbox[0]["claim_id"] == c["claim_id"] and inbox[0]["decision_id"] == d["decision_id"]
    assert api.post(s.url(f"/notifications/{inbox[0]['notification_id']}/read"), s.se).status_code == 200
    assert api.get(s.url("/notifications"), s.se, params={"unread_only": True}).json()["items"] == []
    assert api.post(s.url(f"/notifications/{inbox[0]['notification_id']}/read"), s.se2).status_code == 404            # someone else's notification
    trail = api.get(s.url("/audit"), s.sup, params={"entity_type": "PLANNER_DECISION"}).json()["items"]
    assert len(trail) == 1 and trail[0]["action"] == "CLAIM_APPROVED" and trail[0]["entity_id"] == d["decision_id"]
    # rollups reflect the approved quantity; the reported figure is untouched
    assert s.pct("A2010") == D("25") and s.summary()["physical_pct"] > 0
    stages = {x["wbs_name"]: x["physical_pct"] for x in api.get(s.url("/dashboard/stages"), s.pm, params={"as_of": "2100-01-01"}).json()["items"]}
    assert any(v > 0 for v in stages.values()) and any(v == 0 for v in stages.values())
    final = api.get(s.url(f"/claims/{c['claim_id']}"), s.se).json()
    assert final["quantities"][0]["reported_qty"] == 500 and final["status"] == "APPROVED" and final["decisions"][0]["justification"] == "Verified against the weld log"
    with connect() as cx:
        r = cx.execute("select cumulative_qty, incremental_qty, claim_quantity_id from approved_resource_progress where project_id = %s", (s.project,)).fetchone()
        assert (r["cumulative_qty"], r["incremental_qty"]) == (500, 500) and str(r["claim_quantity_id"]) == detail["quantities"][0]["claim_quantity_id"]
    tl = api.get(s.url(f"/activities/{s.uid('A2010')}/timeline"), s.pm).json()
    assert len(tl["quantity_entries"]) == 1 and tl["claims"] == [] and tl["decisions"] == []                      # the PM sees the ledger entry, not the claim


# ======================================================================================================== workflow 4
def test_workflow_4_hold_clarification_answer_then_approve_and_rejection_then_linked_correction(story):
    s, api = story, story.api
    c = s.claim("A2010", 300, text="Welded 300 joints")
    q = api.post(s.url(f"/claims/{c['claim_id']}/clarification-request"), s.sup, json={"question": "Which spread and which weld-log page?"})
    assert q.status_code == 201 and q.json()["status"] == "DISPUTED"
    assert s.pct("A2010") == 0 and s.count("approved_resource_progress") == 0                                      # on hold: nothing counted
    asked = api.get(s.url(f"/claims/{c['claim_id']}"), s.se).json()
    assert asked["clarification_status"] == "ASKED" and asked["clarification_question"].startswith("Which spread")
    note = api.get(s.url("/notifications"), s.se).json()["items"][0]
    assert note["notification_type"] == "CLAIM_DECISION" and "Question" in note["title"]
    extra = upload(s, api, "log.txt", b"Weld log page 14: joints 1-300 spread 1", "EVIDENCE")
    a = api.post(s.url(f"/claims/{c['claim_id']}/clarification-answer"), s.se, json={"answer": "Spread 1, weld log page 14", "evidence_document_ids": [extra["document_id"]]})
    assert a.status_code == 200 and a.json()["clarification_status"] == "ANSWERED"
    assert [n["notification_type"] for n in api.get(s.url("/notifications"), s.sup).json()["items"]][0] == "CLAIM_CLARIFICATION"
    s.decide(c["claim_id"])
    assert s.pct("A2010") == D("15") and s.count("approved_resource_progress") == 1

    bad = s.claim("A2010", 900, text="Welded 900 joints (second count)", days_ago=0)
    rej = api.post(s.url(f"/claims/{bad['claim_id']}/decision"), s.sup, json={"action": "REJECT"})
    assert rej.status_code == 422                                                                                   # a reason is mandatory
    s.decide(bad["claim_id"], action="REJECT", justification="Exceeds the weld register for the period")
    assert s.pct("A2010") == D("15") and api.get(s.url(f"/claims/{bad['claim_id']}"), s.se).json()["status"] == "REJECTED"
    fix = api.post(s.url(f"/claims/{bad['claim_id']}/correction"), s.se, json={"event_date": apikit_today(), "raw_text": "Corrected: 700 joints per the register", "activity_uid": s.uid("A2010"),
                                                                              "quantities": [{"qty": 700, "uom": "joints", "basis": "CUMULATIVE"}]})
    assert fix.status_code == 201
    with connect() as cx:
        assert str(cx.execute("select resubmits_event_id from execution_events where event_id = %s", (fix.json()["claim_id"],)).fetchone()["resubmits_event_id"]) == bad["claim_id"]
    s.decide(fix.json()["claim_id"])
    assert s.pct("A2010") == D("35") and s.count("approved_resource_progress") == 2
    # the rejected claim stayed rejected; the ledger only holds approved figures
    assert api.get(s.url(f"/claims/{bad['claim_id']}"), s.se).json()["status"] == "REJECTED"
    with connect() as cx:
        assert cx.execute("select count(*) n from approved_resource_progress r join planner_decisions d on d.decision_id=r.decision_id join execution_events e on e.event_id=d.event_id where e.status <> 'APPROVED'").fetchone()["n"] == 0


def apikit_today():
    from apikit import TODAY
    return str(TODAY)


# ======================================================================================================== workflow 5
def test_workflow_5_issue_delay_blocker_cause_impact_resolution_and_dashboards(story):
    s, api = story, story.api
    assert s.summary()["issues"] == {"active": 0, "blocking": 0, "resolved": 0}
    r = api.post(s.url("/issues"), s.se, json={"title": "Excavator breakdown at KM 12", "category_code": "EQUIPMENT_SHORTAGE", "activity_uid": s.uid("A1010"), "severity": "HIGH", "blocks_work": True,
                                              "delay_started_on": "2026-03-01", "impact_days_estimated": 4, "description": "Hydraulic pump failed; spare from Guwahati"})
    assert r.status_code == 201
    iid = r.json()["issue_id"]
    b = api.get(s.url("/blockers"), s.sup).json()
    assert b["blocked_activities"] == [s.uid("A1010")]                                                              # blocked is derived from the active issue
    sm = s.summary()
    assert sm["issues"] == {"active": 1, "blocking": 1, "resolved": 0}
    rc = api.post(s.url("/root-causes"), s.sup, json={"title": "Ageing hired excavators", "category_code": "EQUIPMENT_SHORTAGE", "summary": "Second breakdown this quarter"}).json()["root_cause_id"]
    assert api.post(s.url(f"/issues/{iid}/root-cause"), s.sup, json={"root_cause_id": rc}).status_code == 200
    assert api.post(s.url(f"/issues/{iid}/resolve"), s.se, json={"notes": "fixed"}).status_code == 403
    res = api.post(s.url(f"/issues/{iid}/resolve"), s.sup, json={"notes": "Pump replaced", "delay_ended_on": "2026-03-04", "impact_days_actual": 3})
    assert res.status_code == 200
    assert api.get(s.url("/blockers"), s.sup).json()["blocked_activities"] == []                                    # derived state cleared
    assert s.summary()["issues"] == {"active": 0, "blocking": 0, "resolved": 1}
    got = api.get(s.url(f"/issues/{iid}"), s.pm).json()
    assert got["status"] == "RESOLVED" and got["impact_days_estimated"] == 4 and got["impact_days_actual"] == 3 and got["delay_started_on"] == "2026-03-01" and got["delay_ended_on"] == "2026-03-04" and str(got["root_cause_id"]) == rc
    assert [n["notification_type"] for n in api.get(s.url("/notifications"), s.se).json()["items"]] == ["ISSUE_UPDATE"]
    assert api.post(s.url(f"/issues/{iid}/resolve"), s.sup, json={"notes": "again"}).status_code == 409                # no reopening
    mem = api.post(s.url(f"/issues/{iid}/memory"), s.sup, json={"lessons_learned": "Inspect hired excavators before mobilisation", "visibility": "ORGANISATION"})
    assert mem.status_code == 201 and api.get(s.url("/memory"), s.pm).json()["items"][0]["delay_days"] == 3
    assert s.pct("A1010") == 0 and s.summary()["physical_pct"] == 0                                                 # delays never move progress
    tl = api.get(s.url(f"/activities/{s.uid('A1010')}/timeline"), s.pm).json()
    assert [i["status"] for i in tl["issues"]] == ["RESOLVED"] and tl["issues"][0]["impact_days_actual"] == 3
