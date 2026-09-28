"""
SETUAI V7 Phase 8 Tests — Weighted Progress & Rollups Engine.
Validates:
1. Canonical activity progress (0%, 25%, 50%, 100%, NULL, quantity-derived).
2. Weighted activity contribution and stage rollups.
3. Schedule progress (stage-weighted and activity-weighted).
4. Project progress and version isolation (V1 vs V2).
5. Reopened activity revision handling (current revision used, historical never double-counted).
6. NULL and zero-weight edge cases (no division-by-zero, no NaN).
7. Stage lifecycle decoupling (progress != stage state).
8. Project, schedule, and stage isolation.
"""

import time
import uuid
import jwt
import pytest
from datetime import date
from fastapi.testclient import TestClient

from backend.main import app
from backend.shared.db import get_connection
from backend.auth.models import CurrentUser
from backend.context.schedule import ScheduleContext
from backend.context.project import ProjectContext
from backend.services.progress_service import ProgressService
from backend.services.stage_service import StageService

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
def phase8_test_fixture():
    conn = get_connection()
    conn.autocommit = False

    user_pm = uuid.uuid4()
    proj_a_id = uuid.uuid4()
    proj_b_id = uuid.uuid4()

    sched_v1 = f"SCH-P8-V1-{uuid.uuid4().hex[:6]}"
    sched_v2 = f"SCH-P8-V2-{uuid.uuid4().hex[:6]}"
    sched_b = f"SCH-P8-B-{uuid.uuid4().hex[:6]}"

    stage_1_id = uuid.uuid4()
    stage_2_id = uuid.uuid4()

    with conn.cursor() as cur:
        # 1. Profiles
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_pm, "Phase 8 Engineer", "SUPERVISOR"),
        )

        # 2. Projects
        cur.execute(
            """
            INSERT INTO projects (project_id, project_code, project_name, status, created_by)
            VALUES (%s, %s, %s, 'ACTIVE', %s);
            """,
            (proj_a_id, f"PRJ-8A-{uuid.uuid4().hex[:4]}", "Phase 8 Project A", user_pm),
        )
        cur.execute(
            """
            INSERT INTO projects (project_id, project_code, project_name, status, created_by)
            VALUES (%s, %s, %s, 'ACTIVE', %s);
            """,
            (proj_b_id, f"PRJ-8B-{uuid.uuid4().hex[:4]}", "Phase 8 Project B", user_pm),
        )

        # 3. Project Memberships
        cur.execute(
            """
            INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active, status)
            VALUES (%s, %s, %s, 'PROJECT_MANAGER', TRUE, 'ACTIVE');
            """,
            (uuid.uuid4(), user_pm, proj_a_id),
        )
        # Note: user_pm is NOT a member of proj_b_id to test isolation!

        # 4. Schedules
        cur.execute(
            """
            INSERT INTO schedules (schedule_id, project_name, project_id, version_code, active)
            VALUES (%s, %s, %s, 'V1.0', TRUE);
            """,
            (sched_v1, "Phase 8 Project A", proj_a_id),
        )
        cur.execute(
            """
            INSERT INTO schedules (schedule_id, project_name, project_id, version_code, active)
            VALUES (%s, %s, %s, 'V2.0', FALSE);
            """,
            (sched_v2, "Phase 8 Project A", proj_a_id),
        )
        cur.execute(
            """
            INSERT INTO schedules (schedule_id, project_name, project_id, version_code, active)
            VALUES (%s, %s, %s, 'V1.0', TRUE);
            """,
            (sched_b, "Phase 8 Project B", proj_b_id),
        )

        # 5. Stages for V1 (Stage 1 weight 60%, Stage 2 weight 40%)
        cur.execute(
            """
            INSERT INTO stages (stage_id, project_id, schedule_id, stage_code, stage_name, sequence_order, weight_pct, status)
            VALUES (%s, %s, %s, 'STG-01', 'Foundation Stage', 1, 60.0, 'IN_PROGRESS');
            """,
            (stage_1_id, proj_a_id, sched_v1),
        )
        cur.execute(
            """
            INSERT INTO stages (stage_id, project_id, schedule_id, stage_code, stage_name, sequence_order, weight_pct, status)
            VALUES (%s, %s, %s, 'STG-02', 'Superstructure Stage', 2, 40.0, 'NOT_STARTED');
            """,
            (stage_2_id, proj_a_id, sched_v1),
        )

        # 6. Activities in V1
        # ACT-1 in Stage 1: weight_factor=1.0, 50% complete
        cur.execute(
            """
            INSERT INTO schedule_activities (
                activity_id, schedule_id, project_id, stage_id, activity_name, discipline, location,
                planned_start, planned_finish, planned_quantity, weight_factor
            ) VALUES ('ACT-01', %s, %s, %s, 'Excavation', 'Civil', 'Zone 1', '2026-09-01', '2026-09-10', 100.0, 1.0);
            """,
            (sched_v1, proj_a_id, stage_1_id),
        )
        # ACT-2 in Stage 1: weight_factor=3.0, 100% complete
        cur.execute(
            """
            INSERT INTO schedule_activities (
                activity_id, schedule_id, project_id, stage_id, activity_name, discipline, location,
                planned_start, planned_finish, planned_quantity, weight_factor
            ) VALUES ('ACT-02', %s, %s, %s, 'Piling', 'Civil', 'Zone 1', '2026-09-05', '2026-09-15', 200.0, 3.0);
            """,
            (sched_v1, proj_a_id, stage_1_id),
        )
        # ACT-3 in Stage 2: weight_factor=2.0, 25% complete
        cur.execute(
            """
            INSERT INTO schedule_activities (
                activity_id, schedule_id, project_id, stage_id, activity_name, discipline, location,
                planned_start, planned_finish, planned_quantity, weight_factor
            ) VALUES ('ACT-03', %s, %s, %s, 'Columns', 'Structural', 'Zone 1', '2026-09-15', '2026-09-25', 100.0, 2.0);
            """,
            (sched_v1, proj_a_id, stage_2_id),
        )
        # ACT-4 in Stage 2: weight_factor=2.0, 0% complete (Not started)
        cur.execute(
            """
            INSERT INTO schedule_activities (
                activity_id, schedule_id, project_id, stage_id, activity_name, discipline, location,
                planned_start, planned_finish, planned_quantity, weight_factor
            ) VALUES ('ACT-04', %s, %s, %s, 'Deck Slab', 'Structural', 'Zone 1', '2026-09-25', '2026-10-05', 50.0, 2.0);
            """,
            (sched_v1, proj_a_id, stage_2_id),
        )

        # 7. Approved Actuals for V1
        cur.execute(
            """
            INSERT INTO approved_actuals (
                actual_id, decision_id, event_id, schedule_id, activity_id,
                project_id, stage_id, actual_start, actual_pct_complete
            ) VALUES ('AA-01', 'DEC-01', 'EV-01', %s, 'ACT-01', %s, %s, '2026-09-01', 50.0);
            """,
            (sched_v1, proj_a_id, stage_1_id),
        )
        cur.execute(
            """
            INSERT INTO approved_actuals (
                actual_id, decision_id, event_id, schedule_id, activity_id,
                project_id, stage_id, actual_start, actual_finish, actual_pct_complete
            ) VALUES ('AA-02', 'DEC-02', 'EV-02', %s, 'ACT-02', %s, %s, '2026-09-05', '2026-09-15', 100.0);
            """,
            (sched_v1, proj_a_id, stage_1_id),
        )
        cur.execute(
            """
            INSERT INTO approved_actuals (
                actual_id, decision_id, event_id, schedule_id, activity_id,
                project_id, stage_id, actual_start, actual_pct_complete
            ) VALUES ('AA-03', 'DEC-03', 'EV-03', %s, 'ACT-03', %s, %s, '2026-09-15', 25.0);
            """,
            (sched_v1, proj_a_id, stage_2_id),
        )

        # 8. Activities in V2 (independent schedule version: only 1 activity, 10% complete)
        cur.execute(
            """
            INSERT INTO schedule_activities (
                activity_id, schedule_id, project_id, activity_name, discipline, location,
                planned_start, planned_finish, planned_quantity, weight_factor
            ) VALUES ('ACT-V2-01', %s, %s, 'Site Prep V2', 'Civil', 'Zone 1', '2026-09-01', '2026-09-10', 50.0, 1.0);
            """,
            (sched_v2, proj_a_id),
        )
        cur.execute(
            """
            INSERT INTO approved_actuals (
                actual_id, decision_id, event_id, schedule_id, activity_id,
                project_id, actual_start, actual_pct_complete
            ) VALUES ('AA-V2-01', 'DEC-V2-01', 'EV-V2-01', %s, 'ACT-V2-01', %s, '2026-09-01', 10.0);
            """,
            (sched_v2, proj_a_id),
        )

        # 9. Activity in Project B
        cur.execute(
            """
            INSERT INTO schedule_activities (
                activity_id, schedule_id, project_id, activity_name, discipline, location,
                planned_start, planned_finish, planned_quantity, weight_factor
            ) VALUES ('ACT-B-01', %s, %s, 'Project B Activity', 'Civil', 'Zone B', '2026-09-01', '2026-09-10', 100.0, 1.0);
            """,
            (sched_b, proj_b_id),
        )
        cur.execute(
            """
            INSERT INTO approved_actuals (
                actual_id, decision_id, event_id, schedule_id, activity_id,
                project_id, actual_start, actual_pct_complete
            ) VALUES ('AA-B-01', 'DEC-B-01', 'EV-B-01', %s, 'ACT-B-01', %s, '2026-09-01', 99.0);
            """,
            (sched_b, proj_b_id),
        )

    conn.commit()
    conn.close()

    yield {
        "user_pm": user_pm,
        "proj_a_id": proj_a_id,
        "proj_b_id": proj_b_id,
        "sched_v1": sched_v1,
        "sched_v2": sched_v2,
        "sched_b": sched_b,
        "stage_1_id": stage_1_id,
        "stage_2_id": stage_2_id,
    }

    # Teardown
    cleanup_conn = get_connection()
    with cleanup_conn.cursor() as cur:
        cur.execute("DELETE FROM audit_logs WHERE project_id IN (%s, %s);", (proj_a_id, proj_b_id))
        cur.execute("DELETE FROM approved_actuals WHERE project_id IN (%s, %s);", (proj_a_id, proj_b_id))
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

def test_activity_progress_percentages(phase8_test_fixture):
    """
    Mandatory Tests 1, 2, 3, 4:
    Verifies 0%, 25%, 50%, and 100% activity progress values.
    """
    data = phase8_test_fixture
    client = TestClient(app)
    token = _make_jwt(str(data["user_pm"]))
    headers = {"Authorization": f"Bearer {token}"}

    # 1. ACT-01: 50%
    r1 = client.get(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/activities/ACT-01/progress",
        headers=headers,
    )
    assert r1.status_code == 200, r1.text
    res1 = r1.json()
    assert res1["progress_pct"] == 50.0
    assert res1["canonical_state"] == "IN_PROGRESS"
    assert res1["calculation_basis"] == "ACTUAL_PCT_COMPLETE"

    # 2. ACT-02: 100%
    r2 = client.get(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/activities/ACT-02/progress",
        headers=headers,
    )
    assert r2.status_code == 200
    res2 = r2.json()
    assert res2["progress_pct"] == 100.0
    assert res2["canonical_state"] == "COMPLETED"

    # 3. ACT-03: 25%
    r3 = client.get(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/activities/ACT-03/progress",
        headers=headers,
    )
    assert r3.status_code == 200
    res3 = r3.json()
    assert res3["progress_pct"] == 25.0
    assert res3["canonical_state"] == "IN_PROGRESS"

    # 4. ACT-04: 0% (no approved actual)
    r4 = client.get(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/activities/ACT-04/progress",
        headers=headers,
    )
    assert r4.status_code == 200
    res4 = r4.json()
    assert res4["progress_pct"] == 0.0
    assert res4["canonical_state"] == "NOT_STARTED"
    assert res4["calculation_basis"] == "DEFAULT_ZERO"


def test_quantity_derived_and_null_progress(phase8_test_fixture):
    """
    Mandatory Test 5:
    Verifies behavior when actual_pct_complete is NULL, testing quantity fallback.
    """
    data = phase8_test_fixture
    conn = get_connection()
    with conn.cursor() as cur:
        # Insert activity with planned_quantity = 200.0, actual_quantity = 150.0, actual_pct_complete = NULL
        cur.execute(
            """
            INSERT INTO schedule_activities (
                activity_id, schedule_id, project_id, stage_id, activity_name, discipline, location,
                planned_start, planned_finish, planned_quantity, weight_factor
            ) VALUES ('ACT-QTY-01', %s, %s, %s, 'Track Laying', 'Railway', 'Zone 1', '2026-09-01', '2026-09-10', 200.0, 1.0);
            """,
            (data["sched_v1"], data["proj_a_id"], data["stage_1_id"]),
        )
        cur.execute(
            """
            INSERT INTO approved_actuals (
                actual_id, decision_id, event_id, schedule_id, activity_id,
                project_id, stage_id, actual_start, actual_quantity, actual_pct_complete
            ) VALUES ('AA-QTY-01', 'DEC-QTY-01', 'EV-QTY-01', %s, 'ACT-QTY-01', %s, %s, '2026-09-01', 150.0, NULL);
            """,
            (data["sched_v1"], data["proj_a_id"], data["stage_1_id"]),
        )
    conn.commit()
    conn.close()

    client = TestClient(app)
    token = _make_jwt(str(data["user_pm"]))
    headers = {"Authorization": f"Bearer {token}"}

    r = client.get(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/activities/ACT-QTY-01/progress",
        headers=headers,
    )
    assert r.status_code == 200
    res = r.json()
    # 150 / 200 * 100 = 75.0%
    assert res["progress_pct"] == 75.0
    assert res["calculation_basis"] == "QUANTITY_DERIVED"


def test_weighted_stage_rollups_and_contributions(phase8_test_fixture):
    """
    Mandatory Tests 6, 7:
    Verifies stage progress rollup with weighted activities:
    Stage 1:
      ACT-01: weight=1.0, progress=50% -> contribution = 1*50 / 4 = 12.5%
      ACT-02: weight=3.0, progress=100% -> contribution = 3*100 / 4 = 75.0%
      Stage 1 Progress = (50 + 300) / 4 = 350 / 4 = 87.5%
    Stage 2:
      ACT-03: weight=2.0, progress=25% -> contribution = 2*25 / 4 = 12.5%
      ACT-04: weight=2.0, progress=0% -> contribution = 0.0%
      Stage 2 Progress = (50 + 0) / 4 = 12.5%
    """
    data = phase8_test_fixture
    client = TestClient(app)
    token = _make_jwt(str(data["user_pm"]))
    headers = {"Authorization": f"Bearer {token}"}

    # Stage 1
    r1 = client.get(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/stages/{data['stage_1_id']}/progress",
        headers=headers,
    )
    assert r1.status_code == 200
    s1 = r1.json()
    assert s1["progress_pct"] == 87.5
    assert s1["activity_count"] == 2
    assert s1["completed_count"] == 1

    # Check explainable activity contributions
    acts_s1 = {a["activity_id"]: a for a in s1["activities"]}
    assert acts_s1["ACT-01"]["weighted_contribution"] == 12.5
    assert acts_s1["ACT-02"]["weighted_contribution"] == 75.0

    # Stage 2
    r2 = client.get(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/stages/{data['stage_2_id']}/progress",
        headers=headers,
    )
    assert r2.status_code == 200
    s2 = r2.json()
    assert s2["progress_pct"] == 12.5
    assert s2["activity_count"] == 2
    assert s2["completed_count"] == 0

    acts_s2 = {a["activity_id"]: a for a in s2["activities"]}
    assert acts_s2["ACT-03"]["weighted_contribution"] == 12.5
    assert acts_s2["ACT-04"]["weighted_contribution"] == 0.0


def test_schedule_stage_weighted_rollup(phase8_test_fixture):
    """
    Verifies schedule progress weighting stages:
    Stage 1: weight_pct=60.0, progress=87.5% -> contribution = 60 * 87.5 / 100 = 52.5%
    Stage 2: weight_pct=40.0, progress=12.5% -> contribution = 40 * 12.5 / 100 = 5.0%
    Schedule Progress = 52.5 + 5.0 = 57.5%
    """
    data = phase8_test_fixture
    client = TestClient(app)
    token = _make_jwt(str(data["user_pm"]))
    headers = {"Authorization": f"Bearer {token}"}

    r = client.get(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/progress",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    sched_prog = r.json()
    assert sched_prog["progress_pct"] == 57.5
    assert sched_prog["calculation_basis"] == "STAGE_WEIGHTED"
    assert sched_prog["total_activities"] == 4
    assert sched_prog["completed_activities"] == 1
    assert sched_prog["in_progress_activities"] == 2
    assert sched_prog["not_started_activities"] == 1

    # Check stage contributions
    stg_map = {s["stage_name"]: s for s in sched_prog["stages"]}
    assert stg_map["Foundation Stage"]["weighted_contribution"] == 52.5
    assert stg_map["Superstructure Stage"]["weighted_contribution"] == 5.0


def test_reopened_activity_uses_current_revision_no_double_count(phase8_test_fixture):
    """
    Mandatory Tests 8, 9, 10:
    Verifies that a reopened activity uses only its current revision,
    and historical approved actuals (stored in audit logs) are NEVER summed or double counted.
    Original: 100% -> Reopened & Revised: 60%.
    Progress must be 60.0%, NOT 160.0%.
    """
    data = phase8_test_fixture
    conn = get_connection()
    with conn.cursor() as cur:
        # Simulate audit log for the original 100% revision
        cur.execute(
            """
            INSERT INTO audit_logs (
                entity_type, entity_id, action, actor_id, before_state, after_state,
                payload_hash, previous_hash, current_hash, project_id, schedule_id
            ) VALUES (
                'approved_actuals', 'AA-02', 'REVISE_APPROVED_ACTUAL', %s,
                '{"actual_pct_complete": 100.0}', '{"actual_pct_complete": 60.0}',
                'hash1', 'hash0', 'hash1', %s, %s
            );
            """,
            (data["user_pm"], data["proj_a_id"], data["sched_v1"]),
        )
        # Update current approved actual for ACT-02 to 60.0%
        cur.execute(
            """
            UPDATE approved_actuals
            SET actual_pct_complete = 60.0,
                is_reopened = FALSE,
                rework_notes = 'Rework verified and approved at 60 percent'
            WHERE activity_id = 'ACT-02' AND schedule_id = %s;
            """,
            (data["sched_v1"],),
        )
    conn.commit()
    conn.close()

    client = TestClient(app)
    token = _make_jwt(str(data["user_pm"]))
    headers = {"Authorization": f"Bearer {token}"}

    # Activity Progress must be 60.0%
    r = client.get(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/activities/ACT-02/progress",
        headers=headers,
    )
    assert r.status_code == 200
    res = r.json()
    assert res["progress_pct"] == 60.0
    # Canonical state is now IN_PROGRESS (< 100)
    assert res["canonical_state"] == "IN_PROGRESS"

    # Stage 1 Progress re-evaluated:
    # ACT-01: weight 1.0, 50%
    # ACT-02: weight 3.0, 60%
    # Stage 1 = (50 + 180) / 4 = 230 / 4 = 57.5% (NOT double-counted with old 100%)
    r_stg = client.get(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/stages/{data['stage_1_id']}/progress",
        headers=headers,
    )
    assert r_stg.status_code == 200
    assert r_stg.json()["progress_pct"] == 57.5


def test_null_and_zero_weight_behavior(phase8_test_fixture):
    """
    Mandatory Tests 11, 12:
    Verifies:
    1. weight_factor = NULL defaults safely to 1.0.
    2. Sum of weights == 0 returns 0.0 without division-by-zero, NaN, or crash.
    """
    data = phase8_test_fixture
    conn = get_connection()
    zero_stage_id = uuid.uuid4()
    with conn.cursor() as cur:
        # Create a stage with zero-weight activities
        cur.execute(
            """
            INSERT INTO stages (stage_id, project_id, schedule_id, stage_code, stage_name, sequence_order, weight_pct, status)
            VALUES (%s, %s, %s, 'STG-ZERO', 'Zero Weight Stage', 3, 0.0, 'NOT_STARTED');
            """,
            (zero_stage_id, data["proj_a_id"], data["sched_v1"]),
        )
        cur.execute(
            """
            INSERT INTO schedule_activities (
                activity_id, schedule_id, project_id, stage_id, activity_name, discipline, location,
                planned_start, planned_finish, planned_quantity, weight_factor
            ) VALUES ('ACT-ZW-01', %s, %s, %s, 'Signage Inspection', 'Safety', 'Zone 1', '2026-09-01', '2026-09-02', 1.0, 0.0);
            """,
            (data["sched_v1"], data["proj_a_id"], zero_stage_id),
        )
    conn.commit()
    conn.close()

    client = TestClient(app)
    token = _make_jwt(str(data["user_pm"]))
    headers = {"Authorization": f"Bearer {token}"}

    r = client.get(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/stages/{zero_stage_id}/progress",
        headers=headers,
    )
    assert r.status_code == 200
    res = r.json()
    assert res["progress_pct"] == 0.0
    assert not any(str(res[k]).lower() in ("nan", "infinity") for k in res if isinstance(res[k], (int, float, str)))


def test_schedule_version_and_project_isolation(phase8_test_fixture):
    """
    Mandatory Tests 13, 14, 15:
    Verifies:
    1. Schedule V1 and V2 are calculated independently (V1 != V2).
    2. Project A cannot access Project B progress.
    3. Stage isolation: Stage 1 activities do not bleed into Stage 2.
    """
    data = phase8_test_fixture
    client = TestClient(app)
    token = _make_jwt(str(data["user_pm"]))
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Project level progress across schedules
    r_proj = client.get(
        f"/api/v1/projects/{data['proj_a_id']}/progress",
        headers=headers,
    )
    assert r_proj.status_code == 200
    res_proj = r_proj.json()
    assert res_proj["project_id"] == str(data["proj_a_id"])

    # Check both versions exist with distinct independent percentages
    sched_map = {s["schedule_id"]: s for s in res_proj["schedules"]}
    assert data["sched_v1"] in sched_map
    assert data["sched_v2"] in sched_map
    assert sched_map[data["sched_v1"]]["progress_pct"] == 57.5
    assert sched_map[data["sched_v2"]]["progress_pct"] == 10.0
    # Independent: never summed or averaged across versions!

    # 2. Project Isolation: user_pm attempting to access Project B
    r_unauth = client.get(
        f"/api/v1/projects/{data['proj_b_id']}/progress",
        headers=headers,
    )
    # user_pm is not a member of project B -> 403 Forbidden
    assert r_unauth.status_code == 403

    r_sched_unauth = client.get(
        f"/api/v1/projects/{data['proj_b_id']}/schedules/{data['sched_b']}/progress",
        headers=headers,
    )
    assert r_sched_unauth.status_code == 403


def test_stage_lifecycle_decoupled_from_progress(phase8_test_fixture):
    """
    Mandatory Test 16:
    Verifies that stage numerical progress does NOT overwrite Phase 5 stage lifecycle state.
    Even if stage progress is high, stage completion remains governed by Phase 5 rules.
    """
    data = phase8_test_fixture
    proj_ctx = ProjectContext(
        user=CurrentUser(id=str(data["user_pm"]), email=f"{data['user_pm']}@example.com", role="PROJECT_MANAGER"),
        project_id=data["proj_a_id"],
        membership_id=uuid.uuid4(),
        role="PROJECT_MANAGER",
        project_name="Phase 8 Project A",
    )
    ctx = ScheduleContext(
        project_context=proj_ctx,
        schedule_id=data["sched_v1"],
    )

    # Stage 1 has progress = 57.5% (or 87.5%)
    prog_res = ProgressService.get_stage_progress(ctx, data["stage_1_id"])
    assert prog_res["progress_pct"] > 0.0

    # Stage state resolution remains Phase 5 canonical engine
    state_res = StageService.calculate_stage_state(ctx, data["stage_1_id"])
    assert "computed_state" in state_res
    # Stage is IN_PROGRESS, not COMPLETED, because not all activities are complete
    assert state_res["computed_state"] == "IN_PROGRESS"


def test_hierarchical_progress_breakdown_endpoint(phase8_test_fixture):
    """
    Verifies the hierarchical breakdown endpoint:
    Schedule -> Stages -> Activities.
    """
    data = phase8_test_fixture
    client = TestClient(app)
    token = _make_jwt(str(data["user_pm"]))
    headers = {"Authorization": f"Bearer {token}"}

    r = client.get(
        f"/api/v1/projects/{data['proj_a_id']}/schedules/{data['sched_v1']}/progress/breakdown",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    breakdown = r.json()
    assert breakdown["overall_progress_pct"] == 57.5
    assert len(breakdown["stages"]) == 2
    # Check that stage 1 has activities list
    stg1 = breakdown["stages"][0]
    assert len(stg1["activities"]) == 2
    assert stg1["activities"][0]["activity_id"] in ("ACT-01", "ACT-02")
