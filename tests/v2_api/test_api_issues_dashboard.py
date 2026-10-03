"""Issues, blockers, root causes, memory, dashboards, timelines and audit over HTTP."""
import threading
from datetime import timedelta
from decimal import Decimal as D

from apikit import TODAY, activity_pct, decide, file_claim, summary, upload, url


def post_issue(kit, api, who=None, expect=201, **kw):
    body = {"title": "Excavator breakdown", "category_code": "EQUIPMENT_SHORTAGE", "activity_uid": str(kit.uid("A1010")), **kw}
    r = api.post(url(kit, "/issues"), who or kit.world.se, json=body, headers=kw.pop("headers", {}) if False else {})
    assert r.status_code == expect, r.text
    return r.json()


def test_issue_lifecycle_with_blocker_and_dashboard(kit, api):
    doc = upload(kit, api, "leak.txt", b"photo of the broken pump", "ISSUE_REPORT")
    i = post_issue(kit, api, blocks_work=True, severity="HIGH", delay_started_on=str(TODAY - timedelta(days=2)), impact_days_estimated=4, evidence_document_ids=[doc["document_id"]])
    b = api.get(url(kit, "/blockers"), kit.world.sup).json()
    assert b["blocked_activities"] == [str(kit.uid("A1010"))]
    s = summary(kit, api, kit.world.pm)
    assert s["issues"] == {"active": 1, "blocking": 1, "resolved": 0}
    assert api.post(url(kit, f"/issues/{i['issue_id']}/resolve"), kit.world.se, json={"notes": "fixed it myself"}).status_code == 403
    assert api.post(url(kit, f"/issues/{i['issue_id']}/resolve"), kit.world.pm, json={"notes": "fixed it myself"}).status_code == 403
    r = api.post(url(kit, f"/issues/{i['issue_id']}/resolve"), kit.world.sup, json={"notes": "Pump replaced", "delay_ended_on": str(TODAY), "impact_days_actual": 3})
    assert r.status_code == 200
    assert api.get(url(kit, "/blockers"), kit.world.sup).json()["blocked_activities"] == []
    assert summary(kit, api, kit.world.pm)["issues"] == {"active": 0, "blocking": 0, "resolved": 1}
    got = api.get(url(kit, f"/issues/{i['issue_id']}"), kit.world.pm).json()
    assert got["status"] == "RESOLVED" and got["impact_days_actual"] == 3 and got["impact_days_estimated"] == 4 and [e["document_id"] for e in got["evidence"]] == [doc["document_id"]]
    assert api.post(url(kit, f"/issues/{i['issue_id']}/resolve"), kit.world.sup, json={"notes": "again"}).status_code == 409
    assert activity_pct(kit, api, "A1010") == 0                                                                   # delays do not move progress


def test_issue_idempotency_and_validation(kit, api):
    h = {"Idempotency-Key": "issue-key-0001"}
    body = {"title": "Access road flooded", "category_code": "WEATHER", "activity_uid": str(kit.uid("A1010"))}
    a = api.post(url(kit, "/issues"), kit.world.se, json=body, headers=h)
    b = api.post(url(kit, "/issues"), kit.world.se, json=body, headers=h)
    assert a.status_code == 201 and b.json() == a.json() and b.headers["Idempotent-Replay"] == "true"
    assert kit.count("issues") == 1
    assert api.post(url(kit, "/issues"), kit.world.se, json={**body, "title": "Different"}, headers=h).json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert api.post(url(kit, "/issues"), kit.world.se, json={"title": "No target", "category_code": "WEATHER"}).json()["error"]["code"] == "TARGET_REQUIRED"
    assert api.post(url(kit, "/issues"), kit.world.se, json={**body, "severity": "SEVERE"}).status_code == 422
    assert api.post(url(kit, "/issues"), kit.world.pm, json=body).status_code == 403


def test_concurrent_issue_posts_with_one_key_create_one_issue(kit, api):
    h = {"Idempotency-Key": "issue-key-race1"}
    body = {"title": "Crane unavailable", "category_code": "EQUIPMENT_SHORTAGE", "activity_uid": str(kit.uid("A1010"))}
    out = []
    ts = [threading.Thread(target=lambda: out.append(api.post(url(kit, "/issues"), kit.world.se, json=body, headers=h))) for _ in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert kit.count("issues") == 1 and {r.status_code for r in out} <= {201, 409}


def test_visibility_of_issues(kit, api):
    mine = post_issue(kit, api, title="Mine")["issue_id"]
    theirs = post_issue(kit, api, who=kit.world.se2, title="Theirs")["issue_id"]
    ids = lambda u: {x["issue_id"] for x in api.get(url(kit, "/issues"), u).json()["items"]}
    assert ids(kit.world.se) == {mine} and ids(kit.world.sup) == {mine, theirs} and ids(kit.world.pm) == {mine, theirs}
    assert api.get(url(kit, f"/issues/{theirs}"), kit.world.se).status_code == 404
    assert api.get(url(kit, "/issues"), kit.world.outsider).status_code == 403
    page = api.get(url(kit, "/issues"), kit.world.sup, params={"limit": 1}).json()
    assert len(page["items"]) == 1 and page["next_offset"] == 1
    assert api.get(url(kit, "/issues"), kit.world.sup, params={"status": "RESOLVED"}).json()["items"] == []


def test_root_cause_and_memory_flow(kit, api):
    i = post_issue(kit, api)["issue_id"]
    assert api.post(url(kit, "/root-causes"), kit.world.se, json={"title": "Old fleet", "category_code": "EQUIPMENT_SHORTAGE"}).status_code == 403
    rc = api.post(url(kit, "/root-causes"), kit.world.sup, json={"title": "Ageing hired excavators", "category_code": "EQUIPMENT_SHORTAGE"}).json()["root_cause_id"]
    assert api.post(url(kit, f"/issues/{i}/root-cause"), kit.world.sup, json={"root_cause_id": rc}).status_code == 200
    assert api.post(url(kit, f"/issues/{i}/memory"), kit.world.sup, json={"lessons_learned": "Inspect hired machines first"}).json()["error"]["code"] == "ISSUE_NOT_RESOLVED"
    api.post(url(kit, f"/issues/{i}/resolve"), kit.world.sup, json={"notes": "Replaced", "impact_days_actual": 2})
    m = api.post(url(kit, f"/issues/{i}/memory"), kit.world.sup, json={"lessons_learned": "Inspect hired machines first", "visibility": "ORGANISATION"})
    assert m.status_code == 201
    assert api.post(url(kit, f"/issues/{i}/memory"), kit.world.sup, json={"lessons_learned": "x lesson", "visibility": "WORLD"}).status_code == 422
    assert [r["root_cause_id"] for r in api.get(url(kit, "/root-causes"), kit.world.pm).json()["items"]] == [rc]
    mem = api.get(url(kit, "/memory"), kit.world.pm).json()["items"]
    assert len(mem) == 1 and mem[0]["delay_days"] == 2


def test_dashboard_payloads_disclose_basis_data_date_and_approximation(kit, api):
    file_claim(kit, api, "A2010", 500)
    s = summary(kit, api, kit.world.pm)
    assert s["weight_basis"] in ("MANHOURS", "DURATION", "UNIT") and s["weight_basis_explanation"] and s["data_date"] == "2026-01-05"
    assert "APPROXIMATION" in s["planned_method_note"] and "not a cost-based earned-value" in s["spi_note"] and s["as_of"] == "2100-01-01"
    assert s["physical_pct"] == 0 and s["claims"]["pending_total"] == 1 and s["source"].startswith("approved ledgers only")
    c = file_claim(kit, api, "A2010", 600, text="second")
    decide(kit, api, c["claim_id"])
    s2 = summary(kit, api)
    assert s2["physical_pct"] > 0 and activity_pct(kit, api, "A2010") == D("30")
    for path in ("wbs", "stages", "disciplines"):
        items = api.get(url(kit, f"/dashboard/{path}"), kit.world.pm, params={"as_of": "2100-01-01"}).json()["items"]
        assert items and all("physical_pct" in x and "planned_pct" in x for x in items)
    assert all(x["node_type"] == "STAGE" for x in api.get(url(kit, "/dashboard/stages"), kit.world.pm).json()["items"])
    acts = api.get(url(kit, "/dashboard/activities"), kit.world.pm, params={"state": "IN_PROGRESS", "as_of": "2100-01-01"}).json()
    assert [a["external_activity_id"] for a in acts["items"]] == ["A2010"]
    assert api.get(url(kit, "/dashboard/activities"), kit.world.pm, params={"state": "BOGUS"}).status_code == 422
    t = api.get(url(kit, "/dashboard/timeline"), kit.world.pm, params={"step_days": 30}).json()
    assert t["points"] and "APPROXIMATION" in t["planned_method_note"]
    assert api.get(url(kit, "/dashboard/timeline"), kit.world.pm, params={"date_from": "2000-01-01", "date_to": "2030-01-01", "step_days": 1}).json()["error"]["code"] == "TOO_MANY_POINTS"


def test_dashboard_is_available_to_every_member_but_not_outsiders(kit, api):
    for u in (kit.world.pm, kit.world.sup, kit.world.se):
        assert api.get(url(kit, "/dashboard/summary"), u).status_code == 200
    assert api.get(url(kit, "/dashboard/summary"), kit.world.outsider).status_code == 403
    assert api.get(url(kit, "/dashboard/summary")).status_code == 401
    se = summary(kit, api, kit.world.se)
    assert se["claims"]["scope"] == "own"


def test_activity_timeline_and_audit_trail(kit, api):
    a = file_claim(kit, api, "A2010", 500, text="mine")
    file_claim(kit, api, "A2010", 100, who=kit.world.se2, text="theirs")
    decide(kit, api, a["claim_id"])
    uid = kit.uid("A2010")
    sup = api.get(url(kit, f"/activities/{uid}/timeline"), kit.world.sup).json()
    se = api.get(url(kit, f"/activities/{uid}/timeline"), kit.world.se).json()
    pm = api.get(url(kit, f"/activities/{uid}/timeline"), kit.world.pm).json()
    assert len(sup["claims"]) == 2 and len(se["claims"]) == 1 and pm["claims"] == [] and pm["decisions"] == [] and len(pm["quantity_entries"]) == 1
    assert "raw_claim_text" not in str(pm)
    au = api.get(url(kit, "/audit"), kit.world.pm, params={"entity_type": "PLANNER_DECISION"}).json()["items"]
    assert au and all(x["entity_type"] == "PLANNER_DECISION" for x in au)
    assert api.get(url(kit, "/audit"), kit.world.se).status_code == 403
    v = api.get(url(kit, "/audit/verify"), kit.world.sup).json()
    assert v["valid"] is True and v["entries"] >= 3
    assert api.get(url(kit, "/activities/00000000-0000-0000-0000-000000000000/timeline"), kit.world.pm).status_code == 404
