import sqlite3
from datetime import date
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.routers.export import PMISAdapter
from backend.routers.mock_p6 import clear_received_payloads, get_received_payloads
from backend.shared.actuals import upsert_approved_actual
from backend.shared.p6 import (
    P6RestAdapter,
    get_default_p6_adapter,
    set_default_p6_adapter,
    trigger_p6_actual_push,
)


class SQLitePsycopgAdapter:
    """
    Lightweight DB connection adapter for deterministic in-memory testing.
    Translates Postgres-specific query patterns (%s, FOR UPDATE) to SQLite syntax.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.commit_count = 0

    def execute(self, query: str, params=None):
        clean_q = query.replace("FOR UPDATE", "")
        clean_q = clean_q.replace("%s", "?")
        cur = self.conn.cursor()
        if params is not None:
            cur.execute(clean_q, params)
        else:
            cur.execute(clean_q)
        return cur

    def commit(self):
        self.commit_count += 1
        self.conn.commit()


def create_test_db() -> SQLitePsycopgAdapter:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row

    conn.execute(
        """
        CREATE TABLE execution_events (
            event_id TEXT PRIMARY KEY,
            schedule_id TEXT NOT NULL,
            event_date TEXT,
            claim_mode TEXT DEFAULT 'CUMULATIVE_PCT',
            event_type TEXT,
            claimed_quantity REAL,
            claimed_pct REAL,
            matched_activity_id TEXT
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
            created_at TEXT,
            UNIQUE (schedule_id, activity_id)
        )
        """
    )
    return SQLitePsycopgAdapter(conn)


# ==============================================================================
# Test 1 — PMISAdapter Inheritance
# ==============================================================================

@pytest.fixture(autouse=True)
def _mock_p6_needs_dev_mode(monkeypatch):
    """The mock P6 routes exist only in development mode (AUTH_DEV_MODE=true). Tests opt in explicitly."""
    monkeypatch.setenv("AUTH_DEV_MODE", "true")


def test_mock_p6_is_hidden_outside_dev_mode(monkeypatch):
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    client = TestClient(app)
    assert client.get("/api/v1/mock-p6/health").status_code == 404
    assert client.get("/api/v1/mock-p6/received").status_code == 404
    resp = client.post("/api/v1/mock-p6/activities/A1", json={"Id": "A1", "PercentComplete": 10})
    assert resp.status_code == 404
    monkeypatch.delenv("AUTH_DEV_MODE")
    assert client.get("/api/v1/mock-p6/health").status_code == 404, "unset must default to closed"


def test_pmis_adapter_inheritance():
    adapter = P6RestAdapter()
    assert isinstance(adapter, PMISAdapter)
    assert hasattr(adapter, "push_actual")
    assert callable(adapter.push_actual)


# ==============================================================================
# Test 2 — Canonical P6 Payload Format
# ==============================================================================

def test_p6_payload_format():
    adapter = P6RestAdapter()
    payload = adapter.format_payload(
        activity_id="A1000",
        actual_start=date(2026, 9, 5),
        actual_finish=date(2026, 9, 8),
        actual_pct_complete=75.0,
        actual_quantity=150.0,
    )

    assert set(payload.keys()) == {"Id", "StartDate", "FinishDate", "PercentComplete"}
    assert payload["Id"] == "A1000"
    assert payload["StartDate"] == "2026-09-05"
    assert payload["FinishDate"] == "2026-09-08"
    assert payload["PercentComplete"] == 75.0


# ==============================================================================
# Test 3 — Activity ID Mapping Verbatim
# ==============================================================================

def test_id_mapping_verbatim():
    adapter = P6RestAdapter()
    raw_ids = ["A1000", "CIV-01", "PIP-PS3-WLD-024", "12345"]
    for raw_id in raw_ids:
        payload = adapter.format_payload(activity_id=raw_id)
        assert payload["Id"] == raw_id


# ==============================================================================
# Test 4 — Date Formatting
# ==============================================================================

def test_date_formatting():
    adapter = P6RestAdapter()
    # From datetime.date
    p1 = adapter.format_payload(activity_id="A1", actual_start=date(2026, 8, 1), actual_finish=date(2026, 8, 10))
    assert p1["StartDate"] == "2026-08-01"
    assert p1["FinishDate"] == "2026-08-10"

    # From string
    p2 = adapter.format_payload(activity_id="A2", actual_start="2026-08-01T00:00:00Z", actual_finish="2026-08-10")
    assert p2["StartDate"] == "2026-08-01"
    assert p2["FinishDate"] == "2026-08-10"


# ==============================================================================
# Test 5 — Quantity is Dropped
# ==============================================================================

def test_quantity_dropped():
    adapter = P6RestAdapter()
    payload = adapter.format_payload(
        activity_id="A1000",
        actual_quantity=500.5,
        actual_pct_complete=50.0,
    )
    assert "actual_quantity" not in payload
    assert "quantity" not in payload
    assert "Quantity" not in payload


# ==============================================================================
# Test 6 — No ActualDuration
# ==============================================================================

def test_no_actual_duration():
    adapter = P6RestAdapter()
    payload = adapter.format_payload(
        activity_id="A1000",
        actual_start=date(2026, 9, 1),
        actual_finish=date(2026, 9, 10),
    )
    assert "ActualDuration" not in payload
    assert "actual_duration" not in payload
    assert "duration" not in payload


# ==============================================================================
# Test 7 — Missing Optional Dates Handled Safely
# ==============================================================================

def test_missing_dates_handled_safely():
    adapter = P6RestAdapter()
    # Only start date
    p_start_only = adapter.format_payload(activity_id="A1000", actual_start=date(2026, 9, 1))
    assert "StartDate" in p_start_only
    assert "FinishDate" not in p_start_only

    # Only finish date
    p_finish_only = adapter.format_payload(activity_id="A1000", actual_finish=date(2026, 9, 5))
    assert "StartDate" not in p_finish_only
    assert "FinishDate" in p_finish_only

    # Neither date
    p_no_dates = adapter.format_payload(activity_id="A1000", actual_pct_complete=10.0)
    assert "StartDate" not in p_no_dates
    assert "FinishDate" not in p_no_dates


# ==============================================================================
# Test 8 — Mock P6 Endpoint Registration
# ==============================================================================

def test_mock_p6_endpoint_registration():
    routes = list(app.openapi()["paths"].keys())
    assert "/api/v1/mock-p6/activities/{activity_id}" in routes


# ==============================================================================
# Test 9 — Mock P6 Success Response
# ==============================================================================

def test_mock_p6_success_response():
    client = TestClient(app)
    clear_received_payloads()

    payload = {
        "Id": "A1000",
        "StartDate": "2026-09-05",
        "FinishDate": "2026-09-08",
        "PercentComplete": 75.0,
    }
    resp = client.post("/api/v1/mock-p6/activities/A1000", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["activity_id"] == "A1000"
    assert data["p6_id"] == "A1000"

    received = get_received_payloads()
    assert len(received) == 1
    assert received[0]["Id"] == "A1000"
    assert received[0]["PercentComplete"] == 75.0


# ==============================================================================
# Test 10 — Mock P6 Activity ID Mismatch Rejection
# ==============================================================================

def test_mock_p6_id_mismatch_rejected():
    client = TestClient(app)
    payload = {
        "Id": "MISMATCH-ID",
        "PercentComplete": 50.0,
    }
    resp = client.post("/api/v1/mock-p6/activities/A1000", json=payload)
    assert resp.status_code == 400
    assert "does not match" in resp.json()["detail"].lower()


# ==============================================================================
# Test 11 — End-to-End P6RestAdapter Push to Mock
# ==============================================================================

def test_p6_adapter_push_to_mock():
    client = TestClient(app)
    clear_received_payloads()

    adapter = P6RestAdapter(
        base_url="http://testserver/api/v1/mock-p6",
        http_client=client,
    )

    success = adapter.push_actual(
        activity_id="PIP-01",
        actual_start=date(2026, 9, 1),
        actual_finish=date(2026, 9, 5),
        actual_pct_complete=100.0,
    )
    assert success is True

    received = get_received_payloads()
    assert len(received) == 1
    assert received[0]["Id"] == "PIP-01"
    assert received[0]["StartDate"] == "2026-09-01"
    assert received[0]["FinishDate"] == "2026-09-05"
    assert received[0]["PercentComplete"] == 100.0


# ==============================================================================
# Test 12 — Option A: P6_BASE_URL Unset Behavior
# ==============================================================================

def test_p6_base_url_unset_behavior():
    with patch.dict("os.environ", {}, clear=True):
        adapter = P6RestAdapter(base_url=None)
        # Without base_url, push_actual must return False without throwing
        result = adapter.push_actual(
            activity_id="A1000",
            actual_start=date(2026, 9, 1),
            actual_pct_complete=50.0,
        )
        assert result is False


# ==============================================================================
# Test 13 — Network Error Handled Non-Blockingly
# ==============================================================================

def test_network_error_handled_non_blockingly():
    mock_client = MagicMock()
    mock_client.post.side_effect = Exception("Connection refused / Timeout")

    adapter = P6RestAdapter(
        base_url="https://unreachable.oracle-p6.com/api",
        http_client=mock_client,
    )

    result = adapter.push_actual(
        activity_id="A1000",
        actual_start=date(2026, 9, 1),
    )
    assert result is False


# ==============================================================================
# Test 14 — Post-Commit Execution Ordering
# ==============================================================================

def test_post_commit_p6_trigger():
    db = create_test_db()
    sch_id = "SCH-P6-ORDER"
    act_id = "ACT-P6-1"

    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, claim_mode, claimed_pct, matched_activity_id)
        VALUES ('EV-P6-1', ?, '2026-09-01', 'CUMULATIVE_PCT', 60.0, ?)
        """,
        (sch_id, act_id),
    )
    db.execute(
        """
        INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, approved_pct, planner_id, justification, decided_at)
        VALUES ('DEC-P6-1', 'EV-P6-1', ?, 'APPROVE', 60.0, 'PLN-1', 'Approved', '2026-09-01T10:00:00Z')
        """,
        (act_id,),
    )

    mock_adapter = MagicMock(spec=P6RestAdapter)
    mock_adapter.push_actual.return_value = True

    with patch("backend.shared.p6.get_default_p6_adapter", return_value=mock_adapter):
        upsert_approved_actual(
            schedule_id=sch_id,
            activity_id=act_id,
            event_id="EV-P6-1",
            decision_id="DEC-P6-1",
            conn=db,
        )

    # DB commit must have occurred
    assert db.commit_count >= 1
    # P6 adapter push_actual was called
    assert mock_adapter.push_actual.called
    call_args = mock_adapter.push_actual.call_args[1]
    assert call_args["activity_id"] == act_id


# ==============================================================================
# Test 15 — Adapter Failure Does Not Rollback Approved Actuals
# ==============================================================================

def test_p6_adapter_failure_does_not_rollback_actuals():
    db = create_test_db()
    sch_id = "SCH-P6-FAIL"
    act_id = "ACT-P6-FAIL"

    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, claim_mode, claimed_pct, matched_activity_id)
        VALUES ('EV-P6-FAIL', ?, '2026-09-01', 'CUMULATIVE_PCT', 80.0, ?)
        """,
        (sch_id, act_id),
    )
    db.execute(
        """
        INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, approved_pct, planner_id, justification, decided_at)
        VALUES ('DEC-P6-FAIL', 'EV-P6-FAIL', ?, 'APPROVE', 80.0, 'PLN-1', 'Approved', '2026-09-01T10:00:00Z')
        """,
        (act_id,),
    )

    failing_adapter = MagicMock(spec=P6RestAdapter)
    failing_adapter.push_actual.side_effect = RuntimeError("Oracle P6 EPPM 500 Internal Server Error")

    with patch("backend.shared.p6.get_default_p6_adapter", return_value=failing_adapter):
        result = upsert_approved_actual(
            schedule_id=sch_id,
            activity_id=act_id,
            event_id="EV-P6-FAIL",
            decision_id="DEC-P6-FAIL",
            conn=db,
        )

    # Upsert returned success
    assert result is not None
    assert result["activity_id"] == act_id
    assert result["actual_pct_complete"] == 80.0

    # Database row was committed and remains persistent
    row = db.execute(
        "SELECT * FROM approved_actuals WHERE schedule_id = ? AND activity_id = ?",
        (sch_id, act_id),
    ).fetchone()
    assert row is not None
    assert row["actual_pct_complete"] == 80.0
