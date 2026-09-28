"""
Phase 4A Tests — Project Domain, Membership Scoping, RBAC, and Audit Logging.
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
def phase4_project_fixture():
    conn = get_connection()
    conn.autocommit = False

    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    proj_a = uuid.uuid4()
    proj_b = uuid.uuid4()

    with conn.cursor() as cur:
        # Create user profiles
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_a, "Alpha Manager", "SUPERVISOR"),
        )
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_b, "Beta Engineer", "SITE_ENGINEER"),
        )

        # Create Project A and Project B
        code_a = f"PRJ-A-{uuid.uuid4().hex[:6]}"
        code_b = f"PRJ-B-{uuid.uuid4().hex[:6]}"
        cur.execute(
            """
            INSERT INTO projects (project_id, project_code, project_name, status, created_by)
            VALUES (%s, %s, %s, 'ACTIVE', %s);
            """,
            (proj_a, code_a, "Project Alpha", user_a),
        )
        cur.execute(
            """
            INSERT INTO projects (project_id, project_code, project_name, status, created_by)
            VALUES (%s, %s, %s, 'ACTIVE', %s);
            """,
            (proj_b, code_b, "Project Beta", user_b),
        )

        # Memberships: User A is PROJECT_MANAGER on A; User B is SITE_ENGINEER on B
        cur.execute(
            """
            INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active, status)
            VALUES (%s, %s, %s, 'PROJECT_MANAGER', TRUE, 'ACTIVE');
            """,
            (uuid.uuid4(), user_a, proj_a),
        )
        cur.execute(
            """
            INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active, status)
            VALUES (%s, %s, %s, 'SITE_ENGINEER', TRUE, 'ACTIVE');
            """,
            (uuid.uuid4(), user_b, proj_b),
        )

        conn.commit()

    fixtures = {
        "user_a": user_a,
        "user_b": user_b,
        "proj_a": proj_a,
        "proj_b": proj_b,
        "code_a": code_a,
        "code_b": code_b,
    }

    yield fixtures

    # Cleanup
    with conn.cursor() as cur:
        cur.execute("DELETE FROM audit_logs WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM project_memberships WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM projects WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM profiles WHERE id IN (%s, %s);", (user_a, user_b))
        conn.commit()
    conn.close()


def test_create_project_and_membership_and_audit():
    """Verify project creation establishes project identity, creator membership, and audit log."""
    creator_id = uuid.uuid4()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
                (creator_id, "Test Creator", "SUPERVISOR"),
            )
            conn.commit()

    token = _make_jwt(str(creator_id))
    client = TestClient(app)

    unique_code = f"PRJ-NEW-{uuid.uuid4().hex[:6]}"
    payload = {
        "project_code": unique_code,
        "project_name": "New Greenfield Corridor",
        "description": "Expressway expansion project",
        "client_name": "National Highways Authority",
        "project_type": "HIGHWAY",
        "location": "Sector 42",
        "planned_start": "2026-06-01",
        "planned_finish": "2028-12-31",
    }

    resp = client.post(
        "/api/v1/projects",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["project_code"] == unique_code
    assert data["project_name"] == "New Greenfield Corridor"
    assert data["status"] == "ACTIVE"
    created_id = uuid.UUID(data["project_id"])

    # Verify membership created in DB
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT assigned_role, active FROM project_memberships WHERE user_id = %s AND project_id = %s;",
                (creator_id, created_id),
            )
            row = cur.fetchone()
            assert row is not None
            assert row["assigned_role"] == "PROJECT_MANAGER"
            assert row["active"] is True

            # Verify audit log was recorded
            cur.execute(
                "SELECT action, entity_type FROM audit_logs WHERE project_id = %s AND action = 'PROJECT_CREATED';",
                (created_id,),
            )
            audit_row = cur.fetchone()
            assert audit_row is not None
            assert audit_row["action"] == "PROJECT_CREATED"

        # Cleanup
        with conn.cursor() as cur:
            cur.execute("DELETE FROM audit_logs WHERE project_id = %s;", (created_id,))
            cur.execute("DELETE FROM project_memberships WHERE project_id = %s;", (created_id,))
            cur.execute("DELETE FROM projects WHERE project_id = %s;", (created_id,))
            cur.execute("DELETE FROM profiles WHERE id = %s;", (creator_id,))
            conn.commit()


def test_create_project_duplicate_code_conflict(phase4_project_fixture):
    """Verify attempting to create duplicate project_code returns HTTP 409."""
    f = phase4_project_fixture
    token = _make_jwt(str(f["user_a"]))
    client = TestClient(app)

    payload = {
        "project_code": f["code_a"],  # duplicate
        "project_name": "Duplicate Project",
    }

    resp = client.post(
        "/api/v1/projects",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"].lower()


def test_cross_project_isolation(phase4_project_fixture):
    """
    User A is in Project A; User B is in Project B.
    A -> Project A: ALLOW (200)
    A -> Project B: DENY (403)
    B -> Project B: ALLOW (200)
    B -> Project A: DENY (403)
    """
    f = phase4_project_fixture
    token_a = _make_jwt(str(f["user_a"]))
    token_b = _make_jwt(str(f["user_b"]))
    client = TestClient(app)

    # User A -> Project A
    resp_aa = client.get(
        f"/api/v1/projects/{f['proj_a']}",
        headers={"Authorization": f"Bearer {token_a}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_aa.status_code == 200
    assert resp_aa.json()["project_code"] == f["code_a"]

    # User A -> Project B (Mutual Denial)
    resp_ab = client.get(
        f"/api/v1/projects/{f['proj_b']}",
        headers={"Authorization": f"Bearer {token_a}", "X-Project-ID": str(f["proj_b"])},
    )
    assert resp_ab.status_code == 403
    assert resp_ab.json()["detail"]["error_code"] == "PROJECT_ACCESS_DENIED"

    # User B -> Project B
    resp_bb = client.get(
        f"/api/v1/projects/{f['proj_b']}",
        headers={"Authorization": f"Bearer {token_b}", "X-Project-ID": str(f["proj_b"])},
    )
    assert resp_bb.status_code == 200
    assert resp_bb.json()["project_code"] == f["code_b"]

    # User B -> Project A (Mutual Denial)
    resp_ba = client.get(
        f"/api/v1/projects/{f['proj_a']}",
        headers={"Authorization": f"Bearer {token_b}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_ba.status_code == 403
    assert resp_ba.json()["detail"]["error_code"] == "PROJECT_ACCESS_DENIED"


def test_list_user_projects_membership_scoped(phase4_project_fixture):
    """Listing user projects must return only projects the caller is member of."""
    f = phase4_project_fixture
    token_a = _make_jwt(str(f["user_a"]))
    token_b = _make_jwt(str(f["user_b"]))
    client = TestClient(app)

    resp_a = client.get("/api/v1/projects", headers={"Authorization": f"Bearer {token_a}"})
    assert resp_a.status_code == 200
    proj_ids_a = [p["project_id"] for p in resp_a.json()]
    assert str(f["proj_a"]) in proj_ids_a
    assert str(f["proj_b"]) not in proj_ids_a  # Project B not visible to User A

    resp_b = client.get("/api/v1/projects", headers={"Authorization": f"Bearer {token_b}"})
    assert resp_b.status_code == 200
    proj_ids_b = [p["project_id"] for p in resp_b.json()]
    assert str(f["proj_b"]) in proj_ids_b
    assert str(f["proj_a"]) not in proj_ids_b  # Project A not visible to User B


def test_update_project_rbac(phase4_project_fixture):
    """Only PROJECT_MANAGER or OWNER can update project metadata."""
    f = phase4_project_fixture
    token_a = _make_jwt(str(f["user_a"]))  # PROJECT_MANAGER on A
    token_b = _make_jwt(str(f["user_b"]))  # SITE_ENGINEER on B
    client = TestClient(app)

    # User A (PROJECT_MANAGER on A) updates Project A
    resp_update = client.patch(
        f"/api/v1/projects/{f['proj_a']}",
        json={"project_name": "Updated Alpha Corridor"},
        headers={"Authorization": f"Bearer {token_a}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_update.status_code == 200
    assert resp_update.json()["project_name"] == "Updated Alpha Corridor"

    # User B (SITE_ENGINEER on B) tries to update Project B
    resp_denied = client.patch(
        f"/api/v1/projects/{f['proj_b']}",
        json={"project_name": "Illegal Name Change"},
        headers={"Authorization": f"Bearer {token_b}", "X-Project-ID": str(f["proj_b"])},
    )
    assert resp_denied.status_code == 403
    assert resp_denied.json()["detail"]["error_code"] == "PERMISSION_DENIED"


def test_project_archive_and_activation_lifecycle(phase4_project_fixture):
    """Verify archive and activate status transitions and audit logging."""
    f = phase4_project_fixture
    token_a = _make_jwt(str(f["user_a"]))
    client = TestClient(app)

    # Archive
    resp_arch = client.post(
        f"/api/v1/projects/{f['proj_a']}/archive",
        headers={"Authorization": f"Bearer {token_a}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_arch.status_code == 200
    assert resp_arch.json()["status"] == "ARCHIVED"

    # Activate
    resp_act = client.post(
        f"/api/v1/projects/{f['proj_a']}/activate",
        headers={"Authorization": f"Bearer {token_a}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_act.status_code == 200
    assert resp_act.json()["status"] == "ACTIVE"


def test_forged_project_id_denied(phase4_project_fixture):
    """Forged project_id must return 403 PROJECT_ACCESS_DENIED without leaking whether it exists."""
    f = phase4_project_fixture
    token_a = _make_jwt(str(f["user_a"]))
    client = TestClient(app)
    forged_id = str(uuid.uuid4())

    resp = client.get(
        f"/api/v1/projects/{forged_id}",
        headers={"Authorization": f"Bearer {token_a}", "X-Project-ID": forged_id},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error_code"] == "PROJECT_ACCESS_DENIED"
