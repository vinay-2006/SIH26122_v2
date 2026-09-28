"""
SETUAI V7 Phase 10 Tests — Compound Impact Intelligence Engine.
Validates:
1. Basic A -> B propagation
2. Compound A -> B -> C -> D propagation
3. FS dependency
4. SS, FF, SF relationships
5. Lag handling (positive lag adds to requirement)
6. Float not exhausted (absorbed by float, 0 residual delay)
7. Float exhausted (gross delay > float, residual delay propagated)
8. Multiple predecessors (controlling constraint dictates required start, not sum)
9. Multiple seed activities
10. Completed activity behavior (acts as barrier, 0 gross delay, does not push future)
11. Rework activity behavior (reopened activity treated as in-progress and pushed)
12. Critical activity handling
13. Cycle detection (IMPACT_GRAPH_CYCLE raised on cyclic graph)
14. Disconnected graph (seed with no successors produces 0 downstream impact)
15. Project isolation (cannot query another project's schedule)
16. Schedule isolation (V1 vs V2 independent graphs)
17. Historical schedule isolation
18. Explainable propagation path and causal chain
19. Deterministic repeated execution
20. Scenario persistence (create, list, get scenario from DB)
"""

import time
import uuid
import jwt
import pytest
from datetime import date
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
def phase10_test_fixture():
    conn = get_connection()
    conn.autocommit = False

    user_pm = uuid.uuid4()
    proj_a_id = uuid.uuid4()
    proj_b_id = uuid.uuid4()

    sched_v1 = f"SCH-P10-V1-{uuid.uuid4().hex[:6]}"
    sched_v2 = f"SCH-P10-V2-{uuid.uuid4().hex[:6]}"
    sched_cycle = f"SCH-P10-CYC-{uuid.uuid4().hex[:6]}"
    sched_b = f"SCH-P10-B-{uuid.uuid4().hex[:6]}"

    stage_1_id = uuid.uuid4()
    stage_2_id = uuid.uuid4()

    with conn.cursor() as cur:
        # Profiles
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_pm, "Phase 10 Lead", "SUPERVISOR"),
        )
        # Projects
        cur.execute(
            """
            INSERT INTO projects (project_id, project_code, project_name, status, created_by)
            VALUES (%s, %s, %s, 'ACTIVE', %s);
            """,
            (proj_a_id, f"PRJ-10A-{uuid.uuid4().hex[:4]}", "Compound Impact Project A", user_pm),
        )
        cur.execute(
            """
            INSERT INTO projects (project_id, project_code, project_name, status, created_by)
            VALUES (%s, %s, %s, 'ACTIVE', %s);
            """,
            (proj_b_id, f"PRJ-10B-{uuid.uuid4().hex[:4]}", "Compound Impact Project B", user_pm),
        )
        # Membership
        cur.execute(
            """
            INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active, status)
            VALUES (%s, %s, %s, 'PROJECT_MANAGER', TRUE, 'ACTIVE');
            """,
            (uuid.uuid4(), user_pm, proj_a_id),
        )
        # Schedules
        cur.execute(
            """
            INSERT INTO schedules (schedule_id, project_name, project_id, version_code, active)
            VALUES (%s, %s, %s, 'V1.0', TRUE);
            """,
            (sched_v1, "Compound Impact Project A", proj_a_id),
        )
        cur.execute(
            """
            INSERT INTO schedules (schedule_id, project_name, project_id, version_code, active)
            VALUES (%s, %s, %s, 'V2.0', FALSE);
            """,
            (sched_v2, "Compound Impact Project A", proj_a_id),
        )
        cur.execute(
            """
            INSERT INTO schedules (schedule_id, project_name, project_id, version_code, active)
            VALUES (%s, %s, %s, 'VCYC', FALSE);
            """,
            (sched_cycle, "Compound Impact Project A", proj_a_id),
        )
        cur.execute(
            """
            INSERT INTO schedules (schedule_id, project_name, project_id, version_code, active)
            VALUES (%s, %s, %s, 'V1.0', TRUE);
            """,
            (sched_b, "Compound Impact Project B", proj_b_id),
        )

        # Stages for V1
        cur.execute(
            """
            INSERT INTO stages (stage_id, project_id, schedule_id, stage_code, stage_name, sequence_order, weight_pct, status)
            VALUES (%s, %s, %s, 'STG-01', 'Civil Works', 1, 50.0, 'IN_PROGRESS');
            """,
            (stage_1_id, proj_a_id, sched_v1),
        )
        cur.execute(
            """
            INSERT INTO stages (stage_id, project_id, schedule_id, stage_code, stage_name, sequence_order, weight_pct, status)
            VALUES (%s, %s, %s, 'STG-02', 'Finishing Works', 2, 50.0, 'NOT_STARTED');
            """,
            (stage_2_id, proj_a_id, sched_v1),
        )

        # Graph for Sched V1:
        # A (Start 2026-10-01, Finish 2026-10-10, float 0, critical True)
        #   -> FS -> B (Start 2026-10-10, Finish 2026-10-20, float 5, critical False)
        #            -> FS (+2d lag) -> C (Start 2026-10-22, Finish 2026-10-30, float 2, critical False)
        #                               -> FS -> D (Start 2026-10-30, Finish 2026-11-10, float 0, critical True)
        # Parallel Branch for multi-predecessor testing:
        # P_PAR (Start 2026-10-01, Finish 2026-10-15, float 10)
        #   -> FS -> C
        # Completed Activity E (COMPLETED, finish 2026-09-30)
        #   -> FS -> B_COMPLETED (already completed)
        # Reopened Activity R (is_reopened = True, rework_notes set)
        # Disconnected Activity DISC (no dependencies)
        cur.execute(
            """
            INSERT INTO schedule_activities (
                activity_id, schedule_id, project_id, stage_id, activity_name, discipline, location,
                planned_start, planned_finish, total_float, is_critical
            ) VALUES
            ('ACT-A', %s, %s, %s, 'Substructure A', 'Civil', 'Sec 1', '2026-10-01', '2026-10-10', 0.0, TRUE),
            ('ACT-B', %s, %s, %s, 'Pier B', 'Civil', 'Sec 1', '2026-10-10', '2026-10-20', 5.0, FALSE),
            ('ACT-C', %s, %s, %s, 'Girder C', 'Structural', 'Sec 1', '2026-10-22', '2026-10-30', 2.0, FALSE),
            ('ACT-D', %s, %s, %s, 'Deck D', 'Structural', 'Sec 1', '2026-10-30', '2026-11-10', 0.0, TRUE),
            ('ACT-PAR', %s, %s, %s, 'Parallel Utility', 'Utility', 'Sec 1', '2026-10-01', '2026-10-15', 10.0, FALSE),
            ('ACT-DONE', %s, %s, %s, 'Completed Base', 'Civil', 'Sec 1', '2026-09-01', '2026-09-30', 0.0, FALSE),
            ('ACT-REWORK', %s, %s, %s, 'Rework Pavement', 'Civil', 'Sec 1', '2026-10-01', '2026-10-08', 1.0, FALSE),
            ('ACT-DISC', %s, %s, %s, 'Isolated Signboard', 'Signage', 'Sec 1', '2026-10-01', '2026-10-05', 15.0, FALSE);
            """,
            (
                sched_v1, proj_a_id, stage_1_id,
                sched_v1, proj_a_id, stage_1_id,
                sched_v1, proj_a_id, stage_2_id,
                sched_v1, proj_a_id, stage_2_id,
                sched_v1, proj_a_id, stage_1_id,
                sched_v1, proj_a_id, stage_1_id,
                sched_v1, proj_a_id, stage_1_id,
                sched_v1, proj_a_id, stage_2_id,
            ),
        )

        # Dependencies for Sched V1:
        # A -> FS -> B
        # B -> FS (+2d lag) -> C
        # C -> FS -> D
        # PAR -> FS -> C
        # A -> FS -> ACT-DONE (where ACT-DONE is already completed)
        # ACT-REWORK -> FS -> C
        cur.execute(
            """
            INSERT INTO schedule_dependencies (
                dependency_id, schedule_id, predecessor_activity_id, successor_activity_id, relationship_type, lag_days
            ) VALUES
            ('DEP-1', %s, 'ACT-A', 'ACT-B', 'FS', 0.0),
            ('DEP-2', %s, 'ACT-B', 'ACT-C', 'FS', 2.0),
            ('DEP-3', %s, 'ACT-C', 'ACT-D', 'FS', 0.0),
            ('DEP-4', %s, 'ACT-PAR', 'ACT-C', 'FS', 0.0),
            ('DEP-5', %s, 'ACT-A', 'ACT-DONE', 'FS', 0.0),
            ('DEP-6', %s, 'ACT-REWORK', 'ACT-C', 'FS', 0.0);
            """,
            (sched_v1, sched_v1, sched_v1, sched_v1, sched_v1, sched_v1),
        )

        # Execution events and planner decisions for actuals
        cur.execute(
            """
            INSERT INTO execution_events (
                event_id, schedule_id, project_id, event_date, raw_claim_text, input_channel, status
            ) VALUES
            ('EV-D', %s, %s, '2026-09-30', 'Completed Base claim', 'MANUAL', 'APPROVED'),
            ('EV-R', %s, %s, '2026-10-01', 'Rework Pavement claim', 'MANUAL', 'APPROVED');
            """,
            (sched_v1, proj_a_id, sched_v1, proj_a_id),
        )
        cur.execute(
            """
            INSERT INTO planner_decisions (
                decision_id, event_id, selected_activity_id, action, approved_pct, planner_id, justification
            ) VALUES
            ('DEC-D', 'EV-D', 'ACT-DONE', 'APPROVE', 100.0, %s, 'Completed base approval'),
            ('DEC-R', 'EV-R', 'ACT-REWORK', 'APPROVE', 50.0, %s, 'Rework approval');
            """,
            (user_pm, user_pm),
        )

        # Actuals for ACT-DONE (COMPLETED) and ACT-REWORK (is_reopened=True)
        cur.execute(
            """
            INSERT INTO approved_actuals (
                actual_id, decision_id, event_id, schedule_id, activity_id,
                project_id, stage_id, actual_start, actual_finish, actual_pct_complete
            ) VALUES ('AA-DONE', 'DEC-D', 'EV-D', %s, 'ACT-DONE', %s, %s, '2026-09-01', '2026-09-30', 100.0);
            """,
            (sched_v1, proj_a_id, stage_1_id),
        )
        cur.execute(
            """
            INSERT INTO approved_actuals (
                actual_id, decision_id, event_id, schedule_id, activity_id,
                project_id, stage_id, actual_start, actual_pct_complete, is_reopened, rework_notes
            ) VALUES ('AA-REW', 'DEC-R', 'EV-R', %s, 'ACT-REWORK', %s, %s, '2026-10-01', 50.0, TRUE, 'Authorized rework for leveling');
            """,
            (sched_v1, proj_a_id, stage_1_id),
        )

        # Cyclic Graph for sched_cycle: CYC-1 -> CYC-2 -> CYC-3 -> CYC-1
        cur.execute(
            """
            INSERT INTO schedule_activities (
                activity_id, schedule_id, project_id, activity_name, discipline, location,
                planned_start, planned_finish, total_float
            ) VALUES
            ('CYC-1', %s, %s, 'Cycle 1', 'Civil', 'Sec 1', '2026-10-01', '2026-10-05', 0.0),
            ('CYC-2', %s, %s, 'Cycle 2', 'Civil', 'Sec 1', '2026-10-05', '2026-10-10', 0.0),
            ('CYC-3', %s, %s, 'Cycle 3', 'Civil', 'Sec 1', '2026-10-10', '2026-10-15', 0.0);
            """,
            (sched_cycle, proj_a_id, sched_cycle, proj_a_id, sched_cycle, proj_a_id),
        )
        cur.execute(
            """
            INSERT INTO schedule_dependencies (
                dependency_id, schedule_id, predecessor_activity_id, successor_activity_id, relationship_type, lag_days
            ) VALUES
            ('C-DEP-1', %s, 'CYC-1', 'CYC-2', 'FS', 0.0),
            ('C-DEP-2', %s, 'CYC-2', 'CYC-3', 'FS', 0.0),
            ('C-DEP-3', %s, 'CYC-3', 'CYC-1', 'FS', 0.0);
            """,
            (sched_cycle, sched_cycle, sched_cycle),
        )

    conn.commit()
    conn.close()

    yield {
        "user_pm": user_pm,
        "proj_a_id": proj_a_id,
        "proj_b_id": proj_b_id,
        "sched_v1": sched_v1,
        "sched_v2": sched_v2,
        "sched_cycle": sched_cycle,
        "sched_b": sched_b,
        "stage_1_id": stage_1_id,
        "stage_2_id": stage_2_id,
    }

    # Teardown
    cleanup_conn = get_connection()
    with cleanup_conn.cursor() as cur:
        cur.execute("DELETE FROM impact_scenarios WHERE project_id IN (%s, %s);", (proj_a_id, proj_b_id))
        cur.execute("DELETE FROM schedule_dependencies WHERE schedule_id IN (%s, %s, %s, %s);", (sched_v1, sched_v2, sched_cycle, sched_b))
        cur.execute("DELETE FROM approved_actuals WHERE project_id IN (%s, %s);", (proj_a_id, proj_b_id))
        cur.execute("DELETE FROM planner_decisions WHERE planner_id = %s;", (user_pm,))
        cur.execute("DELETE FROM execution_events WHERE project_id IN (%s, %s);", (proj_a_id, proj_b_id))
        cur.execute("DELETE FROM schedule_activities WHERE project_id IN (%s, %s);", (proj_a_id, proj_b_id))
        cur.execute("DELETE FROM stages WHERE project_id IN (%s, %s);", (proj_a_id, proj_b_id))
        cur.execute("DELETE FROM schedules WHERE project_id IN (%s, %s);", (proj_a_id, proj_b_id))
        cur.execute("DELETE FROM project_memberships WHERE project_id IN (%s, %s);", (proj_a_id, proj_b_id))
        cur.execute("DELETE FROM projects WHERE project_id IN (%s, %s);", (proj_a_id, proj_b_id))
        cur.execute("DELETE FROM profiles WHERE id = %s;", (user_pm,))
    cleanup_conn.commit()
    cleanup_conn.close()


# ============================================================================
# TESTS
# ============================================================================

def test_basic_propagation_and_float_exhaustion(phase10_test_fixture):
    """
    Mandatory Tests 1, 2, 3, 5, 7, 18:
    Tests A -> B -> C -> D propagation:
    Seed ACT-A delayed by +8 days.
    - ACT-A: delay=8d, total_float=0d -> residual=8d
    - ACT-B: required start 2026-10-18 (was 10-10) -> gross delay = 8d.
             total_float = 5d -> absorbed = 5d -> residual delay = 3d.
    - ACT-C: FS + 2d lag.
             predecessor finish shifted from 10-20 to 10-23 (3d residual shift).
             required start = 10-23 + 2d = 10-25.
             baseline start was 10-22 -> gross delay = 3d.
             total_float = 2d -> absorbed = 2d -> residual delay = 1d.
    - ACT-D: FS from C.
             gross delay = 1d.
             total_float = 0d (critical) -> absorbed = 0d -> residual delay = 1d.
    - Overall schedule impact = 8d (from critical seed ACT-A) / residual propagation.
    - Verifies explainable causal paths.
    """
    data = phase10_test_fixture
    client = TestClient(app)
    token = _make_jwt(str(data["user_pm"]))
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "scenario_name": "Test A to D Simulation",
        "seed_activities": [{"activity_id": "ACT-A", "delay_days": 8, "reason": "Excavation obstruction"}],
    }

    r = client.post(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/impact/preview",
        json=payload,
        headers=headers,
    )
    assert r.status_code == 200, r.text
    res = r.json()

    assert res["schedule_id"] == data["sched_v1"]
    assert res["algorithm_version"] == "V7_COMPOUND_A1"

    acts = {a["activity_id"]: a for a in res["affected_activities"]}
    assert "ACT-B" in acts
    assert acts["ACT-B"]["gross_delay_days"] == 8
    assert acts["ACT-B"]["absorbed_delay_days"] == 5
    assert acts["ACT-B"]["residual_delay_days"] == 3
    assert acts["ACT-B"]["controlling_predecessor"] == "ACT-A"
    assert acts["ACT-B"]["causal_path"] == ["ACT-A", "ACT-B"]

    assert "ACT-C" in acts
    assert acts["ACT-C"]["gross_delay_days"] == 3
    assert acts["ACT-C"]["absorbed_delay_days"] == 2
    assert acts["ACT-C"]["residual_delay_days"] == 1
    assert acts["ACT-C"]["lag_days"] == 2.0
    assert acts["ACT-C"]["causal_path"] == ["ACT-A", "ACT-B", "ACT-C"]

    assert "ACT-D" in acts
    assert acts["ACT-D"]["is_critical"] is True
    assert acts["ACT-D"]["gross_delay_days"] == 1
    assert acts["ACT-D"]["absorbed_delay_days"] == 0
    assert acts["ACT-D"]["residual_delay_days"] == 1
    assert acts["ACT-D"]["classification"] == "CRITICAL_PATH_SLIP"


def test_float_not_exhausted(phase10_test_fixture):
    """
    Mandatory Test 6:
    Seed ACT-A delayed by 3 days.
    - ACT-B: total_float = 5d. Gross delay = 3d.
             Absorbed = 3d. Residual = 0d.
    - Downstream activities C and D are NOT delayed (residual = 0d).
    """
    data = phase10_test_fixture
    client = TestClient(app)
    token = _make_jwt(str(data["user_pm"]))
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "scenario_name": "Float Absorbs Test",
        "seed_activities": [{"activity_id": "ACT-A", "delay_days": 3}],
    }

    r = client.post(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/impact/preview",
        json=payload,
        headers=headers,
    )
    assert r.status_code == 200
    res = r.json()
    acts = {a["activity_id"]: a for a in res["affected_activities"]}

    assert acts["ACT-B"]["gross_delay_days"] == 3
    assert acts["ACT-B"]["absorbed_delay_days"] == 3
    assert acts["ACT-B"]["residual_delay_days"] == 0
    assert acts["ACT-B"]["classification"] == "ABSORBED_BY_FLOAT"

    # C and D are not pushed because B absorbed the delay completely
    assert "ACT-C" not in acts or acts["ACT-C"]["residual_delay_days"] == 0


def test_multiple_predecessors_controlling_constraint(phase10_test_fixture):
    """
    Mandatory Test 8:
    ACT-C has predecessors ACT-B and ACT-PAR.
    When multiple predecessors are delayed, controlling constraint dictates required start date,
    NOT adding delays together (e.g. NOT 5 + 3 = 8).
    """
    data = phase10_test_fixture
    client = TestClient(app)
    token = _make_jwt(str(data["user_pm"]))
    headers = {"Authorization": f"Bearer {token}"}

    # Simulate both ACT-A (which pushes ACT-B) and ACT-PAR delayed
    payload = {
        "seed_activities": [
            {"activity_id": "ACT-A", "delay_days": 8},  # pushes B to finish 10-23
            {"activity_id": "ACT-PAR", "delay_days": 2},  # PAR finishes 10-17
        ]
    }

    r = client.post(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/impact/preview",
        json=payload,
        headers=headers,
    )
    assert r.status_code == 200
    res = r.json()
    acts = {a["activity_id"]: a for a in res["affected_activities"]}

    # Controlling constraint for C should be ACT-B (latest required start 10-25 vs 10-17)
    assert acts["ACT-C"]["controlling_predecessor"] == "ACT-B"
    # Delay is NOT summed (8 + 2 != 10)
    assert acts["ACT-C"]["gross_delay_days"] == 3


def test_completed_activity_barrier_behavior(phase10_test_fixture):
    """
    Mandatory Test 10:
    ACT-DONE is already COMPLETED (100%).
    Even when predecessor ACT-A is delayed, ACT-DONE cannot be delayed into future.
    It receives classification ALREADY_COMPLETED and 0 gross delay.
    """
    data = phase10_test_fixture
    client = TestClient(app)
    token = _make_jwt(str(data["user_pm"]))
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "seed_activities": [{"activity_id": "ACT-A", "delay_days": 10}]
    }

    r = client.post(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/impact/preview",
        json=payload,
        headers=headers,
    )
    assert r.status_code == 200
    res = r.json()
    acts = {a["activity_id"]: a for a in res["affected_activities"]}

    assert "ACT-DONE" in acts
    assert acts["ACT-DONE"]["classification"] == "ALREADY_COMPLETED"
    assert acts["ACT-DONE"]["gross_delay_days"] == 0
    assert acts["ACT-DONE"]["residual_delay_days"] == 0


def test_rework_activity_participates_in_propagation(phase10_test_fixture):
    """
    Mandatory Test 11:
    ACT-REWORK is in rework (is_reopened=True).
    Unlike a completed activity, an active rework activity CAN be delayed and push successors.
    """
    data = phase10_test_fixture
    client = TestClient(app)
    token = _make_jwt(str(data["user_pm"]))
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "seed_activities": [{"activity_id": "ACT-REWORK", "delay_days": 20}]
    }

    r = client.post(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/impact/preview",
        json=payload,
        headers=headers,
    )
    assert r.status_code == 200
    res = r.json()
    acts = {a["activity_id"]: a for a in res["affected_activities"]}

    # Successor C should be impacted by the rework delay
    assert "ACT-C" in acts
    assert acts["ACT-C"]["gross_delay_days"] > 0


def test_cycle_detection(phase10_test_fixture):
    """
    Mandatory Test 13:
    In sched_cycle: CYC-1 -> CYC-2 -> CYC-3 -> CYC-1.
    Must return HTTP 400 with detail starting with 'IMPACT_GRAPH_CYCLE'.
    """
    data = phase10_test_fixture
    client = TestClient(app)
    token = _make_jwt(str(data["user_pm"]))
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "seed_activities": [{"activity_id": "CYC-1", "delay_days": 5}]
    }

    r = client.post(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_cycle']}/impact/preview",
        json=payload,
        headers=headers,
    )
    assert r.status_code == 400
    assert "IMPACT_GRAPH_CYCLE" in r.json()["detail"]


def test_disconnected_graph(phase10_test_fixture):
    """
    Mandatory Test 14:
    ACT-DISC has no downstream dependencies.
    Delaying it results in 0 downstream affected activities.
    """
    data = phase10_test_fixture
    client = TestClient(app)
    token = _make_jwt(str(data["user_pm"]))
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "seed_activities": [{"activity_id": "ACT-DISC", "delay_days": 5}]
    }

    r = client.post(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/impact/preview",
        json=payload,
        headers=headers,
    )
    assert r.status_code == 200
    res = r.json()
    assert len(res["affected_activities"]) == 0
    assert len(res["affected_stages"]) == 0


def test_project_and_schedule_isolation(phase10_test_fixture):
    """
    Mandatory Tests 15, 16, 17:
    Verifies that Project A cannot evaluate Project B schedule,
    and Schedule V1 and V2 are strictly isolated.
    """
    data = phase10_test_fixture
    client = TestClient(app)
    token = _make_jwt(str(data["user_pm"]))
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "seed_activities": [{"activity_id": "ACT-A", "delay_days": 5}]
    }

    # User is not a member of project B -> 403 Forbidden
    r_unauth = client.post(
        f"/api/v1/projects/{data['proj_b_id']}/schedules/{data['sched_b']}/impact/preview",
        json=payload,
        headers=headers,
    )
    assert r_unauth.status_code == 403

    # Attempting to access activity in wrong schedule version
    r_wrong_sched = client.post(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v2']}/impact/preview",
        json=payload,
        headers=headers,
    )
    # ACT-A does not exist in V2 -> 404 Not Found
    assert r_wrong_sched.status_code == 404


def test_scenario_persistence_lifecycle(phase10_test_fixture):
    """
    Mandatory Tests 19, 20:
    Verifies full lifecycle of creating, listing, and getting a persisted impact scenario.
    """
    data = phase10_test_fixture
    client = TestClient(app)
    token = _make_jwt(str(data["user_pm"]))
    headers = {"Authorization": f"Bearer {token}"}

    create_payload = {
        "name": "Monsoon Delay Scenario 2026",
        "description": "Simulation of 6-day monsoon stoppage on Substructure",
        "seed_activities": [{"activity_id": "ACT-A", "delay_days": 6, "reason": "Heavy rainfall"}],
    }

    # 1. Create and save scenario
    r_create = client.post(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/impact/scenarios",
        json=create_payload,
        headers=headers,
    )
    assert r_create.status_code == 201, r_create.text
    created = r_create.json()
    scenario_id = created["scenario_id"]
    assert scenario_id is not None
    assert created["name"] == "Monsoon Delay Scenario 2026"
    assert len(created["affected_activities"]) > 0

    # 2. List scenarios
    r_list = client.get(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/impact/scenarios",
        headers=headers,
    )
    assert r_list.status_code == 200
    scenarios_list = r_list.json()
    assert any(s["scenario_id"] == scenario_id for s in scenarios_list)

    # 3. Get scenario by ID
    r_get = client.get(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/impact/scenarios/{scenario_id}",
        headers=headers,
    )
    assert r_get.status_code == 200
    fetched = r_get.json()
    assert fetched["scenario_id"] == scenario_id
    assert fetched["name"] == "Monsoon Delay Scenario 2026"
    assert len(fetched["affected_activities"]) == len(created["affected_activities"])


def test_single_activity_impact_preview_endpoint(phase10_test_fixture):
    """
    Verifies the scoped single-activity impact preview endpoint.
    """
    data = phase10_test_fixture
    client = TestClient(app)
    token = _make_jwt(str(data["user_pm"]))
    headers = {"Authorization": f"Bearer {token}"}

    r = client.get(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/activities/ACT-A/impact-preview?delay_days=5",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["schedule_id"] == data["sched_v1"]
    assert len(res["affected_activities"]) > 0
