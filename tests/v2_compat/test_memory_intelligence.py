"""Institutional memory that works for the project: the Lessons Radar, the insights, and capturing a lesson from a resolved issue."""
import pytest

from domainkit import connect

pytestmark = pytest.mark.db_write


def M(kit, tail):
    return f"/api/v1/projects/{kit.project}/memory{tail}"


def S(kit, tail):
    return f"/api/v1/projects/{kit.project}/schedules/{kit.world.version}{tail}"


@pytest.fixture
def ver(kit):
    with connect() as c:
        kit.world.version = c.execute("select version_id from schedule_versions where project_id = %s and status = 'ACTIVE'", (kit.project,)).fetchone()["version_id"]
    return kit.world.version


def lesson(lg, kit, title, **kw):
    body = {"category_code": "MATERIAL_DELIVERY_DELAY", "title": title, "narrative": "What happened.", "root_cause": "Late supplier release", "resolution": "Place the order at contract award",
            "outcome": "Recovered", "lessons_learned": "Release long-lead orders early", "share_with_organisation": True}
    body.update(kw)
    r = lg.post(M(kit, ""), kit.world.sup, json=body)
    assert r.status_code == 201, r.text
    return r.json()["memory_id"]


def resolve_issue(lg, kit, title="Line pipe delivery late"):
    r = lg.post(S(kit, "/issues"), kit.world.se, json={"activity_id": "A2000", "category_code": "MATERIAL_DELIVERY_DELAY", "title": title, "description": "Pipe not on site", "severity": "HIGH", "blocks_work": True})
    assert r.status_code == 201, r.text
    iid = r.json()["issue_id"]
    assert lg.post(S(kit, f"/issues/{iid}/resolve"), kit.world.sup, json={"resolution_notes": "Expedited a second mill", "add_to_memory": False}).status_code == 200
    return iid


def test_the_radar_matches_lessons_to_upcoming_work_and_explains_why(kit, lg, ver):
    lesson(lg, kit, "Pipe stringing stopped by late line pipe delivery")
    lesson(lg, kit, "Unrelated canteen contract dispute", category_code="TECHNICAL", root_cause=None, resolution=None, lessons_learned="Keep minutes of every meeting")
    r = lg.get(M(kit, "/radar?horizon_days=730"), kit.world.sup)
    assert r.status_code == 200, r.text
    b = r.json()
    ids = {i["activity_id"]: i for i in b["items"]}
    assert "A2000" in ids and b["lessons_considered"] == 2 and b["activities_with_lessons"] >= 1
    top = ids["A2000"]["lessons"][0]
    assert top["title"].startswith("Pipe stringing stopped") and top["why_matched"] and any("shared terms" in x for x in top["why_matched"])
    assert top["corrective_action"] == "Place the order at contract award" and ids["A2000"]["preventive_actions"] == ["Place the order at contract award"]
    assert all(all("canteen" not in l["title"].lower() for l in i["lessons"]) for i in b["items"])                 # a lesson with nothing in common is not shown
    assert b["project_status"] in ("UPCOMING", "ONGOING") and b["retrieval_mode"]


def test_radar_roles_and_isolation(kit, lg, ver):
    lesson(lg, kit, "Pipe stringing stopped by late line pipe delivery")
    for who in (kit.world.sup, kit.world.pm):
        assert lg.get(M(kit, "/radar"), who).status_code == 200, who.name
    for who in (kit.world.se, kit.world.outsider):
        assert lg.get(M(kit, "/radar"), who).status_code == 403, who.name
    assert lg.get(M(kit, "/insights"), kit.world.pm).status_code == 200 and lg.get(M(kit, "/insights"), kit.world.se).status_code == 403
    assert lg.get(M(kit, "/radar?horizon_days=3"), kit.world.sup).status_code == 422                                   # bounded input


def test_insights_summarise_categories_delay_and_capture_rate(kit, lg, ver):
    lesson(lg, kit, "Steel plate delivery slipped")
    lesson(lg, kit, "Valve delivery slipped twice")
    iid = resolve_issue(lg, kit)
    ins = lg.get(M(kit, "/insights"), kit.world.pm).json()
    cat = next(c for c in ins["categories"] if c["category_code"] == "MATERIAL_DELIVERY_DELAY")
    assert cat["lessons"] == 2 and cat["recurring"] and ins["lessons"]["visible"] == 2
    assert ins["capture"] == {"resolved_issues": 1, "captured": 0, "waiting": 1, "rate_pct": 0.0}
    assert lg.post(M(kit, f"/capture/{iid}"), kit.world.sup, json={"lessons_learned": "Qualify a second mill before the order is placed"}).status_code == 201
    assert lg.get(M(kit, "/insights"), kit.world.pm).json()["capture"] == {"resolved_issues": 1, "captured": 1, "waiting": 0, "rate_pct": 100.0}


def test_resolved_issues_wait_in_a_capture_queue_until_the_supervisor_keeps_the_lesson(kit, lg, ver):
    iid = resolve_issue(lg, kit)
    q = lg.get(M(kit, "/capture-queue"), kit.world.sup).json()["items"]
    assert [i["issue_id"] for i in q] == [iid] and q[0]["resolution_notes"] == "Expedited a second mill" and q[0]["activity_id"] == "A2000"
    for who in (kit.world.pm, kit.world.se):
        assert lg.get(M(kit, "/capture-queue"), who).status_code == 403
        assert lg.post(M(kit, f"/capture/{iid}"), who, json={"lessons_learned": "not allowed here"}).status_code == 403
    assert lg.post(M(kit, f"/capture/{iid}"), kit.world.sup, json={"lessons_learned": "x"}).status_code == 422
    ok = lg.post(M(kit, f"/capture/{iid}"), kit.world.sup, json={"lessons_learned": "Qualify a second mill before the order is placed", "outcome": "Two weeks lost", "share_with_organisation": True})
    assert ok.status_code == 201, ok.text
    assert lg.get(M(kit, "/capture-queue"), kit.world.sup).json()["items"] == []
    assert lg.post(M(kit, f"/capture/{iid}"), kit.world.sup, json={"lessons_learned": "Again, a second time"}).status_code == 409
    with connect() as c:
        m = c.execute("select source, visibility, corrective_action, issue_id from institutional_memory").fetchone()
        assert (m["source"], m["visibility"], m["corrective_action"], str(m["issue_id"])) == ("ISSUE_RESOLUTION", "ORGANISATION", "Expedited a second mill", iid)
        assert c.execute("select count(*) n from audit_logs where action = 'MEMORY_RECORDED'").fetchone()["n"] == 1
    assert lg.post(M(kit, "/capture/not-a-uuid"), kit.world.sup, json={"lessons_learned": "whatever here"}).status_code == 404


def test_an_active_issue_cannot_be_captured(kit, lg, ver):
    r = lg.post(S(kit, "/issues"), kit.world.se, json={"activity_id": "A2000", "category_code": "WEATHER", "title": "Still raining", "severity": "LOW", "blocks_work": False})
    assert lg.post(M(kit, f"/capture/{r.json()['issue_id']}"), kit.world.sup, json={"lessons_learned": "Too early for a lesson"}).status_code == 409


def test_the_time_agent_answers_which_lessons_apply_and_cites_the_radar(kit, lg, ver):
    lesson(lg, kit, "Pipe stringing stopped by late line pipe delivery", delay_days=None)
    r = lg.post(f"/api/v1/time-agent/projects/{kit.project}/ask", kit.world.sup, json={"query": "Which past lessons apply to upcoming work?"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert "Lessons that apply" in b["reply"] and "Pipe stringing stopped by late line pipe delivery" in b["reply"] and "what worked: Place the order at contract award" in b["reply"]
    assert any(s["ref"] == "memory_radar" for s in b["sources"]) and {"label": "Open Root Cause & Memory", "to": "/root-cause"} in b["links"]
    empty = lg.post(f"/api/v1/time-agent/projects/{kit.project}/ask", kit.world.sup, json={"query": "What lessons are recorded?"}).json()
    assert "Lessons" in empty["reply"]
