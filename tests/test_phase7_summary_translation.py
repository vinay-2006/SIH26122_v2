"""
Tests for Phase 7 — AI Execution Summary + Dynamic Translation.

Test Suite:
  - SUM-01: Correct aggregation against known fixture data
  - SUM-02: Last 7 Days (records outside selected period excluded)
  - SUM-03: This Month (records outside current month excluded)
  - SUM-04: Custom range (explicit start/end filtering & 400 on invalid start > end)
  - SUM-05: Discipline filtering (CIVIL vs PIPING vs ALL)
  - SUM-06: No invented numbers (mock shared LLM, verify receives deterministic aggregate)
  - SUM-07: LLM failure (safe deterministic fallback response)
  - TRANS-01: Static translation boundary (predefined UI content uses existing i18n, no LLM call)
  - TRANS-02: Dynamic cache MISS (shared LLM called once, translation stored in cache)
  - TRANS-03: Dynamic cache HIT (cached translation returned, shared LLM NOT called again)
  - TRANS-04: Translation failure (returns canonical English, failed translation not cached)
  - AUTH-01: Supervisor-only access (401 unauthenticated, 403 non-supervisor, 200 supervisor)
"""
import sqlite3
from datetime import date, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.routers.summary import (
    build_deterministic_aggregate,
    clear_translation_cache,
    generate_deterministic_summary,
    generate_llm_summary,
    get_translation_cache,
    translate_dynamic_text,
)
from backend.shared.auth import UserProfile, get_current_user


class SQLitePsycopgAdapter:
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
            discipline TEXT NOT NULL,
            is_critical INTEGER,
            total_float REAL,
            planned_start TEXT,
            planned_finish TEXT,
            actual_start TEXT,
            actual_finish TEXT,
            actual_pct_comp REAL DEFAULT 0.0,
            PRIMARY KEY (schedule_id, activity_id)
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
            discipline TEXT,
            action TEXT,
            event_type TEXT,
            claimed_pct REAL,
            delay_reason TEXT,
            matched_activity_id TEXT,
            status TEXT DEFAULT 'EXTRACTED'
        )
        """
    )
    # The summary scopes activity counts to the active (latest) schedule.
    conn.execute(
        """
        CREATE TABLE schedules (
            schedule_id TEXT PRIMARY KEY,
            project_name TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("INSERT INTO schedules (schedule_id, project_name) VALUES ('SCH-01', 'Test')")
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
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE (schedule_id, activity_id)
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
        CREATE TABLE planner_decisions (
            decision_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            selected_activity_id TEXT NOT NULL,
            action TEXT,
            approved_pct REAL,
            planner_id TEXT NOT NULL,
            justification TEXT NOT NULL,
            decided_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    return SQLitePsycopgAdapter(conn)


def populate_standard_fixtures(db: SQLitePsycopgAdapter, ref_date: date):
    ref_iso = ref_date.isoformat()
    d_minus_2 = (ref_date - timedelta(days=2)).isoformat()
    d_minus_5 = (ref_date - timedelta(days=5)).isoformat()
    d_minus_20 = (ref_date - timedelta(days=20)).isoformat()

    # Activities: 1 completed, 1 in progress, 1 not started
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, is_critical, planned_start, planned_finish)
        VALUES
            ('ACT-CIV-01', 'SCH-01', 'Excavation', 'CIVIL', 1, '2026-09-01', '2026-09-10'),
            ('ACT-CIV-02', 'SCH-01', 'Piling Works', 'CIVIL', 0, '2026-09-10', '2026-09-20'),
            ('ACT-PIP-01', 'SCH-01', 'Pipe Laying', 'PIPING', 1, '2026-09-15', '2026-09-25')
        """
    )

    # Approved Actuals
    # ACT-CIV-01 completed (100%)
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_finish, actual_pct_complete)
        VALUES
            ('AA-01', 'DEC-01', 'EV-01', 'SCH-01', 'ACT-CIV-01', '2026-09-01', '2026-09-09', 100.0),
            ('AA-02', 'DEC-02', 'EV-02', 'SCH-01', 'ACT-CIV-02', '2026-09-11', NULL, 45.0)
        """
    )

    # Execution Events
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, discipline, event_type, status, delay_reason)
        VALUES
            ('EV-01', 'SCH-01', %s, 'Finished excavation', 'TYPED_TEXT', 'CIVIL', 'PROGRESS_UPDATE', 'APPROVED', NULL),
            ('EV-02', 'SCH-01', %s, 'Piling 45% done', 'TYPED_TEXT', 'CIVIL', 'PROGRESS_UPDATE', 'APPROVED', 'WEATHER'),
            ('EV-03', 'SCH-01', %s, 'Piping hold due to material', 'FILE_UPLOAD', 'PIPING', 'DELAY', 'REVIEW_REQUIRED', 'MATERIAL'),
            ('EV-OLD', 'SCH-01', %s, 'Old August log', 'TYPED_TEXT', 'CIVIL', 'PROGRESS_UPDATE', 'APPROVED', NULL)
        """,
        (d_minus_2, d_minus_5, d_minus_2, d_minus_20),
    )

    # Conflict
    db.execute(
        """
        INSERT INTO conflict_records (conflict_id, schedule_id, activity_id, reporting_period, event_id_a, event_id_b, value_a, value_b, variance_pct, status)
        VALUES
            ('CONF-01', 'SCH-01', 'ACT-CIV-02', %s, 'EV-02', 'EV-02B', 45.0, 30.0, 15.0, 'OPEN')
        """,
        (d_minus_2,),
    )

    # Validation Issue
    db.execute(
        """
        INSERT INTO validation_issues (issue_id, event_id, rule_code, severity, description)
        VALUES
            ('VAL-01', 'EV-02', 'RULE-W01', 'WARNING', 'Work reported during rain alert')
        """
    )
    db.commit()


@pytest.fixture(autouse=True)
def reset_cache():
    clear_translation_cache()
    yield
    clear_translation_cache()


# =========================================================================
# SUM-01: Correct aggregation against known fixture data
# =========================================================================


def test_sum_01_correct_aggregation():
    ref_date = date(2026, 9, 18)
    db = create_test_db()
    populate_standard_fixtures(db, ref_date)

    agg = build_deterministic_aggregate(
        period="last_7_days",
        discipline="ALL",
        conn=db,
        reference_date=ref_date,
    )

    # 3 events within last 7 days (EV-01, EV-02, EV-03); EV-OLD (20 days ago) excluded
    assert agg["claims"]["total_claims"] == 3
    assert agg["approved_progress"]["total_approved"] == 2
    assert agg["conflicts"]["total_conflicts"] == 1
    assert agg["validation_issues"]["total_issues"] == 1
    assert agg["delays"]["total_delay_events"] == 2
    assert agg["delays"]["reasons"]["WEATHER"] == 1
    assert agg["delays"]["reasons"]["MATERIAL"] == 1

    # Activities canonical execution states:
    # ACT-CIV-01: 100% -> COMPLETED
    # ACT-CIV-02: 45% + actual_start -> IN_PROGRESS
    # ACT-PIP-01: no actuals -> NOT_STARTED
    assert agg["activities"]["total"] == 3
    assert agg["activities"]["completed"] == 1
    assert agg["activities"]["in_progress"] == 1
    assert agg["activities"]["not_started"] == 1


# =========================================================================
# SUM-02: Last 7 Days (records outside selected period excluded)
# =========================================================================


def test_sum_02_last_7_days():
    ref_date = date(2026, 9, 18)
    db = create_test_db()
    populate_standard_fixtures(db, ref_date)

    agg = build_deterministic_aggregate(
        period="last_7_days",
        discipline="ALL",
        conn=db,
        reference_date=ref_date,
    )

    # Check that EV-OLD (20 days ago) was strictly excluded
    assert agg["period"]["type"] == "last_7_days"
    assert agg["period"]["start"] == (ref_date - timedelta(days=7)).isoformat()
    assert agg["period"]["end"] == ref_date.isoformat()
    assert agg["claims"]["total_claims"] == 3


# =========================================================================
# SUM-03: This Month (month boundaries)
# =========================================================================


def test_sum_03_this_month():
    ref_date = date(2026, 9, 18)
    db = create_test_db()
    populate_standard_fixtures(db, ref_date)

    agg = build_deterministic_aggregate(
        period="this_month",
        discipline="ALL",
        conn=db,
        reference_date=ref_date,
    )

    assert agg["period"]["type"] == "this_month"
    assert agg["period"]["start"] == "2026-09-01"
    assert agg["period"]["end"] == "2026-09-18"
    # EV-OLD is on 2026-08-29, so it is strictly outside September
    assert agg["claims"]["total_claims"] == 3


# =========================================================================
# SUM-04: Custom range (explicit start/end & validation)
# =========================================================================


def test_sum_04_custom_range():
    ref_date = date(2026, 9, 18)
    db = create_test_db()
    populate_standard_fixtures(db, ref_date)

    # Valid custom range targeting only EV-01 and EV-03 (d_minus_2 = 2026-09-16)
    agg = build_deterministic_aggregate(
        period="custom",
        start_date="2026-09-16",
        end_date="2026-09-17",
        discipline="ALL",
        conn=db,
        reference_date=ref_date,
    )
    assert agg["period"]["start"] == "2026-09-16"
    assert agg["period"]["end"] == "2026-09-17"
    assert agg["claims"]["total_claims"] == 2

    # Invalid range: start_date > end_date must raise 400
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        build_deterministic_aggregate(
            period="custom",
            start_date="2026-09-20",
            end_date="2026-09-10",
            conn=db,
        )
    assert exc.value.status_code == 400
    assert "start_date" in str(exc.value.detail)


# =========================================================================
# SUM-05: Discipline filtering (CIVIL vs PIPING)
# =========================================================================


def test_sum_05_discipline_filtering():
    ref_date = date(2026, 9, 18)
    db = create_test_db()
    populate_standard_fixtures(db, ref_date)

    # Filter by CIVIL
    agg_civil = build_deterministic_aggregate(
        period="last_7_days",
        discipline="CIVIL",
        conn=db,
        reference_date=ref_date,
    )
    assert agg_civil["discipline"] == "CIVIL"
    assert agg_civil["claims"]["total_claims"] == 2
    assert agg_civil["activities"]["total"] == 2

    # Filter by PIPING
    agg_piping = build_deterministic_aggregate(
        period="last_7_days",
        discipline="PIPING",
        conn=db,
        reference_date=ref_date,
    )
    assert agg_piping["discipline"] == "PIPING"
    assert agg_piping["claims"]["total_claims"] == 1
    assert agg_piping["activities"]["total"] == 1


# =========================================================================
# SUM-06: No invented numbers (mock shared LLM receives verified aggregate)
# =========================================================================


def test_sum_06_no_invented_numbers():
    ref_date = date(2026, 9, 18)
    db = create_test_db()
    populate_standard_fixtures(db, ref_date)

    agg = build_deterministic_aggregate(
        period="last_7_days",
        discipline="ALL",
        conn=db,
        reference_date=ref_date,
    )

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(content="Mock LLM Summary with 3 claims and 1 completed activity."))
    ]

    with patch("backend.routers.summary._get_client", return_value=mock_client), \
         patch("backend.routers.summary._create_completion", return_value=mock_response) as mock_comp:
        summary_text, source = generate_llm_summary(agg)
        assert source == "llm"
        assert "Mock LLM Summary" in summary_text

        # Verify that _create_completion was passed the exact prompt containing aggregate numbers
        call_kwargs = mock_comp.call_args[1]
        messages = call_kwargs["messages"]
        user_content = messages[1]["content"]
        assert '"total_claims": 3' in user_content
        assert '"completed": 1' in user_content
        assert '"total_delay_events": 2' in user_content


# =========================================================================
# SUM-07: LLM failure produces safe deterministic response
# =========================================================================


def test_sum_07_llm_failure_deterministic_fallback():
    ref_date = date(2026, 9, 18)
    db = create_test_db()
    populate_standard_fixtures(db, ref_date)

    agg = build_deterministic_aggregate(
        period="last_7_days",
        discipline="CIVIL",
        conn=db,
        reference_date=ref_date,
    )

    # Force LLM error
    with patch("backend.routers.summary._get_client", side_effect=Exception("External LLM connection failed")):
        summary_text, source = generate_llm_summary(agg)
        assert source == "deterministic_fallback"
        # Must contain authoritative facts without crashing
        assert "Project Execution Summary" in summary_text
        assert "Discipline: CIVIL" in summary_text
        assert "2 activities under scope" in summary_text
        assert "2 field claims submitted" in summary_text


# =========================================================================
# TRANS-01: Static translation boundary (i18n vs dynamic)
# =========================================================================


def test_trans_01_static_translation_boundary():
    # Predefined UI strings remain in frontend i18next bundles
    # Verify that English text translation to 'en' never calls LLM
    with patch("backend.routers.summary._create_completion") as mock_comp:
        text, cached = translate_dynamic_text("Status: In Progress", "en")
        assert text == "Status: In Progress"
        assert cached is False
        mock_comp.assert_not_called()


# =========================================================================
# TRANS-02: Dynamic cache MISS (calls shared LLM and stores in cache)
# =========================================================================


def test_trans_02_dynamic_cache_miss():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(content="गतिविधि ACT-CIV-01 पूर्ण हो चुकी है।"))
    ]

    source_text = "Activity ACT-CIV-01 is complete."
    cache = get_translation_cache()
    assert len(cache) == 0

    with patch("backend.routers.summary._get_client", return_value=mock_client), \
         patch("backend.routers.summary._create_completion", return_value=mock_response) as mock_comp:
        translated, was_cached = translate_dynamic_text(source_text, "hi")
        assert was_cached is False
        assert translated == "गतिविधि ACT-CIV-01 पूर्ण हो चुकी है।"
        assert mock_comp.call_count == 1
        # Stored in cache
        assert len(cache) == 1


# =========================================================================
# TRANS-03: Dynamic cache HIT (returns cached translation without calling LLM)
# =========================================================================


def test_trans_03_dynamic_cache_hit():
    source_text = "Activity ACT-CIV-01 is complete."

    # Pre-populate cache
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(content="ACT-CIV-01 పూర్తి అయింది."))
    ]

    with patch("backend.routers.summary._get_client", return_value=mock_client), \
         patch("backend.routers.summary._create_completion", return_value=mock_response):
        trans1, cached1 = translate_dynamic_text(source_text, "te")
        assert cached1 is False
        assert trans1 == "ACT-CIV-01 పూర్తి అయింది."

    # Second call must HIT cache and NOT invoke LLM
    with patch("backend.routers.summary._create_completion") as mock_comp:
        trans2, cached2 = translate_dynamic_text(source_text, "te")
        assert cached2 is True
        assert trans2 == "ACT-CIV-01 పూర్తి అయింది."
        mock_comp.assert_not_called()


# =========================================================================
# TRANS-04: Translation failure returns canonical English & not cached
# =========================================================================


def test_trans_04_translation_failure():
    source_text = "Activity ACT-CIV-01 has 1 conflict."

    # Force LLM error
    with patch("backend.routers.summary._get_client", side_effect=Exception("Translation quota exhausted")):
        translated, was_cached = translate_dynamic_text(source_text, "hi")
        # Falls back to canonical English
        assert translated == source_text
        assert was_cached is False
        # Failed translation is NOT cached
        cache = get_translation_cache()
        assert len(cache) == 0


# =========================================================================
# AUTH-01: Supervisor-only access
# =========================================================================


def test_auth_01_supervisor_only():
    """Review-level access (REVIEW_CLAIM: supervisor/planner/PM/owner) on an explicit schedule of the caller's project."""
    from tests.v7ctx import act_as

    client = TestClient(app)

    # 1. Unauthenticated -> 401
    resp_unauth = client.get("/api/v1/execution-summary")
    assert resp_unauth.status_code == 401

    # 2. Site engineer (no REVIEW_CLAIM) -> 403
    with act_as("SITE_ENGINEER", schedule_id="SCH-1"):
        resp_forbidden = client.get("/api/v1/execution-summary")
    assert resp_forbidden.status_code == 403

    # 3. No explicit schedule -> 400, never the "active" one
    with act_as("SUPERVISOR", schedule_id=None):
        assert client.get("/api/v1/execution-summary").status_code == 400

    # 4. Supervisor with explicit schedule -> 200 (mocking DB & LLM for clean test)
    mock_db = create_test_db()
    populate_standard_fixtures(mock_db, date.today())

    with act_as("SUPERVISOR", schedule_id="SCH-1"), patch("backend.routers.summary.get_connection") as mock_conn:
        mock_conn.return_value.__enter__.return_value = mock_db
        resp_ok = client.get("/api/v1/execution-summary?period=last_7_days")
        assert resp_ok.status_code == 200
        data = resp_ok.json()
        assert "canonical_summary" in data
        assert "summary" in data
        assert data["language"] == "en"


# =========================================================================
# ISS-05/ISS-06: build_deterministic_aggregate is schedule-scoped -- a
# second schedule's claims/actuals/conflicts/validation issues/activities
# must never leak into the requested schedule's AI Execution Summary
# (and therefore never skew its approval_rate_pct).
# =========================================================================


def _add_second_schedule(db: SQLitePsycopgAdapter, ref_date: date):
    ref_iso = ref_date.isoformat()
    d_minus_2 = (ref_date - timedelta(days=2)).isoformat()

    db.execute("INSERT INTO schedules (schedule_id, project_name) VALUES ('SCH-02', 'Other Project')")
    db.execute(
        """
        INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, is_critical, planned_start, planned_finish)
        VALUES ('ACT-ELE-01', 'SCH-02', 'Cable Pulling', 'ELECTRICAL', 0, '2026-09-01', '2026-09-10')
        """
    )
    # 3 rejected claims on Schedule B -- if these leaked into Schedule A's
    # aggregate they'd drag its approval_rate_pct down.
    db.execute(
        """
        INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, discipline, event_type, status, delay_reason)
        VALUES
            ('EV-B1', 'SCH-02', %s, 'Cable claim 1', 'TYPED_TEXT', 'ELECTRICAL', 'PROGRESS_UPDATE', 'REJECTED', NULL),
            ('EV-B2', 'SCH-02', %s, 'Cable claim 2', 'TYPED_TEXT', 'ELECTRICAL', 'PROGRESS_UPDATE', 'REJECTED', NULL),
            ('EV-B3', 'SCH-02', %s, 'Cable claim 3', 'TYPED_TEXT', 'ELECTRICAL', 'PROGRESS_UPDATE', 'REJECTED', NULL)
        """,
        (d_minus_2, d_minus_2, d_minus_2),
    )
    db.execute(
        """
        INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_start, actual_pct_complete)
        VALUES ('AA-B1', 'DEC-B1', 'EV-B1', 'SCH-02', 'ACT-ELE-01', %s, 20.0)
        """,
        (d_minus_2,),
    )
    db.execute(
        """
        INSERT INTO conflict_records (conflict_id, schedule_id, activity_id, reporting_period, event_id_a, event_id_b, value_a, value_b, variance_pct, status)
        VALUES ('CONF-B1', 'SCH-02', 'ACT-ELE-01', %s, 'EV-B1', 'EV-B2', 20.0, 10.0, 10.0, 'OPEN')
        """,
        (d_minus_2,),
    )
    db.execute(
        """
        INSERT INTO validation_issues (issue_id, event_id, rule_code, severity, description)
        VALUES ('VAL-B1', 'EV-B1', 'RULE-E01', 'ERROR', 'Cable claim missing evidence')
        """
    )
    db.commit()


def test_aggregate_schedule_isolation():
    ref_date = date.today()
    db = create_test_db()
    populate_standard_fixtures(db, ref_date)
    _add_second_schedule(db, ref_date)

    agg_a = build_deterministic_aggregate(
        period="last_7_days", discipline="ALL", conn=db, reference_date=ref_date, schedule_id="SCH-01",
    )
    agg_b = build_deterministic_aggregate(
        period="last_7_days", discipline="ALL", conn=db, reference_date=ref_date, schedule_id="SCH-02",
    )

    # Schedule A's claim/activity counts are unaffected by Schedule B's data
    # (3 claims from populate_standard_fixtures fall in the 7-day window: EV-01, EV-02, EV-03).
    assert agg_a["claims"]["total_claims"] == 3
    assert agg_a["activities"]["total"] == 3
    assert agg_a["conflicts"]["total_conflicts"] == 1
    assert agg_a["validation_issues"]["total_issues"] == 1

    # Schedule B is isolated to its own 3 rejected claims, 1 activity, 1 conflict, 1 validation issue
    assert agg_b["claims"]["total_claims"] == 3
    assert agg_b["claims"]["by_status"].get("REJECTED") == 3
    assert agg_b["activities"]["total"] == 1
    assert agg_b["conflicts"]["total_conflicts"] == 1
    assert agg_b["validation_issues"]["total_issues"] == 1

    # And the unscoped legacy call path (no schedule_id argument) still combines both,
    # documenting why every live endpoint must always pass schedule_id explicitly.
    combined = build_deterministic_aggregate(period="last_7_days", discipline="ALL", conn=db, reference_date=ref_date)
    assert combined["claims"]["total_claims"] == 6
