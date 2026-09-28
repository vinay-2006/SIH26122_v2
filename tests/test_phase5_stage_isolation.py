"""
Tests for Phase 5 — Stage Isolation, Schedule Context Enforcement, and Phase 6 Interface Contract.
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
def phase5_isolation_fixture():
    conn = get_connection()
    conn.autocommit = False

    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    proj_a = uuid.uuid4()
    proj_b = uuid.uuid4()
    sched_a1 = f"SCH-A1-{uuid.uuid4().hex[:6]}"
    sched_a2 = f"SCH-A2-{uuid.uuid4().hex[:6]}"
    sched_b = f"SCH-B-{uuid.uuid4().hex[:6]}"
    stage_a = uuid.uuid4()
    stage_b = uuid.uuid4()

    with conn.cursor() as cur:
        # Profiles
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_a, "User A", "SUPERVISOR"),
        )
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_b, "User B", "SUPERVISOR"),
        )
        # Projects
        cur.execute(
            """
            INSERT INTO projects (project_id, project_code, project_name, status, created_by) VALUES
            (%s, %s, 'Project A', 'ACTIVE', %s),
            (%s, %s, 'Project B', 'ACTIVE', %s);
            """,
            (proj_a, f"PRJ-A-{uuid.uuid4().hex[:6]}", user_a, proj_b, f"PRJ-B-{uuid.uuid4().hex[:6]}", user_b),
        )
        # Memberships: user_a only in proj_a; user_b only in proj_b
        cur.execute(
            """
            INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active, status) VALUES
            (%s, %s, %s, 'PROJECT_MANAGER', TRUE, 'ACTIVE'),
            (%s, %s, %s, 'PROJECT_MANAGER', TRUE, 'ACTIVE');
            """,
            (uuid.uuid4(), user_a, proj_a, uuid.uuid4(), user_b, proj_b),
        )
        # Schedules
        cur.execute(
            """
            INSERT INTO schedules (schedule_id, project_name, project_id, version_code, active) VALUES
            (%s, 'Project A', %s, 'V1.0', TRUE),
            (%s, 'Project A', %s, 'V2.0', FALSE),
            (%s, 'Project B', %s, 'V1.0', TRUE);
            """,
            (sched_a1, proj_a, sched_a2, proj_a, sched_b, proj_b),
        )
        # Stages: stage_a under proj_a and sched_a1; stage_b under proj_b and sched_b
        cur.execute(
            """
            INSERT INTO stages (stage_id, project_id, schedule_id, stage_code, stage_name) VALUES
            (%s, %s, %s, 'STG-A', 'Project A Stage'),
            (%s, %s, %s, 'STG-B', 'Project B Stage');
            """,
            (stage_a, proj_a, sched_a1, stage_b, proj_b, sched_b),
        )
        # Activity in stage_a
        act_id = "ACT-ISO-01"
        cur.execute(
            """
            INSERT INTO schedule_activities (
                activity_id, schedule_id, project_id, stage_id, activity_name,
                planned_start, planned_finish, discipline, location
            ) VALUES (%s, %s, %s, %s, 'Isolation Test Act', '2026-06-01', '2026-06-15', 'Civil', 'Zone 1');
            """,
            (act_id, sched_a1, proj_a, stage_a),
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
        "stage_a": stage_a,
        "stage_b": stage_b,
        "act_id": act_id,
    }

    # Teardown
    cleanup_conn = get_connection()
    with cleanup_conn.cursor() as cur:
        cur.execute("DELETE FROM schedule_activities WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM stages WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM schedules WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM project_memberships WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM projects WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM profiles WHERE id IN (%s, %s);", (user_a, user_b))
    cleanup_conn.commit()
    cleanup_conn.close()


def test_cross_project_stage_isolation_mutual_denial(phase5_isolation_fixture):
    client = TestClient(app)
    token_a = _make_jwt(str(phase5_isolation_fixture["user_a"]))
    token_b = _make_jwt(str(phase5_isolation_fixture["user_b"]))
    proj_a = phase5_isolation_fixture["proj_a"]
    proj_b = phase5_isolation_fixture["proj_b"]
    stage_a = phase5_isolation_fixture["stage_a"]
    stage_b = phase5_isolation_fixture["stage_b"]

    # User A -> Stage A (allowed)
    res_aa = client.get(f"/api/v1/projects/{proj_a}/stages/{stage_a}", headers={"Authorization": f"Bearer {token_a}"})
    assert res_aa.status_code == 200

    # User A -> Stage B in Project B (denied - User A is not in Project B)
    res_ab = client.get(f"/api/v1/projects/{proj_b}/stages/{stage_b}", headers={"Authorization": f"Bearer {token_a}"})
    assert res_ab.status_code == 403

    # User B -> Stage B (allowed)
    res_bb = client.get(f"/api/v1/projects/{proj_b}/stages/{stage_b}", headers={"Authorization": f"Bearer {token_b}"})
    assert res_bb.status_code == 200

    # User B -> Stage A in Project A (denied - User B is not in Project A)
    res_ba = client.get(f"/api/v1/projects/{proj_a}/stages/{stage_a}", headers={"Authorization": f"Bearer {token_b}"})
    assert res_ba.status_code == 403


def test_cross_schedule_stage_isolation(phase5_isolation_fixture):
    client = TestClient(app)
    token_a = _make_jwt(str(phase5_isolation_fixture["user_a"]))
    proj_a = phase5_isolation_fixture["proj_a"]
    sched_a1 = phase5_isolation_fixture["sched_a1"]
    sched_a2 = phase5_isolation_fixture["sched_a2"]
    stage_a = phase5_isolation_fixture["stage_a"]

    # Listing stages under sched_a1 returns stage_a
    res1 = client.get(
        f"/api/v1/projects/{proj_a}/stages",
        headers={"Authorization": f"Bearer {token_a}", "X-Schedule-ID": sched_a1},
    )
    assert res1.status_code == 200
    ids_1 = [s["stage_id"] for s in res1.json()]
    assert str(stage_a) in ids_1

    # Listing stages under sched_a2 does NOT leak stage_a
    res2 = client.get(
        f"/api/v1/projects/{proj_a}/stages",
        headers={"Authorization": f"Bearer {token_a}", "X-Schedule-ID": sched_a2},
    )
    assert res2.status_code == 200
    ids_2 = [s["stage_id"] for s in res2.json()]
    assert str(stage_a) not in ids_2


def test_missing_schedule_context_rejected_400(phase5_isolation_fixture):
    client = TestClient(app)
    token_a = _make_jwt(str(phase5_isolation_fixture["user_a"]))
    proj_a = phase5_isolation_fixture["proj_a"]

    # Creating stage without schedule context -> MUST fail with 400 INVALID_SCHEDULE_CONTEXT (Rule 3)
    res = client.post(
        f"/api/v1/projects/{proj_a}/stages",
        json={"stage_name": "No Schedule Stage"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert res.status_code == 400
    assert "INVALID_SCHEDULE_CONTEXT" in res.text or "Explicit schedule_id is required" in res.text


def test_activity_execution_context_for_phase_6(phase5_isolation_fixture):
    client = TestClient(app)
    token_a = _make_jwt(str(phase5_isolation_fixture["user_a"]))
    proj_a = phase5_isolation_fixture["proj_a"]
    sched_a1 = phase5_isolation_fixture["sched_a1"]
    act_id = phase5_isolation_fixture["act_id"]
    stage_a = phase5_isolation_fixture["stage_a"]

    res = client.get(
        f"/api/v1/projects/{proj_a}/activities/{act_id}/execution-context",
        headers={"Authorization": f"Bearer {token_a}", "X-Schedule-ID": sched_a1},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["activity_id"] == act_id
    assert data["schedule_id"] == sched_a1
    assert data["project_id"] == str(proj_a)
    assert data["stage_id"] == str(stage_a)
    assert data["canonical_execution_state"] == "NOT_STARTED"
    assert data["workflow_condition"] == "NONE"
