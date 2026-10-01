"""
SETUAI V7 — Phase 6: Stage-Aware Matching Tests
Verifies stage scoping, completed stage behavior, and deterministic no-eligible-candidate handling.
"""

import uuid
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from tests.v7ctx import act_as_event
from backend.services.matching_eligibility_service import (
    MatchingEligibilityService,
    REASON_STAGE_COMPLETED,
    REASON_WRONG_STAGE,
    REASON_COMPLETED,
)
from backend.shared.auth import UserProfile, get_current_user


def test_stage_scoping_in_eligibility():
    """
    Activities belonging to Stage 2 must be marked ineligible when matching within Stage 1 context.
    """
    stage_1 = uuid.uuid4()
    stage_2 = uuid.uuid4()

    act_stage_2 = {
        "activity_id": "ACT-STAGE-2",
        "stage_id": str(stage_2),
        "schedule_id": "SCHED-V1",
        "activity_name": "Piping Stage 2",
        "actual_pct_complete": 0.0,
    }

    res = MatchingEligibilityService.get_activity_eligibility(
        act_stage_2,
        expected_stage_id=str(stage_1),
    )
    assert res.eligible is False
    assert res.reason == REASON_WRONG_STAGE


def test_completed_stage_produces_zero_normal_candidates():
    """
    When all activities assigned to a stage are COMPLETED,
    the eligible candidate count for that stage must be exactly 0.
    """
    stage_id = uuid.uuid4()

    stage_activities = [
        {
            "activity_id": "ACT-STG-1",
            "stage_id": str(stage_id),
            "schedule_id": "SCHED-01",
            "activity_name": "Excavation",
            "actual_pct_complete": 100.0,
            "actual_start": "2026-08-01",
        },
        {
            "activity_id": "ACT-STG-2",
            "stage_id": str(stage_id),
            "schedule_id": "SCHED-01",
            "activity_name": "Compaction",
            "actual_pct_complete": 100.0,
            "actual_start": "2026-08-05",
        },
    ]

    eligible, explanations = MatchingEligibilityService.filter_eligible_activities(
        stage_activities,
        expected_stage_id=str(stage_id),
    )

    # 0 normal eligible candidates
    assert len(eligible) == 0
    assert explanations["ACT-STG-1"].reason == REASON_COMPLETED
    assert explanations["ACT-STG-2"].reason == REASON_COMPLETED


def test_completed_stage_match_endpoint_response():
    """
    When matching a claim in a stage where all activities are completed,
    the match endpoint returns UNMATCHED with deterministic explainable reason.
    """
    client = TestClient(app)
    user = UserProfile(id=str(uuid.uuid4()), email="eng@setuai.com", role="site_engineer")
    app.dependency_overrides[get_current_user] = lambda: user

    stage_id = str(uuid.uuid4())
    mock_event = {
        "event_id": "EVT-STG-COMP",
        "project_id": str(uuid.uuid4()),
        "schedule_id": "SCHED-01",
        "stage_id": stage_id,
        "event_date": "2026-09-01",
        "input_channel": "TYPED_TEXT",
        "raw_claim_text": "Excavation Work",
        "clarification_status": None,
        "reported_activity_id": None,
        "asset_tag": None,
        "discipline": "Civil",
        "location": "Zone 1",
    }

    mock_act = {
        "activity_id": "ACT-STG-1",
        "schedule_id": "SCHED-01",
        "project_id": mock_event["project_id"],
        "stage_id": stage_id,
        "activity_name": "Excavation Work",
        "discipline": "Civil",
        "location": "Zone 1",
        "asset_tag": None,
        "planned_start": "2026-08-01",
        "planned_finish": "2026-08-15",
        "planned_quantity": 100.0,
        "uom": "m3",
        "baseline_pct_complete": 0.0,
        "actual_pct_complete": 100.0,  # Canonically COMPLETED
        "actual_start": "2026-08-01",
        "actual_finish": "2026-08-15",
        "is_reopened": False,
        "reopen_status": None,
    }

    with patch("backend.routers.matching.get_connection") as mock_conn_fn:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn_fn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        mock_cur.fetchone.return_value = mock_event
        mock_cur.fetchall.return_value = [mock_act]

        with act_as_event(event_id="EVT-STG-COMP", schedule_id="SCHED-01", project_id=mock_event["project_id"]):
            resp = client.post("/api/v1/claims/EVT-STG-COMP/match")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "UNMATCHED"
    assert data["matched_activity_id"] is None
    assert len(data["candidates"]) == 0
    assert "NO_ELIGIBLE_CANDIDATE" in data["unmatched_reason"]
    assert "stage are COMPLETED" in data["unmatched_reason"]

    app.dependency_overrides.clear()
