import csv
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from backend.main import app as production_app
from backend.shared.actuals import get_approved_actual
from backend.shared.seed import (
    GOLDEN_CLAIM_RELPATH,
    SCANNED_DIARY_RELPATH,
    SCHEDULE_CSV_RELPATH,
    TEST_SITE_ENGINEER_ID,
    TEST_SUPERVISOR_ID,
    WORKSPACE_ROOT,
    XER_FILE_RELPATH,
    check_schedule_upload_endpoint,
    compute_file_hash,
    extract_xer_task_line,
    load_canonical_sample_data,
)


class SQLitePsycopgAdapter:
    """Lightweight DB adapter for in-memory testing."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def execute(self, query: str, params=None):
        clean_q = query.replace("FOR UPDATE", "")
        clean_q = re.sub(r"%s", "?", clean_q)
        cur = self.conn.cursor()
        if params is not None:
            cur.execute(clean_q, params)
        else:
            cur.execute(clean_q)
        return cur

    def commit(self):
        self.conn.commit()


def create_phase10_test_db() -> SQLitePsycopgAdapter:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Profiles table
    conn.execute(
        """
        CREATE TABLE profiles (
            id TEXT PRIMARY KEY,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('SITE_ENGINEER', 'SUPERVISOR')),
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    # Schedules table
    conn.execute(
        """
        CREATE TABLE schedules (
            schedule_id TEXT PRIMARY KEY,
            project_name TEXT NOT NULL,
            data_date TEXT,
            source_format TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    # Schedule activities table
    conn.execute(
        """
        CREATE TABLE schedule_activities (
            activity_id TEXT NOT NULL,
            schedule_id TEXT NOT NULL,
            activity_name TEXT NOT NULL,
            wbs_code TEXT,
            discipline TEXT NOT NULL,
            location TEXT NOT NULL,
            asset_tag TEXT,
            planned_start TEXT NOT NULL,
            planned_finish TEXT NOT NULL,
            planned_quantity REAL,
            uom TEXT,
            baseline_pct_complete REAL DEFAULT 0.0,
            PRIMARY KEY (schedule_id, activity_id)
        )
        """
    )
    # Schedule dependencies table
    conn.execute(
        """
        CREATE TABLE schedule_dependencies (
            dependency_id TEXT PRIMARY KEY,
            schedule_id TEXT NOT NULL,
            predecessor_activity_id TEXT NOT NULL,
            successor_activity_id TEXT NOT NULL,
            relationship_type TEXT DEFAULT 'FS'
        )
        """
    )
    # Source documents table
    conn.execute(
        """
        CREATE TABLE source_documents (
            document_id TEXT PRIMARY KEY,
            file_name TEXT NOT NULL,
            document_type TEXT,
            uploader_id TEXT,
            file_hash TEXT NOT NULL,
            uploaded_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    # Execution events table
    conn.execute(
        """
        CREATE TABLE execution_events (
            event_id TEXT PRIMARY KEY,
            document_id TEXT,
            schedule_id TEXT NOT NULL,
            event_date TEXT NOT NULL,
            raw_claim_text TEXT NOT NULL,
            input_channel TEXT NOT NULL,
            language_detected TEXT,
            reported_activity_id TEXT,
            matched_activity_id TEXT,
            discipline TEXT,
            action TEXT,
            event_type TEXT,
            claim_mode TEXT DEFAULT 'CUMULATIVE_PCT',
            asset_tag TEXT,
            location TEXT,
            claimed_quantity REAL,
            claimed_uom TEXT,
            claimed_pct REAL,
            delay_reason TEXT,
            supervisor_id TEXT,
            photo_path TEXT,
            status TEXT DEFAULT 'EXTRACTED',
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    # Source references table
    conn.execute(
        """
        CREATE TABLE source_references (
            reference_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            file_name TEXT,
            sheet_name TEXT,
            row_cell_ref TEXT,
            message_id TEXT,
            raw_snippet TEXT NOT NULL
        )
        """
    )
    # PRD v6 WBS split rows (read by the approved-actuals quantity recalculation)
    conn.execute(
        """
        CREATE TABLE claim_activity_splits (
            split_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            activity_id TEXT NOT NULL,
            split_basis TEXT,
            split_pct REAL NOT NULL
        )
        """
    )
    # Conflict records table
    conn.execute(
        """
        CREATE TABLE conflict_records (
            conflict_id TEXT PRIMARY KEY,
            schedule_id TEXT NOT NULL,
            activity_id TEXT NOT NULL,
            reporting_period TEXT NOT NULL,
            event_id_a TEXT NOT NULL,
            event_id_b TEXT NOT NULL,
            value_a REAL NOT NULL,
            value_b REAL NOT NULL,
            variance_pct REAL NOT NULL,
            status TEXT DEFAULT 'OPEN'
        )
        """
    )
    # Planner decisions table
    conn.execute(
        """
        CREATE TABLE planner_decisions (
            decision_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            selected_activity_id TEXT NOT NULL,
            action TEXT,
            approved_pct REAL,
            approved_qty REAL,
            planner_id TEXT NOT NULL,
            justification TEXT NOT NULL,
            decided_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    # Approved actuals table
    conn.execute(
        """
        CREATE TABLE approved_actuals (
            actual_id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            schedule_id TEXT NOT NULL,
            activity_id TEXT NOT NULL,
            actual_start TEXT,
            actual_finish TEXT,
            actual_pct_complete REAL,
            actual_quantity REAL,
            exported_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE (schedule_id, activity_id)
        )
        """
    )
    # Audit logs table
    conn.execute(
        """
        CREATE TABLE audit_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            action TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            before_state TEXT,
            after_state TEXT,
            payload_hash TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            current_hash TEXT NOT NULL,
            timestamp TEXT DEFAULT (datetime('now'))
        )
        """
    )

    return SQLitePsycopgAdapter(conn)


def create_mock_schedule_app() -> FastAPI:
    """
    Create a FastAPI app with a mock POST /api/v1/schedules endpoint for
    testing. Mirrors the real contract in routers/schedules.py: a JSON body
    (ScheduleCreateRequest: project_name, data_date, source_format,
    csv_content), not a multipart file upload.
    """
    test_app = FastAPI()

    schedules_router = APIRouter(prefix="/api/v1/schedules")

    class MockScheduleCreateRequest(BaseModel):
        project_name: str
        data_date: Optional[str] = None
        source_format: Optional[str] = "csv"
        csv_content: str

    @schedules_router.post("")
    def upload_schedule(request: MockScheduleCreateRequest):
        lines = request.csv_content.splitlines()
        return {
            "schedule_id": "SIH26122_NFU",
            "project_name": request.project_name,
            "activities_count": max(0, len(lines) - 1),
            "status": "ok",
        }

    test_app.include_router(schedules_router)
    return test_app


def create_app_without_schedule_endpoint() -> FastAPI:
    """A bare app that genuinely has no POST /api/v1/schedules -- used to
    test the seed loader's BLOCKED path. Unlike production_app (which has
    M1's real schedule router since it merged), this app has nothing."""
    return FastAPI()


# =========================================================================
# Phase 10 Verification Tests
# =========================================================================

def test_seed_blocked_when_schedule_endpoint_absent():
    """Test #1: Loader returns structured BLOCKED status when POST /api/v1/schedules is absent."""
    db = create_phase10_test_db()
    # production_app has M1's real schedule router now (it merged) -- use a
    # genuinely bare app to exercise the "endpoint absent" path instead.
    absent_app = create_app_without_schedule_endpoint()
    assert not check_schedule_upload_endpoint(absent_app)

    result = load_canonical_sample_data(conn=db, app=absent_app)
    assert result["status"] == "BLOCKED"
    assert result["schedule_activities_seeded"] == 0
    assert "POST /api/v1/schedules" in result["reason"]

    # Verify no schedule activities were directly inserted
    row_count = db.execute("SELECT COUNT(*) as cnt FROM schedule_activities").fetchone()["cnt"]
    assert row_count == 0


def test_seed_invokes_schedule_endpoint_when_present():
    """Test #2: Loader invokes real endpoint when POST /api/v1/schedules is registered."""
    db = create_phase10_test_db()
    mock_app = create_mock_schedule_app()
    assert check_schedule_upload_endpoint(mock_app)

    result = load_canonical_sample_data(conn=db, app=mock_app, is_test=True)
    assert result["status"] == "SUCCESS"
    assert result["schedule_id"] == "SIH26122_NFU"
    assert result["events_seeded"] > 0
    assert result["approved_actuals_created"] > 0


def test_all_six_disciplines_represented():
    """Test #3: Canonical dataset strictly covers all six required disciplines."""
    schedule_csv = WORKSPACE_ROOT / SCHEDULE_CSV_RELPATH
    assert schedule_csv.exists()

    with open(schedule_csv, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 45
    discipline_counts = {}
    for r in rows:
        d = r["Discipline"]
        discipline_counts[d] = discipline_counts.get(d, 0) + 1

    # schedule.csv uses the parser's real expected column headers/enum
    # spelling (see backend/shared/schedule.py's field-mapping table) --
    # values are e.g. "CIVIL", not "Civil".
    expected_counts = {
        "CIVIL": 8,
        "PIPING": 10,
        "STATIC_ROTATING_EQUIPMENT": 7,
        "ELECTRICAL": 8,
        "INSTRUMENTATION": 7,
        "HSE": 5,
    }
    assert discipline_counts == expected_counts


def test_canonical_activity_ids_preserved():
    """Test #4: Canonical activity IDs are preserved as exact source strings."""
    schedule_csv = WORKSPACE_ROOT / SCHEDULE_CSV_RELPATH
    with open(schedule_csv, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # schedule.csv's activity-id column is named "L6 Task ID" (the real P6
    # source column M1's parser expects) -- see the field-mapping table.
    activity_ids = [r["L6 Task ID"] for r in rows]
    # Key canonical activity IDs from requirements
    assert "CIV-PS3-TR-0180" in activity_ids
    assert "PIP-PS3-WLD-024" in activity_ids
    assert "MECH-PS3-DWP-003" in activity_ids
    assert "ELE-PS3-CBL-001" in activity_ids
    assert "INS-PS3-JB-001" in activity_ids
    assert "HSE-PS3-IND-001" in activity_ids


def test_scanned_diary_evidence_links():
    """Test #5: Scanned diary image exists with verified hash and contains NO fabricated bbox."""
    diary_path = WORKSPACE_ROOT / SCANNED_DIARY_RELPATH
    assert diary_path.exists()
    assert diary_path.stat().st_size == 2303659
    assert compute_file_hash(diary_path) == "df86df578a20c5fc8eeaa0038610b5f723771f79461196cd76cf6a929954dc96"

    db = create_phase10_test_db()
    mock_app = create_mock_schedule_app()
    load_canonical_sample_data(conn=db, app=mock_app, is_test=True)

    ref = db.execute(
        "SELECT * FROM source_references WHERE file_name = 'site_diary_2026-08-14.png'"
    ).fetchone()
    assert ref is not None
    assert ref["event_id"] == "EVT-20260814-002"
    assert "CIV-PS3-TR-0180" in ref["raw_snippet"]
    # Verify no fabricated bounding box or coordinate reference
    assert ref["row_cell_ref"] is None


def test_xer_evidence_links_verbatim(monkeypatch):
    """Test #6: P6 XER export exists with verified hash, verbatim line is extracted from actual file, and loader fails explicitly without fabricating snippet if task is missing."""
    xer_path = WORKSPACE_ROOT / XER_FILE_RELPATH
    assert xer_path.exists()
    assert xer_path.stat().st_size == 6428
    assert compute_file_hash(xer_path) == "accdc5b02a48524790efb3e2bf2ef71d8f4792b94b2561a66066e2187dfd9841"

    expected_verbatim_line = extract_xer_task_line(xer_path, "PIP-PS3-WLD-024")
    assert expected_verbatim_line != ""
    assert "PIP-PS3-WLD-024" in expected_verbatim_line
    assert "%R\t2009\t1\t102" in expected_verbatim_line

    db = create_phase10_test_db()
    mock_app = create_mock_schedule_app()
    load_canonical_sample_data(conn=db, app=mock_app, is_test=True)

    ref = db.execute(
        "SELECT * FROM source_references WHERE file_name = 'sih26122_schedule.xer'"
    ).fetchone()
    assert ref is not None
    assert ref["event_id"] == "EVT-20260815-001"
    # Snippet matches exact line extracted directly from the actual XER file
    assert ref["raw_snippet"] == expected_verbatim_line

    # Failure verification: prove loader raises ValueError and does NOT fabricate an XER snippet when task is absent
    import backend.shared.seed as seed_mod
    monkeypatch.setattr(seed_mod, "extract_xer_task_line", lambda *args: "")
    db_fail = create_phase10_test_db()
    with pytest.raises(ValueError, match="Required XER task PIP-PS3-WLD-024 was not found"):
        load_canonical_sample_data(conn=db_fail, app=mock_app, is_test=True)


def test_incremental_quantity_claims_flow():
    """Test #7: Incremental quantity claims (50 + 30) yield 80 via authoritative upsert calculation."""
    db = create_phase10_test_db()
    mock_app = create_mock_schedule_app()
    load_canonical_sample_data(conn=db, app=mock_app, is_test=True)

    # Inspect approved actuals row for PIP-PS3-WLD-024
    row = db.execute(
        "SELECT * FROM approved_actuals WHERE schedule_id = 'SIH26122_NFU' AND activity_id = 'PIP-PS3-WLD-024'"
    ).fetchone()
    assert row is not None
    # 50.0 + 30.0 recalculated dynamically by upsert_approved_actual()
    assert row["actual_quantity"] == 80.0


def test_conflicting_reports_remain_unapproved():
    """Test #8: Conflicting claims (CONF-001) remain FLAGGED/unapproved and never touch approved_actuals."""
    db = create_phase10_test_db()
    mock_app = create_mock_schedule_app()
    load_canonical_sample_data(conn=db, app=mock_app, is_test=True)

    # Verify conflict record exists with status FLAGGED
    conflict = db.execute("SELECT * FROM conflict_records WHERE conflict_id = 'CONF-001'").fetchone()
    assert conflict is not None
    assert conflict["activity_id"] == "MECH-PS3-DWP-003"
    assert conflict["value_a"] == 75.0
    assert conflict["value_b"] == 30.0
    assert conflict["status"] == "FLAGGED"

    # Verify both events are FLAGGED
    ev_a = db.execute("SELECT status FROM execution_events WHERE event_id = 'EVT-CONF-001A'").fetchone()
    ev_b = db.execute("SELECT status FROM execution_events WHERE event_id = 'EVT-CONF-001B'").fetchone()
    assert ev_a["status"] == "FLAGGED"
    assert ev_b["status"] == "FLAGGED"

    # Verify neither event is in planner_decisions
    dec_a = db.execute("SELECT * FROM planner_decisions WHERE event_id = 'EVT-CONF-001A'").fetchone()
    dec_b = db.execute("SELECT * FROM planner_decisions WHERE event_id = 'EVT-CONF-001B'").fetchone()
    assert dec_a is None
    assert dec_b is None

    # Verify MECH-PS3-DWP-003 has NO approved actual
    actual = db.execute(
        "SELECT * FROM approved_actuals WHERE activity_id = 'MECH-PS3-DWP-003'"
    ).fetchone()
    assert actual is None


def test_separate_approved_delay_reasons():
    """Test #9: Separate approved MATERIAL and WEATHER delay claims exist and aggregate properly."""
    db = create_phase10_test_db()
    mock_app = create_mock_schedule_app()
    load_canonical_sample_data(conn=db, app=mock_app, is_test=True)

    # Check approved actuals for ELE-PS3-CBL-001 (MATERIAL) and CIV-PS3-RD-001 (WEATHER)
    act_ele = db.execute(
        "SELECT * FROM approved_actuals WHERE activity_id = 'ELE-PS3-CBL-001'"
    ).fetchone()
    act_civ = db.execute(
        "SELECT * FROM approved_actuals WHERE activity_id = 'CIV-PS3-RD-001'"
    ).fetchone()
    assert act_ele is not None
    assert act_civ is not None

    # Verify Phase 4 delay reasons query pattern counts approved claims
    delay_rows = db.execute(
        """
        SELECT ee.delay_reason, COUNT(*) as cnt
        FROM execution_events ee
        JOIN planner_decisions pd ON pd.event_id = ee.event_id
        WHERE pd.action IN ('APPROVE', 'EDIT')
          AND ee.delay_reason IS NOT NULL
        GROUP BY ee.delay_reason
        """
    ).fetchall()

    delays = {r["delay_reason"]: r["cnt"] for r in delay_rows}
    assert delays.get("MATERIAL") == 1
    assert delays.get("WEATHER") == 1


def test_guaranteed_successful_claim():
    """Test #10: Golden claim EVT-20260814-001 produces an approved actual with 40% complete."""
    db = create_phase10_test_db()
    mock_app = create_mock_schedule_app()
    load_canonical_sample_data(conn=db, app=mock_app, is_test=True)

    actual = db.execute(
        "SELECT * FROM approved_actuals WHERE activity_id = 'PIP-PS3-WLD-024'"
    ).fetchone()
    assert actual is not None
    # 40% was approved from golden claim
    assert actual["actual_pct_complete"] == 40.0


def test_approved_actuals_never_directly_inserted():
    """Test #11: All approved actuals rows trace directly to valid planner_decisions and execution_events."""
    db = create_phase10_test_db()
    mock_app = create_mock_schedule_app()
    load_canonical_sample_data(conn=db, app=mock_app, is_test=True)

    actuals = db.execute("SELECT * FROM approved_actuals").fetchall()
    assert len(actuals) > 0

    for a in actuals:
        # Every approved actual must have a decision_id and event_id
        dec = db.execute(
            "SELECT * FROM planner_decisions WHERE decision_id = ?", (a["decision_id"],)
        ).fetchone()
        assert dec is not None

        ev = db.execute(
            "SELECT * FROM execution_events WHERE event_id = ?", (a["event_id"],)
        ).fetchone()
        assert ev is not None


def test_demo_profile_handling():
    """Test #12: Test profile identities exist in test mode, and live mode does NOT invent UUIDs when unset."""
    db = create_phase10_test_db()
    mock_app = create_mock_schedule_app()

    # 1. In test mode (is_test=True): documented test identities seeded
    load_canonical_sample_data(conn=db, app=mock_app, is_test=True)
    profiles = db.execute("SELECT * FROM profiles").fetchall()
    assert len(profiles) == 2

    roles = {p["id"]: p["role"] for p in profiles}
    assert roles.get(TEST_SITE_ENGINEER_ID) == "SITE_ENGINEER"
    assert roles.get(TEST_SUPERVISOR_ID) == "SUPERVISOR"

    # 2. In live production mode (is_test=False) without env vars: no synthetic UUIDs invented
    db2 = create_phase10_test_db()
    # Ensure env vars are unset
    old_eng = os.environ.pop("DEMO_SITE_ENGINEER_USER_ID", None)
    old_sup = os.environ.pop("DEMO_SUPERVISOR_USER_ID", None)
    try:
        res = load_canonical_sample_data(conn=db2, app=mock_app, is_test=False)
        assert res["profiles_seeded"] == 0
        profiles2 = db2.execute("SELECT * FROM profiles").fetchall()
        assert len(profiles2) == 0
    finally:
        if old_eng:
            os.environ["DEMO_SITE_ENGINEER_USER_ID"] = old_eng
        if old_sup:
            os.environ["DEMO_SUPERVISOR_USER_ID"] = old_sup


def test_seed_idempotency():
    """Test #13: Running seed loader twice produces identical row counts and state."""
    db = create_phase10_test_db()
    mock_app = create_mock_schedule_app()

    # First run
    res1 = load_canonical_sample_data(conn=db, app=mock_app, is_test=True)
    assert res1["status"] == "SUCCESS"
    doc_cnt1 = db.execute("SELECT COUNT(*) as c FROM source_documents").fetchone()["c"]
    ev_cnt1 = db.execute("SELECT COUNT(*) as c FROM execution_events").fetchone()["c"]
    dec_cnt1 = db.execute("SELECT COUNT(*) as c FROM planner_decisions").fetchone()["c"]
    act_cnt1 = db.execute("SELECT COUNT(*) as c FROM approved_actuals").fetchone()["c"]

    # Second run
    res2 = load_canonical_sample_data(conn=db, app=mock_app, is_test=True)
    assert res2["status"] == "SUCCESS"
    doc_cnt2 = db.execute("SELECT COUNT(*) as c FROM source_documents").fetchone()["c"]
    ev_cnt2 = db.execute("SELECT COUNT(*) as c FROM execution_events").fetchone()["c"]
    dec_cnt2 = db.execute("SELECT COUNT(*) as c FROM planner_decisions").fetchone()["c"]
    act_cnt2 = db.execute("SELECT COUNT(*) as c FROM approved_actuals").fetchone()["c"]

    assert doc_cnt1 == doc_cnt2
    assert ev_cnt1 == ev_cnt2
    assert dec_cnt1 == dec_cnt2
    assert act_cnt1 == act_cnt2


def test_startup_isolation():
    """Test #14: Importing backend.main does NOT trigger sample data seeding."""
    # production_app was imported at the top of the file. Verify that
    # importing it registers no startup event handlers -- i.e. simply
    # importing backend.main never runs the seed loader as a side effect,
    # regardless of whether the real schedule endpoint is present (it is,
    # now that M1's router has merged).
    assert check_schedule_upload_endpoint(production_app)
    assert len(getattr(production_app, "on_startup", [])) == 0


def test_phase1_9_regression_baseline(monkeypatch):
    """Test #15: Baseline functionality from Phases 1-9 remains completely unaffected."""
    client = TestClient(production_app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    monkeypatch.setenv("AUTH_DEV_MODE", "true")  # mock P6 exists only in dev mode
    resp_p6 = client.get("/api/v1/mock-p6/health")
    assert resp_p6.status_code == 200

    resp_sched = client.get("/api/v1/schedule/health")
    assert resp_sched.status_code == 200

    resp_exp = client.get("/api/v1/export/health")
    assert resp_exp.status_code == 200
