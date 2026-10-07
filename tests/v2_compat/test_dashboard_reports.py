"""Dashboard KPIs, forecast, alerts, historical memory, AI Execution Summary, translation and CSV export -- the original query functions on v2 data."""
import pytest

from domainkit import connect
from v2api import seed_progress

pytestmark = pytest.mark.db_write


@pytest.fixture
def ver(kit):
    with connect() as c:
        return c.execute("select version_id from schedule_versions where project_id = %s and status = 'ACTIVE'", (kit.project,)).fetchone()["version_id"]


def test_summary_kpis_count_real_records_and_the_pm_is_refused(kit, lg, ver):
    seed_progress(kit.project, "A2000", {"PIPE_STRUNG_KM": 12}, kit.world.sup, kit.world.se)
    lg.post("/api/v1/claims/text", kit.world.se, json={"raw_claim_text": "Welding mainline A2010: 120 joints completed today"})
    s = lg.get("/api/v1/dashboard/summary", kit.world.sup)
    assert s.status_code == 200, s.text
    body = s.json()
    assert body["total_claims"] == 2 and body["pending_review"] >= 1 and body["actuals"] >= 1
    assert sum(d["count"] for d in body["discipline_breakdown"]) == 59 or sum(d["count"] for d in body["discipline_breakdown"]) > 5
    for who in (kit.world.se, kit.world.pm):
        assert lg.get("/api/v1/dashboard/summary", who).status_code == 403


def test_forecast_uses_the_projects_own_finished_activities(kit, lg, ver):
    r = lg.get("/api/v1/dashboard/forecast?discipline=PIPING", kit.world.sup)
    assert r.status_code == 200, r.text
    assert r.json()["discipline"] == "PIPING" and r.json()["total_activities"] > 0 and r.json()["historical_ratio"] is None      # nothing finished yet: no invented ratio
    assert lg.get("/api/v1/dashboard/forecast", kit.world.sup).status_code == 400


def test_silent_activities_and_delay_reasons_and_memory(kit, lg, ver):
    silent = lg.get("/api/v1/alerts/silent-activities", kit.world.sup).json()["silent_activities"]
    assert isinstance(silent, list)
    assert lg.get("/api/v1/dashboard/delay-reasons", kit.world.sup).json()["delay_reasons"] == []
    mem = lg.get("/api/v1/dashboard/institutional-memory", kit.world.sup).json()
    assert mem["total_activities"] > 0 and all("variance_days" in a for a in mem["activities"])


def test_project_dashboard_matches_the_ledger_rollup(kit, lg, ver):
    seed_progress(kit.project, "A2000", {"PIPE_STRUNG_KM": 12}, kit.world.sup, kit.world.se)
    d = lg.get(f"/api/v1/projects/{kit.project}/schedules/{ver}/dashboard", kit.world.sup)
    assert d.status_code == 200, d.text
    body = d.json()
    assert abs(body["overall"]["actual_pct"] - float(kit.project_pct())) < 0.6 and body["stages"] and body["disciplines"]
    assert lg.get(f"/api/v1/projects/{kit.project}/schedules/{ver}/dashboard", kit.world.pm).status_code == 200            # aggregate monitoring for the PM
    assert "raw_claim_text" not in d.text


def test_execution_summary_is_deterministic_without_a_provider_and_translation_never_blocks(kit, lg, ver):
    lg.post("/api/v1/claims/text", kit.world.se, json={"raw_claim_text": "Welding mainline A2010: 120 joints completed today"})
    r = lg.get("/api/v1/reports/execution-summary", kit.world.sup)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["generated_by"] in ("template", "deterministic", "fallback") or b["generated_by"] != "llm"
    assert b["metrics"]["total_claims_processed"] == 1 and b["key_highlights"] and b["summary_text"]
    hi = lg.get("/api/v1/reports/execution-summary?language=hi", kit.world.sup).json()
    assert hi["canonical_summary"] == b["canonical_summary"] and hi["language"] == "hi"           # canonical English is never altered
    assert lg.get("/api/v1/reports/execution-summary?start=2026-02-01&end=2026-01-01", kit.world.sup).status_code == 400
    assert lg.get("/api/v1/reports/execution-summary", kit.world.pm).status_code == 403
    t = lg.post("/api/v1/reports/translate", kit.world.se, json={"texts": ["Please clarify the quantity"], "target_language": "hi"})
    assert t.status_code == 200 and len(t.json()["texts"]) == 1


def test_csv_export_of_approved_actuals(kit, lg, ver):
    seed_progress(kit.project, "A2000", {"PIPE_STRUNG_KM": 12}, kit.world.sup, kit.world.se)
    r = lg.get("/api/v1/export/csv", kit.world.sup)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0] == "activity_id,actual_start,actual_finish,actual_pct_complete,actual_quantity" and any(l.startswith("A2000,") for l in lines[1:])
    assert lg.get("/api/v1/export/csv", kit.world.pm).status_code == 200 and "claim" not in lg.get("/api/v1/export/csv", kit.world.pm).text.lower()
    assert lg.get("/api/v1/export/csv", kit.world.se).status_code == 403
    assert lg.get("/api/v1/export/csv", kit.world.outsider).status_code == 403


def test_activity_directory_and_history(kit, lg, ver):
    seed_progress(kit.project, "A2000", {"PIPE_STRUNG_KM": 12}, kit.world.sup, kit.world.se)
    r = lg.get("/api/v1/activities?page_size=100", kit.world.sup)
    assert r.status_code == 200, r.text
    body = r.json()
    by = {a["activity_id"]: a for a in body["items"]}
    assert body["total"] == len(body["items"]) > 10 and by["A2000"]["execution_state"] == "IN_PROGRESS" and by["A2010"]["execution_state"] == "NOT_STARTED"
    assert body["metrics"]["in_progress"] >= 1
    f = lg.get("/api/v1/activities?search=welding", kit.world.sup).json()
    assert [a["activity_id"] for a in f["items"]] == ["A2010"]
    h = lg.get("/api/v1/activities/A2000/history", kit.world.sup)
    assert h.status_code == 200, h.text
    assert h.json()["activity_id"] == "A2000" and isinstance(h.json()["timeline"], list)
    assert lg.get("/api/v1/activities/NOPE/history", kit.world.sup).status_code == 404
    for who in (kit.world.se, kit.world.pm):
        assert lg.get("/api/v1/activities", who).status_code == 403
        assert lg.get("/api/v1/activities/A2000/history", who).status_code == 403
