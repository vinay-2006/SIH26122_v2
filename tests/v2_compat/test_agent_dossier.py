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
