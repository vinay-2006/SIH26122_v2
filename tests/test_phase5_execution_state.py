"""
Tests for Phase 5 — Deterministic Canonical Execution State and Workflow Condition Engines.
"""

from __future__ import annotations

import pytest
from backend.schemas.stage import CanonicalExecutionState, WorkflowCondition
from backend.services.stage_service import StageService
from backend.shared.actuals import get_execution_state as shared_get_execution_state


class DummyActivity:
    def __init__(self, actual_pct_complete=None, actual_start=None):
        self.actual_pct_complete = actual_pct_complete
        self.actual_start = actual_start


def test_canonical_execution_state_not_started():
    # 1. None / None -> NOT_STARTED
    assert StageService.get_execution_state(actual_pct_complete=None, actual_start=None) == CanonicalExecutionState.NOT_STARTED.value
    assert shared_get_execution_state(actual_pct_complete=None, actual_start=None) == "NOT_STARTED"

    # 2. 0.0 / None -> NOT_STARTED
    assert StageService.get_execution_state(actual_pct_complete=0.0, actual_start=None) == CanonicalExecutionState.NOT_STARTED.value
    assert shared_get_execution_state(actual_pct_complete=0.0, actual_start=None) == "NOT_STARTED"

    # 3. Via dict
    assert StageService.get_execution_state({"actual_pct_complete": None, "actual_start": None}) == CanonicalExecutionState.NOT_STARTED.value
    assert StageService.get_execution_state({"actual_pct_complete": 0, "actual_start": None}) == CanonicalExecutionState.NOT_STARTED.value

    # 4. Via object
    obj = DummyActivity(actual_pct_complete=0.0, actual_start=None)
    assert shared_get_execution_state(obj) == "NOT_STARTED"


def test_canonical_execution_state_in_progress():
    # 1. 20% with actual_start -> IN_PROGRESS
    assert StageService.get_execution_state(actual_pct_complete=20.0, actual_start="2026-06-01") == CanonicalExecutionState.IN_PROGRESS.value
    assert shared_get_execution_state(actual_pct_complete=20.0, actual_start="2026-06-01") == "IN_PROGRESS"

    # 2. 99% with actual_start -> IN_PROGRESS
    assert StageService.get_execution_state(actual_pct_complete=99.0, actual_start="2026-06-01") == CanonicalExecutionState.IN_PROGRESS.value
    assert shared_get_execution_state(actual_pct_complete=99.0, actual_start="2026-06-01") == "IN_PROGRESS"

    # 3. None pct with valid actual_start -> IN_PROGRESS
    assert StageService.get_execution_state(actual_pct_complete=None, actual_start="2026-06-01") == CanonicalExecutionState.IN_PROGRESS.value
    assert shared_get_execution_state(actual_pct_complete=None, actual_start="2026-06-01") == "IN_PROGRESS"

    # 4. 0% pct with valid actual_start -> IN_PROGRESS (started on site, 0% measured)
    assert StageService.get_execution_state(actual_pct_complete=0.0, actual_start="2026-06-01") == CanonicalExecutionState.IN_PROGRESS.value
    assert shared_get_execution_state(actual_pct_complete=0.0, actual_start="2026-06-01") == "IN_PROGRESS"


def test_canonical_execution_state_completed():
    # 1. 100% with actual_start -> COMPLETED
    assert StageService.get_execution_state(actual_pct_complete=100.0, actual_start="2026-06-01") == CanonicalExecutionState.COMPLETED.value
    assert shared_get_execution_state(actual_pct_complete=100.0, actual_start="2026-06-01") == "COMPLETED"

    # 2. 100% with None actual_start -> COMPLETED (precedence rule)
    assert StageService.get_execution_state(actual_pct_complete=100.0, actual_start=None) == CanonicalExecutionState.COMPLETED.value
    assert shared_get_execution_state(actual_pct_complete=100.0, actual_start=None) == "COMPLETED"

    # 3. > 100% (e.g. 105%) -> COMPLETED
    assert StageService.get_execution_state(actual_pct_complete=105.0, actual_start="2026-06-01") == CanonicalExecutionState.COMPLETED.value
    assert shared_get_execution_state(actual_pct_complete=105.0, actual_start="2026-06-01") == "COMPLETED"

    # 4. String representation "100" -> COMPLETED
    assert StageService.get_execution_state(actual_pct_complete="100.0", actual_start="2026-06-01") == CanonicalExecutionState.COMPLETED.value
    assert shared_get_execution_state(actual_pct_complete="100.0", actual_start="2026-06-01") == "COMPLETED"


def test_canonical_execution_state_edge_cases():
    # Empty string or whitespace start is treated as None
    assert StageService.get_execution_state(actual_pct_complete=None, actual_start="") == CanonicalExecutionState.NOT_STARTED.value
    assert StageService.get_execution_state(actual_pct_complete=None, actual_start="   ") == CanonicalExecutionState.NOT_STARTED.value
    assert shared_get_execution_state(actual_pct_complete=None, actual_start="") == "NOT_STARTED"

    # Negative percentage clamped or parsed
    assert StageService.get_execution_state(actual_pct_complete=-5.0, actual_start=None) == CanonicalExecutionState.NOT_STARTED.value


def test_workflow_condition_separation_from_execution_state():
    # Case 1: Execution State COMPLETED while Workflow Condition is QUALITY_HOLD
    act_data_1 = {
        "actual_pct_complete": 100.0,
        "actual_start": "2026-06-01",
        "status": "QUALITY_HOLD",
    }
    exec_state = StageService.get_execution_state(act_data_1)
    workflow_cond = StageService.get_workflow_condition(act_data_1)

    assert exec_state == CanonicalExecutionState.COMPLETED.value
    assert workflow_cond == WorkflowCondition.QUALITY_HOLD.value
    # Invariant: exec_state is NOT replaced by QUALITY_HOLD
    assert exec_state != "QUALITY_HOLD"

    # Case 2: Execution State COMPLETED while Workflow Condition is REOPEN_REQUESTED
    act_data_2 = {
        "actual_pct_complete": 100.0,
        "actual_start": "2026-06-01",
        "reopen_status": "REQUESTED",
    }
    assert StageService.get_execution_state(act_data_2) == CanonicalExecutionState.COMPLETED.value
    assert StageService.get_workflow_condition(act_data_2) == WorkflowCondition.REOPEN_REQUESTED.value

    # Case 3: Execution State IN_PROGRESS while Workflow Condition is BLOCKED
    act_data_3 = {
        "actual_pct_complete": 45.0,
        "actual_start": "2026-06-01",
        "status": "BLOCKED",
    }
    assert StageService.get_execution_state(act_data_3) == CanonicalExecutionState.IN_PROGRESS.value
    assert StageService.get_workflow_condition(act_data_3) == WorkflowCondition.BLOCKED.value

    # Case 4: Execution State COMPLETED while Workflow Condition is REWORK_IN_PROGRESS
    act_data_4 = {
        "actual_pct_complete": 100.0,
        "actual_start": "2026-06-01",
        "is_reopened": True,
    }
    assert StageService.get_execution_state(act_data_4) == CanonicalExecutionState.COMPLETED.value
    assert StageService.get_workflow_condition(act_data_4) == WorkflowCondition.REWORK_IN_PROGRESS.value
