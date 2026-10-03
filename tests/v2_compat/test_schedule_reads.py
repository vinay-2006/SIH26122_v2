"""Schedule, WBS, stage and progress reads in the legacy contract (what the original project context and WBS explorer call)."""
import pytest

from domainkit import connect
from v2api import seed_progress

pytestmark = pytest.mark.db_write


@pytest.fixture
def ver(kit):
    with connect() as c:
        return c.execute("select version_id from schedule_versions where project_id = %s and status = 'ACTIVE'", (kit.project,)).fetchone()["version_id"]


def test_activities_carry_the_demo_fields_and_ledger_derived_state(kit, lg, ver):
    seed_progress(kit.project, "A2000", {"PIPE_STRUNG_KM": 12}, kit.world.sup, kit.world.se)
    r = lg.get(f"/api/v1/schedules/{ver}/activities", kit.world.se)
    assert r.status_code == 200, r.text
    acts = {a["activity_id"]: a for a in r.json()}
    assert acts["A2000"]["execution_state"] == "IN_PROGRESS" and acts["A2000"]["actual_pct_complete"] == 50 and acts["A2000"]["planned_quantity"] == 24 and acts["A2000"]["uom"] == "KM"
    assert acts["A2010"]["execution_state"] == "NOT_STARTED" and acts["A2010"]["stage_name"] == "Pipeline Construction" and "_uid" not in acts["A2010"]
    assert acts["A2010"]["contractor_id"] is None and acts["A2010"]["work_package_id"] is None          # contractors / work packages are a deferred scope (R15)
    assert lg.get(f"/api/v1/schedules/{ver}/activities/A2010", kit.world.sup).json()["activity_id"] == "A2010"
    assert lg.get(f"/api/v1/schedules/{ver}/activities/NOPE", kit.world.sup).status_code == 404


def test_wbs_tree_groups_activities_by_wbs_code(kit, lg, ver):
    t = lg.get(f"/api/v1/schedules/{ver}/wbs-tree", kit.world.pm).json()
    flat = {a["activity_id"] for g in t["wbs_groups"] for a in g["activities"]}
    assert "A2010" in flat and t["schedule_id"] == str(ver) and all(g["wbs_code"] for g in t["wbs_groups"])


def test_progress_breakdown_matches_the_ledger_rollup(kit, lg, ver):
    seed_progress(kit.project, "A2000", {"PIPE_STRUNG_KM": 12}, kit.world.sup, kit.world.se)
    b = lg.get(f"/api/v1/projects/{kit.project}/schedules/{ver}/progress/breakdown", kit.world.sup).json()
    assert b["overall_progress_pct"] == float(kit.project_pct())
    all_items = [i for s in b["stages"] for i in s["activities"]] + b["unassigned_activities"]
    assert {i["activity_id"] for i in all_items} >= {"A2000", "A2010"}
    item = next(i for i in all_items if i["activity_id"] == "A2000")
    assert item["progress_pct"] == 50 and item["canonical_state"] == "IN_PROGRESS" and item["actual_quantity"] == 12
    assert abs(sum(i["weighted_contribution"] for i in all_items) - b["overall_progress_pct"]) < 0.5
    stages = lg.get(f"/api/v1/projects/{kit.project}/stages?schedule_id={ver}", kit.world.sup).json()
    assert [s["stage_name"] for s in stages] and all(s["schedule_id"] == str(ver) for s in stages)


def test_another_projects_version_is_never_readable(kit, lg, ver, api):
    from conftest import Legacy, build_and_activate
    build_and_activate(api, kit.world.pm2, kit.world.project2, "csv")
    with connect() as c:
        other = c.execute("select version_id from schedule_versions where project_id = %s and status = 'ACTIVE'", (kit.world.project2,)).fetchone()["version_id"]
    assert lg.get(f"/api/v1/schedules/{other}/activities", kit.world.se).status_code == 403
    assert lg.get(f"/api/v1/projects/{kit.project}/schedules/{other}/progress/breakdown", kit.world.sup).status_code == 403
    assert lg.get(f"/api/v1/schedules/{ver}/activities", kit.world.outsider).status_code == 403
