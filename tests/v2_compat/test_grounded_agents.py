"""Time Agent and Project Intelligence answer from live data and the authored project knowledge, say which, and never invent a figure or reveal claim wording to a role that may not see it."""
import re

import pytest

from domainkit import connect

pytestmark = pytest.mark.db_write


@pytest.fixture
def ver(kit):
    with connect() as c:
        return c.execute("select version_id from schedule_versions where project_id = %s and status = 'ACTIVE'", (kit.project,)).fetchone()["version_id"]


def know(api, kit, section, title, body, provenance="AUTHORED", tags=()):
    r = api.post(f"/api/v2/projects/{kit.project}/knowledge", kit.world.pm, json={"section": section, "title": title, "body": body, "provenance": provenance, "tags": list(tags), "sort_order": 10})
    assert r.status_code == 201, r.text
    return r.json()


def ask(lg, kit, who, text):
    return lg.post(f"/api/v1/time-agent/projects/{kit.project}/ask", who, json={"query": text})


def pi(lg, kit, who, text, **kw):
    return lg.post(f"/api/v1/projects/{kit.project}/agent/query", who, json={"query": text, **kw})


def ledger(kit, ver):
    with connect() as c:
        r = c.execute("select physical_pct, planned_pct, activities, completed, in_progress, not_started from project_progress_as_of(%s, current_date)", (ver,)).fetchone()
        n = c.execute("select count(*) n from execution_events where project_id = %s and status in ('EXTRACTED','MATCHED','VALIDATED','DISPUTED')", (kit.project,)).fetchone()["n"]
    return r, n


def test_the_time_agent_answers_the_review_queue_from_live_counts_and_shows_claim_wording_to_the_supervisor(kit, lg, ver):
    a = kit.submit("A2010", qty=100, text="Welding A2010 SECRET-WORDING first")["claim_id"]
    kit.submit("A2000", qty=5, text="Pipe stringing A2000 second")
    _, n = ledger(kit, ver)
    r = ask(lg, kit, kit.world.sup, "What is waiting for my review?")
    assert r.status_code == 200, r.text
    b = r.json()
    assert f"{n} claims waiting" in b["reply"] and str(a) in b["reply"] and "SECRET-WORDING" in b["reply"]
    assert b["answered"] and b["answer_source"] == "DETERMINISTIC_GROUNDED" and any(s["kind"] == "LIVE_DATA" and s["ref"] == "review_queue" for s in b["sources"])
    assert {"label": "Open Review Workspace", "to": "/review"} in b["links"]


def test_progress_figures_are_the_ledgers_and_nothing_else(kit, lg, api, ver):
    kit.approve(kit.submit("A2010", qty=300)["claim_id"])
    row, _ = ledger(kit, ver)
    b = ask(lg, kit, kit.world.sup, "What is the overall project progress?").json()
    nums = set(re.findall(r"(\d+(?:\.\d+)?)%", b["reply"]))
    expected = {f"{float(row['physical_pct']):.1f}".rstrip("0").rstrip(".") , f"{float(row['planned_pct']):.1f}".rstrip("0").rstrip(".")}
    assert expected <= nums
    assert f"{row['activities']} activities" in b["reply"] and f"{row['completed']} completed" in b["reply"]
    assert any(s["ref"] == "progress" for s in b["sources"])


def test_project_context_is_found_cited_and_labelled_with_its_provenance(kit, lg, api):
    know(api, kit, "SITE", "Access and site conditions", "Access is by a single gravel road that floods in the monsoon.", "ILLUSTRATIVE", ["access"])
    know(api, kit, "PROCUREMENT", "Vendors and purchase orders", "Not specified.", "NOT_SPECIFIED", ["vendors"])
    b = ask(lg, kit, kit.world.sup, "How is the site accessed?").json()
    assert "gravel road" in b["reply"] and "illustrative, not a contractual fact" in b["reply"]
    k = [s for s in b["sources"] if s["kind"] == "PROJECT_KNOWLEDGE"]
    assert k and k[0]["section"] == "SITE" and k[0]["provenance"] == "ILLUSTRATIVE"
    v = ask(lg, kit, kit.world.sup, "Who are the vendors?").json()
    assert "Not specified" in v["reply"] and "not specified" in v["reply"].lower()


def test_an_unknown_question_is_declined_not_invented(kit, lg):
    b = ask(lg, kit, kit.world.sup, "What colour is the site engineer's helmet on Tuesdays?").json()
    assert b["answered"] is False and "cannot answer that from this project's records" in b["reply"] and b["sources"] == []
    assert not re.search(r"\d+(\.\d+)?%", b["reply"])


def test_an_activity_question_reports_its_state(kit, lg):
    b = ask(lg, kit, kit.world.sup, "Where are we on A2010?").json()
    assert "Activity A2010" in b["reply"] and "not started" in b["reply"] and any(s["ref"] == "activity:A2010" for s in b["sources"])


def test_only_a_supervisor_may_use_the_time_agent(kit, lg):
    for who in (kit.world.pm, kit.world.se, kit.world.outsider):
        assert ask(lg, kit, who, "pending reviews").status_code == 403, who.name
    assert ask(lg, kit, kit.world.sup, "x").status_code == 422
    with connect() as c:
        assert c.execute("select count(*) n from audit_logs where action = 'TIME_AGENT_QUERY'").fetchone()["n"] == 0


def test_every_time_agent_question_is_audited(kit, lg):
    ask(lg, kit, kit.world.sup, "Pending reviews please")
    with connect() as c:
        assert c.execute("select count(*) n from audit_logs where action = 'TIME_AGENT_QUERY'").fetchone()["n"] == 1


def test_project_intelligence_is_grounded_and_never_shows_claim_wording(kit, lg, api):
    kit.submit("A2010", qty=100, text="Welding A2010 SECRET-WORDING")
    know(api, kit, "SCOPE", "Scope of work", "The scope is the pipeline spread described by the baseline stages.", "AUTHORED", ["scope"])
    for who in (kit.world.pm, kit.world.se):
        r = pi(lg, kit, who, "What is the scope, and what is waiting for review?")
        assert r.status_code == 200, r.text
        b = r.json()
        assert "SECRET-WORDING" not in r.text and "1 claim waiting" in b["answer"] and "pipeline spread" in b["answer"]
        assert b["answer_source"] == "DETERMINISTIC_GROUNDED" and {s["kind"] for s in b["sources"]} == {"LIVE_DATA", "PROJECT_KNOWLEDGE"}
        assert {e["entity_type"] for e in b["evidence"]} == {"LIVE_DATA", "PROJECT_KNOWLEDGE"}
    assert pi(lg, kit, kit.world.sup, "scope").status_code == 403


def test_project_intelligence_says_when_it_does_not_know(kit, lg):
    b = pi(lg, kit, kit.world.pm, "What is the weather on the moon?").json()
    assert "cannot answer that from this project's records" in b["answer"] and b["sources"] == [] and b["context_used"]["answered"] is False


def test_the_briefing_carries_the_authored_context_beside_the_live_state(kit, lg, api):
    know(api, kit, "OVERVIEW", "Project at a glance", "Pipeline project on the record.", "FROM_RECORDS" if False else "AUTHORED")
    b = lg.get(f"/api/v1/projects/{kit.project}/agent/briefing", kit.world.pm).json()
    assert b["project_context_available"] and b["project_context"][0]["title"] == "Project at a glance" and b["project_context"][0]["provenance"] == "AUTHORED"
    assert b["sources"][0]["kind"] == "LIVE_DATA" and any(s["kind"] == "PROJECT_KNOWLEDGE" for s in b["sources"])
