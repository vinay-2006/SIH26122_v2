"""Project Intelligence (supervising agent) and the audit dossier on v2 data -- advisory, read-only, and scoped by role."""
import pytest

from domainkit import connect
from v2api import seed_progress

pytestmark = pytest.mark.db_write


def P(kit, tail):
    return f"/api/v1/projects/{kit.project}{tail}"


@pytest.fixture
def ver(kit):
    with connect() as c:
        return c.execute("select version_id from schedule_versions where project_id = %s and status = 'ACTIVE'", (kit.project,)).fetchone()["version_id"]


def test_the_briefing_is_deterministic_without_a_provider_and_reflects_engine_facts(kit, lg, ver):
    lg.post(P(kit, f"/schedules/{ver}/issues"), kit.world.se, json={"activity_id": "A2010", "category_code": "WEATHER", "title": "Flooded trench", "severity": "HIGH", "blocks_work": True})
    lg.post("/api/v1/claims/text", kit.world.se, json={"raw_claim_text": "Welding mainline A2010: 120 joints completed today"})
    r = lg.get(P(kit, "/agent/briefing"), kit.world.sup)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["agent_status"] == "DEGRADED" and "Deterministic Mode" in b["summary"]
    assert any(f["affected_entity_id"] == "A2010" for f in b["findings"]) and b["review_queue_summary"]["total_claims"] == 1
    assert b["audit_verification"]["status"] == "VALID"
    with connect() as c:
        assert c.execute("select count(*) n from audit_logs where action = 'AGENT_BRIEFING_GENERATED'").fetchone()["n"] == 1
        assert c.execute("select count(*) n from planner_decisions").fetchone()["n"] == 0                       # advisory only


def test_claim_content_never_reaches_engineers_or_project_managers(kit, lg, ver):
    lg.post("/api/v1/claims/text", kit.world.se, json={"raw_claim_text": "Welding mainline A2010: 120 joints SECRET-WORDING"})
    sup = lg.get(P(kit, "/agent/briefing"), kit.world.sup)
    assert "SECRET-WORDING" in sup.text
    for who in (kit.world.se, kit.world.pm):
        r = lg.get(P(kit, "/agent/briefing"), who)
        assert r.status_code == 200 and "SECRET-WORDING" not in r.text and r.json()["review_queue_summary"]["total_claims"] == 1
    assert lg.get(P(kit, "/agent/review-queue"), kit.world.pm).status_code == 403
    assert lg.get(P(kit, "/agent/review-queue"), kit.world.sup).json()["total_pending_claims"] == 1
    assert lg.get(P(kit, "/agent/briefing"), kit.world.outsider).status_code == 403


def test_query_answers_about_an_activity_and_findings_list(kit, lg, ver):
    seed_progress(kit.project, "A2000", {"PIPE_STRUNG_KM": 12}, kit.world.sup, kit.world.se)
    q = lg.post(P(kit, "/agent/query"), kit.world.sup, json={"query": "What is the state of pipe stringing?", "activity_id": "A2000"})
    assert q.status_code == 200, q.text
    a = q.json()
    assert a["agent_status"] == "DEGRADED" and "A2000" in a["answer"] and "IN_PROGRESS" in a["answer"]
    assert isinstance(lg.get(P(kit, "/agent/findings"), kit.world.pm).json(), list)


def test_dossier_has_the_original_sections_and_hides_claim_content_from_the_pm(kit, lg, ver):
    kit.approve(kit.submit("A2010", qty=100, text="Welding A2010 DOSSIER-MARKER")["claim_id"])
    d = lg.get(P(kit, "/dossier"), kit.world.sup)
    assert d.status_code == 200, d.text
    body = d.json()
    for k in ("project", "schedule", "stages", "activities", "execution_evidence", "matching", "validation", "human_decisions", "approved_actuals", "reopen_history", "progress", "audit_chain", "completeness"):
        assert k in body, k
    assert any("DOSSIER-MARKER" in e["raw_claim_text"] for e in body["execution_evidence"]["evidence"]) and body["human_decisions"]["decisions"]
    assert body["audit_chain"]["verification"]["status"] == "VALID"
    pm = lg.get(P(kit, "/dossier"), kit.world.pm)
    assert pm.status_code == 200 and "DOSSIER-MARKER" not in pm.text and pm.json()["execution_evidence"]["status"] == "RESTRICTED" and pm.json()["audit_chain"]["verification"]["status"] == "VALID"
    one = lg.get(P(kit, f"/schedules/{ver}/activities/A2010/dossier"), kit.world.sup).json()
    assert one["scope"] == "ACTIVITY" and len(one["activities"]["activities"]) == 1
    assert lg.get(P(kit, "/dossier"), kit.world.se).status_code == 403
    assert lg.get(P(kit, "/dossier/audit-verification"), kit.world.pm).json()["status"] == "VALID"


def test_ask_why_knowledge_graph_and_investigation_run_the_original_code(kit, lg, ver):
    c = kit.submit("A2010", qty=100, text="Welding A2010 WHY-MARKER")
    eid = c["claim_id"]
    why = lg.get(f"/api/v1/graph/explain/A2010?event_id={eid}&depth=2", kit.world.sup)
    assert why.status_code == 200, why.text
    w = why.json()
    assert w["activity_id"] == "A2010" and w["reasoning_steps"] and w["entities_involved"][0]["name"] == "A2010"
    assert any("depends on" in s for s in w["reasoning_steps"])                       # the upstream dependency walk ran on v2 dependencies
    kg = lg.get(f"/api/v1/claims/{eid}/knowledge-graph", kit.world.sup)
    assert kg.status_code == 200, kg.text
    assert kg.json()["nodes"]
    ag = lg.get("/api/v1/graph/activity/A2010?depth=1", kit.world.sup)
    assert ag.status_code == 200 and ag.json()["nodes"], ag.text
    inv = lg.get("/api/v1/investigation/activity/A2010?depth=1", kit.world.sup)
    assert inv.status_code == 200 and inv.json()["root_activity_id"] == "A2010", inv.text
    for who in (kit.world.pm, kit.world.se, kit.world.outsider):                       # claim content: Supervisor only
        for url in (f"/api/v1/graph/explain/A2010?event_id={eid}", f"/api/v1/claims/{eid}/knowledge-graph", "/api/v1/graph/activity/A2010", "/api/v1/investigation/activity/A2010"):
            assert lg.get(url, who).status_code == 403, (url, who.name)


def test_mock_p6_is_a_development_stand_in(kit, lg, monkeypatch):
    assert lg.get("/api/v1/mock-p6/received", kit.world.sup).status_code == 404         # off unless local sign-in is enabled
    monkeypatch.setenv("V2_LOCAL_LOGIN_PASSWORD", "x" * 14)
    body = {"Id": "A2010", "PercentComplete": 40.0}
    r = lg.post("/api/v1/mock-p6/activities/A2010", kit.world.sup, json=body)
    assert r.status_code == 200 and r.json()["status"] == "success", r.text
    assert lg.post("/api/v1/mock-p6/activities/A2020", kit.world.sup, json=body).status_code == 400   # the original id-mismatch rule
    assert lg.get("/api/v1/mock-p6/received", kit.world.sup).json()["count"] >= 1
    assert lg.get("/api/v1/mock-p6/received", kit.world.se).status_code == 403


def test_time_agent_hand_off_goes_to_the_site_engineer_and_a_supervisor_never_files(kit, lg):
    draft = {"rawText": "Welding mainline A2010: 120 joints completed", "reportedActivityId": "A2010", "eventType": "PROGRESS_UPDATE"}
    assert lg.post("/api/v1/claims/text", kit.world.sup, json={"raw_claim_text": draft["rawText"]}).status_code == 403            # the role rule stands
    r = lg.post("/api/v1/time-agent/handoffs", kit.world.sup, json={"draft": draft, "note": "from my walk-round"})
    assert r.status_code == 200, r.text
    hid = r.json()["handoff_id"]
    for who in (kit.world.pm, kit.world.outsider):
        assert lg.post("/api/v1/time-agent/handoffs", who, json={"draft": draft}).status_code == 403
        assert lg.get("/api/v1/time-agent/handoffs", who).status_code == 403
    assert lg.post("/api/v1/time-agent/handoffs", kit.world.se, json={"draft": draft}).status_code == 403                          # an engineer cannot hand off
    mine = lg.get("/api/v1/time-agent/handoffs", kit.world.se).json()
    assert [h["handoff_id"] for h in mine] == [hid] and mine[0]["draft"]["reportedActivityId"] == "A2010"
    with connect() as c:
        assert c.execute("select count(*) n from execution_events").fetchone()["n"] == 0                                           # a draft is not a claim
    claim = kit.submit("A2010", qty=120, text=draft["rawText"])
    assert lg.post(f"/api/v1/time-agent/handoffs/{hid}/filed", kit.world.sup, json={"event_id": str(claim["claim_id"])}).status_code == 403
    ok = lg.post(f"/api/v1/time-agent/handoffs/{hid}/filed", kit.world.se, json={"event_id": str(claim["claim_id"])})
    assert ok.status_code == 200 and ok.json()["status"] == "FILED", ok.text
    assert lg.post(f"/api/v1/time-agent/handoffs/{hid}/dismiss", kit.world.se).status_code == 409
    assert lg.get("/api/v1/time-agent/handoffs", kit.world.se).json() == []
    with connect() as c:
        acts = [r["action"] for r in c.execute("select action from audit_logs where entity_type = 'CLAIM_HANDOFF' order by log_id").fetchall()]
    assert acts == ["CLAIM_DRAFT_HANDED_OFF", "CLAIM_DRAFT_FILED"]
