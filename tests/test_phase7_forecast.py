import sqlite3
from typing import Any

from fastapi.testclient import TestClient

from backend.main import app
from tests.v7ctx import act_as
from backend.routers.dashboard import query_forecast
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
    return SQLitePsycopgAdapter(conn)


# =========================================================================
# Test 1 — Endpoint Registration
# =========================================================================

def test_endpoint_is_registered():
    routes = list(app.openapi()["paths"].keys())
    assert "/api/v1/dashboard/forecast" in routes, (
        "GET /api/v1/dashboard/forecast not registered on app"
    )


# =========================================================================
# Test 2 & 7 — Activity-level Forecast & Target Planned Duration
# =========================================================================

def test_activity_level_forecast():
    db = create_test_db()
    # Historical completed activity in CIVIL: planned 10 days, actual 12 days -> ratio = 1.2
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('CIV-HIST-1', 'SCH-1', 'Old Trench', 'CIVIL', 'Zone A', '2026-07-01', '2026-07-11')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish)
        VALUES ('ACT-1', 'DEC-1', 'EV-1', 'SCH-1', 'CIV-HIST-1', '2026-07-01', '2026-07-13')
        """
    )

    # Target activity in CIVIL: planned 30 days
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('A1000', 'SCH-1', 'New Foundation', 'CIVIL', 'Zone B', '2026-08-01', '2026-08-31')
        """
    )

    data = query_forecast(activity_id="A1000", conn=db)
    assert data["activity_id"] == "A1000"
    assert data["discipline"] == "CIVIL"
    assert data["planned_duration"] == 30
    assert data["historical_ratio"] == 1.2
    assert data["forecast_duration"] == 36  # 30 * 1.2 = 36


# =========================================================================
# Test 3 — Ratio Calculation (actual / planned)
# =========================================================================

def test_ratio_calculation():
    db = create_test_db()
    # planned = 10, actual = 12 -> ratio = 1.2
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('HIST-1', 'SCH-1', 'Hist Task', 'CIVIL', 'Loc', '2026-08-01', '2026-08-11')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish)
        VALUES ('ACT-1', 'DEC-1', 'EV-1', 'SCH-1', 'HIST-1', '2026-08-01', '2026-08-13')
        """
    )
    # Target planned = 20
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('TARGET-1', 'SCH-1', 'Target', 'CIVIL', 'Loc', '2026-09-01', '2026-09-21')
        """
    )

    data = query_forecast(activity_id="TARGET-1", conn=db)
    assert data["historical_ratio"] == 1.2
    assert data["forecast_duration"] == 24  # 20 * 1.2 = 24


# =========================================================================
# Test 4 — Average Historical Ratio (Arithmetic Mean)
# =========================================================================

def test_average_historical_ratio():
    db = create_test_db()
    # Three historical activities with ratios: 1.2, 1.1, 1.3 -> average = 1.2
    # Act 1: planned 10, actual 12 -> 1.2
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('H1', 'SCH-1', 'H1', 'CIVIL', 'Loc', '2026-01-01', '2026-01-11')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish)
        VALUES ('A1', 'D1', 'E1', 'SCH-1', 'H1', '2026-01-01', '2026-01-13')
        """
    )

    # Act 2: planned 20, actual 22 -> 1.1
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('H2', 'SCH-1', 'H2', 'CIVIL', 'Loc', '2026-02-01', '2026-02-21')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish)
        VALUES ('A2', 'D2', 'E2', 'SCH-1', 'H2', '2026-02-01', '2026-02-23')
        """
    )

    # Act 3: planned 10, actual 13 -> 1.3
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('H3', 'SCH-1', 'H3', 'CIVIL', 'Loc', '2026-03-01', '2026-03-11')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish)
        VALUES ('A3', 'D3', 'E3', 'SCH-1', 'H3', '2026-03-01', '2026-03-14')
        """
    )

    # Target planned = 10
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('TARGET-AVG', 'SCH-1', 'Target Avg', 'CIVIL', 'Loc', '2026-04-01', '2026-04-11')
        """
    )

    data = query_forecast(activity_id="TARGET-AVG", conn=db)
    assert abs(data["historical_ratio"] - 1.2) < 0.001
    assert data["forecast_duration"] == 12


# =========================================================================
# Test 5 & 6 — Same-Discipline Filtering & Cross-Discipline Exclusion
# =========================================================================

def test_same_discipline_isolation():
    db = create_test_db()
    # CIVIL historical activity: planned 10, actual 15 -> ratio = 1.5
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('CIV-HIST', 'SCH-1', 'Civil Task', 'CIVIL', 'Loc', '2026-01-01', '2026-01-11')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish)
        VALUES ('ACT-CIV', 'D1', 'E1', 'SCH-1', 'CIV-HIST', '2026-01-01', '2026-01-16')
        """
    )

    # PIPING historical activity: planned 10, actual 30 -> ratio = 3.0 (must NOT affect CIVIL)
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('PIP-HIST', 'SCH-1', 'Piping Task', 'PIPING', 'Loc', '2026-01-01', '2026-01-11')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish)
        VALUES ('ACT-PIP', 'D2', 'E2', 'SCH-1', 'PIP-HIST', '2026-01-01', '2026-01-31')
        """
    )

    # Target in CIVIL: planned 10 days
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('CIV-TARGET', 'SCH-1', 'Target Civil', 'CIVIL', 'Loc', '2026-05-01', '2026-05-11')
        """
    )

    data = query_forecast(activity_id="CIV-TARGET", conn=db)
    # Ratio must be 1.5, NOT influenced by PIPING's 3.0 (which would have yielded 2.25)
    assert data["historical_ratio"] == 1.5
    assert data["forecast_duration"] == 15


# =========================================================================
# Test 8 — Zero Planned Duration (Division-by-Zero Safety)
# =========================================================================

def test_zero_planned_duration_safety():
    db = create_test_db()
    # Milestone with planned_finish == planned_start (0 days)
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('MILESTONE-0', 'SCH-1', 'Milestone', 'CIVIL', 'Loc', '2026-01-01', '2026-01-01')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish)
        VALUES ('ACT-M0', 'D1', 'E1', 'SCH-1', 'MILESTONE-0', '2026-01-01', '2026-01-02')
        """
    )

    # Valid task: planned 10, actual 12 -> 1.2
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('VALID-HIST', 'SCH-1', 'Task', 'CIVIL', 'Loc', '2026-02-01', '2026-02-11')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish)
        VALUES ('ACT-V', 'D2', 'E2', 'SCH-1', 'VALID-HIST', '2026-02-01', '2026-02-13')
        """
    )

    # Target task
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('TARGET-Z', 'SCH-1', 'Target Z', 'CIVIL', 'Loc', '2026-03-01', '2026-03-11')
        """
    )

    data = query_forecast(activity_id="TARGET-Z", conn=db)
    # Zero-duration milestone ignored; only valid ratio (1.2) used
    assert data["historical_ratio"] == 1.2
    assert data["forecast_duration"] == 12


# =========================================================================
# Test 9 — Missing Actual Dates (Incomplete Actuals Excluded)
# =========================================================================

def test_missing_actual_dates_excluded_from_history():
    db = create_test_db()
    # In-progress activity with actual_start but NO actual_finish
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('IN-PROG', 'SCH-1', 'In Progress', 'CIVIL', 'Loc', '2026-01-01', '2026-01-11')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish)
        VALUES ('ACT-IN-PROG', 'D1', 'E1', 'SCH-1', 'IN-PROG', '2026-01-01', NULL)
        """
    )

    # One completed activity: planned 10, actual 14 -> 1.4
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('COMPLETED', 'SCH-1', 'Done', 'CIVIL', 'Loc', '2026-02-01', '2026-02-11')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish)
        VALUES ('ACT-DONE', 'D2', 'E2', 'SCH-1', 'COMPLETED', '2026-02-01', '2026-02-15')
        """
    )

    # Target
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('TARGET-INCOMPLETE', 'SCH-1', 'Target', 'CIVIL', 'Loc', '2026-03-01', '2026-03-11')
        """
    )

    data = query_forecast(activity_id="TARGET-INCOMPLETE", conn=db)
    assert data["historical_ratio"] == 1.4
    assert data["forecast_duration"] == 14


# =========================================================================
# Test 10 — Missing Historical Data Handled Safely (No Fabrication)
# =========================================================================

def test_missing_historical_data():
    db = create_test_db()
    # Target activity exists in PIPING, but zero approved actuals exist for PIPING
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('NO-HIST-TARGET', 'SCH-1', 'New Pipeline', 'PIPING', 'Loc', '2026-01-01', '2026-01-21')
        """
    )

    data = query_forecast(activity_id="NO-HIST-TARGET", conn=db)
    assert data["activity_id"] == "NO-HIST-TARGET"
    assert data["discipline"] == "PIPING"
    assert data["planned_duration"] == 20
    # Must NOT fabricate 1.0; returns null
    assert data["historical_ratio"] is None
    assert data["forecast_duration"] is None


# =========================================================================
# Test 11 — Target Activity Not Contaminating Own History
# =========================================================================

def test_target_activity_not_contaminating_own_history():
    db = create_test_db()
    # Historical activity: planned 10, actual 12 -> ratio = 1.2
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('HIST-CLEAN', 'SCH-1', 'Old Task', 'CIVIL', 'Loc', '2026-01-01', '2026-01-11')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish)
        VALUES ('ACT-CLEAN', 'D1', 'E1', 'SCH-1', 'HIST-CLEAN', '2026-01-01', '2026-01-13')
        """
    )

    # Target activity has an in-progress or partial approved actual row
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('TARGET-SELF', 'SCH-1', 'Target Task', 'CIVIL', 'Loc', '2026-02-01', '2026-02-21')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish)
        VALUES ('ACT-SELF', 'D2', 'E2', 'SCH-1', 'TARGET-SELF', '2026-02-01', '2026-02-15')
        """
    )

    data = query_forecast(activity_id="TARGET-SELF", conn=db)
    # The ratio MUST be 1.2 from HIST-CLEAN; TARGET-SELF (14/20 = 0.7) must NOT be included in reference history
    assert data["historical_ratio"] == 1.2
    assert data["forecast_duration"] == 24  # 20 * 1.2 = 24


# =========================================================================
# Test 12 — Discipline-Level Forecast (?discipline=CIVIL)
# =========================================================================

def test_discipline_level_forecast():
    db = create_test_db()
    # Historical completed activities in CIVIL: ratios 1.2 and 1.4 -> avg 1.3
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('C-HIST-1', 'SCH-1', 'H1', 'CIVIL', 'Loc', '2026-01-01', '2026-01-11')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish)
        VALUES ('ACT-C1', 'D1', 'E1', 'SCH-1', 'C-HIST-1', '2026-01-01', '2026-01-13')
        """
    )

    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('C-HIST-2', 'SCH-1', 'H2', 'CIVIL', 'Loc', '2026-01-01', '2026-01-11')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish)
        VALUES ('ACT-C2', 'D2', 'E2', 'SCH-1', 'C-HIST-2', '2026-01-01', '2026-01-15')
        """
    )

    # Upcoming task in CIVIL: planned 20 days
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('C-UPCOMING', 'SCH-1', 'Upcoming Task', 'CIVIL', 'Loc', '2026-06-01', '2026-06-21')
        """
    )

    # Discipline forecast query (case-insensitive "civil")
    data = query_forecast(discipline="civil", conn=db)
    assert data["discipline"] == "CIVIL"
    assert abs(data["historical_ratio"] - 1.3) < 0.001
    assert data["total_activities"] == 3

    upcoming = next(a for a in data["activities"] if a["activity_id"] == "C-UPCOMING")
    assert upcoming["planned_duration"] == 20
    assert upcoming["forecast_duration"] == 26  # 20 * 1.3 = 26


# =========================================================================
# Test 13 — Canonical Source Activity ID Handling
# =========================================================================

def test_source_activity_id_preserved():
    db = create_test_db()
    complex_id = "PIP-PS3-WLD-024"
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES (?, 'SCH-1', 'Weld Joint', 'PIPING', 'Loc', '2026-01-01', '2026-01-11')
        """,
        (complex_id,),
    )

    data = query_forecast(activity_id=complex_id, conn=db)
    assert data["activity_id"] == complex_id


# =========================================================================
# Test 14 — Authorization (Supervisor 200, Site Engineer 403, Unauth 401)
# =========================================================================

def test_authorization(monkeypatch):
    client = TestClient(app)

    # 1. Unauthenticated -> 401
    resp = client.get("/api/v1/dashboard/forecast?activity_id=A1000")
    assert resp.status_code == 401

    # 2. SITE_ENGINEER (no REVIEW_CLAIM) -> 403
    with act_as("SITE_ENGINEER"):
        resp = client.get("/api/v1/dashboard/forecast?activity_id=A1000", headers={"Authorization": "Bearer mock-token"})
    assert resp.status_code == 403

    # 3. SUPERVISOR -> 200, scoped to the caller's project and explicit schedule
    captured = {}

    def _stub(activity_id=None, discipline=None, conn=None, project_id=None, schedule_id=None):
        captured.update(project_id=project_id, schedule_id=schedule_id)
        return {
            "activity_id": activity_id or "A1000",
            "discipline": "CIVIL",
            "planned_duration": 30,
            "historical_ratio": 1.167,
            "forecast_duration": 35,
        }

    monkeypatch.setattr("backend.routers.dashboard.query_forecast", _stub)
    with act_as("SUPERVISOR", schedule_id="SCH-F"):
        resp = client.get("/api/v1/dashboard/forecast?activity_id=A1000", headers={"Authorization": "Bearer mock-token"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["activity_id"] == "A1000"
    assert body["forecast_duration"] == 35
    assert captured["schedule_id"] == "SCH-F" and captured["project_id"]


# =========================================================================
# Test 15 — Response Contract Matching
# =========================================================================

def test_response_contract():
    db = create_test_db()
    # Historical activity
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('H-CONTRACT', 'SCH-1', 'Hist', 'CIVIL', 'Loc', '2026-01-01', '2026-01-31')
        """
    )
    # planned 30, actual 35 -> ratio = 35 / 30 = 1.16666...
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish)
        VALUES ('ACT-H', 'D1', 'E1', 'SCH-1', 'H-CONTRACT', '2026-01-01', '2026-02-05')
        """
    )

    # Target: planned 30 days
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('A1000', 'SCH-1', 'Target Contract', 'CIVIL', 'Loc', '2026-03-01', '2026-03-31')
        """
    )

    data = query_forecast(activity_id="A1000", conn=db)
    expected_keys = {
        "activity_id",
        "discipline",
        "planned_duration",
        "historical_ratio",
        "forecast_duration",
    }
    assert set(data.keys()) == expected_keys
    assert data["activity_id"] == "A1000"
    assert data["discipline"] == "CIVIL"
    assert data["planned_duration"] == 30
    assert data["historical_ratio"] == 1.167
    assert data["forecast_duration"] == 35


# =========================================================================
# Test 16 — Deterministic Result
# =========================================================================

def test_deterministic_result():
    db = create_test_db()
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('HIST-DET', 'SCH-1', 'H', 'CIVIL', 'Loc', '2026-01-01', '2026-01-11')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish)
        VALUES ('A-DET', 'D', 'E', 'SCH-1', 'HIST-DET', '2026-01-01', '2026-01-13')
        """
    )
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('TARGET-DET', 'SCH-1', 'T', 'CIVIL', 'Loc', '2026-02-01', '2026-02-11')
        """
    )

    data1 = query_forecast(activity_id="TARGET-DET", conn=db)
    data2 = query_forecast(activity_id="TARGET-DET", conn=db)
    assert data1 == data2


# =========================================================================
# Test 17 & 18 — Missing Parameters (400) & Unknown Activity (404)
# =========================================================================

def test_missing_parameters_400():
    from fastapi import HTTPException
    import pytest

    with pytest.raises(HTTPException) as exc_info:
        query_forecast(activity_id=None, discipline=None)
    assert exc_info.value.status_code == 400


def test_unknown_activity_404():
    from fastapi import HTTPException
    import pytest

    db = create_test_db()
    with pytest.raises(HTTPException) as exc_info:
        query_forecast(activity_id="UNKNOWN-ACT-999", conn=db)
    assert exc_info.value.status_code == 404
