"""Baseline import: upload -> validation report -> mapping -> atomic build -> activate, for every supported format; invalid input fails
without writing anything."""
import pytest

from v2api import build_and_activate, connect, file_bytes, upload
from backend.v2 import audit

P = "/api/v2/projects"
TABLES = ["source_documents", "schedule_imports", "schedule_versions", "activities", "assignments", "schedule_wbs", "wbs_stage_rules",
          "baseline_activities", "baseline_resources", "schedule_dependencies", "project_resources", "activity_lineage"]


def counts():
    with connect() as c:
        return {t: c.execute(f"select count(*) n from {t}").fetchone()["n"] for t in TABLES}


@pytest.mark.parametrize("fmt", ["xer", "msp", "csv"])
def test_baseline_import_build_and_activate_for_each_format(world, api, fmt):
    r = upload(api, world.pm, world.project, fmt)
    assert r.status_code == 201, r.text
    imp = r.json()
    assert imp["status"] == "PARSED" and imp["format"] == {"xer": "XER", "msp": "MSPDI", "csv": "CSV"}[fmt]
    rep = imp["report"]
    assert rep["valid"] and rep["ready_to_build"] and rep["errors"] == []
    assert rep["stats"]["activities"] == 12 and rep["stats"]["assignments"] == 20 and rep["stats"]["dependencies"] == 11
    assert imp["reconciliation"] is None and imp["base_version_id"] is None            # first schedule: nothing to reconcile against
    types = {w["type"] for w in imp["wbs"]}
    assert "PROJECT" in types and "STAGE" in types and sum(w["type"] == "PROJECT" for w in imp["wbs"]) == 1
    assert imp["header"]["data_date"] == "2026-01-05" and imp["header"]["planned_finish"] == "2026-07-31"
    # nothing is in the schedule tables yet: staging only
    assert counts()["schedule_versions"] == 0 and counts()["baseline_activities"] == 0

    b = api.post(f"{P}/{world.project}/schedule-imports/{imp['import_id']}/build", world.pm)
    assert b.status_code == 201, b.text
    v = b.json()
    assert v["kind"] == "BASELINE" and v["status"] == "VALIDATED" and v["version_no"] == 1
    assert (v["activities"], v["assignments"], v["dependencies"]) == (12, 20, 11)
    assert api.get(f"{P}/{world.project}/schedule-imports/{imp['import_id']}", world.pm).json()["status"] == "BUILT"

    a = api.post(f"{P}/{world.project}/schedule-versions/{v['version_id']}/activate", world.pm)
    assert a.status_code == 200 and a.json()["previous_active_version_id"] is None
    ver = api.get(f"{P}/{world.project}/schedule-versions/{v['version_id']}", world.pm).json()
    assert ver["status"] == "ACTIVE" and ver["locked_at"] is not None and ver["activities"] == 12
    assert api.get(f"{P}/{world.project}", world.se).json()["active_version"]["version_id"] == v["version_id"]

    with connect() as c:
        acts = {r["external_activity_id"]: r for r in c.execute("select * from baseline_activities").fetchall()}
        assert len(acts) == 12 and len({r["activity_uid"] for r in acts.values()}) == 12
        assert acts["A2010"]["discipline_code"] == "PIPING" and acts["A0100"]["discipline_code"] == "HSE" and acts["A1020"]["discipline_code"] == "CIVIL"
        assert acts["A3030"]["activity_type"] == "MILESTONE" and float(acts["A3030"]["baseline_duration"]) == 0
        assert float(acts["A1000"]["baseline_duration"]) == 18 and acts["A1000"]["is_critical"] is True and acts["A1020"]["is_critical"] is False
        res = {r["resource_code"]: r for r in c.execute("select * from project_resources").fetchall()}
        assert res["MANHOURS"]["resource_class"] == "LABOR" and res["EXCAVATOR_HOURS"]["resource_class"] == "EQUIPMENT" and res["WELD_JOINTS"]["resource_class"] == "MATERIAL"
        flags = {(r["external_activity_id"], r["resource_code"]): (r["measures_progress"], r["unit_of_measure"], float(r["baseline_qty"])) for r in c.execute(
            "select a.external_activity_id, p.resource_code, r.* from baseline_resources r join baseline_activities a on a.activity_row_id = r.activity_row_id "
            "join project_resources p on p.resource_id = r.resource_id").fetchall()}
        assert flags[("A2010", "WELD_JOINTS")] == (True, "JOINT", 2000.0)
        assert flags[("A2010", "MANHOURS")][0] is False and flags[("A1010", "EXCAVATOR_HOURS")][0] is False       # effort never drives physical progress
        assert flags[("A1020", "CONCRETE_M3")] == (True, "M3", 480.0) and flags[("A1020", "STEEL_TONNES")][:2] == (True, "TONNE")
        assert c.execute("select count(*) n from wbs_stage_rules").fetchone()["n"] == sum(w["type"] == "STAGE" for w in imp["wbs"])
        dep = c.execute("select relationship_type, lag_days from schedule_dependencies d join baseline_activities s on s.activity_uid = d.successor_uid and s.version_id = d.version_id "
                        "where s.external_activity_id = 'A1020'").fetchone()
        assert (dep["relationship_type"], float(dep["lag_days"])) == ("SS", 5.0)
        assert audit.verify_chain(c)["valid"]


def test_the_wbs_tree_and_activities_can_be_read_back(world, api):
    _, vid = build_and_activate(api, world.pm, world.project, "xer")
    wbs = api.get(f"{P}/{world.project}/schedule-versions/{vid}/wbs", world.se).json()
    assert wbs[0]["node_type"] == "PROJECT" and wbs[0]["level"] == 0 and max(w["level"] for w in wbs) == 2
    acts = api.get(f"{P}/{world.project}/schedule-versions/{vid}/activities", world.sup).json()
    a = next(x for x in acts if x["external_activity_id"] == "A1010")
    assert {r["resource"] for r in a["assignments"]} == {"CLEARED_ROW_KM", "EXCAVATOR_HOURS", "MANHOURS"}


def test_csv_without_header_dates_derives_them_and_says_so(world, api):
    files = {"file": ("nsp.csv", file_bytes("nsp.csv"), "text/csv"), "resources_file": ("r.csv", file_bytes("nsp_resources.csv"), "text/csv")}
    r = api.post(f"{P}/{world.project}/schedule-imports", world.pm, files=files)
    assert r.status_code == 201
    w = {x["code"] for x in r.json()["report"]["warnings"]}
    assert {"DATA_DATE_ASSUMED", "PLANNED_START_DERIVED", "PLANNED_FINISH_DERIVED"} <= w
    assert r.json()["header"]["planned_start"] == "2026-01-12" and r.json()["header"]["data_date"] == "2026-01-12"


def test_baseline_name_label_and_dates_can_be_set_by_decision(world, api):
    iid = upload(api, world.pm, world.project, "csv").json()["import_id"]
    d = api.put(f"{P}/{world.project}/schedule-imports/{iid}/decisions", world.pm,
                json={"header": {"baseline_name": "Contract Baseline Rev 0", "label": "Signed 5 Jan"}}).json()
    assert d["header"]["baseline_name"] == "Contract Baseline Rev 0"
    v = api.post(f"{P}/{world.project}/schedule-imports/{iid}/build", world.pm).json()
    got = api.get(f"{P}/{world.project}/schedule-versions/{v['version_id']}", world.pm).json()
    assert got["baseline_name"] == "Contract Baseline Rev 0" and got["label"] == "Signed 5 Jan"


# ---------------------------------------------------------------------------------------------------- mapping decisions
CSV_UNMAPPED = (b"Activity ID,Activity Name,WBS Path,Discipline,Baseline Start,Baseline Finish,Baseline Duration\n"
                b"X1,Radiograph welds,Spur > Piping > Section 1,Radiography & NDT,03-03-2026,14-03-2026,10\n"
                b"X2,Lay pipe,Spur > Piping > Section 1,Pipeline,16-03-2026,28-03-2026,11\n")
RES_UNMAPPED = b"Activity ID,Resource ID,Baseline Qty,Unit\nX1,FILMS,400,reels\nX2,PIPE_LAID_KM,6,km\n"


def test_unmapped_discipline_and_unit_must_be_resolved_before_build(world, api):
    files = {"file": ("s.csv", CSV_UNMAPPED, "text/csv"), "resources_file": ("r.csv", RES_UNMAPPED, "text/csv")}
    r = api.post(f"{P}/{world.project}/schedule-imports", world.pm, files=files,
                 data={"data_date": "2026-03-01", "planned_start": "2026-03-03", "planned_finish": "2026-03-28"})
    assert r.status_code == 201
    imp = r.json()
    assert imp["report"]["valid"] and not imp["report"]["ready_to_build"]
    assert imp["report"]["mapping"] == {"unmapped_disciplines": ["radiography & ndt"], "unmapped_units": ["reels"]}
    url = f"{P}/{world.project}/schedule-imports/{imp['import_id']}"
    b = api.post(f"{url}/build", world.pm)
    assert b.status_code == 409 and b.json()["error"]["code"] == "IMPORT_NOT_READY"
    assert counts()["schedule_versions"] == 0 and counts()["activities"] == 0           # the refused build wrote nothing
    # bad decisions are rejected
    assert api.put(f"{url}/decisions", world.pm, json={"discipline_map": {"radiography & ndt": "WIZARDRY"}}).status_code == 422
    assert api.put(f"{url}/decisions", world.pm, json={"uom_map": {"reels": "PARSEC"}}).status_code == 422
    assert api.put(f"{url}/decisions", world.pm, json={"wbs_types": {"X": "CASTLE"}}).status_code == 422
    assert api.put(f"{url}/decisions", world.pm, json={"nonsense": 1}).status_code == 422
    # good decisions
    ok = api.put(f"{url}/decisions", world.pm, json={"discipline_map": {"Radiography & NDT": "PIPING"}, "uom_map": {"reels": "NOS"}})
    assert ok.status_code == 200 and ok.json()["report"]["ready_to_build"]
    v = api.post(f"{url}/build", world.pm)
    assert v.status_code == 201, v.text
    with connect() as c:
        a = c.execute("select discipline_code, discipline_source from baseline_activities where external_activity_id = 'X1'").fetchone()
        assert (a["discipline_code"], a["discipline_source"]) == ("PIPING", "Radiography & NDT")      # original label preserved
        assert c.execute("select discipline_code from baseline_activities where external_activity_id = 'X2'").fetchone()["discipline_code"] == "PIPING"   # alias 'pipeline'
        assert c.execute("select unit_of_measure, measures_progress from baseline_resources r join project_resources p on p.resource_id = r.resource_id "
                         "where p.resource_code = 'FILMS'").fetchone() == {"unit_of_measure": "NOS", "measures_progress": True}


def test_wbs_node_types_can_be_overridden_and_a_synthetic_root_is_added_when_needed(world, api):
    csv = (b"Activity ID,Activity Name,WBS Code,WBS Name,Discipline,Start,Finish\n"
           b"A,Dig,1.1,Digging,Civil,01-03-2026,05-03-2026\nB,Pour,2.1,Pouring,Civil,06-03-2026,10-03-2026\nC,Loose,,,Civil,11-03-2026,12-03-2026\n")
    iid = api.post(f"{P}/{world.project}/schedule-imports", world.pm, files={"file": ("s.csv", csv, "text/csv")},
                   data={"data_date": "2026-03-01"}).json()["import_id"]
    wbs = api.get(f"{P}/{world.project}/schedule-imports/{iid}", world.pm).json()["wbs"]
    assert sum(w["type"] == "PROJECT" for w in wbs) == 1 and any(w["name"] == "Unassigned activities" for w in wbs)      # one root, loose activity placed
    api.put(f"{P}/{world.project}/schedule-imports/{iid}/decisions", world.pm, json={"wbs_types": {"1": "AREA", "1.1": "PACKAGE"}})
    api.post(f"{P}/{world.project}/schedule-imports/{iid}/build", world.pm)
    with connect() as c:
        t = {r["wbs_code"]: r["node_type"] for r in c.execute("select wbs_code, node_type from schedule_wbs").fetchall()}
        assert t["1"] == "AREA" and t["1.1"] == "PACKAGE" and t["ROOT"] == "PROJECT"


# ---------------------------------------------------------------------------------------------------- invalid input: nothing is written
BAD = {
    "duplicate_ids": (b"Activity ID,Activity Name,Start,Finish\nA,x,01-03-2026,02-03-2026\nA,y,03-03-2026,04-03-2026\n", "DUPLICATE_ACTIVITY_ID"),
    "finish_before_start": (b"Activity ID,Activity Name,Start,Finish\nA,x,05-03-2026,02-03-2026\n", "FINISH_BEFORE_START"),
    "missing_dates": (b"Activity ID,Activity Name,Start,Finish\nA,x,,\n", "MISSING_DATES"),
    "unparseable_date": (b"Activity ID,Activity Name,Start,Finish\nA,x,someday,02-03-2026\n", "BAD_DATE"),
    "dependency_cycle": (b"Activity ID,Activity Name,Start,Finish,Predecessors\nA,x,01-03-2026,02-03-2026,B\nB,y,03-03-2026,04-03-2026,A\n", "DEPENDENCY_CYCLE"),
    "unknown_predecessor": (b"Activity ID,Activity Name,Start,Finish,Predecessors\nA,x,01-03-2026,02-03-2026,GHOST\n", "UNKNOWN_DEPENDENCY_ACTIVITY"),
    "no_rows": (b"Activity ID,Activity Name,Start,Finish\n", "EMPTY_FILE"),
    "missing_columns": (b"Foo,Bar\n1,2\n", "MISSING_COLUMNS"),
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_invalid_schedules_are_rejected_with_a_report_and_nothing_is_written(world, api, case):
    body, code = BAD[case]
    before = counts()
    r = api.post(f"{P}/{world.project}/schedule-imports", world.pm, files={"file": ("bad.csv", body, "text/csv")}, data={"data_date": "2026-03-01"})
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    found = {e["code"] for e in (err["details"] or {}).get("errors", [])} | {err["code"]}
    assert code in found, found
    assert counts() == before                                                             # no document, import, version, or any row at all


def test_invalid_resource_quantities_are_rejected_atomically(world, api):
    before = counts()
    bad_res = b"Activity ID,Resource ID,Baseline Qty,Unit\nA,CONCRETE_M3,-5,m3\nA,STEEL,0,tonne\nA,GHOST,1,m3\nZ,MH,1,mh\n"
    csv = b"Activity ID,Activity Name,Start,Finish\nA,x,01-03-2026,02-03-2026\n"
    r = api.post(f"{P}/{world.project}/schedule-imports", world.pm, files={"file": ("s.csv", csv, "text/csv"), "resources_file": ("r.csv", bad_res, "text/csv")},
                 data={"data_date": "2026-03-01"})
    assert r.status_code == 422
    codes = {e["code"] for e in r.json()["error"]["details"]["errors"]}
    assert {"NON_POSITIVE_QUANTITY", "ASSIGNMENT_UNKNOWN_ACTIVITY"} <= codes
    assert counts() == before


@pytest.mark.parametrize("name,body,code", [
    ("plan.mpp", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 200, "MPP_NOT_SUPPORTED"),
    ("notes.txt", b"hello", "EMPTY_FILE"), ("empty.csv", b"", "EMPTY_FILE"), ("broken.xer", b"ERMHDR\t19\r\n%T\tTASK\r\n", "EMPTY_FILE"),
    ("evil.xml", b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><Project xmlns="http://schemas.microsoft.com/project"/>', "UNSAFE_XML"),
    ("book.xlsx", b"PK\x03\x04" + b"\x00" * 50, "XLSX_NOT_SUPPORTED"), ("random.bin", b"\x01\x02\x03\x04", "UNKNOWN_FORMAT"),
])
def test_unreadable_files_get_a_specific_actionable_error(world, api, name, body, code):
    before = counts()
    r = api.post(f"{P}/{world.project}/schedule-imports", world.pm, files={"file": (name, body, "application/octet-stream")})
    assert r.status_code == 422 and r.json()["error"]["code"] == code, r.text
    if code == "MPP_NOT_SUPPORTED":
        assert "Save As" in r.json()["error"]["message"]
    assert counts() == before


# ---------------------------------------------------------------------------------------------------- atomic build, duplicates, discard
def test_a_failure_during_build_leaves_no_partial_schedule(world, client_500, monkeypatch):
    from v2api import Api
    from backend.v2.services import schedules as svc
    api = Api(client_500)
    iid = upload(api, world.pm, world.project, "csv").json()["import_id"]
    before = counts()

    def boom(*a, **k):
        raise RuntimeError("simulated crash after every row was inserted")
    with monkeypatch.context() as m:
        m.setattr(svc.audit, "log", boom)                                                  # the last step of the build transaction
        r = api.post(f"{P}/{world.project}/schedule-imports/{iid}/build", world.pm)
    assert r.status_code == 500
    assert counts() == before, "build must be all-or-nothing"
    assert api.get(f"{P}/{world.project}/schedule-imports/{iid}", world.pm).json()["status"] == "PARSED"
    assert api.post(f"{P}/{world.project}/schedule-imports/{iid}/build", world.pm).status_code == 201     # retry succeeds


def test_the_same_file_cannot_be_uploaded_twice(world, api):
    first = upload(api, world.pm, world.project, "xer")
    again = upload(api, world.pm, world.project, "xer")
    assert again.status_code == 409 and again.json()["error"]["code"] == "DUPLICATE_UPLOAD"
    assert again.json()["error"]["details"]["import_id"] == first.json()["import_id"]
    assert counts()["schedule_imports"] == 1


def test_discarding_an_import_and_an_unlocked_version(world, api):
    iid = upload(api, world.pm, world.project, "csv").json()["import_id"]
    url = f"{P}/{world.project}"
    v = api.post(f"{url}/schedule-imports/{iid}/build", world.pm).json()
    assert api.delete(f"{url}/schedule-imports/{iid}", world.pm).status_code == 409           # built: discard the version instead
    assert api.delete(f"{url}/schedule-versions/{v['version_id']}", world.pm).status_code == 204
    t = counts()
    assert t["schedule_versions"] == 0 and t["baseline_activities"] == 0 and t["baseline_resources"] == 0 and t["schedule_wbs"] == 0
    assert t["activities"] == 0 and t["assignments"] == 0                                    # the discarded draft's identities go with it
    assert api.get(f"{url}/schedule-imports/{iid}", world.pm).json()["status"] == "PARSED"
    assert api.post(f"{url}/schedule-imports/{iid}/build", world.pm).status_code == 201     # can be rebuilt
    assert api.delete(f"{url}/schedule-imports/{iid}", world.pm).status_code == 409
    iid2 = upload(api, world.pm, world.project, "xer").json()["import_id"]
    assert api.delete(f"{url}/schedule-imports/{iid2}", world.pm).status_code == 204
    assert api.post(f"{url}/schedule-imports/{iid2}/build", world.pm).json()["error"]["code"] == "IMPORT_CLOSED"
    assert api.put(f"{url}/schedule-imports/{iid2}/decisions", world.pm, json={}).status_code == 409


def test_an_activated_version_is_history_and_cannot_be_discarded(world, api):
    _, vid = build_and_activate(api, world.pm, world.project, "msp")
    r = api.delete(f"{P}/{world.project}/schedule-versions/{vid}", world.pm)
    assert r.status_code == 409 and r.json()["error"]["code"] == "VERSION_LOCKED"
    assert api.post(f"{P}/{world.project}/schedule-versions/{vid}/activate", world.pm).json()["error"]["code"] == "ALREADY_ACTIVE"
    assert counts()["baseline_activities"] == 12


def test_only_a_built_version_can_be_activated_and_ids_are_project_scoped(world, api):
    iid = upload(api, world.pm, world.project, "csv").json()["import_id"]
    vid = api.post(f"{P}/{world.project}/schedule-imports/{iid}/build", world.pm).json()["version_id"]
    # the same ids under another project are simply not found / not allowed
    assert api.post(f"{P}/{world.project2}/schedule-versions/{vid}/activate", world.pm2).status_code == 404
    assert api.post(f"{P}/{world.project2}/schedule-imports/{iid}/build", world.pm2).status_code == 404
    assert api.get(f"{P}/{world.project2}/schedule-versions/{vid}", world.pm2).status_code == 404
    assert api.post(f"{P}/{world.project}/schedule-versions/00000000-0000-0000-0000-000000000000/activate", world.pm).status_code == 404


def test_the_audit_trail_records_the_whole_import(world, api):
    build_and_activate(api, world.pm, world.project, "xer")
    with connect() as c:
        acts = [r["action"] for r in c.execute("select action from audit_logs where project_id = %s order by log_id", (world.project,)).fetchall()]
        assert acts.index("SCHEDULE_IMPORT_STAGED") < acts.index("SCHEDULE_VERSION_BUILT") < acts.index("SCHEDULE_VERSION_ACTIVATED")
        assert audit.verify_chain(c)["valid"]
