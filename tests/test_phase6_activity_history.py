import sqlite3
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.main import app
from tests.v7ctx import act_as
from backend.routers.activities import query_activity_history
from backend.shared.auth import UserProfile, get_current_user


class SQLitePsycopgAdapter:
    """
    Lightweight DB connection adapter for deterministic in-memory testing.
    Translates Postgres query patterns (%s) to SQLite (?).
    """

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


def create_test_db() -> SQLitePsycopgAdapter:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

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
            created_at TEXT
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
            decided_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE schedule_activities (
            activity_id TEXT NOT NULL,
            schedule_id TEXT NOT NULL,
            activity_name TEXT NOT NULL,
            wbs_code TEXT,
            discipline TEXT,
            location TEXT,
            planned_start TEXT,
            planned_finish TEXT,
            PRIMARY KEY (schedule_id, activity_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE approved_actuals (
            actual_id TEXT PRIMARY KEY,
            decision_id TEXT,
            event_id TEXT,
            schedule_id TEXT NOT NULL,
            activity_id TEXT NOT NULL,
            actual_start TEXT,
            actual_finish TEXT,
            actual_pct_complete REAL,
            actual_quantity REAL,
            created_at TEXT
        )
        """
    )
    return SQLitePsycopgAdapter(conn)


def _seed_activity(db: SQLitePsycopgAdapter, activity_id: str, schedule_id: str = "SCH-1", **overrides: Any) -> None:
    """Every test activity must exist in schedule_activities (ISS-23) --
    this seeds a minimal real row so 'exists, zero events' tests are
    distinguishable from the genuinely-unknown-activity 404 case."""
    fields = {
        "activity_name": overrides.get("activity_name", f"Test Activity {activity_id}"),
        "wbs_code": overrides.get("wbs_code"),
        "discipline": overrides.get("discipline", "CIVIL"),
        "location": overrides.get("location", "Test Zone"),
        "planned_start": overrides.get("planned_start", "2026-08-01"),
        "planned_finish": overrides.get("planned_finish", "2026-08-31"),
    }
    db.execute(
        """
        INSERT INTO schedule_activities
            (activity_id, schedule_id, activity_name, wbs_code, discipline, location, planned_start, planned_finish)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            activity_id, schedule_id, fields["activity_name"], fields["wbs_code"],
            fields["discipline"], fields["location"], fields["planned_start"], fields["planned_finish"],
        ),
    )


# =========================================================================
# Test 1 — Endpoint Registration
# =========================================================================

def test_endpoint_is_registered():
    routes = list(app.openapi()["paths"].keys())
    assert "/api/v1/activities/{activity_id}/history" in routes, (
        "GET /api/v1/activities/{activity_id}/history not registered on app"
    )


# =========================================================================
# Test 2 — Activity Filtering (No Cross-Activity Leakage)
# =========================================================================

def test_activity_filtering_no_leakage():
    db = create_test_db()
    _seed_activity(db, "A1000")
    _seed_activity(db, "A1001")
    # Event 1 for A1000
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id, created_at)
        VALUES ('EV-1', 'SCH-1', '2026-08-01', 'Claim for A1000', 'whatsapp', 'A1000', '2026-08-01 10:00:00')
        """
    )
    # Event 2 for A1001
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id, created_at)
        VALUES ('EV-2', 'SCH-1', '2026-08-02', 'Claim for A1001', 'whatsapp', 'A1001', '2026-08-02 10:00:00')
        """
    )

    data = query_activity_history("A1000", schedule_id="SCH-1", conn=db)
    assert data["activity_id"] == "A1000"
    assert len(data["timeline"]) == 1
    assert data["timeline"][0]["event_id"] == "EV-1"

    # Querying A1001 returns only A1001
    data_1001 = query_activity_history("A1001", schedule_id="SCH-1", conn=db)
    assert data_1001["activity_id"] == "A1001"
    assert len(data_1001["timeline"]) == 1
    assert data_1001["timeline"][0]["event_id"] == "EV-2"


# =========================================================================
# Test 3 — Execution Events Included
# =========================================================================

def test_execution_events_included():
    db = create_test_db()
    _seed_activity(db, "ACT-SINGLE")
    db.execute(
        """
        INSERT INTO execution_events (
            event_id, schedule_id, event_date, raw_claim_text, input_channel,
            matched_activity_id, claimed_pct, created_at
        )
        VALUES ('EV-SINGLE', 'SCH-1', '2026-08-01', 'Initial excavation claim', 'typed', 'ACT-SINGLE', 40.0, '2026-08-01 08:00:00')
        """
    )

    data = query_activity_history("ACT-SINGLE", schedule_id="SCH-1", conn=db)
    assert data["activity_id"] == "ACT-SINGLE"
    assert len(data["timeline"]) == 1
    ev = data["timeline"][0]
    assert ev["type"] == "execution_event"
    assert ev["event_id"] == "EV-SINGLE"
    assert ev["claimed_pct"] == 40.0
    assert ev["raw_claim_text"] == "Initial excavation claim"


# =========================================================================
# Test 4 — Multiple Execution Events
# =========================================================================

def test_multiple_execution_events():
    db = create_test_db()
    _seed_activity(db, "ACT-MULTI")
    for i in range(1, 4):
        db.execute(
            """
            INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id, claimed_pct, created_at)
            VALUES (?, 'SCH-1', ?, ?, 'typed', 'ACT-MULTI', ?, ?)
            """,
            (f"EV-{i}", f"2026-08-0{i}", f"Claim {i}", i * 25.0, f"2026-08-0{i} 08:00:00"),
        )

    data = query_activity_history("ACT-MULTI", schedule_id="SCH-1", conn=db)
    assert data["activity_id"] == "ACT-MULTI"
    assert len(data["timeline"]) == 3
    event_ids = [item["event_id"] for item in data["timeline"]]
    assert event_ids == ["EV-1", "EV-2", "EV-3"]


# =========================================================================
# Test 5 — Source References Included
# =========================================================================

def test_source_references_included():
    db = create_test_db()
    _seed_activity(db, "ACT-SREF")
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id, created_at)
        VALUES ('EV-SREF', 'SCH-1', '2026-08-01', 'Foundation claim', 'typed', 'ACT-SREF', '2026-08-01 10:00:00')
        """
    )
    db.execute(
        """
        INSERT INTO source_references (reference_id, event_id, file_name, raw_snippet)
        VALUES ('REF-1', 'EV-SREF', 'report.pdf', 'Foundations poured')
        """
    )

    data = query_activity_history("ACT-SREF", schedule_id="SCH-1", conn=db)
    assert len(data["timeline"]) == 1
    assert len(data["timeline"][0]["source_references"]) == 1
    assert data["timeline"][0]["source_references"][0]["reference_id"] == "REF-1"
    assert data["timeline"][0]["source_references"][0]["file_name"] == "report.pdf"


# =========================================================================
# Test 6 — Multiple Source References
# =========================================================================

def test_multiple_source_references():
    db = create_test_db()
    _seed_activity(db, "ACT-REFS")
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id, created_at)
        VALUES ('EV-REF', 'SCH-1', '2026-08-01', 'Piping inspection complete', 'typed', 'ACT-REFS', '2026-08-01 10:00:00')
        """
    )
    # Insert 3 source references for EV-REF
    db.execute(
        """
        INSERT INTO source_references (reference_id, event_id, file_name, sheet_name, row_cell_ref, raw_snippet)
        VALUES ('REF-1', 'EV-REF', 'site_diary.xlsx', 'Civil', 'B12', 'Piping test passed')
        """
    )
    db.execute(
        """
        INSERT INTO source_references (reference_id, event_id, file_name, message_id, raw_snippet)
        VALUES ('REF-2', 'EV-REF', 'whatsapp_log.txt', 'MSG-999', 'Foreman: test completed')
        """
    )
    db.execute(
        """
        INSERT INTO source_references (reference_id, event_id, file_name, raw_snippet)
        VALUES ('REF-3', 'EV-REF', 'site_photo.jpg', 'Visual inspection passed')
        """
    )

    data = query_activity_history("ACT-REFS", schedule_id="SCH-1", conn=db)
    assert len(data["timeline"]) == 1
    event_entry = data["timeline"][0]
    assert event_entry["type"] == "execution_event"
    assert len(event_entry["source_references"]) == 3

    ref_ids = [r["reference_id"] for r in event_entry["source_references"]]
    assert "REF-1" in ref_ids
    assert "REF-2" in ref_ids
    assert "REF-3" in ref_ids

    ref1 = next(r for r in event_entry["source_references"] if r["reference_id"] == "REF-1")
    assert ref1["file_name"] == "site_diary.xlsx"
    assert ref1["sheet_name"] == "Civil"
    assert ref1["row_cell_ref"] == "B12"


# =========================================================================
# Test 7 — Final Planner Decision Included
# =========================================================================

def test_final_planner_decision_included():
    db = create_test_db()
    _seed_activity(db, "ACT-DEC")
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id, created_at)
        VALUES ('EV-DEC', 'SCH-1', '2026-08-01', 'Claim ready for decision', 'typed', 'ACT-DEC', '2026-08-01 09:00:00')
        """
    )
    db.execute(
        """
        INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, approved_pct, planner_id, justification, decided_at)
        VALUES ('DEC-FINAL', 'EV-DEC', 'ACT-DEC', 'APPROVE', 100.0, 'P-01', 'Approved per inspection', '2026-08-01 17:00:00')
        """
    )

    data = query_activity_history("ACT-DEC", schedule_id="SCH-1", conn=db)
    # Timeline should have execution_event, followed by planner_decision
    assert len(data["timeline"]) == 2
    assert data["timeline"][0]["type"] == "execution_event"
    assert data["timeline"][1]["type"] == "planner_decision"
    dec = data["timeline"][1]
    assert dec["decision_id"] == "DEC-FINAL"
    assert dec["action"] == "APPROVE"
    assert dec["approved_pct"] == 100.0
    assert dec["justification"] == "Approved per inspection"


# =========================================================================
# Test 8 — Earlier Decision Does Not Replace Final Decision
# =========================================================================

def test_earlier_decision_does_not_replace_final():
    db = create_test_db()
    _seed_activity(db, "ACT-REV")
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id, created_at)
        VALUES ('EV-REV', 'SCH-1', '2026-08-01', 'Trenching claim', 'typed', 'ACT-REV', '2026-08-01 08:00:00')
        """
    )
    # Earlier decision: HOLD at 10:00
    db.execute(
        """
        INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, approved_pct, planner_id, justification, decided_at)
        VALUES ('DEC-EARLY', 'EV-REV', 'ACT-REV', 'HOLD', 0.0, 'P-01', 'Need photo proof', '2026-08-01 10:00:00')
        """
    )
    # Later final decision: EDIT at 16:00
    db.execute(
        """
        INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, approved_pct, planner_id, justification, decided_at)
        VALUES ('DEC-LATEST', 'EV-REV', 'ACT-REV', 'EDIT', 60.0, 'P-01', 'Photo confirmed 60%', '2026-08-01 16:00:00')
        """
    )

    data = query_activity_history("ACT-REV", schedule_id="SCH-1", conn=db)
    decisions_in_timeline = [item for item in data["timeline"] if item["type"] == "planner_decision"]
    # Multiple historical decisions appear chronologically without earlier replacing final
    assert len(decisions_in_timeline) == 2
    assert decisions_in_timeline[0]["decision_id"] == "DEC-EARLY"
    assert decisions_in_timeline[0]["action"] == "HOLD"
    assert decisions_in_timeline[1]["decision_id"] == "DEC-LATEST"
    assert decisions_in_timeline[1]["action"] == "EDIT"
    assert decisions_in_timeline[1]["approved_pct"] == 60.0




# =========================================================================
# Test 9 — Chronological Ordering
# =========================================================================

def test_chronological_ordering():
    db = create_test_db()
    _seed_activity(db, "ACT-TIME")
    # Insert in reverse order
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id, created_at)
        VALUES ('EV-DAY3', 'SCH-1', '2026-08-03', 'Third event', 'typed', 'ACT-TIME', '2026-08-03 12:00:00')
        """
    )
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id, created_at)
        VALUES ('EV-DAY1', 'SCH-1', '2026-08-01', 'First event', 'typed', 'ACT-TIME', '2026-08-01 09:00:00')
        """
    )
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id, created_at)
        VALUES ('EV-DAY2', 'SCH-1', '2026-08-02', 'Second event', 'typed', 'ACT-TIME', '2026-08-02 11:00:00')
        """
    )
    # Decision after day 3
    db.execute(
        """
        INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, planner_id, justification, decided_at)
        VALUES ('DEC-FINAL', 'EV-DAY3', 'ACT-TIME', 'APPROVE', 'P-01', 'Approved', '2026-08-03 18:00:00')
        """
    )

    data = query_activity_history("ACT-TIME", schedule_id="SCH-1", conn=db)
    timeline_ids = [
        item["decision_id"] if item["type"] == "planner_decision" else item["event_id"]
        for item in data["timeline"]
    ]
    assert timeline_ids == ["EV-DAY1", "EV-DAY2", "EV-DAY3", "DEC-FINAL"]


# =========================================================================
# Test 10 — Deterministic Tie Handling
# =========================================================================

def test_deterministic_tie_handling():
    db = create_test_db()
    _seed_activity(db, "ACT-TIE")
    # Two events with identical timestamps
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id, created_at)
        VALUES ('EV-B', 'SCH-1', '2026-08-01', 'Event B', 'typed', 'ACT-TIE', '2026-08-01 10:00:00')
        """
    )
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id, created_at)
        VALUES ('EV-A', 'SCH-1', '2026-08-01', 'Event A', 'typed', 'ACT-TIE', '2026-08-01 10:00:00')
        """
    )
    # Decision at same timestamp
    db.execute(
        """
        INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, planner_id, justification, decided_at)
        VALUES ('DEC-TIE', 'EV-A', 'ACT-TIE', 'APPROVE', 'P-01', 'Tied decision', '2026-08-01 10:00:00')
        """
    )

    data = query_activity_history("ACT-TIE", schedule_id="SCH-1", conn=db)
    # Events come first (ordered by event_id: EV-A, EV-B), followed by decision
    timeline_ids = [
        item["decision_id"] if item["type"] == "planner_decision" else item["event_id"]
        for item in data["timeline"]
    ]
    assert timeline_ids == ["EV-A", "EV-B", "DEC-TIE"]


# =========================================================================
# Test 11 — Empty History Handled Safely (real activity, zero events -> 200
# with metadata + "no history yet", not confused with a nonexistent activity)
# =========================================================================

def test_empty_history_handled_safely():
    db = create_test_db()
    _seed_activity(db, "ACT-NO-HISTORY", activity_name="Cable Tray Install")
    data = query_activity_history("ACT-NO-HISTORY", schedule_id="SCH-1", conn=db)
    assert data["activity_id"] == "ACT-NO-HISTORY"
    assert data["activity_name"] == "Cable Tray Install"
    assert data["schedule_id"] == "SCH-1"
    assert data["timeline"] == []


# =========================================================================
# Test 12 — Unknown Activity Returns 404 (ISS-23: never infer schedule
# metadata only from execution_events -- a genuinely nonexistent activity_id
# must 404, not a silent 200 with an empty timeline)
# =========================================================================

def test_unknown_activity_returns_404():
    db = create_test_db()
    with pytest.raises(HTTPException) as exc_info:
        query_activity_history("NONEXISTENT-ACT-9999", schedule_id="SCH-1", conn=db)
    assert exc_info.value.status_code == 404


# =========================================================================
# Test 13 — Authorization (Supervisor 200, Site Engineer 403, Unauth 401)
# =========================================================================

def test_authorization(monkeypatch):
    client = TestClient(app)

    # 1. Unauthenticated -> 401
    resp = client.get("/api/v1/activities/ACT-1/history?schedule_id=SCH-1")
    assert resp.status_code == 401

    # 2. SITE_ENGINEER (no REVIEW_CLAIM) -> 403
    with act_as("SITE_ENGINEER", schedule_id="SCH-1"):
        resp = client.get("/api/v1/activities/ACT-1/history", headers={"Authorization": "Bearer mock-token"})
    assert resp.status_code == 403

    # 3. SUPERVISOR -> 200, scoped to the validated schedule
    captured = {}

    def _stub(activity_id, schedule_id=None, conn=None):
        captured["schedule_id"] = schedule_id
        return {"activity_id": activity_id, "timeline": []}

    monkeypatch.setattr("backend.routers.activities.query_activity_history", _stub)
    with act_as("SUPERVISOR", schedule_id="SCH-1"):
        resp = client.get("/api/v1/activities/ACT-1/history", headers={"Authorization": "Bearer mock-token"})
    assert resp.status_code == 200
    assert resp.json()["activity_id"] == "ACT-1"
    assert captured["schedule_id"] == "SCH-1"


def test_endpoint_404_passthrough_not_masked_as_500(monkeypatch):
    """
    The route's generic `except Exception` must not swallow the 404/409
    HTTPExceptions query_activity_history raises for an unknown/ambiguous
    activity -- those must reach the client as-is, not become a 500.
    """
    client = TestClient(app)

    def _raise_404(activity_id, schedule_id=None, conn=None):
        raise HTTPException(status_code=404, detail=f"Activity '{activity_id}' not found")

    monkeypatch.setattr("backend.routers.activities.query_activity_history", _raise_404)
    with act_as("SUPERVISOR", schedule_id="SCH-1"):
        resp = client.get("/api/v1/activities/NOPE/history", headers={"Authorization": "Bearer mock-token"})
    assert resp.status_code == 404


# =========================================================================
# Test 14 — Response Contract
# =========================================================================

def test_response_contract():
    db = create_test_db()
    _seed_activity(db, "ACT-C")
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id, created_at)
        VALUES ('EV-CONTRACT', 'SCH-1', '2026-08-01', 'Verified concrete footing', 'typed', 'ACT-C', '2026-08-01 10:00:00')
        """
    )
    db.execute(
        """
        INSERT INTO source_references (reference_id, event_id, file_name, raw_snippet)
        VALUES ('REF-C', 'EV-CONTRACT', 'diary.pdf', 'Footing verified')
        """
    )
    db.execute(
        """
        INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, approved_pct, planner_id, justification, decided_at)
        VALUES ('DEC-C', 'EV-CONTRACT', 'ACT-C', 'APPROVE', 100.0, 'P-01', 'Passed', '2026-08-01 14:00:00')
        """
    )

    data = query_activity_history("ACT-C", schedule_id="SCH-1", conn=db)
    assert set(data.keys()) == {
        "activity_id", "schedule_id", "activity_name", "discipline",
        "location", "wbs_code", "planned_start", "planned_finish", "timeline",
    }
    assert isinstance(data["timeline"], list)
    assert len(data["timeline"]) == 2

    # Verify event entry
    ev_item = data["timeline"][0]
    assert ev_item["type"] == "execution_event"
    assert ev_item["event_id"] == "EV-CONTRACT"
    assert isinstance(ev_item["source_references"], list)
    assert len(ev_item["source_references"]) == 1
    ref_item = ev_item["source_references"][0]
    assert ref_item["reference_id"] == "REF-C"

    # Verify decision entry
    dec_item = data["timeline"][1]
    assert dec_item["type"] == "planner_decision"
    assert dec_item["decision_id"] == "DEC-C"
    assert dec_item["action"] == "APPROVE"


# =========================================================================
# Test 15 — Planner Override Activity Routing
# =========================================================================

def test_planner_override_activity_routing():
    """
    If machine matched an event to ACT-A, but the planner decided on ACT-B,
    the event belongs to ACT-B's history, NOT ACT-A.
    """
    db = create_test_db()
    _seed_activity(db, "ACT-A")
    _seed_activity(db, "ACT-B")
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id, created_at)
        VALUES ('EV-OVERRIDE', 'SCH-1', '2026-08-01', 'Claim misclassified by AI', 'typed', 'ACT-A', '2026-08-01 10:00:00')
        """
    )
    db.execute(
        """
        INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, approved_pct, planner_id, justification, decided_at)
        VALUES ('DEC-OVERRIDE', 'EV-OVERRIDE', 'ACT-B', 'APPROVE', 80.0, 'P-01', 'Reassigned to ACT-B', '2026-08-01 12:00:00')
        """
    )

    # Under ACT-A: Timeline is empty (reassigned)
    data_a = query_activity_history("ACT-A", schedule_id="SCH-1", conn=db)
    assert data_a["timeline"] == []

    # Under ACT-B: Timeline contains the event and decision
    data_b = query_activity_history("ACT-B", schedule_id="SCH-1", conn=db)
    assert len(data_b["timeline"]) == 2
    assert data_b["timeline"][0]["event_id"] == "EV-OVERRIDE"
    assert data_b["timeline"][1]["decision_id"] == "DEC-OVERRIDE"


# =========================================================================
# Test 16 — Cross-Schedule Isolation (ISS-05): the same activity_id in two
# different schedules must never mix history, and an unscoped lookup must
# 409 rather than silently pick one.
# =========================================================================

def test_cross_schedule_isolation_same_activity_id():
    db = create_test_db()
    _seed_activity(db, "ACT-001", schedule_id="SCHED_A", activity_name="Excavation Sched A")
    _seed_activity(db, "ACT-001", schedule_id="SCHED_B", activity_name="Welding Sched B")

    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id, created_at)
        VALUES ('EV-A', 'SCHED_A', '2026-09-01', 'Schedule A claim', 'typed', 'ACT-001', '2026-09-01 09:00:00')
        """
    )
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id, created_at)
        VALUES ('EV-B', 'SCHED_B', '2026-09-02', 'Schedule B claim', 'typed', 'ACT-001', '2026-09-02 09:00:00')
        """
    )
    db.execute(
        """
        INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, approved_pct, planner_id, justification, decided_at)
        VALUES ('DEC-A', 'EV-A', 'ACT-001', 'APPROVE', 50.0, 'P-01', 'Approved A', '2026-09-01 12:00:00')
        """
    )
    db.execute(
        """
        INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, approved_pct, planner_id, justification, decided_at)
        VALUES ('DEC-B', 'EV-B', 'ACT-001', 'APPROVE', 30.0, 'P-01', 'Approved B', '2026-09-02 12:00:00')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_pct_complete, created_at)
        VALUES ('ACTL-A', 'DEC-A', 'EV-A', 'SCHED_A', 'ACT-001', 50.0, '2026-09-01 12:00:00')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_pct_complete, created_at)
        VALUES ('ACTL-B', 'DEC-B', 'EV-B', 'SCHED_B', 'ACT-001', 30.0, '2026-09-02 12:00:00')
        """
    )

    data_a = query_activity_history("ACT-001", schedule_id="SCHED_A", conn=db)
    assert data_a["schedule_id"] == "SCHED_A"
    assert data_a["activity_name"] == "Excavation Sched A"
    event_ids_a = {item.get("event_id") for item in data_a["timeline"] if item["type"] == "execution_event"}
    assert event_ids_a == {"EV-A"}
    actual_ids_a = {item.get("actual_id") for item in data_a["timeline"] if item["type"] == "approved_actual"}
    assert actual_ids_a == {"ACTL-A"}

    data_b = query_activity_history("ACT-001", schedule_id="SCHED_B", conn=db)
    assert data_b["schedule_id"] == "SCHED_B"
    assert data_b["activity_name"] == "Welding Sched B"
    event_ids_b = {item.get("event_id") for item in data_b["timeline"] if item["type"] == "execution_event"}
    assert event_ids_b == {"EV-B"}
    actual_ids_b = {item.get("actual_id") for item in data_b["timeline"] if item["type"] == "approved_actual"}
    assert actual_ids_b == {"ACTL-B"}

    # No schedule_id given -> 400, never a silent guess (no active/latest fallback, no cross-schedule lookup)
    with pytest.raises(HTTPException) as exc_info:
        query_activity_history("ACT-001", conn=db)
    assert exc_info.value.status_code == 400

    # An activity that exists only in ANOTHER schedule is not resolvable through this one
    _seed_activity(db, "ACT-ONLY-B", schedule_id="SCHED_B")
    with pytest.raises(HTTPException) as exc_info:
        query_activity_history("ACT-ONLY-B", schedule_id="SCHED_A", conn=db)
    assert exc_info.value.status_code == 404
