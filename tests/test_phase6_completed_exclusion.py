"""
SETUAI V7 — Phase 6: Completed Activity Exclusion Tests
Verifies that canonically COMPLETED activities are never selected by normal matching,
including EXACT_ID, EXACT_ASSET, and HYBRID_FALLBACK / FAISS / RapidFuzz cascades.
"""

from unittest.mock import MagicMock, patch
import pytest

from backend.routers.matching import (
    EXACT_ID,
    EXACT_ASSET,
    HYBRID_FALLBACK,
    match_claim,
    match_exact_id,
    match_exact_asset,
    generate_hybrid_candidates,
)
from backend.shared.schedule_index import SearchCandidate
from backend.shared.schemas import ExecutionClaim, ScheduleActivity


def test_completed_activity_excluded_from_candidate_pool():
    """
    Activity A: COMPLETED (actual_pct_complete = 100.0)
    Activity B: NOT_STARTED (actual_pct_complete = 0.0)
    Incoming report matches both textually.
    Expected: A is excluded; B is eligible and selected.
    """
    activities = [
        {
            "activity_id": "ACT-A",
            "schedule_id": "SCHED-01",
            "activity_name": "Turbine Foundation Pour Zone 1",
            "discipline": "Civil",
            "location": "Zone 1",
            "actual_pct_complete": 100.0,
            "actual_start": "2026-08-01",
        },
        {
            "activity_id": "ACT-B",
            "schedule_id": "SCHED-01",
            "activity_name": "Turbine Foundation Pour Zone 2",
            "discipline": "Civil",
            "location": "Zone 1",
            "actual_pct_complete": 0.0,
            "actual_start": None,
        },
    ]

    claim = {
        "event_id": "EVT-100",
        "schedule_id": "SCHED-01",
        "raw_claim_text": "Turbine Foundation Pour",
        "discipline": "Civil",
        "location": "Zone 1",
    }

    candidates = match_claim(claim, activities)
    assert len(candidates) >= 1
    # Activity A must NOT be in the candidates!
    cand_act_ids = [c.activity_id for c in candidates]
    assert "ACT-A" not in cand_act_ids
    assert candidates[0].activity_id == "ACT-B"


def test_exact_id_cannot_bypass_completed_eligibility():
    """
    If the incoming report exactly identifies a completed activity ID:
    EXACT_ID must NOT accept it. It must return None.
    """
    activities = [
        {
            "activity_id": "ACT-COMPLETED",
            "schedule_id": "SCHED-01",
            "activity_name": "Completed Foundation Work",
            "actual_pct_complete": 100.0,
            "actual_start": "2026-08-01",
        },
    ]

    claim = {
        "event_id": "EVT-EXACT-01",
        "schedule_id": "SCHED-01",
        "reported_activity_id": "ACT-COMPLETED",
        "raw_claim_text": "Completed Foundation Work",
    }

    exact_res = match_exact_id(claim, activities)
    assert exact_res is None

    # Entire matching cascade must not match it
    cascade_res = match_claim(claim, activities)
    assert len(cascade_res) == 0


def test_exact_asset_cannot_bypass_completed_eligibility():
    """
    If an activity matches asset_tag but is completed, EXACT_ASSET must exclude it.
    """
    activities = [
        {
            "activity_id": "PUMP-01-ACT",
            "schedule_id": "SCHED-01",
            "activity_name": "Install Feedwater Pump",
            "asset_tag": "P-101",
            "discipline": "Mechanical",
            "location": "Pump House",
            "actual_pct_complete": 100.0,
            "actual_start": "2026-08-01",
        }
    ]

    claim = {
        "event_id": "EVT-ASSET-01",
        "schedule_id": "SCHED-01",
        "asset_tag": "P-101",
        "discipline": "Mechanical",
        "location": "Pump House",
    }

    asset_res = match_exact_asset(claim, activities)
    assert len(asset_res) == 0


def test_faiss_semantic_results_filter_out_completed_activities():
    """
    Even when FAISS returns a high similarity score for a completed activity,
    the hybrid candidate generation must not admit it into candidates.
    """
    activities = [
        {
            "activity_id": "ACT-HIGH-SEM",
            "schedule_id": "SCHED-01",
            "activity_name": "Underground Cable Laying",
            "discipline": "Electrical",
            "location": "Substation",
            "actual_pct_complete": 100.0,
            "actual_start": "2026-07-01",
        },
        {
            "activity_id": "ACT-LOW-SEM",
            "schedule_id": "SCHED-01",
            "activity_name": "Aboveground Cable Tray Installation",
            "discipline": "Electrical",
            "location": "Substation",
            "actual_pct_complete": 20.0,
            "actual_start": "2026-08-01",
        },
    ]

    # Semantic search returned top score for ACT-HIGH-SEM
    semantic_results = [
        SearchCandidate(schedule_id="SCHED-01", activity_id="ACT-HIGH-SEM", score=0.98),
        SearchCandidate(schedule_id="SCHED-01", activity_id="ACT-LOW-SEM", score=0.75),
    ]

    claim = {
        "event_id": "EVT-SEM-01",
        "schedule_id": "SCHED-01",
        "raw_claim_text": "Underground Cable Laying",
        "discipline": "Electrical",
        "location": "Substation",
    }

    candidates = generate_hybrid_candidates(claim, activities, semantic_results)
    cand_act_ids = [c.activity_id for c in candidates]
    assert "ACT-HIGH-SEM" not in cand_act_ids
    assert "ACT-LOW-SEM" in cand_act_ids


def test_repeat_report_protection():
    """
    Report 1 matched Activity A, which is then completed to 100%.
    Report 2 arrives with identical text.
    Report 2 must NOT match Activity A.
    """
    # Activity A has now been approved and reached 100%
    activities = [
        {
            "activity_id": "ACT-PIPE-01",
            "schedule_id": "SCHED-01",
            "activity_name": "Piping Spool Installation",
            "discipline": "Piping",
            "location": "Unit 10",
            "actual_pct_complete": 100.0,
            "actual_start": "2026-08-10",
        }
    ]

    report_2 = {
        "event_id": "EVT-RPT-2",
        "schedule_id": "SCHED-01",
        "raw_claim_text": "Piping Spool Installation",
        "discipline": "Piping",
        "location": "Unit 10",
    }

    candidates = match_claim(report_2, activities)
    assert len(candidates) == 0
