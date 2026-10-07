import sqlite3
from typing import Any
from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.main import app
from tests.v7ctx import act_as
from backend.routers.schedule import query_impact_preview
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
    conn = sqlite3.connect(":memory:", check_same_thread=False)
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
        CREATE TABLE schedule_dependencies (
            dependency_id TEXT PRIMARY KEY,
            schedule_id TEXT NOT NULL,
            predecessor_activity_id TEXT NOT NULL,
            successor_activity_id TEXT NOT NULL,
            relationship_type TEXT DEFAULT 'FS'
        )
        """
    )
    return SQLitePsycopgAdapter(conn)


# ==============================================================================
# Test 1 — Endpoint Registration
# ==============================================================================

def test_endpoint_is_registered():
    routes = list(app.openapi()["paths"].keys())
    assert "/api/v1/schedule/{activity_id}/impact-preview" in routes


# ==============================================================================
# Test 2 — Basic FS Impact
# ==============================================================================

def test_basic_fs_impact():
    db = create_test_db()
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('A1000', 'SCHED-001', 'Excavation', 'CIVIL', 'Site A', '2026-08-01', '2026-08-10'),
               ('A1001', 'SCHED-001', 'Foundation', 'CIVIL', 'Site A', '2026-08-11', '2026-08-20')
        """
    )
    db.execute(
        """
        INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id, relationship_type)
        VALUES ('DEP-1', 'SCHED-001', 'A1000', 'A1001', 'FS')
        """
    )

    result = query_impact_preview("A1000", delay_days=5, conn=db)
    assert result["activity_id"] == "A1000"
    assert result["delay_days"] == 5
    assert len(result["impacts"]) == 1

    impact = result["impacts"][0]
    assert impact["successor_activity_id"] == "A1001"
    assert impact["dependency_type"] == "FS"
    assert impact["original_earliest_start"] == "2026-08-11"
    assert impact["shifted_earliest_start"] == "2026-08-15"


# ==============================================================================
# Test 3 — Zero Delay
# ==============================================================================

def test_zero_delay():
    db = create_test_db()
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('A1000', 'SCHED-001', 'Excavation', 'CIVIL', 'Site A', '2026-08-01', '2026-08-10'),
               ('A1001', 'SCHED-001', 'Foundation', 'CIVIL', 'Site A', '2026-08-11', '2026-08-20')
        """
    )
    db.execute(
        """
        INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id, relationship_type)
        VALUES ('DEP-1', 'SCHED-001', 'A1000', 'A1001', 'FS')
        """
    )

    result = query_impact_preview("A1000", delay_days=0, conn=db)
    assert result["delay_days"] == 0
    assert len(result["impacts"]) == 1
    impact = result["impacts"][0]
    assert impact["original_earliest_start"] == "2026-08-11"
    assert impact["shifted_earliest_start"] == "2026-08-11"


# ==============================================================================
# Test 4 — Multiple Successors
# ==============================================================================

def test_multiple_successors():
    db = create_test_db()
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('A1000', 'SCHED-001', 'Trenching', 'CIVIL', 'Area 1', '2026-08-01', '2026-08-05'),
               ('A1001', 'SCHED-001', 'Piping Lay', 'PIPING', 'Area 1', '2026-08-06', '2026-08-12'),
               ('A1002', 'SCHED-001', 'Cable Pull', 'ELECTRICAL', 'Area 1', '2026-08-07', '2026-08-14'),
               ('A1003', 'SCHED-001', 'Inspection', 'HSE', 'Area 1', '2026-08-08', '2026-08-09')
        """
    )
    db.execute(
        """
        INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id, relationship_type)
        VALUES ('DEP-1', 'SCHED-001', 'A1000', 'A1001', 'FS'),
               ('DEP-2', 'SCHED-001', 'A1000', 'A1002', 'FS'),
               ('DEP-3', 'SCHED-001', 'A1000', 'A1003', 'FS')
        """
    )

    result = query_impact_preview("A1000", delay_days=3, conn=db)
    assert len(result["impacts"]) == 3
    succ_ids = [imp["successor_activity_id"] for imp in result["impacts"]]
    assert succ_ids == ["A1001", "A1002", "A1003"]

    # Verify shifted dates
    assert result["impacts"][0]["shifted_earliest_start"] == "2026-08-08"
    assert result["impacts"][1]["shifted_earliest_start"] == "2026-08-08"
    assert result["impacts"][2]["shifted_earliest_start"] == "2026-08-08"


# ==============================================================================
# Test 5 — Multiple Relationship Types Supported (A1)
# ==============================================================================

def test_relationship_types_included():
    db = create_test_db()
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('A1000', 'SCHED-001', 'Excavation', 'CIVIL', 'Site A', '2026-08-01', '2026-08-10'),
               ('A1001', 'SCHED-001', 'FS Successor', 'CIVIL', 'Site A', '2026-08-11', '2026-08-20'),
               ('A1002', 'SCHED-001', 'SS Successor', 'CIVIL', 'Site A', '2026-08-05', '2026-08-15'),
               ('A1003', 'SCHED-001', 'FF Successor', 'CIVIL', 'Site A', '2026-08-06', '2026-08-16'),
               ('A1004', 'SCHED-001', 'SF Successor', 'CIVIL', 'Site A', '2026-08-07', '2026-08-17')
        """
    )
    db.execute(
        """
        INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id, relationship_type)
        VALUES ('DEP-1', 'SCHED-001', 'A1000', 'A1001', 'FS'),
               ('DEP-2', 'SCHED-001', 'A1000', 'A1002', 'SS'),
               ('DEP-3', 'SCHED-001', 'A1000', 'A1003', 'FF'),
               ('DEP-4', 'SCHED-001', 'A1000', 'A1004', 'SF')
        """
    )

    result = query_impact_preview("A1000", delay_days=4, conn=db)
    assert len(result["impacts"]) == 4
    rel_types = [imp["dependency_type"] for imp in result["impacts"]]
    assert rel_types == ["FS", "SS", "FF", "SF"]


# ==============================================================================
# Test 6 — One-Level Traversal (No Recursive CPM Propagation)
# ==============================================================================

def test_one_level_traversal_only():
    db = create_test_db()
    # A1000 -> A1001 -> A1002
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('A1000', 'SCHED-001', 'First', 'CIVIL', 'Site A', '2026-08-01', '2026-08-05'),
               ('A1001', 'SCHED-001', 'Second', 'CIVIL', 'Site A', '2026-08-06', '2026-08-10'),
               ('A1002', 'SCHED-001', 'Third', 'CIVIL', 'Site A', '2026-08-11', '2026-08-15')
        """
    )
    db.execute(
        """
        INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id, relationship_type)
        VALUES ('DEP-1', 'SCHED-001', 'A1000', 'A1001', 'FS'),
               ('DEP-2', 'SCHED-001', 'A1001', 'A1002', 'FS')
        """
    )

    result = query_impact_preview("A1000", delay_days=7, conn=db)
    # Must report impact on A1001 only, NOT propagate to A1002
    assert len(result["impacts"]) == 1
    assert result["impacts"][0]["successor_activity_id"] == "A1001"
    reported_ids = [imp["successor_activity_id"] for imp in result["impacts"]]
    assert "A1002" not in reported_ids


# ==============================================================================
# Test 7 — Deterministic Ordering
# ==============================================================================

def test_deterministic_ordering():
    db = create_test_db()
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('A1000', 'SCHED-001', 'Root', 'CIVIL', 'Site A', '2026-08-01', '2026-08-05'),
               ('Z9000', 'SCHED-001', 'Last', 'CIVIL', 'Site A', '2026-08-06', '2026-08-10'),
               ('B2000', 'SCHED-001', 'Middle', 'CIVIL', 'Site A', '2026-08-06', '2026-08-10'),
               ('A1500', 'SCHED-001', 'First', 'CIVIL', 'Site A', '2026-08-06', '2026-08-10')
        """
    )
    # Insert in unsorted order
    db.execute(
        """
        INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id, relationship_type)
        VALUES ('DEP-1', 'SCHED-001', 'A1000', 'Z9000', 'FS'),
               ('DEP-2', 'SCHED-001', 'A1000', 'A1500', 'FS'),
               ('DEP-3', 'SCHED-001', 'A1000', 'B2000', 'FS')
        """
    )

    result = query_impact_preview("A1000", delay_days=2, conn=db)
    succ_ids = [imp["successor_activity_id"] for imp in result["impacts"]]
    assert succ_ids == ["A1500", "B2000", "Z9000"]


# ==============================================================================
# Test 8 — No Successors
# ==============================================================================

def test_no_successors():
    db = create_test_db()
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('A1000', 'SCHED-001', 'Terminal Activity', 'CIVIL', 'Site A', '2026-08-01', '2026-08-10')
        """
    )

    result = query_impact_preview("A1000", delay_days=5, conn=db)
    assert result["activity_id"] == "A1000"
    assert result["delay_days"] == 5
    assert result["impacts"] == []


# ==============================================================================
# Test 9 — Unknown Activity
# ==============================================================================

def test_unknown_activity_raises_404():
    db = create_test_db()
    with pytest.raises(HTTPException) as exc_info:
        query_impact_preview("NONEXISTENT", delay_days=5, conn=db)
    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail.lower()


# ==============================================================================
# Test 10 — Invalid Delay (HTTP Client Validation)
# ==============================================================================

def test_invalid_delay_non_integer(monkeypatch):
    client = TestClient(app)
    with act_as("SUPERVISOR", schedule_id="SCHED-001"):
        response = client.get("/api/v1/schedule/A1000/impact-preview?delay_days=abc")
    assert response.status_code == 422


# ==============================================================================
# Test 11 — Negative Delay
# ==============================================================================

def test_negative_delay_rejected(monkeypatch):
    # Direct function call raises 400
    db = create_test_db()
    with pytest.raises(HTTPException) as exc_info:
        query_impact_preview("A1000", delay_days=-5, conn=db)
    assert exc_info.value.status_code == 400
    assert "non-negative" in exc_info.value.detail.lower()

    # HTTP client query param with ge=0 rejected with 422
    client = TestClient(app)
    with act_as("SUPERVISOR", schedule_id="SCHED-001"):
        response = client.get("/api/v1/schedule/A1000/impact-preview?delay_days=-1")
    assert response.status_code == 422


# ==============================================================================
# Test 12 — Schedule Isolation
# ==============================================================================

def test_schedule_isolation():
    db = create_test_db()
    # Same activity_id 'A1000' in two distinct schedules
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('A1000', 'SCHED-A', 'Activity in Sched A', 'CIVIL', 'Site A', '2026-08-01', '2026-08-10'),
               ('A1001', 'SCHED-A', 'Successor in Sched A', 'CIVIL', 'Site A', '2026-08-11', '2026-08-20'),
               ('A2000', 'SCHED-B', 'Successor in Sched B', 'CIVIL', 'Site B', '2026-08-15', '2026-08-25')
        """
    )
    db.execute(
        """
        INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id, relationship_type)
        VALUES ('DEP-A', 'SCHED-A', 'A1000', 'A1001', 'FS'),
               ('DEP-B', 'SCHED-B', 'A1000', 'A2000', 'FS')
        """
    )

    result = query_impact_preview("A1000", delay_days=2, conn=db)
    # Must only return A1001 from SCHED-A, NOT A2000 from SCHED-B
    assert len(result["impacts"]) == 1
    assert result["impacts"][0]["successor_activity_id"] == "A1001"
    succ_ids = [imp["successor_activity_id"] for imp in result["impacts"]]
    assert "A2000" not in succ_ids


# ==============================================================================
# Test 13 — Database Immutability
# ==============================================================================

def test_database_immutability():
    db = create_test_db()
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('A1000', 'SCHED-001', 'Activity', 'CIVIL', 'Site A', '2026-08-01', '2026-08-10'),
               ('A1001', 'SCHED-001', 'Successor', 'CIVIL', 'Site A', '2026-08-11', '2026-08-20')
        """
    )
    db.execute(
        """
        INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id, relationship_type)
        VALUES ('DEP-1', 'SCHED-001', 'A1000', 'A1001', 'FS')
        """
    )

    # Capture initial DB state
    act_count_before = db.execute("SELECT COUNT(*) AS cnt FROM schedule_activities").fetchone()["cnt"]
    dep_count_before = db.execute("SELECT COUNT(*) AS cnt FROM schedule_dependencies").fetchone()["cnt"]
    succ_date_before = db.execute("SELECT planned_start FROM schedule_activities WHERE activity_id='A1001'").fetchone()["planned_start"]

    # Execute preview with large delay
    query_impact_preview("A1000", delay_days=100, conn=db)

    # Verify no state mutated
    act_count_after = db.execute("SELECT COUNT(*) AS cnt FROM schedule_activities").fetchone()["cnt"]
    dep_count_after = db.execute("SELECT COUNT(*) AS cnt FROM schedule_dependencies").fetchone()["cnt"]
    succ_date_after = db.execute("SELECT planned_start FROM schedule_activities WHERE activity_id='A1001'").fetchone()["planned_start"]

    assert act_count_before == act_count_after == 2
    assert dep_count_before == dep_count_after == 1
    assert succ_date_before == succ_date_after == "2026-08-11"


# ==============================================================================
# Test 14 — Response Contract
# ==============================================================================

def test_response_contract():
    db = create_test_db()
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('CIV-01', 'SCHED-001', 'Trench', 'CIVIL', 'Loc 1', '2026-08-01', '2026-08-05'),
               ('CIV-02', 'SCHED-001', 'Backfill', 'CIVIL', 'Loc 1', '2026-08-06', '2026-08-10')
        """
    )
    db.execute(
        """
        INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id, relationship_type)
        VALUES ('DEP-1', 'SCHED-001', 'CIV-01', 'CIV-02', 'FS')
        """
    )

    result = query_impact_preview("CIV-01", delay_days=4, conn=db)

    # Top-level keys
    assert {"activity_id", "delay_days", "impacts"}.issubset(set(result.keys()))
    assert isinstance(result["activity_id"], str)
    assert isinstance(result["delay_days"], int)
    assert isinstance(result["impacts"], list)

    # Impact item keys
    item = result["impacts"][0]
    assert {
        "successor_activity_id",
        "dependency_type",
        "original_earliest_start",
        "shifted_earliest_start",
    }.issubset(set(item.keys()))
    assert item["successor_activity_id"] == "CIV-02"
    assert item["dependency_type"] == "FS"
    assert item["original_earliest_start"] == "2026-08-06"
    assert item["shifted_earliest_start"] == "2026-08-09"


# ==============================================================================
# Test 15 — Authorization (RBAC)
# ==============================================================================

def test_authorization(monkeypatch):
    client = TestClient(app)

    db = create_test_db()
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('A1000', 'SCHED-001', 'Excavation', 'CIVIL', 'Site A', '2026-08-01', '2026-08-10'),
               ('A1001', 'SCHED-001', 'Foundation', 'CIVIL', 'Site A', '2026-08-11', '2026-08-20')
        """
    )
    db.execute(
        """
        INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id, relationship_type)
        VALUES ('DEP-1', 'SCHED-001', 'A1000', 'A1001', 'FS')
        """
    )

    class MockContextManager:
        def __enter__(self):
            return db

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    monkeypatch.setattr("backend.routers.schedule.get_connection", lambda: MockContextManager())

    # 1. Unauthenticated -> 401
    resp_unauth = client.get("/api/v1/schedule/A1000/impact-preview?delay_days=5")
    assert resp_unauth.status_code == 401

    # 2. Site Engineer (no REVIEW_CLAIM) -> 403
    with act_as("SITE_ENGINEER", schedule_id="SCHED-001"):
        resp_engineer = client.get("/api/v1/schedule/A1000/impact-preview?delay_days=5")
    assert resp_engineer.status_code == 403

    # 3. Supervisor, explicit schedule -> 200
    with act_as("SUPERVISOR", schedule_id="SCHED-001"):
        resp_sup = client.get("/api/v1/schedule/A1000/impact-preview?delay_days=5")
    assert resp_sup.status_code == 200
    data = resp_sup.json()
    assert data["activity_id"] == "A1000"
    assert len(data["impacts"]) == 1

    # 4. No explicit schedule -> 400 (no active/latest fallback)
    with act_as("SUPERVISOR", schedule_id=None):
        assert client.get("/api/v1/schedule/A1000/impact-preview?delay_days=5").status_code == 400


# ==============================================================================
# Test 16 — Source Activity ID Preservation
# ==============================================================================

def test_source_activity_id_preserved():
    db = create_test_db()
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES ('PIP-PS3-WLD-024', 'SCHED-001', 'Piping Weld', 'PIPING', 'Pump Station 3', '2026-08-11', '2026-08-15'),
               ('PIP-PS3-TST-001', 'SCHED-001', 'Hydrotest', 'PIPING', 'Pump Station 3', '2026-08-16', '2026-08-18')
        """
    )
    db.execute(
        """
        INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id, relationship_type)
        VALUES ('DEP-1', 'SCHED-001', 'PIP-PS3-WLD-024', 'PIP-PS3-TST-001', 'FS')
        """
    )

    result = query_impact_preview("PIP-PS3-WLD-024", delay_days=2, conn=db)
    assert result["activity_id"] == "PIP-PS3-WLD-024"
    assert result["impacts"][0]["successor_activity_id"] == "PIP-PS3-TST-001"
