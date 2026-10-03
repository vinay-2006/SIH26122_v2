"""Batch upload: many files -> many claims -> matched, de-duplicated, checked; nothing approved."""
import pytest

from domainkit import connect

pytestmark = pytest.mark.db_write

REPORT_A = b"Daily report\nA2000 pipe stringing 40 percent complete\nA2010 welding mainline 10 percent complete\n"
REPORT_B = b"Site note\nA2010 welding mainline 10 percent complete\n"            # the same A2010 claim as report A, from a second file
CSV = b"Activity ID,Activity Name,Discipline,Progress Pct\nA1000,Survey and stake right of way,Civil,60\n"


def files(*items):
    return [("files", (n, d, "application/octet-stream")) for n, d in items]


def test_a_batch_reports_files_claims_matches_and_merges_duplicates(kit, lg):
    r = lg.post("/api/v1/claims/batch", kit.world.se, files=files(("report_a.txt", REPORT_A), ("report_b.txt", REPORT_B), ("progress.csv", CSV)))
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["file_count"] == 3 and b["status"] in ("COMPLETED", "PARTIAL")
    assert {f["file_name"]: f["extraction_status"] for f in b["files"]} == {"report_a.txt": "EXTRACTED", "report_b.txt": "EXTRACTED", "progress.csv": "EXTRACTED"}
    acts = {a["activity_id"]: a for a in b["activities"]}
    assert {"A1000", "A2000", "A2010"} <= set(acts)
    assert b["merged_count"] == 1 and len(acts["A2010"]["claim_ids"]) == 1                      # A2010 was reported twice: ONE claim, two sources
    a2010 = next(c for c in b["claims"] if c["matched_activity_id"] == "A2010")
    assert a2010["reported_by_multiple_files"] is True and sorted(a2010["file_names"]) == ["report_a.txt", "report_b.txt"]
    assert all(c["candidates"] for c in b["claims"] if c["matched_activity_id"])
    with connect() as c:
        assert c.execute("select count(*) n from execution_events where status = 'WITHDRAWN'").fetchone()["n"] == 1      # the duplicate is withdrawn (append-only), never deleted
        assert c.execute("select count(*) n from audit_logs where action = 'BATCH_INTAKE'").fetchone()["n"] == 1
    assert kit.count("planner_decisions") == 0 and kit.count("approved_resource_progress") == 0


def test_a_failing_file_never_fails_the_batch_and_is_reported(kit, lg):
    r = lg.post("/api/v1/claims/batch", kit.world.se, files=files(("good.csv", CSV), ("bad.xlsx", b"this is not a spreadsheet"), ("empty.txt", b"")))
    assert r.status_code == 200, r.text
    st = {f["file_name"]: f["extraction_status"] for f in r.json()["files"]}
    assert st["good.csv"] == "EXTRACTED" and st.get("bad.xlsx") == "FAILED"
    assert r.json()["claim_count"] == 1


def test_batches_are_listed_for_their_owner_only(kit, lg):
    b = lg.post("/api/v1/claims/batch", kit.world.se, files=files(("progress.csv", CSV))).json()
    mine = lg.get(f"/api/v1/projects/{kit.project}/upload-batches?mine=true", kit.world.se).json()
    assert [x["batch_id"] for x in mine] == [b["batch_id"]]
    assert lg.get(f"/api/v1/projects/{kit.project}/upload-batches", kit.world.se2).json() == []
    assert lg.get(f"/api/v1/projects/{kit.project}/upload-batches/{b['batch_id']}", kit.world.se2).status_code == 404
    assert lg.get(f"/api/v1/projects/{kit.project}/upload-batches/{b['batch_id']}", kit.world.se).status_code == 200
    assert lg.post("/api/v1/claims/batch", kit.world.sup, files=files(("progress.csv", CSV))).status_code == 403
    assert lg.post("/api/v1/claims/batch", kit.world.pm, files=files(("progress.csv", CSV))).status_code == 403
    assert lg.post("/api/v1/claims/batch", kit.world.se, files=[]).status_code in (400, 422)
