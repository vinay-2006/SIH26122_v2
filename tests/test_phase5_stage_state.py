"""
Tests for Phase 5 — Stage State Engine, Progress Aggregation, Dependencies, Quality Gates, and Completion Rules.
"""

import time
import uuid
import jwt
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.shared.db import get_connection

TEST_JWT_SECRET = "phase3-super-secret-key-12345678901234567890"


@pytest.fixture(autouse=True)
def configure_test_jwt(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")


def _make_jwt(sub: str) -> str:
    payload = {
        "sub": str(sub),
        "email": f"{sub}@example.com",
        "exp": int(time.time()) + 3600,
    }
    return jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")


@pytest.fixture
def phase5_state_fixture():
    conn = get_connection()
    conn.autocommit = False

    user_pm = uuid.uuid4()
    proj_id = uuid.uuid4()
    sched_id = f"SCH-ST-{uuid.uuid4().hex[:6]}"

    stage_1_id = uuid.uuid4()
    stage_2_id = uuid.uuid4()

    with conn.cursor() as cur:
        # Profile & Project
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_pm, "Stage PM", "SUPERVISOR"),
        )
        cur.execute(
            """
            INSERT INTO projects (project_id, project_code, project_name, status, created_by)
            VALUES (%s, %s, %s, 'ACTIVE', %s);
            """,
            (proj_id, f"PRJ-ST-{uuid.uuid4().hex[:6]}", "State Test Project", user_pm),
        )
        cur.execute(
            """
            INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active, status)
            VALUES (%s, %s, %s, 'PROJECT_MANAGER', TRUE, 'ACTIVE');
            """,
            (uuid.uuid4(), user_pm, proj_id),
        )
        cur.execute(
            """
            INSERT INTO schedules (schedule_id, project_name, project_id, version_code, active)
            VALUES (%s, %s, %s, 'V1.0', TRUE);
            """,
            (sched_id, "State Test Project", proj_id),
        )

        # Stage 1: Earthworks (predecessor)
        cur.execute(
            """
            INSERT INTO stages (stage_id, project_id, schedule_id, stage_code, stage_name, sequence_order, status)
            VALUES (%s, %s, %s, 'STG-EW', 'Earthworks', 1, 'IN_PROGRESS');
            """,
            (stage_1_id, proj_id, sched_id),
        )

        # Stage 2: Foundation (gated by Stage 1)
        cur.execute(
            """
            INSERT INTO stages (stage_id, project_id, schedule_id, stage_code, stage_name, sequence_order, status, gating_predecessor_stage_id)
            VALUES (%s, %s, %s, 'STG-FND', 'Foundation', 2, 'NOT_STARTED', %s);
            """,
            (stage_2_id, proj_id, sched_id, stage_1_id),
        )

        # Activities for Stage 2
        act_1 = "ACT-FND-01"
        act_2 = "ACT-FND-02"
        cur.execute(
            """
            INSERT INTO schedule_activities (
                activity_id, schedule_id, project_id, stage_id, activity_name,
                planned_start, planned_finish, weight_factor, discipline, location
            ) VALUES
            (%s, %s, %s, %s, 'Excavation Pit', '2026-06-01', '2026-06-10', 2.0, 'Civil', 'Area 1'),
            (%s, %s, %s, %s, 'Rebar & Formwork', '2026-06-11', '2026-06-20', 3.0, 'Civil', 'Area 1');
            """,
            (act_1, sched_id, proj_id, stage_2_id, act_2, sched_id, proj_id, stage_2_id),
        )

    conn.commit()
    conn.close()

    yield {
        "user_pm": user_pm,
        "project_id": proj_id,
        "schedule_id": sched_id,
        "stage_1_id": stage_1_id,
        "stage_2_id": stage_2_id,
        "act_1": "ACT-FND-01",
        "act_2": "ACT-FND-02",
    }

    # Teardown
    clean_conn = get_connection()
    with clean_conn.cursor() as cur:
        cur.execute("DELETE FROM quality_gates WHERE project_id = %s;", (proj_id,))
        cur.execute("DELETE FROM approved_actuals WHERE project_id = %s;", (proj_id,))
        cur.execute("DELETE FROM schedule_activities WHERE project_id = %s;", (proj_id,))
        cur.execute("DELETE FROM stages WHERE project_id = %s;", (proj_id,))
        cur.execute("DELETE FROM schedules WHERE project_id = %s;", (proj_id,))
        cur.execute("DELETE FROM project_memberships WHERE project_id = %s;", (proj_id,))
        cur.execute("DELETE FROM projects WHERE project_id = %s;", (proj_id,))
        cur.execute("DELETE FROM profiles WHERE id = %s;", (user_pm,))
    clean_conn.commit()
    clean_conn.close()


def test_stage_state_initial_not_started(phase5_state_fixture):
    client = TestClient(app)
    pm_token = _make_jwt(str(phase5_state_fixture["user_pm"]))
    headers = {"Authorization": f"Bearer {pm_token}"}
    proj_id = phase5_state_fixture["project_id"]
    stage_2_id = phase5_state_fixture["stage_2_id"]

    res = client.get(f"/api/v1/projects/{proj_id}/stages/{stage_2_id}/state", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["computed_state"] == "NOT_STARTED"
    assert data["total_activities"] == 2
    assert data["not_started_activities"] == 2
    assert data["completed_activities"] == 0


def test_stage_state_in_progress(phase5_state_fixture):
    client = TestClient(app)
    pm_token = _make_jwt(str(phase5_state_fixture["user_pm"]))
    headers = {"Authorization": f"Bearer {pm_token}"}
    proj_id = phase5_state_fixture["project_id"]
    sched_id = phase5_state_fixture["schedule_id"]
    stage_2_id = phase5_state_fixture["stage_2_id"]
    act_1 = phase5_state_fixture["act_1"]

    # Insert approved actual for ACT-FND-01: 50% in progress
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO approved_actuals (
                actual_id, decision_id, event_id, schedule_id, activity_id, project_id, stage_id,
                actual_start, actual_pct_complete
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, '2026-06-02', 50.0);
            """,
            (str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), sched_id, act_1, proj_id, stage_2_id),
        )
    conn.commit()
    conn.close()

    res = client.get(f"/api/v1/projects/{proj_id}/stages/{stage_2_id}/state", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["computed_state"] == "IN_PROGRESS"
    assert data["in_progress_activities"] == 1
    assert data["not_started_activities"] == 1


def test_stage_progress_weighted_calculation(phase5_state_fixture):
    client = TestClient(app)
    pm_token = _make_jwt(str(phase5_state_fixture["user_pm"]))
    headers = {"Authorization": f"Bearer {pm_token}"}
    proj_id = phase5_state_fixture["project_id"]
    sched_id = phase5_state_fixture["schedule_id"]
    stage_2_id = phase5_state_fixture["stage_2_id"]
    act_1 = phase5_state_fixture["act_1"]
    act_2 = phase5_state_fixture["act_2"]

    # ACT 1 has weight 2.0 and is 100% complete
    # ACT 2 has weight 3.0 and is 50% complete
    # Weighted calculation: (2.0 * 100 + 3.0 * 50) / (2.0 + 3.0) = (200 + 150) / 5 = 350 / 5 = 70.0%
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO approved_actuals (
                actual_id, decision_id, event_id, schedule_id, activity_id, project_id, stage_id,
                actual_start, actual_finish, actual_pct_complete
            ) VALUES
            (%s, %s, %s, %s, %s, %s, %s, '2026-06-02', '2026-06-10', 100.0),
            (%s, %s, %s, %s, %s, %s, %s, '2026-06-11', NULL, 50.0);
            """,
            (
                str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), sched_id, act_1, proj_id, stage_2_id,
                str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), sched_id, act_2, proj_id, stage_2_id,
            ),
        )
    conn.commit()
    conn.close()

    res = client.get(f"/api/v1/projects/{proj_id}/stages/{stage_2_id}/progress", headers=headers)
    assert res.status_code == 200
    prog = res.json()
    assert prog["calculation_basis"] == "WEIGHTED_FACTOR"
    assert prog["progress_pct"] == 70.0


def test_stage_completion_rule_gated_by_dependencies_and_quality(phase5_state_fixture):
    client = TestClient(app)
    pm_token = _make_jwt(str(phase5_state_fixture["user_pm"]))
    headers = {"Authorization": f"Bearer {pm_token}"}
    proj_id = phase5_state_fixture["project_id"]
    sched_id = phase5_state_fixture["schedule_id"]
    stage_1_id = phase5_state_fixture["stage_1_id"]
    stage_2_id = phase5_state_fixture["stage_2_id"]
    act_1 = phase5_state_fixture["act_1"]
    act_2 = phase5_state_fixture["act_2"]

    # Complete 100% of both activities in Stage 2
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO approved_actuals (
                actual_id, decision_id, event_id, schedule_id, activity_id, project_id, stage_id,
                actual_start, actual_finish, actual_pct_complete
            ) VALUES
            (%s, %s, %s, %s, %s, %s, %s, '2026-06-02', '2026-06-10', 100.0),
            (%s, %s, %s, %s, %s, %s, %s, '2026-06-11', '2026-06-20', 100.0);
            """,
            (
                str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), sched_id, act_1, proj_id, stage_2_id,
                str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), sched_id, act_2, proj_id, stage_2_id,
            ),
        )
        # Add a required Quality Gate on Stage 2 that is still PENDING
        gate_id = uuid.uuid4()
        cur.execute(
            """
            INSERT INTO quality_gates (
                quality_gate_id, project_id, stage_id, schedule_id, gate_type, gate_name, required, status
            ) VALUES (%s, %s, %s, %s, 'CLEARANCE', 'Foundation Structural Inspection', TRUE, 'PENDING');
            """,
            (gate_id, proj_id, stage_2_id, sched_id),
        )
    conn.commit()
    conn.close()

    # Even though activities are 100% complete:
    # 1. Predecessor Stage 1 is IN_PROGRESS (not COMPLETED)
    # 2. Quality Gate is PENDING (not PASSED)
    # Result: Stage is NOT complete!
    res_comp = client.get(f"/api/v1/projects/{proj_id}/stages/{stage_2_id}/completion-check", headers=headers)
    assert res_comp.status_code == 200
    comp_data = res_comp.json()
    assert comp_data["is_complete"] is False
    assert comp_data["activities_completed"] is True
    assert comp_data["dependencies_satisfied"] is False
    assert comp_data["gates_cleared"] is False
    assert comp_data["has_blockers"] is True

    # Now clear predecessor Stage 1 (set status = COMPLETED)
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("UPDATE stages SET status = 'COMPLETED' WHERE stage_id = %s;", (stage_1_id,))
    conn.commit()
    conn.close()

    # Still not complete because quality gate is still PENDING
    res_comp2 = client.get(f"/api/v1/projects/{proj_id}/stages/{stage_2_id}/completion-check", headers=headers)
    assert res_comp2.json()["is_complete"] is False
    assert res_comp2.json()["dependencies_satisfied"] is True
    assert res_comp2.json()["gates_cleared"] is False

    # Now pass the quality gate
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("UPDATE quality_gates SET status = 'PASSED', passed_at = now() WHERE quality_gate_id = %s;", (gate_id,))
    conn.commit()
    conn.close()

    # Now ALL constraints are satisfied: Stage is COMPLETED!
    res_comp3 = client.get(f"/api/v1/projects/{proj_id}/stages/{stage_2_id}/completion-check", headers=headers)
    assert res_comp3.json()["is_complete"] is True
    assert res_comp3.json()["activities_completed"] is True
    assert res_comp3.json()["dependencies_satisfied"] is True
    assert res_comp3.json()["gates_cleared"] is True
    assert res_comp3.json()["has_blockers"] is False

    # Check that stage state calculation now returns COMPLETED
    res_state = client.get(f"/api/v1/projects/{proj_id}/stages/{stage_2_id}/state", headers=headers)
    assert res_state.json()["computed_state"] == "COMPLETED"
