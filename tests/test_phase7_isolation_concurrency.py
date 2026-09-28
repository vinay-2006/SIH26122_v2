"""
SETUAI V7 Phase 7 Tests — Cross-Project, Cross-Schedule Isolation and Concurrency
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
def phase7_isolation_fixture():
    conn = get_connection()
    conn.autocommit = False

    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    proj_a = uuid.uuid4()
    proj_b = uuid.uuid4()
    sched_a1 = f"SCH-ISO-A1-{uuid.uuid4().hex[:6]}"
    sched_a2 = f"SCH-ISO-A2-{uuid.uuid4().hex[:6]}"
    sched_b = f"SCH-ISO-B-{uuid.uuid4().hex[:6]}"
    act_id = "ACT-COMMON-001"
    actual_a1 = f"ACTUAL-A1-{uuid.uuid4().hex[:6]}"
    actual_a2 = f"ACTUAL-A2-{uuid.uuid4().hex[:6]}"

    with conn.cursor() as cur:
        # Profiles
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s), (%s, %s, %s);",
            (user_a, "User A", "SUPERVISOR", user_b, "User B", "SUPERVISOR"),
        )
        # Projects
        cur.execute(
            """
            INSERT INTO projects (project_id, project_code, project_name, status, created_by)
            VALUES (%s, %s, %s, 'ACTIVE', %s), (%s, %s, %s, 'ACTIVE', %s);
            """,
            (proj_a, f"PRJ-A-{uuid.uuid4().hex[:6]}", "Project A", user_a,
             proj_b, f"PRJ-B-{uuid.uuid4().hex[:6]}", "Project B", user_b),
        )
        # Memberships: user_a only in proj_a, user_b only in proj_b
        cur.execute(
            """
            INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active, status)
            VALUES (%s, %s, %s, 'PROJECT_MANAGER', TRUE, 'ACTIVE'),
                   (%s, %s, %s, 'PROJECT_MANAGER', TRUE, 'ACTIVE');
            """,
            (uuid.uuid4(), user_a, proj_a,
             uuid.uuid4(), user_b, proj_b),
        )
        # Schedules
        cur.execute(
            """
            INSERT INTO schedules (schedule_id, project_name, project_id, version_code, active)
            VALUES (%s, %s, %s, 'V1.0', TRUE),
                   (%s, %s, %s, 'V2.0', TRUE),
                   (%s, %s, %s, 'V1.0', TRUE);
            """,
            (sched_a1, "Project A", proj_a,
             sched_a2, "Project A", proj_a,
             sched_b, "Project B", proj_b),
        )
        # Activities: ACT-COMMON-001 in sched_a1 and sched_a2
        cur.execute(
            """
            INSERT INTO schedule_activities (
                activity_id, schedule_id, project_id, activity_name, discipline, location,
                planned_start, planned_finish, planned_quantity, baseline_pct_complete
            ) VALUES
            (%s, %s, %s, 'Common Activity Sched 1', 'Civil', 'Zone A', '2026-08-01', '2026-08-15', 100.0, 100.0),
            (%s, %s, %s, 'Common Activity Sched 2', 'Civil', 'Zone A', '2026-09-01', '2026-09-15', 100.0, 100.0);
            """,
            (act_id, sched_a1, proj_a, act_id, sched_a2, proj_a),
        )
        # Approved Actuals in both schedules
        cur.execute(
            """
            INSERT INTO approved_actuals (
                actual_id, decision_id, event_id, schedule_id, activity_id,
                project_id, actual_start, actual_finish, actual_pct_complete,
                actual_quantity, is_reopened
            ) VALUES
            (%s, 'DEC-A1', 'EV-A1', %s, %s, %s, '2026-08-01', '2026-08-15', 100.0, 100.0, FALSE),
            (%s, 'DEC-A2', 'EV-A2', %s, %s, %s, '2026-09-01', '2026-09-15', 100.0, 100.0, FALSE);
            """,
            (actual_a1, sched_a1, act_id, proj_a,
             actual_a2, sched_a2, act_id, proj_a),
        )

    conn.commit()
    conn.close()

    yield {
        "user_a": user_a,
        "user_b": user_b,
        "proj_a": proj_a,
        "proj_b": proj_b,
        "sched_a1": sched_a1,
        "sched_a2": sched_a2,
        "sched_b": sched_b,
        "act_id": act_id,
    }

    cleanup_conn = get_connection()
    with cleanup_conn.cursor() as cur:
        cur.execute("DELETE FROM audit_logs WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM approved_actuals WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM execution_events WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM schedule_activities WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM schedules WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM project_memberships WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM projects WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM profiles WHERE id IN (%s, %s);", (user_a, user_b))
    cleanup_conn.commit()
    cleanup_conn.close()


def test_cross_project_isolation(phase7_isolation_fixture):
    """User B (only member of Project B) cannot challenge or view activities in Project A."""
    client = TestClient(app)
    f = phase7_isolation_fixture
    token_b = _make_jwt(str(f["user_b"]))

    payload = {
        "reason": "QUALITY_FAILURE",
        "justification": "Cross-project unauthorized challenge attempt.",
    }

    # Attempt to reopen Project A's activity using Project B user credentials
    resp = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules/{f['sched_a1']}/activities/{f['act_id']}/reopen",
        json=payload,
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code in (403, 404), resp.text


def test_cross_schedule_isolation(phase7_isolation_fixture):
    """
    Reopening ACT-COMMON-001 in Schedule 1 leaves ACT-COMMON-001 in Schedule 2 completely untouched.
    """
    client = TestClient(app)
    f = phase7_isolation_fixture
    token_a = _make_jwt(str(f["user_a"]))

    # 1. Request reopen for ACT-COMMON-001 in sched_a1
    resp_req = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules/{f['sched_a1']}/activities/{f['act_id']}/reopen",
        json={"reason": "QUALITY_FAILURE", "justification": "Challenge in Schedule 1 only."},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_req.status_code == 201

    # 2. Approve reopen in sched_a1
    resp_app = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules/{f['sched_a1']}/activities/{f['act_id']}/reopen/decide",
        json={"decision": "APPROVED", "rework_instructions": "Rework for Sched 1."},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_app.status_code == 200
    assert resp_app.json()["workflow_condition"] == "REWORK_IN_PROGRESS"

    # 3. Check status of ACT-COMMON-001 in sched_a2
    resp_s2 = client.get(
        f"/api/v1/projects/{f['proj_a']}/schedules/{f['sched_a2']}/activities/{f['act_id']}/reopen",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_s2.status_code == 200
    s2_data = resp_s2.json()
    assert s2_data["workflow_condition"] == "NONE"
    assert s2_data["reopen_status"] == "NONE"
    assert s2_data["canonical_state"] == "COMPLETED"
