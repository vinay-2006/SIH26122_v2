"""
Phase 3 Tests — Outputs & Operational Analytics: Live Dashboard & Activity History.

Covers:
- DASH-01: Total Claims is database-derived
- DASH-02: Pending Review is database-derived
- DASH-03: Approved Actuals count is database-derived
- DASH-04: Conflict count is database-derived
- DASH-05: Discipline breakdown is live
- DASH-06: Empty/zero-data behavior
- HIST-01: Multiple historical records are returned
- HIST-02: History is chronologically ordered
- HIST-03: Execution events are included
- HIST-04: Decisions are included
- HIST-05: Approved actual is included
- HIST-06: Source/audit context is preserved
"""

import sqlite3
from typing import Any, List, Optional
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.routers.activities import query_activity_history
from backend.routers.dashboard import query_dashboard_summary
from backend.shared.auth import UserProfile, get_current_user


class SQLitePsycopgAdapter:
    """In-memory SQLite adapter translating %s to ? for testing."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def execute(self, query: str, params=None):
        clean_q = query.replace("%s", "?")
        cur = self.conn.cursor()
        if params is not None:
            cur.execute(clean_q, params)
        else:
            cur.execute(clean_q)
        return cur

    def commit(self):
        self.conn.commit()


def create_phase3_test_db() -> SQLitePsycopgAdapter:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row

    conn.execute(
        """
        CREATE TABLE execution_events (
            event_id TEXT PRIMARY KEY,
            document_id TEXT,
            schedule_id TEXT NOT NULL,
            event_date TEXT,
            raw_claim_text TEXT NOT NULL,
            input_channel TEXT DEFAULT 'TYPED_TEXT',
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

    conn.execute(
        """
        CREATE TABLE planner_decisions (
            decision_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            selected_activity_id TEXT NOT NULL,
            action TEXT,
            approved_pct REAL,
            approved_qty REAL,
            planner_id TEXT,
            justification TEXT,
            decided_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
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
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE conflict_records (
            conflict_id TEXT PRIMARY KEY,
            schedule_id TEXT NOT NULL,
            activity_id TEXT NOT NULL,
            reporting_period TEXT,
            event_id_a TEXT,
            event_id_b TEXT,
            value_a REAL,
            value_b REAL,
            variance_pct REAL,
            status TEXT DEFAULT 'OPEN'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE schedule_activities (
            activity_id TEXT NOT NULL,
            schedule_id TEXT NOT NULL,
            activity_name TEXT NOT NULL,
            discipline TEXT NOT NULL,
            location TEXT NOT NULL,
            planned_start TEXT NOT NULL,
            planned_finish TEXT,
            total_float REAL,
            is_critical BOOLEAN,
            PRIMARY KEY (schedule_id, activity_id)
        )
        """
    )
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
    return SQLitePsycopgAdapter(conn)


def _seed_activity(db: SQLitePsycopgAdapter, activity_id: str, schedule_id: str = "SCH-1") -> None:
    """query_activity_history now resolves activity metadata from
    schedule_activities first (ISS-23) -- every HIST test activity must
    exist there, or the lookup correctly 404s."""
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start)
        VALUES (?, ?, ?, 'CIVIL', 'Test Zone', '2026-08-01')
        """,
        (activity_id, schedule_id, f"Test Activity {activity_id}"),
    )


# ==============================================================================
# DASH-01: Total Claims is Database-Derived
# ==============================================================================

def test_dash_01_total_claims_is_database_derived():
    db = create_phase3_test_db()
    for i in range(5):
        db.execute(
            "INSERT INTO execution_events (event_id, schedule_id, raw_claim_text) VALUES (?, ?, ?)",
            (f"EV-{i}", "SCH-1", f"Claim {i}"),
        )

    summary = query_dashboard_summary(conn=db)
    assert summary["total_claims"] == 5


# ==============================================================================
# DASH-02: Pending Review is Database-Derived
# ==============================================================================

def test_dash_02_pending_review_is_database_derived():
    db = create_phase3_test_db()
    # 2 pending review (VALIDATED, REVIEW_REQUIRED)
    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, raw_claim_text, status) VALUES (?, ?, ?, ?)",
        ("EV-VAL", "SCH-1", "Claim Validated", "VALIDATED"),
    )
    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, raw_claim_text, status) VALUES (?, ?, ?, ?)",
        ("EV-REV", "SCH-1", "Claim Review Required", "REVIEW_REQUIRED"),
    )
    # 1 on hold (pending supervisor re-decision)
    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, raw_claim_text, status) VALUES (?, ?, ?, ?)",
        ("EV-HOLD", "SCH-1", "Claim On Hold", "HOLD"),
    )
    # 1 newly extracted (unreviewed)
    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, raw_claim_text, status) VALUES (?, ?, ?, ?)",
        ("EV-EXT", "SCH-1", "Claim Extracted", "EXTRACTED"),
    )
    # 2 already decided (APPROVED, REJECTED)
    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, raw_claim_text, status) VALUES (?, ?, ?, ?)",
        ("EV-APP", "SCH-1", "Claim Approved", "APPROVED"),
    )
    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, raw_claim_text, status) VALUES (?, ?, ?, ?)",
        ("EV-REJ", "SCH-1", "Claim Rejected", "REJECTED"),
    )

    summary = query_dashboard_summary(conn=db)
    # 4 pending (VAL, REV, HOLD, EXT), 2 decided (APP, REJ)
    assert summary["pending_review"] == 4
    assert summary["total_claims"] == 6


# ==============================================================================
# DASH-03: Approved Actuals Count is Database-Derived
# ==============================================================================

def test_dash_03_actuals_count_is_database_derived():
    db = create_phase3_test_db()
    # Raw claims in execution_events do NOT count as actuals
    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, raw_claim_text) VALUES (?, ?, ?)",
        ("EV-RAW", "SCH-1", "Raw Claim"),
    )

    # 3 authoritative records in approved_actuals
    for i in range(3):
        db.execute(
            """
            INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_pct_complete)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (f"ACT-{i}", f"DEC-{i}", f"EV-{i}", "SCH-1", f"A100{i}", 100.0),
        )

    summary = query_dashboard_summary(conn=db)
    assert summary["actuals"] == 3


# ==============================================================================
# DASH-04: Conflict Count is Database-Derived
# ==============================================================================

def test_dash_04_conflict_count_is_database_derived():
    db = create_phase3_test_db()
    # 2 open conflicts
    db.execute(
        "INSERT INTO conflict_records (conflict_id, schedule_id, activity_id, status) VALUES (?, ?, ?, ?)",
        ("CONF-1", "SCH-1", "A1000", "OPEN"),
    )
    db.execute(
        "INSERT INTO conflict_records (conflict_id, schedule_id, activity_id, status) VALUES (?, ?, ?, ?)",
        ("CONF-2", "SCH-1", "A1001", "OPEN"),
    )
    # 1 resolved conflict
    db.execute(
        "INSERT INTO conflict_records (conflict_id, schedule_id, activity_id, status) VALUES (?, ?, ?, ?)",
        ("CONF-3", "SCH-1", "A1002", "RESOLVED"),
    )

    summary = query_dashboard_summary(conn=db)
    assert summary["conflicts"] == 2


# ==============================================================================
# DASH-05: Discipline Breakdown is Live
# ==============================================================================

def test_dash_05_discipline_breakdown_is_live():
    db = create_phase3_test_db()
    # 3 CIVIL, 2 PIPING, 1 ELECTRICAL
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start)
        VALUES ('A1', 'SCH-1', 'Excavation', 'CIVIL', 'Site A', '2026-08-01'),
               ('A2', 'SCH-1', 'Foundation', 'CIVIL', 'Site A', '2026-08-05'),
               ('A3', 'SCH-1', 'Grading', 'CIVIL', 'Site A', '2026-08-10'),
               ('A4', 'SCH-1', 'Welding', 'PIPING', 'Site B', '2026-08-01'),
               ('A5', 'SCH-1', 'Spool Fit', 'PIPING', 'Site B', '2026-08-05'),
               ('A6', 'SCH-1', 'Cabling', 'ELECTRICAL', 'Site C', '2026-08-01')
        """
    )

    summary = query_dashboard_summary(conn=db)
    breakdown = {d["name"]: d["count"] for d in summary["discipline_breakdown"]}

    assert breakdown["CIVIL"] == 3
    assert breakdown["PIPING"] == 2
    assert breakdown["ELECTRICAL"] == 1


# ==============================================================================
# DASH-06: Empty / Zero-Data Behavior
# ==============================================================================

def test_dash_06_empty_database_safety():
    db = create_phase3_test_db()
    summary = query_dashboard_summary(conn=db)

    assert summary["total_claims"] == 0
    assert summary["pending_review"] == 0
    assert summary["actuals"] == 0
    assert summary["conflicts"] == 0
    assert summary["discipline_breakdown"] == []


# ==============================================================================
# HIST-01: Multiple Historical Records are Returned
# ==============================================================================

def test_hist_01_multiple_historical_records_returned():
    db = create_phase3_test_db()
    _seed_activity(db, "ACT-M")
    # 2 execution events for ACT-M
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, raw_claim_text, matched_activity_id, event_date, created_at)
        VALUES ('EV-1', 'SCH-1', 'First claim', 'ACT-M', '2026-08-01', '2026-08-01 09:00:00'),
               ('EV-2', 'SCH-1', 'Second claim', 'ACT-M', '2026-08-03', '2026-08-03 09:00:00')
        """
    )
    # 2 planner decisions
    db.execute(
        """
        INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, decided_at)
        VALUES ('DEC-1', 'EV-1', 'ACT-M', 'HOLD', '2026-08-01 11:00:00'),
               ('DEC-2', 'EV-2', 'ACT-M', 'APPROVE', '2026-08-03 12:00:00')
        """
    )

    data = query_activity_history("ACT-M", schedule_id="SCH-1", conn=db)
    assert data["activity_id"] == "ACT-M"
    assert len(data["timeline"]) == 4

    types = [item["type"] for item in data["timeline"]]
    assert types.count("execution_event") == 2
    assert types.count("planner_decision") == 2


# ==============================================================================
# HIST-02: History is Chronologically Ordered
# ==============================================================================

def test_hist_02_chronological_ordering():
    db = create_phase3_test_db()
    _seed_activity(db, "ACT-ORDER")
    # Insert out of order
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, raw_claim_text, matched_activity_id, created_at)
        VALUES ('EV-LATE', 'SCH-1', 'Late claim', 'ACT-ORDER', '2026-08-05 10:00:00'),
               ('EV-EARLY', 'SCH-1', 'Early claim', 'ACT-ORDER', '2026-08-01 10:00:00')
        """
    )
    db.execute(
        """
        INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, decided_at)
        VALUES ('DEC-MID', 'EV-EARLY', 'ACT-ORDER', 'APPROVE', '2026-08-02 14:00:00')
        """
    )

    data = query_activity_history("ACT-ORDER", schedule_id="SCH-1", conn=db)
    timeline = data["timeline"]
    assert len(timeline) == 3

    # Must be ordered strictly by timestamp ASC
    timestamps = [item["timestamp"] for item in timeline]
    assert timestamps == sorted(timestamps)
    assert timeline[0]["event_id"] == "EV-EARLY"
    assert timeline[1]["decision_id"] == "DEC-MID"
    assert timeline[2]["event_id"] == "EV-LATE"


# ==============================================================================
# HIST-03: Execution Events are Included
# ==============================================================================

def test_hist_03_execution_events_included():
    db = create_phase3_test_db()
    _seed_activity(db, "ACT-DETAIL")
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, raw_claim_text, matched_activity_id, claimed_pct, delay_reason, created_at)
        VALUES ('EV-DETAILED', 'SCH-1', 'Foundation 50% done', 'ACT-DETAIL', 50.0, 'WEATHER', '2026-08-02 08:30:00')
        """
    )

    data = query_activity_history("ACT-DETAIL", schedule_id="SCH-1", conn=db)
    assert len(data["timeline"]) == 1
    ev = data["timeline"][0]

    assert ev["type"] == "execution_event"
    assert ev["event_id"] == "EV-DETAILED"
    assert ev["claimed_pct"] == 50.0
    assert ev["delay_reason"] == "WEATHER"
    assert ev["raw_claim_text"] == "Foundation 50% done"


# ==============================================================================
# HIST-04: Decisions are Included
# ==============================================================================

def test_hist_04_decisions_included():
    db = create_phase3_test_db()
    _seed_activity(db, "ACT-DEC")
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, raw_claim_text, matched_activity_id, created_at)
        VALUES ('EV-1', 'SCH-1', 'Initial claim', 'ACT-DEC', '2026-08-01 08:00:00')
        """
    )
    # 2 decisions for this activity
    db.execute(
        """
        INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, approved_pct, planner_id, justification, decided_at)
        VALUES ('DEC-HOLD', 'EV-1', 'ACT-DEC', 'HOLD', 0.0, 'P-01', 'Needs verification', '2026-08-01 10:00:00'),
               ('DEC-APP', 'EV-1', 'ACT-DEC', 'APPROVE', 100.0, 'P-01', 'Verified and approved', '2026-08-01 16:00:00')
        """
    )

    data = query_activity_history("ACT-DEC", schedule_id="SCH-1", conn=db)
    decisions = [t for t in data["timeline"] if t["type"] == "planner_decision"]

    assert len(decisions) == 2
    assert decisions[0]["decision_id"] == "DEC-HOLD"
    assert decisions[0]["action"] == "HOLD"
    assert decisions[0]["justification"] == "Needs verification"

    assert decisions[1]["decision_id"] == "DEC-APP"
    assert decisions[1]["action"] == "APPROVE"
    assert decisions[1]["justification"] == "Verified and approved"


# ==============================================================================
# HIST-05: Approved Actual is Included
# ==============================================================================

def test_hist_05_approved_actual_included():
    db = create_phase3_test_db()
    _seed_activity(db, "ACT-ACTUAL")
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, raw_claim_text, matched_activity_id, created_at)
        VALUES ('EV-ACT', 'SCH-1', 'Paving claim', 'ACT-ACTUAL', '2026-08-01 08:00:00')
        """
    )
    db.execute(
        """
        INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, decided_at)
        VALUES ('DEC-ACT', 'EV-ACT', 'ACT-ACTUAL', 'APPROVE', '2026-08-01 10:00:00')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish, actual_pct_complete, created_at)
        VALUES ('AA-1', 'DEC-ACT', 'EV-ACT', 'SCH-1', 'ACT-ACTUAL', '2026-08-01', '2026-08-05', 100.0, '2026-08-01 11:00:00')
        """
    )

    data = query_activity_history("ACT-ACTUAL", schedule_id="SCH-1", conn=db)
    actuals = [t for t in data["timeline"] if t["type"] == "approved_actual"]

    assert len(actuals) == 1
    aa = actuals[0]
    assert aa["actual_id"] == "AA-1"
    assert aa["actual_start"] == "2026-08-01"
    assert aa["actual_finish"] == "2026-08-05"
    assert aa["actual_pct_complete"] == 100.0


# ==============================================================================
# HIST-06: Source / Audit Context is Preserved
# ==============================================================================

def test_hist_06_source_references_preserved():
    db = create_phase3_test_db()
    _seed_activity(db, "ACT-SRC")
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, raw_claim_text, matched_activity_id, created_at)
        VALUES ('EV-SRC', 'SCH-1', 'Welding joint 10', 'ACT-SRC', '2026-08-01 09:00:00')
        """
    )
    db.execute(
        """
        INSERT INTO source_references (reference_id, event_id, file_name, sheet_name, row_cell_ref, raw_snippet)
        VALUES ('REF-1', 'EV-SRC', 'welding_report.xlsx', 'Daily Log', 'B14', 'Joint 10 welded and inspected')
        """
    )

    data = query_activity_history("ACT-SRC", schedule_id="SCH-1", conn=db)
    ev = data["timeline"][0]

    assert len(ev["source_references"]) == 1
    ref = ev["source_references"][0]
    assert ref["reference_id"] == "REF-1"
    assert ref["file_name"] == "welding_report.xlsx"
    assert ref["sheet_name"] == "Daily Log"
    assert ref["row_cell_ref"] == "B14"
    assert ref["raw_snippet"] == "Joint 10 welded and inspected"


# ==============================================================================
# Endpoint Integration & RBAC
# ==============================================================================

def test_dashboard_summary_endpoint_rbac():
    from tests.v7ctx import act_as

    # Anonymous -> 401
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/dashboard/summary")
    assert resp.status_code == 401

    # Site Engineer (no REVIEW_CLAIM) -> 403
    with act_as("SITE_ENGINEER", schedule_id="SCH-1"):
        assert client.get("/api/v1/dashboard/summary").status_code == 403

    # Supervisor on an explicit schedule -> 200
    with act_as("SUPERVISOR", schedule_id="SCH-1"):
        resp_sup = client.get("/api/v1/dashboard/summary")
    assert resp_sup.status_code == 200
    body = resp_sup.json()
    assert "total_claims" in body
    assert "pending_review" in body
    assert "actuals" in body
    assert "conflicts" in body
    assert "discipline_breakdown" in body
