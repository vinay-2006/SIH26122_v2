"""Impact preview (original A1 + compound engines) and the problem-state watch list, on v2 data."""
import pytest

from domainkit import connect

pytestmark = pytest.mark.db_write


@pytest.fixture
def ver(kit):
    with connect() as c:
        return c.execute("select version_id from schedule_versions where project_id = %s and status = 'ACTIVE'", (kit.project,)).fetchone()["version_id"]


def test_the_original_a1_preview_propagates_a_delay_downstream(kit, lg, ver):
    r = lg.get("/api/v1/schedule/A2000/impact-preview?delay_days=5", kit.world.sup)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["activity_id"] == "A2000" and body["delay_days"] == 5 and isinstance(body["impacts"], list)
    assert any(i["successor_activity_id"] == "A2010" for i in body["impacts"])
    assert lg.get("/api/v1/schedule/A2000/impact-preview?delay_days=-1", kit.world.sup).status_code == 400


def test_the_compound_engine_and_the_watchlist(kit, lg, ver):
    base = f"/api/v1/projects/{kit.project}/schedules/{ver}"
    one = lg.get(f"{base}/activities/A2000/impact-preview?delay_days=10", kit.world.se)
    assert one.status_code == 200, one.text
    assert one.json()["algorithm_version"] == "V7_COMPOUND_A1" and one.json()["schedule_impact_days"] >= 0
    multi = lg.post(f"{base}/impact/preview", kit.world.sup, json={"seed_activities": [{"activity_id": "A2000", "delay_days": 7}, {"activity_id": "A1010", "delay_days": 3}]})
    assert multi.status_code == 200 and multi.json()["affected_activities"]
    # an active blocking issue puts the activity on the watch list with its real downstream reach
    assert lg.get(f"{base}/impact/watchlist", kit.world.sup).json() == []
    lg.post(f"{base}/issues", kit.world.se, json={"activity_id": "A2000", "category_code": "WEATHER", "title": "Washout", "severity": "HIGH", "blocks_work": True})
    wl = lg.get(f"{base}/impact/watchlist", kit.world.sup).json()
    assert [w["activity_id"] for w in wl] == ["A2000"] and wl[0]["workflow_condition"] == "BLOCKED" and wl[0]["total_downstream_count"] >= 1


def test_impact_is_scoped_to_the_callers_project(kit, lg, ver, api):
    from v2api import build_and_activate
    build_and_activate(api, kit.world.pm2, kit.world.project2, "csv")
    with connect() as c:
        other = c.execute("select version_id from schedule_versions where project_id = %s and status = 'ACTIVE'", (kit.world.project2,)).fetchone()["version_id"]
    assert lg.get(f"/api/v1/projects/{kit.project}/schedules/{other}/activities/A2000/impact-preview?delay_days=3", kit.world.sup).status_code == 403
    assert lg.get(f"/api/v1/projects/{kit.world.project2}/schedules/{other}/impact/watchlist", kit.world.sup).status_code == 403
    assert lg.get("/api/v1/schedule/A2000/impact-preview?delay_days=3", kit.world.outsider).status_code == 403
