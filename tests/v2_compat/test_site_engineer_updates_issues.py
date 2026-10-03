"""Site Engineer: My Updates, Issues & Delays, memory suggestions -- through the legacy contract on v2 data."""
import pytest

from domainkit import connect

pytestmark = pytest.mark.db_write
TEXT = "Welding mainline A2010: 120 joints completed today"


def P(kit, tail):
    return f"/api/v1/projects/{kit.project}{tail}"


def S(kit, tail):
    return f"/api/v1/projects/{kit.project}/schedules/{kit.world.version}{tail}"


@pytest.fixture
def ver(kit):
    with connect() as c:
        kit.world.version = c.execute("select version_id from schedule_versions where project_id = %s and status = 'ACTIVE'", (kit.project,)).fetchone()["version_id"]
    return kit.world.version


def test_my_claims_and_notifications_follow_the_supervisors_decision(kit, lg, ver):
    r = lg.post("/api/v1/claims/text", kit.world.se, json={"raw_claim_text": TEXT}).json()
    mine = lg.get(P(kit, "/my-claims"), kit.world.se).json()
    assert [m["event_id"] for m in mine] == [r["event_id"]] and mine[0]["decision_action"] is None and mine[0]["activity_id"] == "A2010"
    from backend.v2.domain import decisions
    import uuid
    decisions.decide(kit.sup, uuid.UUID(r["event_id"]), action="REJECT", justification="Quantity not verified on site")
    mine = lg.get(P(kit, "/my-claims"), kit.world.se).json()
    assert mine[0]["status"] == "REJECTED" and mine[0]["decision_action"] == "REJECT" and mine[0]["decision_comment"] == "Quantity not verified on site"
    n = lg.get(P(kit, "/notifications?unread_only=true"), kit.world.se).json()
    assert n["unread_count"] == 1 and n["items"][0]["notification_type"] == "CLAIM_DECISION" and n["items"][0]["decision_action"] == "REJECT"
    nid = n["items"][0]["notification_id"]
    assert lg.post(P(kit, f"/notifications/{nid}/read"), kit.world.se).status_code == 200
    assert lg.get(P(kit, "/notifications?unread_only=true"), kit.world.se).json()["unread_count"] == 0
    assert lg.post(P(kit, f"/notifications/{nid}/read"), kit.world.se2).status_code == 404            # someone else's notification
    assert lg.get(P(kit, "/my-claims"), kit.world.se2).json() == []


def test_the_path_project_is_authoritative(kit, lg, ver, api):
    from conftest import Legacy
    assert lg.get(f"/api/v1/projects/{kit.world.project2}/my-claims", kit.world.se).status_code == 403
    assert lg.get(P(kit, "/my-claims"), kit.world.sup).status_code == 403           # a Supervisor has no "my claims"
    assert lg.get(P(kit, "/my-claims"), kit.world.pm).status_code == 403


def test_report_list_and_resolve_an_issue_and_promote_it_to_memory(kit, lg, ver):
    cats = lg.get(P(kit, "/issue-categories"), kit.world.se).json()
    assert {"code": "WEATHER", "name": "Weather disruption"} in cats
    r = lg.post(S(kit, "/issues"), kit.world.se, json={"activity_id": "A2010", "category_code": "WEATHER", "title": "Heavy rain stops welding", "description": "Trench flooded",
                                                       "severity": "HIGH", "blocks_work": True, "expected_duration_days": 3})
    assert r.status_code == 201, r.text
    iss = r.json()
    assert iss["activity_id"] == "A2010" and iss["status"] == "ACTIVE" and iss["category_name"] == "Weather disruption" and iss["stage_name"] == "Pipeline Construction"
    assert [i["issue_id"] for i in lg.get(S(kit, "/issues?status=ACTIVE&mine=true"), kit.world.se).json()] == [iss["issue_id"]]
    assert lg.get(S(kit, "/issues"), kit.world.se2).json() == []                                # an engineer sees what they raised
    assert lg.get(S(kit, "/issues"), kit.world.sup).json()[0]["issue_id"] == iss["issue_id"]
    assert lg.post(S(kit, f"/issues/{iss['issue_id']}/resolve"), kit.world.se, json={"resolution_notes": "Pumped out", "add_to_memory": False}).status_code == 403
    res = lg.post(S(kit, f"/issues/{iss['issue_id']}/resolve"), kit.world.sup, json={"resolution_notes": "Pumped out the trench", "lessons_learned": "Check forecast before trenching",
                                                                                     "add_to_memory": True, "share_with_organisation": True})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "RESOLVED" and body["memory_incident_id"]
    sug = lg.post(P(kit, "/memory/for-issue"), kit.world.se, json={"category_code": "WEATHER", "query": "rain flooded trench", "activity_id": "A2010"}).json()
    assert sug["total_candidates"] >= 1 and "forecast" in sug["results"][0]["record"]["content"].lower() and sug["retrieval_mode"]
    assert lg.post(P(kit, "/memory"), kit.world.se, json={"category_code": "WEATHER", "title": "x", "narrative": "y"}).status_code == 403     # only a Supervisor records memory


def test_pm_can_read_issues_and_root_causes_but_never_change_them(kit, lg, ver):
    lg.post(S(kit, "/issues"), kit.world.se, json={"activity_id": "A2010", "category_code": "WEATHER", "title": "Rain", "severity": "LOW", "blocks_work": False})
    assert len(lg.get(S(kit, "/issues"), kit.world.pm).json()) == 1
    assert lg.post(S(kit, "/issues"), kit.world.pm, json={"activity_id": "A2010", "category_code": "WEATHER", "title": "Rain", "severity": "LOW"}).status_code == 403
    assert lg.post(S(kit, "/root-causes"), kit.world.pm, json={"category_code": "WEATHER", "title": "Monsoon"}).status_code == 403
    assert lg.get(S(kit, "/root-cause-analysis"), kit.world.pm).status_code == 200


def test_root_causes_group_issues_and_the_analysis_flags_repeated_patterns(kit, lg, ver):
    ids = []
    for act in ("A2000", "A2010", "A2000"):
        ids.append(lg.post(S(kit, "/issues"), kit.world.se, json={"activity_id": act, "category_code": "WEATHER", "title": f"Rain on {act}", "severity": "MEDIUM", "blocks_work": True,
                                                                  "expected_duration_days": 2}).json()["issue_id"])
    rc = lg.post(S(kit, "/root-causes"), kit.world.sup, json={"category_code": "WEATHER", "title": "Monsoon onset", "issue_ids": ids[:2]})
    assert rc.status_code == 201, rc.text
    assert rc.json()["issue_count"] == 2
    again = lg.post(S(kit, f"/root-causes/{rc.json()['root_cause_id']}/issues"), kit.world.sup, json={"issue_ids": [ids[2]]}).json()
    assert again["issue_count"] == 3
    an = lg.get(S(kit, "/root-cause-analysis"), kit.world.sup).json()
    cat = an["categories"][0]
    assert cat["category_code"] == "WEATHER" and cat["issue_count"] == 3 and cat["activity_count"] == 2 and cat["is_repeated_pattern"] is True and cat["linked_to_root_cause"] == 3
    assert an["total_issues"] == 3 and an["open_issues"] == 3 and an["delayed_stages"][0]["stage_name"] == "Pipeline Construction"
