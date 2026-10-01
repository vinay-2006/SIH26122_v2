"""
SETUAI V7 — Phase 6: Project & Schedule Isolation Matching Tests
Verifies strict project isolation and schedule version isolation during candidate retrieval and matching.
"""

import uuid
from unittest.mock import MagicMock, patch
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.main import app
from tests.v7ctx import act_as_event
from backend.routers.matching import match_claim, match_exact_id
from backend.services.matching_eligibility_service import (
    MatchingEligibilityService,
    REASON_WRONG_PROJECT,
    REASON_WRONG_SCHEDULE,
    REASON_COMPLETED,
)
from backend.shared.auth import UserProfile, get_current_user


def test_project_isolation_in_eligibility():
    """
    Candidate activity from Project B must be marked ineligible for Project A event.
    """
    proj_a = uuid.uuid4()
    proj_b = uuid.uuid4()

    act_proj_b = {
        "activity_id": "ACT-B",
        "project_id": str(proj_b),
        "schedule_id": "SCHED-V1",
        "activity_name": "Excavation",
        "actual_pct_complete": 0.0,
    }

    # Tested against expected Project A
    eligibility = MatchingEligibilityService.get_activity_eligibility(
        act_proj_b,
        expected_project_id=str(proj_a),
        expected_schedule_id="SCHED-V1",
    )
    assert eligibility.eligible is False
    assert eligibility.reason == REASON_WRONG_PROJECT


def test_schedule_isolation_in_eligibility():
    """
    Project A has Schedule V1 and Schedule V2.
    A V2 report must never match a V1 activity.
    """
    proj_id = uuid.uuid4()

    act_v1 = {
        "activity_id": "ACT-COMMON",
        "project_id": str(proj_id),
        "schedule_id": "SCHED-V1",
        "activity_name": "Structural Steel Erection",
        "actual_pct_complete": 0.0,
    }

    # Tested against expected Schedule V2
    eligibility = MatchingEligibilityService.get_activity_eligibility(
        act_v1,
        expected_project_id=str(proj_id),
        expected_schedule_id="SCHED-V2",
    )
    assert eligibility.eligible is False
    assert eligibility.reason == REASON_WRONG_SCHEDULE


def test_matching_cascade_rejects_cross_schedule_activities():
    """
    Matching cascade for Schedule V2 ignores activities from Schedule V1 even with identical IDs.
    """
    activities = [
        {
            "activity_id": "ACT-100",
            "schedule_id": "SCHED-V1",
            "activity_name": "HVAC Ducting V1",
            "actual_pct_complete": 0.0,
        },
        {
            "activity_id": "ACT-100",
            "schedule_id": "SCHED-V2",
            "activity_name": "HVAC Ducting V2",
            "actual_pct_complete": 0.0,
        },
    ]

    claim_v2 = {
        "event_id": "EVT-V2",
        "schedule_id": "SCHED-V2",
        "reported_activity_id": "ACT-100",
        "raw_claim_text": "HVAC Ducting V2",
    }

    candidates = match_claim(claim_v2, activities)
    assert len(candidates) == 1
    assert candidates[0].activity_id == "ACT-100"
    assert candidates[0].schedule_id == "SCHED-V2"


def test_missing_schedule_context_rejected_400():
    """
    If execution event has no valid schedule_id, match endpoint raises HTTP 400 INVALID_SCHEDULE_CONTEXT.
    """
    client = TestClient(app)
    user = UserProfile(id=str(uuid.uuid4()), email="eng@setuai.com", role="site_engineer")
    app.dependency_overrides[get_current_user] = lambda: user

    mock_event = {
        "event_id": "EVT-NO-SCHED",
        "project_id": str(uuid.uuid4()),
        "schedule_id": None,  # Missing schedule context!
        "raw_claim_text": "Some work",
        "clarification_status": None,
    }

    with patch("backend.routers.matching.get_connection") as mock_conn_fn:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn_fn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = mock_event

        with act_as_event(event_id="EVT-NO-SCHED", schedule_id=None, project_id=mock_event["project_id"]):
            resp = client.post("/api/v1/claims/EVT-NO-SCHED/match")

    assert resp.status_code == 400
    assert "INVALID_SCHEDULE_CONTEXT" in resp.json()["detail"]
    app.dependency_overrides.clear()
