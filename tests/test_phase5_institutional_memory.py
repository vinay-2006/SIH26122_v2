import sqlite3
from typing import Any

from fastapi.testclient import TestClient

from backend.main import app
from tests.v7ctx import act_as
from backend.routers.dashboard import query_institutional_memory
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
# 1. Endpoint Registration
# =========================================================================

def test_endpoint_is_registered():
    routes = list(app.openapi()["paths"].keys())
    assert "/api/v1/dashboard/institutional-memory" in routes, (
        "GET /api/v1/dashboard/institutional-memory not registered on app"
    )


# =========================================================================
# 2. Supervisor Access (2xx Success)
# =========================================================================

def test_supervisor_access_succeeds(monkeypatch):
    client = TestClient(app)
    captured = {}

    def _stub(discipline=None, conn=None, project_id=None):
        captured["project_id"] = project_id
        return {
            "activities": [
                {
                    "activity_id": "A1000",
                    "discipline": "CIVIL",
                    "planned_duration": 4,
                    "actual_duration": 6,
                    "variance_days": 2,
                }
            ],
            "total_activities": 1,
        }

    monkeypatch.setattr("backend.routers.dashboard.query_institutional_memory", _stub)
    with act_as("SUPERVISOR"):
        resp = client.get("/api/v1/dashboard/institutional-memory", headers={"Authorization": "Bearer mocked-sup-token"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_activities"] == 1
    assert len(body["activities"]) == 1
    assert body["activities"][0]["activity_id"] == "A1000"
    assert captured["project_id"], "history must be scoped to the caller's project"


# =========================================================================
# 3. Unauthenticated Access Rejected (401)
# =========================================================================

def test_unauthenticated_rejected():
    client = TestClient(app)
    # No header
    resp = client.get("/api/v1/dashboard/institutional-memory")
    assert resp.status_code == 401

    # Invalid token
    resp_invalid = client.get(
        "/api/v1/dashboard/institutional-memory",
        headers={"Authorization": "Bearer invalid.token"},
    )
    assert resp_invalid.status_code == 401


# =========================================================================
# 4. Site Engineer Forbidden (403)
# =========================================================================

def test_site_engineer_forbidden():
    client = TestClient(app)
    with act_as("SITE_ENGINEER"):
        resp = client.get("/api/v1/dashboard/institutional-memory", headers={"Authorization": "Bearer mocked-eng-token"})
    assert resp.status_code == 403


# =========================================================================
# 5. Planned Duration Derivation
# =========================================================================

def test_planned_duration_derivation():
    db = create_test_db()
    # 2026-08-01 to 2026-08-11 = 10 days
    db.execute(
        """
        INSERT INTO schedule_activities (
            activity_id, schedule_id, activity_name, discipline, location,
            planned_start, planned_finish
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("ACT-PLAN-1", "SCH-1", "Foundation Pour", "CIVIL", "Zone A", "2026-08-01", "2026-08-11"),
    )

    data = query_institutional_memory(conn=db)
    assert data["total_activities"] == 1
    act = data["activities"][0]
    assert act["activity_id"] == "ACT-PLAN-1"
    assert act["planned_duration"] == 10


# =========================================================================
# 6. Actual Duration Derivation
# =========================================================================

def test_actual_duration_derivation():
    db = create_test_db()
    # Planned: 2026-08-01 to 2026-08-11 (10 days)
    # Actual: 2026-08-01 to 2026-08-15 (14 days)
    db.execute(
        """
        INSERT INTO schedule_activities (
            activity_id, schedule_id, activity_name, discipline, location,
            planned_start, planned_finish
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("ACT-ACT-1", "SCH-1", "Pipe Welding", "PIPING", "Zone B", "2026-08-01", "2026-08-11"),
    )
    db.execute(
        """
        INSERT INTO approved_actuals (
            actual_id, decision_id, event_id, schedule_id, activity_id,
            actual_start, actual_finish
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("ACTUAL-1", "DEC-1", "EV-1", "SCH-1", "ACT-ACT-1", "2026-08-01", "2026-08-15"),
    )

    data = query_institutional_memory(conn=db)
    assert data["total_activities"] == 1
    act = data["activities"][0]
    assert act["actual_duration"] == 14
    assert act["planned_duration"] == 10
    assert act["variance_days"] == 4  # 14 - 10 = +4 days delayed


# =========================================================================
# 7. Correct Activity Join on (schedule_id, activity_id)
# =========================================================================

def test_activity_join_scoped_to_schedule_id():
    db = create_test_db()
    # Same activity_id 'A1000' in two different schedules
    db.execute(
        """
        INSERT INTO schedule_activities (
            activity_id, schedule_id, activity_name, discipline, location,
            planned_start, planned_finish
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("A1000", "SCH-A", "Excavation A", "CIVIL", "Loc 1", "2026-08-01", "2026-08-05"),
    )
    db.execute(
        """
        INSERT INTO schedule_activities (
            activity_id, schedule_id, activity_name, discipline, location,
            planned_start, planned_finish
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("A1000", "SCH-B", "Excavation B", "CIVIL", "Loc 2", "2026-08-10", "2026-08-20"),
    )

    # Approved actual exists ONLY for SCH-A
    db.execute(
        """
        INSERT INTO approved_actuals (
            actual_id, decision_id, event_id, schedule_id, activity_id,
            actual_start, actual_finish
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("ACTUAL-SCH-A", "DEC-1", "EV-1", "SCH-A", "A1000", "2026-08-01", "2026-08-07"),
    )

    data = query_institutional_memory(conn=db)
    assert data["total_activities"] == 2

    # Both activities returned; SCH-A has actuals, SCH-B does not
    sch_a_act = next(a for a in data["activities"] if a["planned_duration"] == 4)
    sch_b_act = next(a for a in data["activities"] if a["planned_duration"] == 10)

    assert sch_a_act["actual_duration"] == 6  # 2026-08-07 - 2026-08-01
    assert sch_a_act["variance_days"] == 2

    # SCH-B must NOT join with SCH-A's approved actual
    assert sch_b_act["actual_duration"] is None
    assert sch_b_act["variance_days"] is None


# =========================================================================
# 8. Discipline Filter
# =========================================================================

def test_discipline_filter():
    db = create_test_db()
    activities = [
        ("CIV-1", "CIVIL", "2026-08-01", "2026-08-05"),
        ("PIP-1", "PIPING", "2026-08-02", "2026-08-08"),
        ("ELE-1", "ELECTRICAL", "2026-08-03", "2026-08-09"),
        ("CIV-2", "CIVIL", "2026-08-04", "2026-08-10"),
    ]
    for act_id, disc, start, finish in activities:
        db.execute(
            """
            INSERT INTO schedule_activities (
                activity_id, schedule_id, activity_name, discipline, location,
                planned_start, planned_finish
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (act_id, "SCH-1", f"Name {act_id}", disc, "Loc", start, finish),
        )

    # Filter for CIVIL
    civil_data = query_institutional_memory(discipline="CIVIL", conn=db)
    assert civil_data["total_activities"] == 2
    assert all(a["discipline"] == "CIVIL" for a in civil_data["activities"])
    assert [a["activity_id"] for a in civil_data["activities"]] == ["CIV-1", "CIV-2"]

    # Filter for PIPING (case-insensitive)
    piping_data = query_institutional_memory(discipline="piping", conn=db)
    assert piping_data["total_activities"] == 1
    assert piping_data["activities"][0]["activity_id"] == "PIP-1"


# =========================================================================
# 9. Multiple Disciplines Handled Unfiltered
# =========================================================================

def test_multiple_disciplines_unfiltered():
    db = create_test_db()
    disciplines = [
        ("C1", "CIVIL"),
        ("P1", "PIPING"),
        ("E1", "ELECTRICAL"),
        ("I1", "INSTRUMENTATION"),
        ("S1", "STATIC_ROTATING_EQUIPMENT"),
        ("H1", "HSE"),
    ]
    for act_id, disc in disciplines:
        db.execute(
            """
            INSERT INTO schedule_activities (
                activity_id, schedule_id, activity_name, discipline, location,
                planned_start, planned_finish
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (act_id, "SCH-MULTI", f"Task {act_id}", disc, "Zone", "2026-08-01", "2026-08-05"),
        )

    data = query_institutional_memory(conn=db)
    assert data["total_activities"] == 6
    found_disciplines = {a["discipline"] for a in data["activities"]}
    assert found_disciplines == {
        "CIVIL",
        "PIPING",
        "ELECTRICAL",
        "INSTRUMENTATION",
        "STATIC_ROTATING_EQUIPMENT",
        "HSE",
    }


# =========================================================================
# 10. Missing Approved Actual Handled Safely
# =========================================================================

def test_missing_approved_actual_handled_safely():
    db = create_test_db()
    db.execute(
        """
        INSERT INTO schedule_activities (
            activity_id, schedule_id, activity_name, discipline, location,
            planned_start, planned_finish
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("ACT-NO-ACTUAL", "SCH-1", "Unstarted Task", "CIVIL", "Area 1", "2026-08-01", "2026-08-06"),
    )

    data = query_institutional_memory(conn=db)
    assert data["total_activities"] == 1
    act = data["activities"][0]
    assert act["activity_id"] == "ACT-NO-ACTUAL"
    assert act["planned_duration"] == 5
    assert act["actual_duration"] is None
    assert act["variance_days"] is None


# =========================================================================
# 11. Incomplete Actual Dates Do Not Cause Errors or Fabrications
# =========================================================================

def test_incomplete_actual_dates_safe():
    db = create_test_db()
    # Activity 1: actual_start present, actual_finish is NULL (in-progress)
    db.execute(
        """
        INSERT INTO schedule_activities (
            activity_id, schedule_id, activity_name, discipline, location,
            planned_start, planned_finish
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("ACT-IN-PROG", "SCH-1", "In Progress", "PIPING", "Loc", "2026-08-01", "2026-08-10"),
    )
    db.execute(
        """
        INSERT INTO approved_actuals (
            actual_id, decision_id, event_id, schedule_id, activity_id,
            actual_start, actual_finish
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("ACTUAL-IN-PROG", "DEC-1", "EV-1", "SCH-1", "ACT-IN-PROG", "2026-08-02", None),
    )

    # Activity 2: both actual_start and actual_finish are NULL
    db.execute(
        """
        INSERT INTO schedule_activities (
            activity_id, schedule_id, activity_name, discipline, location,
            planned_start, planned_finish
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("ACT-NULL-DATES", "SCH-1", "Null Dates", "CIVIL", "Loc", "2026-08-01", "2026-08-10"),
    )
    db.execute(
        """
        INSERT INTO approved_actuals (
            actual_id, decision_id, event_id, schedule_id, activity_id,
            actual_start, actual_finish
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("ACTUAL-NULLS", "DEC-2", "EV-2", "SCH-1", "ACT-NULL-DATES", None, None),
    )

    data = query_institutional_memory(conn=db)
    assert data["total_activities"] == 2

    for act in data["activities"]:
        assert act["planned_duration"] == 9
        # No fabricated dates or today's date
        assert act["actual_duration"] is None
        assert act["variance_days"] is None


# =========================================================================
# 12. Response Contract & Shape Verification
# =========================================================================

def test_response_contract():
    db = create_test_db()
    db.execute(
        """
        INSERT INTO schedule_activities (
            activity_id, schedule_id, activity_name, discipline, location,
            planned_start, planned_finish
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("A1000", "SCH-1", "Civil Foundation", "CIVIL", "Plot 4", "2026-08-01", "2026-08-05"),
    )
    db.execute(
        """
        INSERT INTO approved_actuals (
            actual_id, decision_id, event_id, schedule_id, activity_id,
            actual_start, actual_finish
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("ACT-1", "DEC-1", "EV-1", "SCH-1", "A1000", "2026-08-01", "2026-08-07"),
    )

    data = query_institutional_memory(conn=db)

    # Top-level keys
    assert isinstance(data, dict)
    assert set(data.keys()) == {"activities", "total_activities"}
    assert isinstance(data["activities"], list)
    assert isinstance(data["total_activities"], int)

    # Record keys and exact values matching PRD illustration
    record = data["activities"][0]
    expected_keys = {
        "activity_id",
        "discipline",
        "planned_duration",
        "actual_duration",
        "variance_days",
    }
    assert set(record.keys()) == expected_keys
    assert record == {
        "activity_id": "A1000",
        "discipline": "CIVIL",
        "planned_duration": 4,
        "actual_duration": 6,
        "variance_days": 2,
    }


# =========================================================================
# 13. Variance Calculation Accuracy (Early, On-time, Delayed)
# =========================================================================

def test_variance_calculation_scenarios():
    db = create_test_db()
    # 1. Delayed (+2)
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('ACT-LATE', 'SCH-1', 'Late Activity', 'CIVIL', 'Loc', '2026-08-01', '2026-08-05')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish)
        VALUES ('ACTUAL-LATE', 'D1', 'E1', 'SCH-1', 'ACT-LATE', '2026-08-01', '2026-08-07')
        """
    )

    # 2. Early (-2)
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('ACT-EARLY', 'SCH-1', 'Early Activity', 'CIVIL', 'Loc', '2026-08-01', '2026-08-11')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish)
        VALUES ('ACTUAL-EARLY', 'D2', 'E2', 'SCH-1', 'ACT-EARLY', '2026-08-01', '2026-08-09')
        """
    )

    # 3. On-time (0)
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('ACT-ONTIME', 'SCH-1', 'On-time Activity', 'CIVIL', 'Loc', '2026-08-01', '2026-08-06')
        """
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish)
        VALUES ('ACTUAL-ONTIME', 'D3', 'E3', 'SCH-1', 'ACT-ONTIME', '2026-08-01', '2026-08-06')
        """
    )

    data = query_institutional_memory(conn=db)
    act_map = {a["activity_id"]: a for a in data["activities"]}

    assert act_map["ACT-LATE"]["variance_days"] == 2    # actual 6 - planned 4 = +2
    assert act_map["ACT-EARLY"]["variance_days"] == -2  # actual 8 - planned 10 = -2
    assert act_map["ACT-ONTIME"]["variance_days"] == 0  # actual 5 - planned 5 = 0


# =========================================================================
# 14. Deterministic Ordering (activity_id ASC, schedule_id ASC)
# =========================================================================

def test_deterministic_ordering():
    db = create_test_db()
    # Insert in reverse order
    for act_id in ["ZEB-100", "MID-050", "ACT-001"]:
        db.execute(
            """
            INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
            VALUES (?, 'SCH-1', 'Name', 'CIVIL', 'Loc', '2026-08-01', '2026-08-05')
            """,
            (act_id,),
        )

    data = query_institutional_memory(conn=db)
    ordered_ids = [a["activity_id"] for a in data["activities"]]
    assert ordered_ids == ["ACT-001", "MID-050", "ZEB-100"]
