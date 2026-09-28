"""
SETUAI V7 — Phase 6: Activity Eligibility Tests
Tests canonical execution state eligibility, boundary percentages, and workflow condition separation.
"""

import pytest
from datetime import date
from backend.schemas.stage import CanonicalExecutionState, WorkflowCondition
from backend.services.matching_eligibility_service import (
    MatchingEligibilityService,
    REASON_ELIGIBLE,
    REASON_COMPLETED,
    REASON_BLOCKED,
    REASON_QUALITY_HOLD,
    REASON_REOPEN_REQUIRED,
    REASON_REWORK_REQUIRED,
)


def test_canonical_execution_state_eligibility():
    """
    NOT_STARTED -> eligible
    IN_PROGRESS -> eligible
    COMPLETED -> not eligible
    """
    # 1. NOT_STARTED: no start, no pct
    act_not_started = {
        "activity_id": "ACT-001",
        "actual_start": None,
        "actual_pct_complete": None,
    }
    res_not_started = MatchingEligibilityService.get_activity_eligibility(act_not_started)
    assert res_not_started.eligible is True
    assert res_not_started.execution_state == CanonicalExecutionState.NOT_STARTED.value
    assert res_not_started.reason == REASON_ELIGIBLE

    # 2. IN_PROGRESS: has start, pct < 100
    act_in_progress = {
        "activity_id": "ACT-002",
        "actual_start": date(2026, 9, 1),
        "actual_pct_complete": 50.0,
    }
    res_in_progress = MatchingEligibilityService.get_activity_eligibility(act_in_progress)
    assert res_in_progress.eligible is True
    assert res_in_progress.execution_state == CanonicalExecutionState.IN_PROGRESS.value
    assert res_in_progress.reason == REASON_ELIGIBLE

    # 3. COMPLETED: pct >= 100
    act_completed = {
        "activity_id": "ACT-003",
        "actual_start": date(2026, 9, 1),
        "actual_pct_complete": 100.0,
    }
    res_completed = MatchingEligibilityService.get_activity_eligibility(act_completed)
    assert res_completed.eligible is False
    assert res_completed.execution_state == CanonicalExecutionState.COMPLETED.value
    assert res_completed.reason == REASON_COMPLETED


def test_boundary_percentage_conditions():
    """
    Verify boundary conditions:
    actual_pct_complete = 99.9 -> IN_PROGRESS -> eligible
    actual_pct_complete = 100.0 -> COMPLETED -> NOT eligible
    actual_pct_complete = 100.1 -> COMPLETED -> NOT eligible
    actual_pct_complete = None, actual_start = None -> NOT_STARTED -> eligible
    actual_pct_complete = None, actual_start != None -> IN_PROGRESS -> eligible
    """
    # 99.9%
    res_99 = MatchingEligibilityService.get_activity_eligibility({
        "activity_id": "ACT-99",
        "actual_start": "2026-09-01",
        "actual_pct_complete": 99.9,
    })
    assert res_99.eligible is True
    assert res_99.execution_state == CanonicalExecutionState.IN_PROGRESS.value

    # 100.0%
    res_100 = MatchingEligibilityService.get_activity_eligibility({
        "activity_id": "ACT-100",
        "actual_start": "2026-09-01",
        "actual_pct_complete": 100.0,
    })
    assert res_100.eligible is False
    assert res_100.execution_state == CanonicalExecutionState.COMPLETED.value
    assert res_100.reason == REASON_COMPLETED

    # 100.1%
    res_100_1 = MatchingEligibilityService.get_activity_eligibility({
        "activity_id": "ACT-100-1",
        "actual_start": "2026-09-01",
        "actual_pct_complete": 100.1,
    })
    assert res_100_1.eligible is False
    assert res_100_1.execution_state == CanonicalExecutionState.COMPLETED.value
    assert res_100_1.reason == REASON_COMPLETED

    # NULL pct, NULL start
    res_null = MatchingEligibilityService.get_activity_eligibility({
        "activity_id": "ACT-NULL",
        "actual_start": None,
        "actual_pct_complete": None,
    })
    assert res_null.eligible is True
    assert res_null.execution_state == CanonicalExecutionState.NOT_STARTED.value

    # NULL pct, valid start
    res_start_only = MatchingEligibilityService.get_activity_eligibility({
        "activity_id": "ACT-START",
        "actual_start": "2026-09-01",
        "actual_pct_complete": None,
    })
    assert res_start_only.eligible is True
    assert res_start_only.execution_state == CanonicalExecutionState.IN_PROGRESS.value


def test_workflow_condition_separation_and_exclusion():
    """
    Canonical execution state and workflow condition must remain separate concepts.
    COMPLETED + QUALITY_HOLD -> canonical state is COMPLETED, condition is QUALITY_HOLD, not eligible.
    IN_PROGRESS + BLOCKED -> canonical state is IN_PROGRESS, condition is BLOCKED, not eligible.
    REOPEN_REQUESTED -> REOPEN_WORKFLOW_REQUIRED, not eligible.
    REWORK_IN_PROGRESS -> REWORK_WORKFLOW_REQUIRED, not eligible.
    """
    # 1. COMPLETED with QUALITY_HOLD
    act_completed_hold = {
        "activity_id": "ACT-CH",
        "actual_start": "2026-08-01",
        "actual_pct_complete": 100.0,
        "status": "QUALITY_HOLD",
    }
    res_ch = MatchingEligibilityService.get_activity_eligibility(act_completed_hold)
    assert res_ch.execution_state == CanonicalExecutionState.COMPLETED.value
    assert res_ch.workflow_condition == WorkflowCondition.QUALITY_HOLD.value
    assert res_ch.eligible is False
    assert res_ch.reason == REASON_COMPLETED  # Completed invariant takes priority

    # 2. IN_PROGRESS with BLOCKED
    act_blocked = {
        "activity_id": "ACT-BLK",
        "actual_start": "2026-08-01",
        "actual_pct_complete": 40.0,
        "status": "BLOCKED",
    }
    res_blk = MatchingEligibilityService.get_activity_eligibility(act_blocked)
    assert res_blk.execution_state == CanonicalExecutionState.IN_PROGRESS.value
    assert res_blk.workflow_condition == WorkflowCondition.BLOCKED.value
    assert res_blk.eligible is False
    assert res_blk.reason == REASON_BLOCKED

    # 3. REOPEN_REQUESTED
    act_reopen = {
        "activity_id": "ACT-REOPEN",
        "actual_start": "2026-08-01",
        "actual_pct_complete": 100.0,
        "reopen_status": "REQUESTED",
    }
    # When completed, completion check takes priority, but if checked prior to 100% or under reopen
    res_reopen = MatchingEligibilityService.get_activity_eligibility({
        "activity_id": "ACT-REOPEN-2",
        "actual_start": "2026-08-01",
        "actual_pct_complete": 50.0,
        "reopen_status": "REQUESTED",
    })
    assert res_reopen.workflow_condition == WorkflowCondition.REOPEN_REQUESTED.value
    assert res_reopen.eligible is False
    assert res_reopen.reason == REASON_REOPEN_REQUIRED

    # 4. REWORK_IN_PROGRESS
    res_rework = MatchingEligibilityService.get_activity_eligibility({
        "activity_id": "ACT-REWORK",
        "actual_start": "2026-08-01",
        "actual_pct_complete": 50.0,
        "is_reopened": True,
    })
    assert res_rework.workflow_condition == WorkflowCondition.REWORK_IN_PROGRESS.value
    assert res_rework.eligible is False
    assert res_rework.reason == REASON_REWORK_REQUIRED
