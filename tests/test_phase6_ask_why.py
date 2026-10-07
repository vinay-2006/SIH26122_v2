import sqlite3
from typing import Any, Dict, List, Optional
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.main import app
from tests.v7ctx import act_as
from backend.routers.investigation import build_investigation_context
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
    conn.execute(
        """
        CREATE TABLE validation_issues (
            issue_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            rule_code TEXT,
            severity TEXT,
            description TEXT NOT NULL
        )
        """
    )
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


# ==============================================================================
# WHY-01 — Depth 1
# ==============================================================================
def test_why_01_depth_1():
    """
    WHY-01: Request depth=1.
    Verify immediate investigation context is returned and deeper dependency traversal
    (depth 2) is not included in context, dependencies, or graph.
    """
    db = create_test_db()
    sch_id = "SCHED-WHY-01"

    # 3-activity chain: ROOT -> DEP1 -> DEP2
    for act in ["ACT-ROOT", "ACT-DEP1", "ACT-DEP2"]:
        db.execute(
            """
            INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (act, sch_id, f"Activity {act}", "CIVIL", "Site A", "2026-09-01", "2026-09-10"),
        )

    # Dependencies: ROOT -> DEP1 and DEP1 -> DEP2
    db.execute(
        "INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id) VALUES (?, ?, ?, ?)",
        ("D1", sch_id, "ACT-ROOT", "ACT-DEP1"),
    )
    db.execute(
        "INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id) VALUES (?, ?, ?, ?)",
        ("D2", sch_id, "ACT-DEP1", "ACT-DEP2"),
    )

    # Events for each activity
    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id) VALUES (?, ?, ?, ?, ?, ?)",
        ("EVT-ROOT", sch_id, "2026-09-02", "Root claim", "TYPED_TEXT", "ACT-ROOT"),
    )
    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id) VALUES (?, ?, ?, ?, ?, ?)",
        ("EVT-DEP1", sch_id, "2026-09-03", "Dep 1 claim", "TYPED_TEXT", "ACT-DEP1"),
    )
    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id) VALUES (?, ?, ?, ?, ?, ?)",
        ("EVT-DEP2", sch_id, "2026-09-04", "Dep 2 claim", "TYPED_TEXT", "ACT-DEP2"),
    )

    res = build_investigation_context(activity_id="ACT-ROOT", depth=1, conn=db)

    # 1. Root and depth metadata
    assert res["root_activity_id"] == "ACT-ROOT"
    assert res["depth"] == 1

    # 2. Activity context
    assert res["context"]["activity"]["activity_id"] == "ACT-ROOT"

    # 3. Traversed activity IDs in graph: includes ROOT and DEP1, excludes DEP2
    graph_act_ids = [n["data"].get("activity_id") for n in res["graph"]["nodes"] if n["type"] == "activity"]
    assert "ACT-ROOT" in graph_act_ids
    assert "ACT-DEP1" in graph_act_ids
    assert "ACT-DEP2" not in graph_act_ids, "ACT-DEP2 should not be traversed at depth=1"

    # 4. Events in context: includes EVT-ROOT and EVT-DEP1, excludes EVT-DEP2
    context_event_ids = [e["event_id"] for e in res["context"]["execution_events"]]
    assert "EVT-ROOT" in context_event_ids
    assert "EVT-DEP1" in context_event_ids
    assert "EVT-DEP2" not in context_event_ids, "EVT-DEP2 should not be in context at depth=1"

    # 5. Dependencies in context: includes ROOT -> DEP1, excludes DEP1 -> DEP2
    deps = res["context"]["dependencies"]
    pred_succ_pairs = [(d["predecessor_activity_id"], d["successor_activity_id"]) for d in deps]
    assert ("ACT-ROOT", "ACT-DEP1") in pred_succ_pairs
    assert ("ACT-DEP1", "ACT-DEP2") not in pred_succ_pairs, "Deeper dependency should not be in context at depth=1"


# ==============================================================================
# WHY-02 — Depth 2
# ==============================================================================
def test_why_02_depth_2():
    """
    WHY-02: Request depth=2.
    Verify the additional dependency level (ACT-DEP2, EVT-DEP2, DEP1 -> DEP2) is included.
    """
    db = create_test_db()
    sch_id = "SCHED-WHY-02"

    for act in ["ACT-ROOT", "ACT-DEP1", "ACT-DEP2"]:
        db.execute(
            """
            INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (act, sch_id, f"Activity {act}", "CIVIL", "Site A", "2026-09-01", "2026-09-10"),
        )

    db.execute(
        "INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id) VALUES (?, ?, ?, ?)",
        ("D1", sch_id, "ACT-ROOT", "ACT-DEP1"),
    )
    db.execute(
        "INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id) VALUES (?, ?, ?, ?)",
        ("D2", sch_id, "ACT-DEP1", "ACT-DEP2"),
    )

    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id) VALUES (?, ?, ?, ?, ?, ?)",
        ("EVT-ROOT", sch_id, "2026-09-02", "Root claim", "TYPED_TEXT", "ACT-ROOT"),
    )
    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id) VALUES (?, ?, ?, ?, ?, ?)",
        ("EVT-DEP1", sch_id, "2026-09-03", "Dep 1 claim", "TYPED_TEXT", "ACT-DEP1"),
    )
    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id) VALUES (?, ?, ?, ?, ?, ?)",
        ("EVT-DEP2", sch_id, "2026-09-04", "Dep 2 claim", "TYPED_TEXT", "ACT-DEP2"),
    )

    res = build_investigation_context(activity_id="ACT-ROOT", depth=2, conn=db)

    assert res["root_activity_id"] == "ACT-ROOT"
    assert res["depth"] == 2

    # Traversed activity IDs in graph: includes all 3 activities at depth=2
    graph_act_ids = [n["data"].get("activity_id") for n in res["graph"]["nodes"] if n["type"] == "activity"]
    assert "ACT-ROOT" in graph_act_ids
    assert "ACT-DEP1" in graph_act_ids
    assert "ACT-DEP2" in graph_act_ids, "ACT-DEP2 should be included at depth=2"

    # Events in context: includes EVT-DEP2
    context_event_ids = [e["event_id"] for e in res["context"]["execution_events"]]
    assert "EVT-DEP2" in context_event_ids, "EVT-DEP2 should be in context at depth=2"

    # Dependencies in context: includes DEP1 -> DEP2
    deps = res["context"]["dependencies"]
    pred_succ_pairs = [(d["predecessor_activity_id"], d["successor_activity_id"]) for d in deps]
    assert ("ACT-DEP1", "ACT-DEP2") in pred_succ_pairs, "DEP1 -> DEP2 should be included at depth=2"


# ==============================================================================
# WHY-03 — Root changes
# ==============================================================================
def test_why_03_root_changes():
    """
    WHY-03: Request investigation for two different activities.
    Verify each response uses the requested activity as its root and does not
    leak state from the previous request.
    """
    db = create_test_db()
    sch_id = "SCHED-WHY-03"

    for act in ["ACT-ALPHA", "ACT-BETA"]:
        db.execute(
            """
            INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (act, sch_id, f"Activity {act}", "PIPING", "Sector 7", "2026-09-01", "2026-09-10"),
        )

    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id) VALUES (?, ?, ?, ?, ?, ?)",
        ("EVT-ALPHA", sch_id, "2026-09-01", "Alpha work", "TYPED_TEXT", "ACT-ALPHA"),
    )
    db.execute(
        "INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id) VALUES (?, ?, ?, ?, ?, ?)",
        ("EVT-BETA", sch_id, "2026-09-02", "Beta work", "TYPED_TEXT", "ACT-BETA"),
    )

    # 1. Investigate ACT-ALPHA
    res_alpha = build_investigation_context(activity_id="ACT-ALPHA", depth=1, conn=db)
    assert res_alpha["root_activity_id"] == "ACT-ALPHA"
    assert res_alpha["context"]["activity"]["activity_id"] == "ACT-ALPHA"
    alpha_event_ids = [e["event_id"] for e in res_alpha["context"]["execution_events"]]
    assert "EVT-ALPHA" in alpha_event_ids
    assert "EVT-BETA" not in alpha_event_ids

    # 2. Investigate ACT-BETA
    res_beta = build_investigation_context(activity_id="ACT-BETA", depth=1, conn=db)
    assert res_beta["root_activity_id"] == "ACT-BETA"
    assert res_beta["context"]["activity"]["activity_id"] == "ACT-BETA"
    beta_event_ids = [e["event_id"] for e in res_beta["context"]["execution_events"]]
    assert "EVT-BETA" in beta_event_ids
    assert "EVT-ALPHA" not in beta_event_ids, "State leaked from previous investigation"

    # Confirms independent root identity
    assert res_alpha["root_activity_id"] != res_beta["root_activity_id"]


# ==============================================================================
# WHY-04 — Existing-data-only causal context
# ==============================================================================
def test_why_04_existing_data_only_causal_context():
    """
    WHY-04: Verify the investigation response exposes existing facts/relationships
    (validation, conflict, execution event, evidence, decision, dependency)
    and does NOT create unsupported causal claims or hallucinated explanations.
    """
    db = create_test_db()
    sch_id = "SCHED-WHY-04"
    act_id = "ACT-FLAGGED"
    pred_id = "ACT-UPSTREAM"

    # 1. Schedule activities
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish, is_critical, total_float)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (act_id, sch_id, "Trenching", "CIVIL", "Km 12", "2026-09-01", "2026-09-10", 1, 0.0),
    )
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, planned_start, planned_finish, is_critical, total_float)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (pred_id, sch_id, "Survey", "CIVIL", "Km 12", "2026-08-20", "2026-08-30", 0, 5.0),
    )

    # 2. Dependency: ACT-UPSTREAM -> ACT-FLAGGED
    db.execute(
        "INSERT INTO schedule_dependencies (dependency_id, schedule_id, predecessor_activity_id, successor_activity_id, relationship_type, lag_days) VALUES (?, ?, ?, ?, ?, ?)",
        ("DEP-REAL", sch_id, pred_id, act_id, "FS", 2.0),
    )

    # 3. Execution Event
    evt_id = "EVT-WHY-01"
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, matched_activity_id, delay_reason, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (evt_id, sch_id, "2026-09-05", "Excavator broke down at Km 12", "TYPED_TEXT", act_id, "EQUIPMENT", "REVIEW_REQUIRED"),
    )

    # 4. Validation Issue
    db.execute(
        "INSERT INTO validation_issues (issue_id, event_id, rule_code, severity, description) VALUES (?, ?, ?, ?, ?)",
        ("VAL-01", evt_id, "FUTURE_EVENT_DATE", "WARNING", "Claim date is in the future relative to baseline report"),
    )

    # 5. Conflict Record
    db.execute(
        """
        INSERT INTO conflict_records (conflict_id, schedule_id, activity_id, reporting_period, event_id_a, event_id_b, value_a, value_b, variance_pct, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("CONF-01", sch_id, act_id, "2026-09-05", evt_id, "EVT-OTHER", 50.0, 75.0, 25.0, "OPEN"),
    )

    # 6. Source Reference (Evidence)
    db.execute(
        "INSERT INTO source_references (reference_id, event_id, file_name, sheet_name, raw_snippet) VALUES (?, ?, ?, ?, ?)",
        ("REF-01", evt_id, "daily_report_01.pdf", "Log", "Hydraulic seal failure on Excavator EX-04"),
    )

    # 7. Planner Decision (HOLD)
    db.execute(
        "INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, approved_pct, planner_id, justification) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("DEC-01", evt_id, act_id, "HOLD", 0.0, "4b8e6901-de81-490c-8bec-9761f62bee70", "Awaiting maintenance log verification"),
    )

    # Build investigation context
    res = build_investigation_context(activity_id=act_id, depth=1, conn=db)

    # Assert genuine factual context is exposed accurately
    ctx = res["context"]
    summary = res["summary"]

    # Factual activity context
    assert ctx["activity"]["activity_id"] == act_id
    assert ctx["activity"]["is_critical"] == 1
    assert ctx["activity"]["total_float"] == 0.0

    # Factual dependency
    assert len(ctx["dependencies"]) >= 1
    dep = next(d for d in ctx["dependencies"] if d["successor_activity_id"] == act_id)
    assert dep["predecessor_activity_id"] == pred_id
    assert dep["relationship_type"] == "FS"
    assert dep["lag_days"] == 2.0

    # Factual execution event with recorded delay_reason
    assert len(ctx["execution_events"]) == 1
    assert ctx["execution_events"][0]["delay_reason"] == "EQUIPMENT"
    assert ctx["execution_events"][0]["raw_claim_text"] == "Excavator broke down at Km 12"

    # Factual validation issue
    assert len(ctx["validations"]) == 1
    assert ctx["validations"][0]["rule_code"] == "FUTURE_EVENT_DATE"
    assert ctx["validations"][0]["severity"] == "WARNING"

    # Factual conflict record
    assert len(ctx["conflicts"]) == 1
    assert ctx["conflicts"][0]["conflict_id"] == "CONF-01"
    assert ctx["conflicts"][0]["variance_pct"] == 25.0
    assert ctx["conflicts"][0]["status"] == "OPEN"

    # Factual evidence
    assert len(ctx["evidence"]) == 1
    assert ctx["evidence"][0]["file_name"] == "daily_report_01.pdf"
    assert "Hydraulic seal failure" in ctx["evidence"][0]["raw_snippet"]

    # Factual decision
    assert len(ctx["decisions"]) == 1
    assert ctx["decisions"][0]["action"] == "HOLD"
    assert ctx["decisions"][0]["justification"] == "Awaiting maintenance log verification"

    # No approved actual (since action was HOLD)
    assert len(ctx["approved_actuals"]) == 0

    # Objective status labels (no invented causal claims)
    assert summary["conflict_status"] == "present"
    assert summary["validation_status"] == "present"
    assert summary["evidence_status"] == "present"
    assert summary["approved_actual_status"] == "not_present"


# ==============================================================================
# Supporting test — Supervisor Authentication / RBAC
# ==============================================================================
def test_why_endpoint_authentication(monkeypatch):
    """
    /api/v1/investigation/activity/{activity_id}: unauthenticated -> 401; supervisor and site engineer
    (VIEW_EXECUTION_EVENTS) -> 200 within an explicit schedule; a role without it -> 403.
    """
    client = TestClient(app)

    resp_unauth = client.get("/api/v1/investigation/activity/ACT-TEST-01")
    assert resp_unauth.status_code == 401

    monkeypatch.setattr(
        "backend.routers.investigation.build_investigation_context",
        lambda activity_id, depth=1, schedule_id=None: {
            "root_activity_id": activity_id,
            "depth": depth,
            "context": {"activity": {"activity_id": activity_id}},
            "summary": {"conflict_status": "not_present"},
            "graph": {"nodes": [], "edges": []},
        },
    )
    for role in ("SUPERVISOR", "SITE_ENGINEER"):
        with act_as(role):
            resp = client.get("/api/v1/investigation/activity/ACT-TEST-01", headers={"Authorization": "Bearer t"})
        assert resp.status_code == 200, role
        assert resp.json()["root_activity_id"] == "ACT-TEST-01"
    with act_as("VIEWER"):
        assert client.get("/api/v1/investigation/activity/ACT-TEST-01", headers={"Authorization": "Bearer t"}).status_code == 403
