import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.main import app
from tests.v7ctx import PROJECT_ID, act_as, fake_schedules
from backend.routers.dashboard import query_dashboard_summary
from backend.routers.graph import build_activity_graph
from backend.routers.schedule import query_impact_preview
from backend.shared.auth import UserProfile, get_current_user
from tests.test_phase5_knowledge_graph import SQLitePsycopgAdapter, create_test_db


def setup_overlapping_schedules():
    """
    Create two independent schedules (SCHED_A and SCHED_B) with deliberately
    identical/reused activity IDs (ACT-001) but distinct graph structures and data.
    """
    db = create_test_db()

    # --- SCHEDULE A ---
    # ACT-001 -> ACT-002 (FS, 0 lag)
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish, total_float, is_critical)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("ACT-001", "SCHED_A", "Excavation Sched A", "CIVIL", "Zone A", "2026-09-01", "2026-09-10", 0.0, 1),
    )
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish, total_float, is_critical)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("ACT-002", "SCHED_A", "Foundation Sched A", "CIVIL", "Zone A", "2026-09-11", "2026-09-20", 0.0, 1),
    )
    db.execute(
        """
        INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id, relationship_type, lag_days)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("DEP-A1", "SCHED_A", "ACT-001", "ACT-002", "FS", 0.0),
    )
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("EVT-A1", "SCHED_A", "2026-09-02", "Claim A1", "TYPED_TEXT", "ACT-001", "APPROVED"),
    )
    db.execute(
        """
        INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, approved_pct, planner_id, justification)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("DEC-A1", "EVT-A1", "ACT-001", "APPROVE", 50.0, "PLN-A", "Justification A1"),
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_pct_complete)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("ACTL-A1", "DEC-A1", "EVT-A1", "SCHED_A", "ACT-001", "2026-09-02", 50.0),
    )

    # --- SCHEDULE B ---
    # ACT-001 -> ACT-003 (FS, 2 lag)
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish, total_float, is_critical)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("ACT-001", "SCHED_B", "Welding Sched B", "PIPING", "Zone B", "2026-09-01", "2026-09-10", 5.0, 0),
    )
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish, total_float, is_critical)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("ACT-003", "SCHED_B", "Hydrotest Sched B", "PIPING", "Zone B", "2026-09-13", "2026-09-25", 5.0, 0),
    )
    db.execute(
        """
        INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id, relationship_type, lag_days)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("DEP-B1", "SCHED_B", "ACT-001", "ACT-003", "FS", 2.0),
    )
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("EVT-B1", "SCHED_B", "2026-09-03", "Claim B1", "TYPED_TEXT", "ACT-001", "APPROVED"),
    )
    db.execute(
        """
        INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, approved_pct, planner_id, justification)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("DEC-B1", "EVT-B1", "ACT-001", "APPROVE", 30.0, "PLN-B", "Justification B1"),
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_pct_complete)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("ACTL-B1", "DEC-B1", "EVT-B1", "SCHED_B", "ACT-001", "2026-09-03", 30.0),
    )

    return db


def test_graph_isolation_sched_a():
    """Graph query for Schedule A contains ONLY Schedule A nodes/edges, zero Schedule B records."""
    db = setup_overlapping_schedules()
    graph_a = build_activity_graph("ACT-001", depth=1, schedule_id="SCHED_A", conn=db)

    node_ids = {n["id"] for n in graph_a["nodes"]}
    assert "activity:SCHED_A:ACT-001" in node_ids
    assert "activity:SCHED_A:ACT-002" in node_ids
    assert "event:EVT-A1" in node_ids
    assert "decision:DEC-A1" in node_ids
    assert "actual:ACTL-A1" in node_ids

    # Zero Schedule B leakage
    assert "activity:SCHED_B:ACT-001" not in node_ids
    assert "activity:SCHED_B:ACT-003" not in node_ids
    assert "event:EVT-B1" not in node_ids
    assert "decision:DEC-B1" not in node_ids
    assert "actual:ACTL-B1" not in node_ids

    # Edge checks with explicit edge IDs
    edges = graph_a["edges"]
    assert all("SCHED_B" not in e["source"] and "SCHED_B" not in e["target"] for e in edges)
    assert any(e["id"] == "dependency:activity:SCHED_A:ACT-001->activity:SCHED_A:ACT-002" for e in edges)


def test_graph_isolation_sched_b():
    """Graph query for Schedule B contains ONLY Schedule B nodes/edges, zero Schedule A records."""
    db = setup_overlapping_schedules()
    graph_b = build_activity_graph("ACT-001", depth=1, schedule_id="SCHED_B", conn=db)

    node_ids = {n["id"] for n in graph_b["nodes"]}
    assert "activity:SCHED_B:ACT-001" in node_ids
    assert "activity:SCHED_B:ACT-003" in node_ids
    assert "event:EVT-B1" in node_ids
    assert "decision:DEC-B1" in node_ids
    assert "actual:ACTL-B1" in node_ids

    # Zero Schedule A leakage
    assert "activity:SCHED_A:ACT-001" not in node_ids
    assert "activity:SCHED_A:ACT-002" not in node_ids
    assert "event:EVT-A1" not in node_ids
    assert "decision:DEC-A1" not in node_ids
    assert "actual:ACTL-A1" not in node_ids


def test_graph_ambiguity_raises_409():
    """Querying graph for an ambiguous activity ID without schedule_id raises HTTP 409 Conflict."""
    db = setup_overlapping_schedules()
    with pytest.raises(HTTPException) as exc_info:
        build_activity_graph("ACT-001", depth=1, schedule_id=None, conn=db)
    assert exc_info.value.status_code == 409
    assert "exists in multiple schedules" in exc_info.value.detail


def test_impact_isolation_sched_a():
    """Impact preview for Schedule A propagates to ACT-002 only, completely isolated from Schedule B."""
    db = setup_overlapping_schedules()
    impact_a = query_impact_preview("ACT-001", delay_days=5, schedule_id="SCHED_A", conn=db)

    assert impact_a["schedule_id"] == "SCHED_A"
    assert len(impact_a["impacts"]) == 1
    succ_impact = impact_a["impacts"][0]
    assert succ_impact["successor_activity_id"] == "ACT-002"
    # Zero influence from Schedule B's ACT-003
    assert all(i["successor_activity_id"] != "ACT-003" for i in impact_a["impacts"])


def test_impact_isolation_sched_b():
    """Impact preview for Schedule B propagates to ACT-003 only, completely isolated from Schedule A."""
    db = setup_overlapping_schedules()
    impact_b = query_impact_preview("ACT-001", delay_days=5, schedule_id="SCHED_B", conn=db)

    assert impact_b["schedule_id"] == "SCHED_B"
    assert len(impact_b["impacts"]) == 1
    succ_impact = impact_b["impacts"][0]
    assert succ_impact["successor_activity_id"] == "ACT-003"
    # Zero influence from Schedule A's ACT-002
    assert all(i["successor_activity_id"] != "ACT-002" for i in impact_b["impacts"])


def test_impact_ambiguity_raises_409():
    """Querying impact preview for an ambiguous activity ID without schedule_id raises HTTP 409 Conflict."""
    db = setup_overlapping_schedules()
    with pytest.raises(HTTPException) as exc_info:
        query_impact_preview("ACT-001", delay_days=5, schedule_id=None, conn=db)
    assert exc_info.value.status_code == 409
    assert "exists in multiple schedules" in exc_info.value.detail


# ── Dashboard isolation (ISS-05) ────────────────────────────────────────────

def test_dashboard_summary_isolation_sched_a():
    """Dashboard summary scoped to Schedule A counts only Schedule A's claims/actuals/disciplines."""
    db = setup_overlapping_schedules()
    summary_a = query_dashboard_summary(conn=db, schedule_id="SCHED_A")

    assert summary_a["total_claims"] == 1
    assert summary_a["actuals"] == 1
    disciplines = {d["discipline"] for d in summary_a["discipline_breakdown"]}
    assert disciplines == {"CIVIL"}
    civil_count = next(d["count"] for d in summary_a["discipline_breakdown"] if d["discipline"] == "CIVIL")
    assert civil_count == 2  # ACT-001 + ACT-002, both Schedule A
    assert "PIPING" not in disciplines


def test_dashboard_summary_isolation_sched_b():
    """Dashboard summary scoped to Schedule B counts only Schedule B's claims/actuals/disciplines."""
    db = setup_overlapping_schedules()
    summary_b = query_dashboard_summary(conn=db, schedule_id="SCHED_B")

    assert summary_b["total_claims"] == 1
    assert summary_b["actuals"] == 1
    disciplines = {d["discipline"] for d in summary_b["discipline_breakdown"]}
    assert disciplines == {"PIPING"}
    piping_count = next(d["count"] for d in summary_b["discipline_breakdown"] if d["discipline"] == "PIPING")
    assert piping_count == 2  # ACT-001 + ACT-003, both Schedule B
    assert "CIVIL" not in disciplines


def test_dashboard_summary_unscoped_combines_both_schedules():
    """
    Documents the pre-existing unscoped behavior (no schedule_id argument at
    all): callers that don't pass schedule_id get every schedule's data
    combined. The live /summary and /delay-reasons endpoints never call the
    function this way -- they always resolve and pass a schedule_id -- this
    guards against a future regression reintroducing an unscoped call path.
    """
    db = setup_overlapping_schedules()
    combined = query_dashboard_summary(conn=db)
    assert combined["total_claims"] == 2
    assert combined["actuals"] == 2


def _insert_event(db, *, event_id, schedule_id, event_date, matched_activity_id="ACT-001"):
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (event_id, schedule_id, event_date, f"Claim {event_id}", "TYPED_TEXT", matched_activity_id, "EXTRACTED"),
    )


def test_dashboard_claims_trend_isolated_per_schedule():
    """
    Week-over-week claims_trend_pct (ISS-11) is computed only from the
    requested schedule's events -- a spike in Schedule B's current week must
    not shift Schedule A's trend, and vice versa.
    """
    db = create_test_db()
    today = date.today()
    this_week = (today - timedelta(days=2)).isoformat()
    last_week = (today - timedelta(days=9)).isoformat()

    # Schedule A: 1 claim last week, 2 this week -> +100%
    _insert_event(db, event_id="A-LAST", schedule_id="SCHED_A", event_date=last_week)
    _insert_event(db, event_id="A-NOW-1", schedule_id="SCHED_A", event_date=this_week)
    _insert_event(db, event_id="A-NOW-2", schedule_id="SCHED_A", event_date=this_week)

    # Schedule B: 4 claims last week, 1 this week -> -75%
    for i in range(4):
        _insert_event(db, event_id=f"B-LAST-{i}", schedule_id="SCHED_B", event_date=last_week)
    _insert_event(db, event_id="B-NOW", schedule_id="SCHED_B", event_date=this_week)

    summary_a = query_dashboard_summary(conn=db, schedule_id="SCHED_A")
    summary_b = query_dashboard_summary(conn=db, schedule_id="SCHED_B")

    assert summary_a["claims_trend_pct"] == 100.0
    assert summary_b["claims_trend_pct"] == -75.0


def test_dashboard_claims_trend_none_without_prior_period_data():
    """No claims in the prior 7-14 day window -> claims_trend_pct is None, never a fabricated number."""
    db = create_test_db()
    today = date.today()
    this_week = (today - timedelta(days=1)).isoformat()
    _insert_event(db, event_id="ONLY-EVENT", schedule_id="SCHED_A", event_date=this_week)

    summary_a = query_dashboard_summary(conn=db, schedule_id="SCHED_A")
    assert summary_a["claims_trend_pct"] is None


# ── Daily Digest isolation (ISS-05) ─────────────────────────────────────────

class _CursorCM:
    """Wraps a sqlite3.Cursor so `with conn.cursor() as cur:` works (sqlite3
    cursors, unlike psycopg3's, aren't themselves context managers)."""

    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self._cursor

    def __exit__(self, *args):
        pass


class _DigestTestConn:
    """Minimal psycopg3-shaped connection adapter (`.cursor()`, `%s`
    placeholders, itself usable as `with get_connection() as conn:`) for
    exercising routers/decisions.py's inline `get_connection()` calls
    against a real in-memory SQLite DB instead of Supabase."""

    def __init__(self, sqlite_conn):
        self._conn = sqlite_conn

    def cursor(self):
        return _CursorCM(_TranslatingCursor(self._conn.cursor()))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def commit(self):
        self._conn.commit()


class _TranslatingCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, query, params=None):
        clean_q = query.replace("%s", "?")
        if params is not None:
            self._cursor.execute(clean_q, params)
        else:
            self._cursor.execute(clean_q)
        return self

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchone(self):
        return self._cursor.fetchone()


def _make_digest_test_db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE execution_events (
            event_id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, event_date TEXT,
            discipline TEXT, priority_score REAL, created_at TEXT, status TEXT, project_id TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO execution_events (event_id, schedule_id, event_date, status, created_at, project_id) VALUES (?, ?, ?, ?, ?, ?)",
        ("EVT-A1", "SCHED_A", "2026-09-02", "VALIDATED", "2026-09-02 09:00:00", str(PROJECT_ID)),
    )
    conn.execute(
        "INSERT INTO execution_events (event_id, schedule_id, event_date, status, created_at, project_id) VALUES (?, ?, ?, ?, ?, ?)",
        ("EVT-B1", "SCHED_B", "2026-09-03", "VALIDATED", "2026-09-03 09:00:00", str(PROJECT_ID)),
    )
    conn.execute(
        "INSERT INTO execution_events (event_id, schedule_id, event_date, status, created_at, project_id) VALUES (?, ?, ?, ?, ?, ?)",
        ("EVT-OTHER-PROJECT", "SCHED_A", "2026-09-02", "VALIDATED", "2026-09-02 09:00:00", "99999999-9999-4999-8999-999999999999"),
    )
    conn.commit()
    return conn


def _supervisor():
    return UserProfile(id="22222222-2222-2222-2222-222222222222", full_name="Bob", role="SUPERVISOR")


def test_digest_isolation_explicit_schedule_id(monkeypatch):
    """GET /api/v1/digest?schedule_id=... returns only that schedule's claims AND only the caller's project's."""
    sqlite_conn = _make_digest_test_db()
    fake_conn = _DigestTestConn(sqlite_conn)
    client = TestClient(app)
    fake_schedules(monkeypatch, {"SCHED_A": str(PROJECT_ID), "SCHED_B": str(PROJECT_ID)})

    @contextmanager
    def _fake_get_connection():
        yield fake_conn

    with act_as("SUPERVISOR", schedule_id=None), patch("backend.routers.decisions.get_connection", _fake_get_connection):
        resp_a = client.get("/api/v1/digest?schedule_id=SCHED_A", headers={"Authorization": "Bearer x"})
        assert resp_a.status_code == 200
        ids_a = {row["event_id"] for row in resp_a.json()}
        assert ids_a == {"EVT-A1"}, "another project's event in the same schedule id must not appear"

        resp_b = client.get("/api/v1/digest?schedule_id=SCHED_B", headers={"Authorization": "Bearer x"})
        assert resp_b.status_code == 200
        assert {row["event_id"] for row in resp_b.json()} == {"EVT-B1"}

        # no schedule -> rejected, never "the active one"
        assert client.get("/api/v1/digest", headers={"Authorization": "Bearer x"}).status_code == 400


# ── Feature 31 Evidence Fusion isolation (ISS-05) ───────────────────────────

def _make_evidence_fusion_test_db():
    """Real in-memory SQLite backing evaluate_evidence_fusion's actual
    queries (execution_events, evidence_links, claim_activity_splits,
    source_documents) -- not a hand-rolled pattern-matching fake, since this
    exercises several distinct real queries."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE execution_events (
            event_id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, event_date TEXT,
            document_type TEXT, input_channel TEXT, matched_activity_id TEXT,
            claim_mode TEXT, claimed_pct REAL, claimed_quantity REAL, claimed_uom TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE evidence_links (
            link_id TEXT PRIMARY KEY, event_id_a TEXT, event_id_b TEXT,
            relation_type TEXT, confidence REAL, rationale TEXT, created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE claim_activity_splits (
            split_id TEXT PRIMARY KEY, event_id TEXT, activity_id TEXT,
            split_basis TEXT, split_pct REAL
        )
        """
    )
    conn.commit()
    return SQLitePsycopgAdapter(conn)


def _insert_fusion_event(db, *, event_id, schedule_id, event_date, document_type,
                          matched_activity_id, claimed_pct):
    db.execute(
        """
        INSERT INTO execution_events
            (event_id, schedule_id, event_date, document_type, input_channel,
             matched_activity_id, claim_mode, claimed_pct)
        VALUES (?, ?, ?, ?, 'TYPED_TEXT', ?, 'CUMULATIVE_PCT', ?)
        """,
        (event_id, schedule_id, event_date, document_type, matched_activity_id, claimed_pct),
    )


def test_evidence_fusion_links_within_same_schedule():
    """Two cross-channel, same-activity, same-date, agreeing claims in the
    SAME schedule produce a CORROBORATES evidence_links row (sanity check
    the fixture models the real classify path before testing isolation)."""
    from backend.routers.checks import evaluate_evidence_fusion

    db = _make_evidence_fusion_test_db()
    _insert_fusion_event(
        db, event_id="EV-1", schedule_id="SCHED_A", event_date="2026-09-01",
        document_type="DPR", matched_activity_id="ACT-001", claimed_pct=50.0,
    )
    _insert_fusion_event(
        db, event_id="EV-2", schedule_id="SCHED_A", event_date="2026-09-01",
        document_type="QC_INSPECTION", matched_activity_id="ACT-001", claimed_pct=50.0,
    )
    event_row = dict(db.execute("SELECT * FROM execution_events WHERE event_id = 'EV-1'").fetchone())

    links = evaluate_evidence_fusion(
        conn=db, event_id="EV-1", schedule_id="SCHED_A", event_row=event_row, splits=[],
    )
    assert len(links) == 1
    assert links[0]["relation_type"] == "CORROBORATES"
    assert {links[0]["event_id_a"], links[0]["event_id_b"]} == {"EV-1", "EV-2"}

    persisted = db.execute("SELECT * FROM evidence_links").fetchall()
    assert len(persisted) == 1


def test_evidence_fusion_never_links_across_schedules():
    """The identical setup, but EV-2 belongs to a DIFFERENT schedule -- even
    though it's otherwise a perfect corroboration match (same activity_id,
    same date, agreeing pct, different channel), no evidence_links row is
    created, because evaluate_evidence_fusion's candidate query is scoped
    to schedule_id."""
    from backend.routers.checks import evaluate_evidence_fusion

    db = _make_evidence_fusion_test_db()
    _insert_fusion_event(
        db, event_id="EV-1", schedule_id="SCHED_A", event_date="2026-09-01",
        document_type="DPR", matched_activity_id="ACT-001", claimed_pct=50.0,
    )
    _insert_fusion_event(
        db, event_id="EV-2", schedule_id="SCHED_B", event_date="2026-09-01",
        document_type="QC_INSPECTION", matched_activity_id="ACT-001", claimed_pct=50.0,
    )
    event_row = dict(db.execute("SELECT * FROM execution_events WHERE event_id = 'EV-1'").fetchone())

    links = evaluate_evidence_fusion(
        conn=db, event_id="EV-1", schedule_id="SCHED_A", event_row=event_row, splits=[],
    )
    assert links == []

    persisted = db.execute("SELECT * FROM evidence_links").fetchall()
    assert len(persisted) == 0
