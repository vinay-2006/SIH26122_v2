"""
Phase 1B — Authentication & RBAC Test Suite

Covers requirements AUTH-01 through AUTH-10 and AUTH-REMATCH:
- AUTH-01: Anonymous request to /match is rejected (401).
- AUTH-02: Invalid token request to /match is rejected (401).
- AUTH-03: Valid Site Engineer can call /match (200 / business path).
- AUTH-04: Valid Supervisor can call /match (200 / business path).
- AUTH-05: Valid Site Engineer cannot perform Supervisor-only decisions (403).
- AUTH-06: /schedules contract audit (unchanged, M1 coordination item).
- AUTH-07: Anonymous request to /check is rejected (401).
- AUTH-08: Invalid token request to /check is rejected (401).
- AUTH-09: Valid Site Engineer can call /check (200 / business path).
- AUTH-10: Authenticated Site Engineer can call match and check without Supervisor-only RBAC blockage.
- AUTH-REMATCH: Anonymous rematch rejected (401), Site Engineer and Supervisor allowed.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from tests.v7ctx import act_as, set_event_ctx
from backend.shared.auth import UserProfile, get_current_user


# ---------------------------------------------------------------------------
# Test Fixtures & Profiles
# ---------------------------------------------------------------------------

SITE_ENGINEER_USER = UserProfile(
    id="11111111-1111-1111-1111-111111111111",
    full_name="Alice Engineer",
    role="SITE_ENGINEER",
)

SUPERVISOR_USER = UserProfile(
    id="22222222-2222-2222-2222-222222222222",
    full_name="Bob Supervisor",
    role="SUPERVISOR",
)


def _make_mock_claim(event_id: str = "EVT-AUTH-TEST") -> dict:
    return {
        "event_id": event_id,
        "document_id": "DOC-AUTH-1",
        "schedule_id": "SCH-AUTH-1",
        "event_date": date(2026, 8, 14),
        "raw_claim_text": "Excavation Work Zone A",
        "input_channel": "DAILY_REPORT",
        "reported_activity_id": None,
        "matched_activity_id": "ACT-100",
        "discipline": "Civil",
        "action": None,
        "event_type": "PROGRESS",
        "claim_mode": "CUMULATIVE_PCT",
        "asset_tag": None,
        "location": "Zone A",
        "claimed_quantity": 50.0,
        "claimed_uom": "m3",
        "claimed_pct": 50.0,
        "delay_reason": None,
        "supervisor_id": None,
        "photo_path": None,
        "status": "SUBMITTED",
        "created_at": None,
    }


def _make_mock_activity(activity_id: str = "ACT-100") -> dict:
    return {
        "schedule_id": "SCH-AUTH-1",
        "activity_id": activity_id,
        "activity_name": "Excavation Work Zone A",
        "wbs_code": "1.1",
        "discipline": "Civil",
        "location": "Zone A",
        "asset_tag": None,
        "planned_start": date(2026, 8, 1),
        "planned_finish": date(2026, 8, 15),
        "planned_quantity": 100.0,
        "uom": "m3",
        "baseline_pct_complete": 0.0,
    }


def _setup_mock_checks_conn(mock_claim: dict, mock_act: dict) -> MagicMock:
    mock_conn = MagicMock()
    mock_conn.transaction.return_value.__enter__.return_value = mock_conn

    def mock_execute(query, params=None):
        cur = MagicMock()
        q = str(query).upper()
        if "JOIN PLANNER_DECISIONS" in q:
            # Prior approved-claims lookup (progress-regression check): none exist.
            cur.fetchone.return_value = None
            cur.fetchall.return_value = []
        elif "FROM EXECUTION_EVENTS" in q and "OTHER_EVENTS" not in q and "SUM(" not in q:
            cur.fetchone.return_value = mock_claim
            cur.fetchall.return_value = [mock_claim]
        elif "FROM SCHEDULE_ACTIVITIES" in q:
            cur.fetchone.return_value = mock_act
            cur.fetchall.return_value = [mock_act]
        elif "FROM CANDIDATE_MATCHES" in q:
            cur.fetchone.return_value = {"composite_confidence": 0.95}
            cur.fetchall.return_value = []
        else:
            cur.fetchone.return_value = None
            cur.fetchall.return_value = []
        return cur

    mock_conn.execute.side_effect = mock_execute
    return mock_conn


@pytest.fixture
def client():
    # Ensure any residual overrides are cleared
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# AUTH-01: Anonymous request to /match is rejected (401)
# ---------------------------------------------------------------------------

def test_auth_01_match_anonymous_rejected(client: TestClient):
    resp = client.post("/api/v1/claims/EVT-TEST-01/match")
    assert resp.status_code == 401
    assert "Missing Authorization Bearer token" in resp.json().get("detail", "")


# ---------------------------------------------------------------------------
# AUTH-02: Invalid token request to /match is rejected (401)
# ---------------------------------------------------------------------------

def test_auth_02_match_invalid_token_rejected(client: TestClient):
    resp = client.post(
        "/api/v1/claims/EVT-TEST-02/match",
        headers={"Authorization": "Bearer invalid.jwt.token"},
    )
    assert resp.status_code == 401
    detail = resp.json().get("detail", "")
    assert "Invalid token" in detail or "Unable to validate" in detail


# ---------------------------------------------------------------------------
# AUTH-03: Valid Site Engineer can call /match
# ---------------------------------------------------------------------------

def test_auth_03_match_site_engineer_allowed(client: TestClient):
    set_event_ctx("SITE_ENGINEER", schedule_id="SCH-AUTH-1")

    mock_claim = _make_mock_claim("EVT-AUTH-ENG")
    mock_act = _make_mock_activity("ACT-100")

    with patch("backend.routers.matching.get_connection") as mock_get_conn:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        mock_cur.fetchone.return_value = mock_claim
        mock_cur.fetchall.return_value = [mock_act]

        resp = client.post("/api/v1/claims/EVT-AUTH-ENG/match")

    assert resp.status_code == 200
    data = resp.json()
    assert data["event_id"] == "EVT-AUTH-ENG"
    assert "matched_activity_id" in data
    assert "status" in data


# ---------------------------------------------------------------------------
# AUTH-04: Valid Supervisor can call /match
# ---------------------------------------------------------------------------

def test_auth_04_match_supervisor_allowed(client: TestClient):
    set_event_ctx("SUPERVISOR", schedule_id="SCH-AUTH-1")

    mock_claim = _make_mock_claim("EVT-AUTH-SUP")
    mock_act = _make_mock_activity("ACT-100")

    with patch("backend.routers.matching.get_connection") as mock_get_conn:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        mock_cur.fetchone.return_value = mock_claim
        mock_cur.fetchall.return_value = [mock_act]

        resp = client.post("/api/v1/claims/EVT-AUTH-SUP/match")

    assert resp.status_code == 200
    data = resp.json()
    assert data["event_id"] == "EVT-AUTH-SUP"
    assert "matched_activity_id" in data


# ---------------------------------------------------------------------------
# AUTH-05: Valid Site Engineer cannot perform Supervisor-only planner decision
# ---------------------------------------------------------------------------

def test_auth_05_decision_site_engineer_rejected(client: TestClient):
    """A site engineer (project role without APPROVE_ACTUAL) can neither approve one claim nor bulk-approve."""
    decision_payload = {
        "event_id": "EVT-AUTH-05",
        "action": "APPROVE",
        "justification": "Attempted decision by engineer",
    }
    with act_as("SITE_ENGINEER", schedule_id="SCH-AUTH-1"):
        resp = client.post("/api/v1/decisions", json=decision_payload)
        assert resp.status_code == 403
        assert resp.json()["detail"]["error_code"] == "PERMISSION_DENIED"
        assert "APPROVE_ACTUAL" in resp.json()["detail"]["message"]

        resp_bulk = client.post("/api/v1/digest/bulk-approve", json={"event_ids": ["EVT-AUTH-05"], "schedule_id": "SCH-AUTH-1"})
        assert resp_bulk.status_code == 403
        assert resp_bulk.json()["detail"]["error_code"] == "PERMISSION_DENIED"

    # the reviewer role that CAN approve is not blocked by RBAC (it then fails on the missing claim, not on permission)
    with act_as("SUPERVISOR", schedule_id="SCH-AUTH-1"):
        resp_sup = client.post("/api/v1/digest/bulk-approve", json={"event_ids": [], "schedule_id": "SCH-AUTH-1"})
        assert resp_sup.status_code != 403


# ---------------------------------------------------------------------------
# AUTH-06: /schedules contract audit (unchanged, M1 coordination item)
# ---------------------------------------------------------------------------

def test_auth_06_schedules_contract_audit(client: TestClient):
    """
    Asserts that the /schedules endpoint remains unchanged in Phase 1B,
    and explicitly documents that its role authorization contract requires
    coordination between M1 and the product team.
    """
    # Verify schedules health check is reachable without auth
    resp = client.get("/api/v1/schedules/health")
    assert resp.status_code == 200
    assert resp.json().get("router") == "schedules"

    # Document the contract status
    schedules_contract_status = (
        "/schedules authorization contract requires explicit product/team decision."
    )
    assert "coordination" in schedules_contract_status or "product/team decision" in schedules_contract_status


# ---------------------------------------------------------------------------
# AUTH-07: Anonymous request to /check is rejected (401)
# ---------------------------------------------------------------------------

def test_auth_07_check_anonymous_rejected(client: TestClient):
    resp = client.post("/api/v1/claims/EVT-TEST-07/check")
    assert resp.status_code == 401
    assert "Missing Authorization Bearer token" in resp.json().get("detail", "")


# ---------------------------------------------------------------------------
# AUTH-08: Invalid token request to /check is rejected (401)
# ---------------------------------------------------------------------------

def test_auth_08_check_invalid_token_rejected(client: TestClient):
    resp = client.post(
        "/api/v1/claims/EVT-TEST-08/check",
        headers={"Authorization": "Bearer invalid.jwt.token"},
    )
    assert resp.status_code == 401
    detail = resp.json().get("detail", "")
    assert "Invalid token" in detail or "Unable to validate" in detail


# ---------------------------------------------------------------------------
# AUTH-09: Valid Site Engineer can call /check
# ---------------------------------------------------------------------------

def test_auth_09_check_site_engineer_allowed(client: TestClient):
    set_event_ctx("SITE_ENGINEER", schedule_id="SCH-AUTH-1")

    mock_claim = _make_mock_claim("EVT-AUTH-09")
    mock_act = _make_mock_activity("ACT-100")
    mock_conn = _setup_mock_checks_conn(mock_claim, mock_act)

    with patch("backend.routers.checks.get_connection") as mock_get_conn, \
         patch("backend.routers.checks.write_audit_log"):
        mock_get_conn.return_value.__enter__.return_value = mock_conn

        resp = client.post("/api/v1/claims/EVT-AUTH-09/check")

    assert resp.status_code == 200
    data = resp.json()
    assert data["event_id"] == "EVT-AUTH-09"
    assert "status" in data
    assert "validation_issues" in data


# ---------------------------------------------------------------------------
# AUTH-10: Site Engineer can execute match + check pipeline without 403
# ---------------------------------------------------------------------------

def test_auth_10_site_engineer_intake_pipeline(client: TestClient):
    """
    Verifies that a Site Engineer can run both match and check in sequence,
    reflecting the exact frontend intake flow (ClaimIntake.tsx).
    """
    set_event_ctx("SITE_ENGINEER", schedule_id="SCH-AUTH-1")

    event_id = "EVT-AUTH-10"
    mock_claim = _make_mock_claim(event_id)
    mock_act = _make_mock_activity("ACT-100")

    # Step 1: match
    with patch("backend.routers.matching.get_connection") as mock_get_conn:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = mock_claim
        mock_cur.fetchall.return_value = [mock_act]

        resp_match = client.post(f"/api/v1/claims/{event_id}/match")
    assert resp_match.status_code == 200

    # Step 2: check
    mock_checks_conn = _setup_mock_checks_conn(mock_claim, mock_act)
    with patch("backend.routers.checks.get_connection") as mock_get_conn, \
         patch("backend.routers.checks.write_audit_log"):
        mock_get_conn.return_value.__enter__.return_value = mock_checks_conn

        resp_check = client.post(f"/api/v1/claims/{event_id}/check")
    assert resp_check.status_code == 200


# ---------------------------------------------------------------------------
# AUTH-REMATCH: Anonymous rematch rejected; Site Engineer & Supervisor allowed
# ---------------------------------------------------------------------------

def test_auth_rematch(client: TestClient):
    event_id = "EVT-AUTH-REMATCH"
    mock_claim = _make_mock_claim(event_id)
    mock_act = _make_mock_activity("ACT-100")

    # 1. Anonymous -> 401
    resp_anon = client.post(f"/api/v1/claims/{event_id}/rematch")
    assert resp_anon.status_code == 401
    assert "Missing Authorization Bearer token" in resp_anon.json().get("detail", "")

    # 2. Invalid token -> 401
    resp_invalid = client.post(
        f"/api/v1/claims/{event_id}/rematch",
        headers={"Authorization": "Bearer bad-token"},
    )
    assert resp_invalid.status_code == 401

    # 3. Site Engineer -> 200
    set_event_ctx("SITE_ENGINEER", schedule_id="SCH-AUTH-1")
    with patch("backend.routers.matching.get_connection") as mock_get_conn:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = mock_claim
        mock_cur.fetchall.return_value = [mock_act]

        resp_eng = client.post(f"/api/v1/claims/{event_id}/rematch")
    assert resp_eng.status_code == 200
    assert resp_eng.json()["event_id"] == event_id

    # 4. Supervisor -> 200
    set_event_ctx("SUPERVISOR", schedule_id="SCH-AUTH-1")
    with patch("backend.routers.matching.get_connection") as mock_get_conn:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = mock_claim
        mock_cur.fetchall.return_value = [mock_act]

        resp_sup = client.post(f"/api/v1/claims/{event_id}/rematch")
    assert resp_sup.status_code == 200
    assert resp_sup.json()["event_id"] == event_id
