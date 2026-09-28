"""
Tests for Phase 5 — Stage Domain, Hierarchy Tree, Lifecycle, RBAC, and Audit Logging.
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
def phase5_fixture():
    conn = get_connection()
    conn.autocommit = False

    user_pm = uuid.uuid4()
    user_site_eng = uuid.uuid4()
    proj_id = uuid.uuid4()
    sched_id = f"SCH-P5-{uuid.uuid4().hex[:6]}"

    with conn.cursor() as cur:
        # Profiles
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_pm, "Stage PM", "SUPERVISOR"),
        )
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_site_eng, "Site Eng", "SITE_ENGINEER"),
        )

        # Project
        proj_code = f"PRJ-P5-{uuid.uuid4().hex[:6]}"
        cur.execute(
            """
            INSERT INTO projects (project_id, project_code, project_name, status, created_by)
            VALUES (%s, %s, %s, 'ACTIVE', %s);
            """,
            (proj_id, proj_code, "Phase 5 Test Project", user_pm),
        )

        # Memberships: PM -> PROJECT_MANAGER, Site Eng -> SITE_ENGINEER
        cur.execute(
            """
            INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active, status)
            VALUES (%s, %s, %s, 'PROJECT_MANAGER', TRUE, 'ACTIVE');
            """,
            (uuid.uuid4(), user_pm, proj_id),
        )
        cur.execute(
            """
            INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active, status)
            VALUES (%s, %s, %s, 'SITE_ENGINEER', TRUE, 'ACTIVE');
            """,
            (uuid.uuid4(), user_site_eng, proj_id),
        )

        # Schedule Version
        cur.execute(
            """
            INSERT INTO schedules (schedule_id, project_name, project_id, version_code, active)
            VALUES (%s, %s, %s, 'V1.0', TRUE);
            """,
            (sched_id, "Phase 5 Test Project", proj_id),
        )

    conn.commit()
    conn.close()

    yield {
        "user_pm": user_pm,
        "user_site_eng": user_site_eng,
        "project_id": proj_id,
        "schedule_id": sched_id,
    }

    # Teardown
    cleanup_conn = get_connection()
    with cleanup_conn.cursor() as cur:
        cur.execute("DELETE FROM stages WHERE project_id = %s;", (proj_id,))
        cur.execute("DELETE FROM audit_logs WHERE project_id = %s;", (proj_id,))
        cur.execute("DELETE FROM schedules WHERE project_id = %s;", (proj_id,))
        cur.execute("DELETE FROM project_memberships WHERE project_id = %s;", (proj_id,))
        cur.execute("DELETE FROM projects WHERE project_id = %s;", (proj_id,))
        cur.execute("DELETE FROM profiles WHERE id IN (%s, %s);", (user_pm, user_site_eng))
    cleanup_conn.commit()
    cleanup_conn.close()


def test_stage_create_and_hierarchy_tree(phase5_fixture):
    client = TestClient(app)
    pm_token = _make_jwt(str(phase5_fixture["user_pm"]))
    headers = {
        "Authorization": f"Bearer {pm_token}",
        "X-Schedule-ID": phase5_fixture["schedule_id"],
    }
    proj_id = phase5_fixture["project_id"]

    # 1. Create Parent Stage (e.g. Substructure)
    parent_payload = {
        "stage_code": "STG-SUB",
        "stage_name": "Substructure Works",
        "sequence_order": 1,
        "weight_pct": 40.0,
        "planned_start": "2026-06-01",
        "planned_finish": "2026-07-31",
    }
    res_parent = client.post(f"/api/v1/projects/{proj_id}/stages", json=parent_payload, headers=headers)
    assert res_parent.status_code == 201, res_parent.text
    parent_data = res_parent.json()
    assert parent_data["stage_name"] == "Substructure Works"
    parent_id = parent_data["stage_id"]

    # 2. Create Child Stage (e.g. Foundation Piling)
    child_payload = {
        "stage_code": "STG-PIL",
        "stage_name": "Foundation Piling",
        "parent_stage_id": parent_id,
        "sequence_order": 1,
        "weight_pct": 20.0,
    }
    res_child = client.post(f"/api/v1/projects/{proj_id}/stages", json=child_payload, headers=headers)
    assert res_child.status_code == 201, res_child.text
    child_data = res_child.json()
    assert child_data["parent_stage_id"] == parent_id

    # 3. Retrieve Stage Tree
    res_tree = client.get(f"/api/v1/projects/{proj_id}/stages/tree", headers=headers)
    assert res_tree.status_code == 200, res_tree.text
    tree = res_tree.json()
    assert len(tree) >= 1
    root_node = next((n for n in tree if n["stage_id"] == parent_id), None)
    assert root_node is not None
    assert len(root_node["children"]) == 1
    assert root_node["children"][0]["stage_id"] == child_data["stage_id"]

    # 4. Verify Audit Log
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT action, entity_type FROM audit_logs WHERE project_id = %s AND action = 'STAGE_CREATED';",
            (proj_id,),
        )
        logs = cur.fetchall()
        assert len(logs) >= 2
    conn.close()


def test_stage_duplicate_code_conflict(phase5_fixture):
    client = TestClient(app)
    pm_token = _make_jwt(str(phase5_fixture["user_pm"]))
    headers = {
        "Authorization": f"Bearer {pm_token}",
        "X-Schedule-ID": phase5_fixture["schedule_id"],
    }
    proj_id = phase5_fixture["project_id"]

    payload = {
        "stage_code": "STG-SUPER",
        "stage_name": "Superstructure",
    }
    res1 = client.post(f"/api/v1/projects/{proj_id}/stages", json=payload, headers=headers)
    assert res1.status_code == 201

    # Duplicate code in same schedule must be rejected
    res2 = client.post(f"/api/v1/projects/{proj_id}/stages", json=payload, headers=headers)
    assert res2.status_code in (400, 403, 409)


def test_stage_update_rbac_enforcement(phase5_fixture):
    client = TestClient(app)
    pm_token = _make_jwt(str(phase5_fixture["user_pm"]))
    eng_token = _make_jwt(str(phase5_fixture["user_site_eng"]))
    sched_id = phase5_fixture["schedule_id"]
    proj_id = phase5_fixture["project_id"]

    # Create stage as PM
    res = client.post(
        f"/api/v1/projects/{proj_id}/stages",
        json={"stage_code": "STG-MEP", "stage_name": "MEP Works"},
        headers={"Authorization": f"Bearer {pm_token}", "X-Schedule-ID": sched_id},
    )
    assert res.status_code == 201
    stage_id = res.json()["stage_id"]

    # SITE_ENGINEER attempts to update stage -> Forbidden (requires MANAGE_SCHEDULE)
    res_eng = client.patch(
        f"/api/v1/projects/{proj_id}/stages/{stage_id}",
        json={"stage_name": "Hacked MEP"},
        headers={"Authorization": f"Bearer {eng_token}"},
    )
    assert res_eng.status_code == 403

    # PROJECT_MANAGER updates stage -> Allowed
    res_pm = client.patch(
        f"/api/v1/projects/{proj_id}/stages/{stage_id}",
        json={"stage_name": "MEP & Instrumentation"},
        headers={"Authorization": f"Bearer {pm_token}"},
    )
    assert res_pm.status_code == 200
    assert res_pm.json()["stage_name"] == "MEP & Instrumentation"
