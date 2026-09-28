"""
SETUAI V7 Phase 7 Tests — Rework Matching Engine Integration
Tests eligibility service and matching router candidate selection under normal vs rework matching.
"""

import pytest
from datetime import date

from backend.routers.matching import match_claim
from backend.schemas.stage import CanonicalExecutionState, WorkflowCondition
from backend.services.matching_eligibility_service import (
    MatchingEligibilityService,
    REASON_COMPLETED,
    REASON_ELIGIBLE,
    REASON_REOPEN_REQUIRED,
    REASON_REWORK_REQUIRED,
)


def test_normal_matching_excludes_rework_in_progress():
    """In normal matching (is_rework=False), REWORK_IN_PROGRESS is excluded."""
    act_rework = {
        "activity_id": "ACT-REW-01",
        "actual_start": date(2026, 8, 1),
        "actual_pct_complete": 100.0,
        "is_reopened": True,
        "reopen_status": "APPROVED",
    }
    res = MatchingEligibilityService.get_activity_eligibility(act_rework, is_rework=False)
    assert res.eligible is False
    assert res.reason == REASON_COMPLETED
    assert res.workflow_condition == WorkflowCondition.REWORK_IN_PROGRESS.value


def test_rework_matching_permits_rework_in_progress():
    """Under authorized rework context (is_rework=True), REWORK_IN_PROGRESS is eligible."""
    act_rework = {
        "activity_id": "ACT-REW-01",
        "actual_start": date(2026, 8, 1),
        "actual_pct_complete": 100.0,
        "is_reopened": True,
        "reopen_status": "APPROVED",
    }
    res = MatchingEligibilityService.get_activity_eligibility(act_rework, is_rework=True)
    assert res.eligible is True
    assert res.reason == REASON_ELIGIBLE
    assert res.execution_state == CanonicalExecutionState.COMPLETED.value
    assert res.workflow_condition == WorkflowCondition.REWORK_IN_PROGRESS.value


def test_rework_matching_strictly_excludes_unopened_completed():
    """Under rework context (is_rework=True), an activity that is completed but NOT reopened remains excluded."""
    act_completed = {
        "activity_id": "ACT-COMP-01",
        "actual_start": date(2026, 8, 1),
        "actual_pct_complete": 100.0,
        "is_reopened": False,
        "reopen_status": "NONE",
    }
    res = MatchingEligibilityService.get_activity_eligibility(act_completed, is_rework=True)
    assert res.eligible is False
    assert res.reason == REASON_COMPLETED


def test_rework_matching_router_exact_id():
    """
    Router match_claim test:
    Claim marked with is_rework=True correctly matches the reopened activity,
    while claim marked is_rework=False is rejected.
    """
    activities = [
        {
            "activity_id": "ACT-REW",
            "schedule_id": "SCHED-01",
            "activity_name": "Concrete Grouting Rework",
            "discipline": "Civil",
            "location": "Sector 4",
            "actual_pct_complete": 100.0,
            "actual_start": "2026-08-01",
            "is_reopened": True,
            "reopen_status": "APPROVED",
        },
    ]

    # Normal claim without rework flag
    normal_claim = {
        "event_id": "EV-NORM",
        "schedule_id": "SCHED-01",
        "reported_activity_id": "ACT-REW",
        "raw_claim_text": "Concrete Grouting",
        "is_rework": False,
    }
    normal_matches = match_claim(normal_claim, activities)
    assert len(normal_matches) == 0, "Normal matching must exclude rework activity"

    # Authorized rework claim
    rework_claim = {
        "event_id": "EV-REWORK",
        "schedule_id": "SCHED-01",
        "reported_activity_id": "ACT-REW",
        "raw_claim_text": "Concrete Grouting Rework Phase 1",
        "is_rework": True,
        "reopened_from_actual_id": "ACTUAL-ORIG-01",
    }
    rework_matches = match_claim(rework_claim, activities)
    assert len(rework_matches) >= 1
    assert rework_matches[0].activity_id == "ACT-REW"
