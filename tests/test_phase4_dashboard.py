import sqlite3
from typing import Any

from fastapi.testclient import TestClient

from backend.main import app
from tests.v7ctx import PROJECT_ID, act_as, fake_schedules
from backend.routers.dashboard import query_delay_reason_aggregates
from backend.shared.auth import UserProfile, get_current_user


class SQLitePsycopgAdapter:
    """
    Lightweight DB connection adapter for deterministic in-memory testing.
    Translates Postgres query patterns to SQLite.
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
            schedule_id TEXT NOT NULL,
            event_date TEXT,
            claim_mode TEXT DEFAULT 'CUMULATIVE_PCT',
            event_type TEXT,
            claimed_quantity REAL,
            claimed_pct REAL,
            delay_reason TEXT,
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
            decided_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    return SQLitePsycopgAdapter(conn)


# =========================================================================
# 1. Endpoint Registration
# =========================================================================

def test_endpoint_is_registered():
    routes = list(app.openapi()["paths"].keys())
    assert "/api/v1/dashboard/delay-reasons" in routes, "GET /api/v1/dashboard/delay-reasons not registered"
    assert "/api/v1/dashboard/health" in routes, "GET /api/v1/dashboard/health not registered"


# =========================================================================
# 2. Approved Claim with Delay Reason is Counted
# =========================================================================

def test_approved_claim_counted():
    db = create_test_db()
    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, delay_reason) VALUES (?, ?, ?)",
        ("EV-1", "SCH-1", "MATERIAL"),
    )
    db.execute(
        "INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, decided_at) VALUES (?, ?, ?, ?, ?)",
        ("DEC-1", "EV-1", "ACT-1", "APPROVE", "2026-08-01 10:00:00"),
    )

    data = query_delay_reason_aggregates(conn=db)
    assert data["total_approved_delay_claims"] == 1
    assert len(data["delay_reasons"]) == 1
    assert data["delay_reasons"][0] == {"delay_reason": "MATERIAL", "count": 1}


# =========================================================================
# 3. EDIT Claim is Counted
# =========================================================================

def test_edit_claim_counted():
    db = create_test_db()
    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, delay_reason) VALUES (?, ?, ?)",
        ("EV-2", "SCH-1", "WEATHER"),
    )
    db.execute(
        "INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, decided_at) VALUES (?, ?, ?, ?, ?)",
        ("DEC-2", "EV-2", "ACT-1", "EDIT", "2026-08-02 10:00:00"),
    )

    data = query_delay_reason_aggregates(conn=db)
    assert data["total_approved_delay_claims"] == 1
    assert len(data["delay_reasons"]) == 1
    assert data["delay_reasons"][0] == {"delay_reason": "WEATHER", "count": 1}


# =========================================================================
# 4. Latest REJECT Removes a Previously Approved Claim
# =========================================================================

def test_latest_reject_removes_claim():
    db = create_test_db()
    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, delay_reason) VALUES (?, ?, ?)",
        ("EV-3", "SCH-1", "EQUIPMENT"),
    )
    # Earlier APPROVE
    db.execute(
        "INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, decided_at) VALUES (?, ?, ?, ?, ?)",
        ("DEC-3A", "EV-3", "ACT-1", "APPROVE", "2026-08-01 10:00:00"),
    )
    # Later REJECT
    db.execute(
        "INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, decided_at) VALUES (?, ?, ?, ?, ?)",
        ("DEC-3B", "EV-3", "ACT-1", "REJECT", "2026-08-03 10:00:00"),
    )

    data = query_delay_reason_aggregates(conn=db)
    # Since latest decision is REJECT, claim must not be counted
    assert data["total_approved_delay_claims"] == 0
    assert data["delay_reasons"] == []


# =========================================================================
# 5. HOLD / Latest Non-approved Decision Does Not Contribute
# =========================================================================

def test_hold_and_non_approved_decisions_excluded():
    db = create_test_db()
    # Event with HOLD decision
    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, delay_reason) VALUES (?, ?, ?)",
        ("EV-4", "SCH-1", "LABOUR"),
    )
    db.execute(
        "INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, decided_at) VALUES (?, ?, ?, ?, ?)",
        ("DEC-4", "EV-4", "ACT-1", "HOLD", "2026-08-01 10:00:00"),
    )

    # Event with no decision at all
    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, delay_reason) VALUES (?, ?, ?)",
        ("EV-5", "SCH-1", "ACCESS"),
    )

    data = query_delay_reason_aggregates(conn=db)
    assert data["total_approved_delay_claims"] == 0
    assert data["delay_reasons"] == []


# =========================================================================
# 6. NULL / Blank Delay Reason is Excluded
# =========================================================================

def test_null_and_blank_delay_reasons_excluded():
    db = create_test_db()
    # Event with NULL delay_reason
    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, delay_reason) VALUES (?, ?, ?)",
        ("EV-NULL", "SCH-1", None),
    )
    db.execute(
        "INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, decided_at) VALUES (?, ?, ?, ?, ?)",
        ("DEC-N", "EV-NULL", "ACT-1", "APPROVE", "2026-08-01 10:00:00"),
    )

    # Event with empty string delay_reason
    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, delay_reason) VALUES (?, ?, ?)",
        ("EV-EMPTY", "SCH-1", ""),
    )
    db.execute(
        "INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, decided_at) VALUES (?, ?, ?, ?, ?)",
        ("DEC-E", "EV-EMPTY", "ACT-1", "APPROVE", "2026-08-01 10:00:00"),
    )

    # Event with whitespace-only delay_reason
    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, delay_reason) VALUES (?, ?, ?)",
        ("EV-SPACE", "SCH-1", "   "),
    )
    db.execute(
        "INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, decided_at) VALUES (?, ?, ?, ?, ?)",
        ("DEC-S", "EV-SPACE", "ACT-1", "APPROVE", "2026-08-01 10:00:00"),
    )

    data = query_delay_reason_aggregates(conn=db)
    assert data["total_approved_delay_claims"] == 0
    assert data["delay_reasons"] == []


# =========================================================================
# 7. Multiple Claims with the Same Delay Reason are Grouped
# =========================================================================

def test_multiple_claims_same_reason_grouped():
    db = create_test_db()
    for i in range(3):
        ev_id = f"EV-MAT-{i}"
        dec_id = f"DEC-MAT-{i}"
        db.execute(
            "INSERT INTO execution_events (event_id, schedule_id, delay_reason) VALUES (?, ?, ?)",
            (ev_id, "SCH-1", "MATERIAL"),
        )
        db.execute(
            "INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, decided_at) VALUES (?, ?, ?, ?, ?)",
            (dec_id, ev_id, "ACT-1", "APPROVE", "2026-08-01 10:00:00"),
        )

    data = query_delay_reason_aggregates(conn=db)
    assert data["total_approved_delay_claims"] == 3
    assert len(data["delay_reasons"]) == 1
    assert data["delay_reasons"][0] == {"delay_reason": "MATERIAL", "count": 3}


# =========================================================================
# 8. Deterministic Ordering (count DESC, delay_reason ASC)
# =========================================================================

def test_deterministic_ordering():
    db = create_test_db()
    # 3 claims for MATERIAL
    # 2 claims for WEATHER
    # 2 claims for ACCESS (tied with WEATHER -> ACCESS comes before WEATHER alphabetically)
    # 1 claim for REWORK
    claims = [
        ("EV-M1", "MATERIAL"),
        ("EV-M2", "MATERIAL"),
        ("EV-M3", "MATERIAL"),
        ("EV-W1", "WEATHER"),
        ("EV-W2", "WEATHER"),
        ("EV-A1", "ACCESS"),
        ("EV-A2", "ACCESS"),
        ("EV-R1", "REWORK"),
    ]
    for ev_id, reason in claims:
        db.execute(
            "INSERT INTO execution_events (event_id, schedule_id, delay_reason) VALUES (?, ?, ?)",
            (ev_id, "SCH-1", reason),
        )
        db.execute(
            "INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, decided_at) VALUES (?, ?, ?, ?, ?)",
            (f"DEC-{ev_id}", ev_id, "ACT-1", "APPROVE", "2026-08-01 10:00:00"),
        )

    data = query_delay_reason_aggregates(conn=db)
    assert data["total_approved_delay_claims"] == 8

    reasons = [item["delay_reason"] for item in data["delay_reasons"]]
    counts = [item["count"] for item in data["delay_reasons"]]

    assert reasons == ["MATERIAL", "ACCESS", "WEATHER", "REWORK"]
    assert counts == [3, 2, 2, 1]


# =========================================================================
# 9. SUPERVISOR Access Succeeds
# =========================================================================

def test_supervisor_access_succeeds(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr(
        "backend.routers.dashboard.query_delay_reason_aggregates",
        lambda conn=None, schedule_id=None: {
            "delay_reasons": [
                {"delay_reason": "MATERIAL", "count": 2},
                {"delay_reason": "WEATHER", "count": 1},
            ],
            "total_approved_delay_claims": 3,
        },
    )

    with act_as("SUPERVISOR"):
        resp = client.get("/api/v1/dashboard/delay-reasons", headers={"Authorization": "Bearer mocked-sup-token"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_approved_delay_claims"] == 3
    assert len(body["delay_reasons"]) == 2


# =========================================================================
# 10. SITE_ENGINEER Receives 403
# =========================================================================

def test_site_engineer_forbidden():
    """A SITE_ENGINEER (no REVIEW_CLAIM permission in the project) cannot read review dashboards."""
    client = TestClient(app)
    with act_as("SITE_ENGINEER"):
        resp = client.get("/api/v1/dashboard/delay-reasons", headers={"Authorization": "Bearer mocked-eng-token"})
    assert resp.status_code == 403
    assert resp.json()["detail"]["error_code"] == "PERMISSION_DENIED"


# =========================================================================
# 11. Unauthenticated Access is Rejected
# =========================================================================

def test_unauthenticated_rejected():
    client = TestClient(app)
    # No header
    resp = client.get("/api/v1/dashboard/delay-reasons")
    assert resp.status_code == 401

    # Invalid token
    resp_invalid = client.get(
        "/api/v1/dashboard/delay-reasons",
        headers={"Authorization": "Bearer invalid.token"},
    )
    assert resp_invalid.status_code == 401


# =========================================================================
# 12. Response Shape Matches Contract
# =========================================================================

def test_response_shape_matches_contract():
    db = create_test_db()
    data = query_delay_reason_aggregates(conn=db)
    assert isinstance(data, dict)
    assert set(data.keys()) == {"delay_reasons", "total_approved_delay_claims"}
    assert isinstance(data["delay_reasons"], list)
    assert isinstance(data["total_approved_delay_claims"], int)
    assert data["total_approved_delay_claims"] == 0
    assert data["delay_reasons"] == []


# =========================================================================
# 13. Active-schedule resolution (ISS-02 / ISS-05): no query param resolves
#     the one canonical active schedule; an explicit schedule_id is
#     validated; neither ever silently falls back to a stale/guessed id.
# =========================================================================

_STUB_SUMMARY = {
    "total_claims": 0, "pending_review": 0, "actuals": 0, "conflicts": 0,
    "discipline_breakdown": [], "claims_trend_pct": None,
}


def test_summary_requires_explicit_schedule_no_active_fallback():
    """No schedule_id -> 400 INVALID_SCHEDULE_CONTEXT. There is no 'active schedule' fallback any more."""
    client = TestClient(app)
    with act_as("SUPERVISOR", schedule_id=None):
        resp = client.get("/api/v1/dashboard/summary", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["error_code"] == "INVALID_SCHEDULE_CONTEXT"


def test_summary_404_for_unknown_explicit_schedule_id(monkeypatch):
    """An explicit but nonexistent schedule_id must 404, never silently fall back to another schedule."""
    client = TestClient(app)
    fake_schedules(monkeypatch, {})
    with act_as("SUPERVISOR", schedule_id=None):
        resp = client.get("/api/v1/dashboard/summary?schedule_id=sched-OIL-2026", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 404


def test_summary_rejects_a_schedule_of_another_project(monkeypatch):
    client = TestClient(app)
    fake_schedules(monkeypatch, {"SCHED_OTHER": "99999999-9999-4999-8999-999999999999", "SCHED_LEGACY": None})
    with act_as("SUPERVISOR", schedule_id=None):
        for sid in ("SCHED_OTHER", "SCHED_LEGACY"):
            resp = client.get(f"/api/v1/dashboard/summary?schedule_id={sid}", headers={"Authorization": "Bearer t"})
            assert resp.status_code == 403, sid
            assert resp.json()["detail"]["error_code"] == "SCHEDULE_ACCESS_DENIED"


def test_summary_uses_valid_explicit_schedule_id(monkeypatch):
    client = TestClient(app)
    captured = {}
    fake_schedules(monkeypatch, {"SCHED_REAL": str(PROJECT_ID)})
    monkeypatch.setattr(
        "backend.routers.dashboard.query_dashboard_summary",
        lambda conn=None, schedule_id=None: captured.setdefault("schedule_id", schedule_id) and _STUB_SUMMARY,
    )
    with act_as("SUPERVISOR", schedule_id=None):
        resp = client.get("/api/v1/dashboard/summary?schedule_id=SCHED_REAL", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 200
    assert captured["schedule_id"] == "SCHED_REAL"


def test_summary_conflicting_schedule_headers_rejected(monkeypatch):
    client = TestClient(app)
    fake_schedules(monkeypatch, {"A": str(PROJECT_ID), "B": str(PROJECT_ID)})
    with act_as("SUPERVISOR", schedule_id=None):
        resp = client.get("/api/v1/dashboard/summary?schedule_id=A", headers={"X-Schedule-ID": "B"})
    assert resp.status_code == 400
