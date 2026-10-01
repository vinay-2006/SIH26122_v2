import sqlite3
from typing import Any, Dict, List, Optional
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.main import app
from tests.v7ctx import act_as
from backend.routers.graph import build_activity_graph
from backend.shared.auth import UserProfile, get_current_user


class SQLitePsycopgAdapter:
    """
    Lightweight DB connection adapter for deterministic in-memory testing.
    Translates Postgres-specific query patterns (%s, FOR UPDATE) to SQLite syntax.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

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
            discipline TEXT NOT NULL,
            location TEXT NOT NULL,
            planned_start TEXT NOT NULL,
            planned_finish TEXT NOT NULL,
            planned_quantity REAL,
            baseline_pct_complete REAL DEFAULT 0.0,
            total_float REAL,
            is_critical INTEGER,
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
            relationship_type TEXT DEFAULT 'FS',
            lag_days REAL DEFAULT 0.0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE execution_events (
            event_id TEXT PRIMARY KEY,
            schedule_id TEXT NOT NULL,
            event_date TEXT NOT NULL,
            raw_claim_text TEXT NOT NULL,
            input_channel TEXT NOT NULL,
            matched_activity_id TEXT,
            reported_activity_id TEXT,
            discipline TEXT,
            claim_mode TEXT DEFAULT 'CUMULATIVE_PCT',
            claimed_quantity REAL,
            claimed_pct REAL,
            delay_reason TEXT,
            photo_path TEXT,
            status TEXT DEFAULT 'VALIDATED',
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
            action TEXT NOT NULL,
            approved_pct REAL,
            approved_qty REAL,
            planner_id TEXT NOT NULL,
            justification TEXT NOT NULL,
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
    return SQLitePsycopgAdapter(conn)


# ==============================================================================
# GRAPH-01 — Root exists
# ==============================================================================
def test_graph_01_root_exists():
    """
    GRAPH-01: A valid activity request returns the root activity node and its
    connected execution chain (event -> decision -> actual).
    """
    db = create_test_db()
    sch_id = "SCHED-01"
    act_id = "ACT-ROOT"

    # Seed Activity
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (act_id, sch_id, "Excavation", "CIVIL", "Zone 1", "2026-09-01", "2026-09-10"),
    )

    # Seed Execution Event
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("EVT-01", sch_id, "2026-09-02", "Dug 20m trench", "TYPED_TEXT", act_id, "APPROVED"),
    )

    # Seed Decision
    db.execute(
        """
        INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, approved_pct, planner_id, justification)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("DEC-01", "EVT-01", act_id, "APPROVE", 20.0, "PLN-1", "Verified trench"),
    )

    # Seed Approved Actual
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_pct_complete)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("ACTUAL-01", "DEC-01", "EVT-01", sch_id, act_id, "2026-09-02", 20.0),
    )

    graph = build_activity_graph(activity_id=act_id, depth=1, conn=db)

    nodes = graph["nodes"]
    edges = graph["edges"]

    # 1. Root activity node exists
    act_node = next((n for n in nodes if n["id"] == f"activity:{sch_id}:{act_id}"), None)
    assert act_node is not None, "Root activity node not found"
    assert act_node["type"] == "activity"
    assert act_node["data"]["activity_id"] == act_id
    assert act_node["data"]["activity_name"] == "Excavation"

    # 2. Execution event node exists
    evt_node = next((n for n in nodes if n["id"] == "event:EVT-01"), None)
    assert evt_node is not None, "Execution event node not found"
    assert evt_node["type"] == "execution_event"

    # 3. Decision node exists
    dec_node = next((n for n in nodes if n["id"] == "decision:DEC-01"), None)
    assert dec_node is not None, "Decision node not found"
    assert dec_node["type"] == "decision"

    # 4. Approved actual node exists
    actl_node = next((n for n in nodes if n["id"] == "actual:ACTUAL-01"), None)
    assert actl_node is not None, "Approved actual node not found"
    assert actl_node["type"] == "approved_actual"

    # 5. Edges exist in correct topology
    assert any(
        e["source"] == f"activity:{sch_id}:{act_id}" and e["target"] == "event:EVT-01" and e["type"] == "has_execution_event"
        for e in edges
    )
    assert any(
        e["source"] == "event:EVT-01" and e["target"] == "decision:DEC-01" and e["type"] == "produces_decision"
        for e in edges
    )
    assert any(
        e["source"] == "decision:DEC-01" and e["target"] == "actual:ACTUAL-01" and e["type"] == "produces_actual"
        for e in edges
    )


# ==============================================================================
# GRAPH-02 — Unknown activity
# ==============================================================================
def test_graph_02_unknown_activity_404():
    """
    GRAPH-02: An unknown activity returns HTTP 404.
    """
    db = create_test_db()
    with pytest.raises(HTTPException) as exc_info:
        build_activity_graph(activity_id="NON-EXISTENT-ACTIVITY", depth=1, conn=db)

    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail.lower()


# ==============================================================================
# GRAPH-03 — Depth bound
# ==============================================================================
def test_graph_03_depth_bound():
    """
    GRAPH-03: Traversal does not return nodes beyond the requested depth.
    Chain: ACT-0 -> ACT-1 -> ACT-2 -> ACT-3
    - Depth 0: only ACT-0
    - Depth 1: ACT-0 and ACT-1 (ACT-2 and ACT-3 excluded)
    - Depth 2: ACT-0, ACT-1, ACT-2 (ACT-3 excluded)
    """
    db = create_test_db()
    sch_id = "SCHED-01"

    for act in ["ACT-0", "ACT-1", "ACT-2", "ACT-3"]:
        db.execute(
            """
            INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (act, sch_id, f"Activity {act}", "CIVIL", "Zone 1", "2026-09-01", "2026-09-10"),
        )

    # Dependencies: 0 -> 1 -> 2 -> 3
    db.execute(
        "INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id) VALUES (?, ?, ?, ?)",
        ("DEP-1", sch_id, "ACT-0", "ACT-1"),
    )
    db.execute(
        "INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id) VALUES (?, ?, ?, ?)",
        ("DEP-2", sch_id, "ACT-1", "ACT-2"),
    )
    db.execute(
        "INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id) VALUES (?, ?, ?, ?)",
        ("DEP-3", sch_id, "ACT-2", "ACT-3"),
    )

    # Test depth = 1
    g1 = build_activity_graph(activity_id="ACT-0", depth=1, conn=db)
    act_ids_1 = {n["data"].get("activity_id") for n in g1["nodes"] if n["type"] == "activity"}
    assert "ACT-0" in act_ids_1
    assert "ACT-1" in act_ids_1
    assert "ACT-2" not in act_ids_1, "ACT-2 should be excluded at depth 1"
    assert "ACT-3" not in act_ids_1, "ACT-3 should be excluded at depth 1"

    # Test depth = 2
    g2 = build_activity_graph(activity_id="ACT-0", depth=2, conn=db)
    act_ids_2 = {n["data"].get("activity_id") for n in g2["nodes"] if n["type"] == "activity"}
    assert "ACT-0" in act_ids_2
    assert "ACT-1" in act_ids_2
    assert "ACT-2" in act_ids_2
    assert "ACT-3" not in act_ids_2, "ACT-3 should be excluded at depth 2"


# ==============================================================================
# GRAPH-04 — Deduplication
# ==============================================================================
def test_graph_04_deduplication():
    """
    GRAPH-04: A node reachable through multiple paths appears only once.
    Diamond graph:
      A -> B -> D
      A -> C -> D
    """
    db = create_test_db()
    sch_id = "SCHED-01"

    for act in ["A", "B", "C", "D"]:
        db.execute(
            """
            INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (act, sch_id, f"Activity {act}", "CIVIL", "Zone 1", "2026-09-01", "2026-09-10"),
        )

    db.execute(
        "INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id) VALUES (?, ?, ?, ?)",
        ("D-1", sch_id, "A", "B"),
    )
    db.execute(
        "INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id) VALUES (?, ?, ?, ?)",
        ("D-2", sch_id, "A", "C"),
    )
    db.execute(
        "INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id) VALUES (?, ?, ?, ?)",
        ("D-3", sch_id, "B", "D"),
    )
    db.execute(
        "INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id) VALUES (?, ?, ?, ?)",
        ("D-4", sch_id, "C", "D"),
    )

    graph = build_activity_graph(activity_id="A", depth=2, conn=db)

    nodes = graph["nodes"]
    node_ids = [n["id"] for n in nodes]
    assert len(node_ids) == len(set(node_ids)), f"Duplicate nodes found: {node_ids}"

    d_nodes = [n for n in nodes if n["id"] == f"activity:{sch_id}:D"]
    assert len(d_nodes) == 1, f"Expected exactly 1 node for activity D, got {len(d_nodes)}"


# ==============================================================================
# GRAPH-05 — Cycle protection
# ==============================================================================
def test_graph_05_cycle_protection():
    """
    GRAPH-05: A dependency cycle terminates safely without infinite loops or duplicates.
    Cycle:
      CYC-A -> CYC-B -> CYC-C -> CYC-A
    """
    db = create_test_db()
    sch_id = "SCHED-01"

    for act in ["CYC-A", "CYC-B", "CYC-C"]:
        db.execute(
            """
            INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (act, sch_id, f"Activity {act}", "CIVIL", "Zone 1", "2026-09-01", "2026-09-10"),
        )

    db.execute(
        "INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id) VALUES (?, ?, ?, ?)",
        ("C-1", sch_id, "CYC-A", "CYC-B"),
    )
    db.execute(
        "INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id) VALUES (?, ?, ?, ?)",
        ("C-2", sch_id, "CYC-B", "CYC-C"),
    )
    db.execute(
        "INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id) VALUES (?, ?, ?, ?)",
        ("C-3", sch_id, "CYC-C", "CYC-A"),
    )

    # Request depth = 6 (much larger than cycle length)
    graph = build_activity_graph(activity_id="CYC-A", depth=6, conn=db)

    act_nodes = [n for n in graph["nodes"] if n["type"] == "activity"]
    act_ids = [n["data"]["activity_id"] for n in act_nodes]

    # Traversal terminated cleanly
    assert len(act_ids) == 3
    assert set(act_ids) == {"CYC-A", "CYC-B", "CYC-C"}
    assert len(act_ids) == len(set(act_ids)), "Cycle caused duplicate node entries"


# ==============================================================================
# GRAPH-06 — Authentication
# ==============================================================================
def test_graph_06_authentication(monkeypatch):
    """
    GRAPH-06: Unauthenticated access is rejected with HTTP 401. Authorization is by PROJECT role
    (VIEW_EXECUTION_EVENTS) on an explicit schedule of the caller's project.
    """
    client = TestClient(app)

    # 1. Unauthenticated request -> 401
    resp_unauth = client.get("/api/v1/graph/activity/ACT-TEST-01")
    assert resp_unauth.status_code == 401, f"Expected 401, got {resp_unauth.status_code}"

    captured = {}

    def _graph(activity_id, depth=1, schedule_id=None):
        captured["schedule_id"] = schedule_id
        return {"nodes": [{"id": f"activity:{activity_id}", "type": "activity", "data": {}}], "edges": []}

    monkeypatch.setattr("backend.routers.graph.build_activity_graph", _graph)

    # 2. Site engineer and supervisor authorized -> 200, scoped to the validated schedule
    for role in ("SITE_ENGINEER", "SUPERVISOR"):
        with act_as(role, schedule_id="SCH-G"):
            resp = client.get("/api/v1/graph/activity/ACT-TEST-01", headers={"Authorization": "Bearer t"})
        assert resp.status_code == 200, role
        assert resp.json()["nodes"][0]["id"] == "activity:ACT-TEST-01"
        assert captured["schedule_id"] == "SCH-G"

    # 3. A project role without VIEW_EXECUTION_EVENTS -> 403
    with act_as("VIEWER", schedule_id="SCH-G"):
        resp_forbid = client.get("/api/v1/graph/activity/ACT-TEST-01", headers={"Authorization": "Bearer t"})
    assert resp_forbid.status_code == 403
