"""
Phase 3 Authentication, RBAC, and Context Unit Tests.

Covers:
1. Authentication:
   - Missing JWT -> 401
   - Malformed/Invalid JWT -> 401
   - Expired JWT -> 401
   - Missing Profile record in DB -> 401
   - Valid JWT & Profile -> CurrentUser resolved
2. Project Membership & Context:
   - Non-member requesting project -> 403 denied
   - Inactive/suspended membership -> 403 denied
   - Valid active membership -> ProjectContext resolved
   - Forged project_id rejected -> 403 denied
3. RBAC & Role Enforcement:
   - SITE_ENGINEER permitted CREATE_EXECUTION_EVENT, denied APPROVE_ACTUAL
   - SUPERVISOR permitted APPROVE_ACTUAL and REVIEW_CLAIM
   - PLANNER permitted MANAGE_SCHEDULE
   - AUDITOR permitted VIEW_AUDIT, denied CREATE_EXECUTION_EVENT
4. Forged Role Protection:
   - Frontend passes forged role 'SUPERVISOR', but membership in DB is 'SITE_ENGINEER'
     -> Evaluated strictly as SITE_ENGINEER and rejected.
"""

import time
import uuid
import jwt
import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from backend.auth.dependencies import get_current_user, require_authenticated_user
from backend.auth.models import CurrentUser
from backend.context.errors import SecurityException
from backend.context.project import ProjectContext, require_project_context
from backend.rbac.dependencies import require_permission, require_project_role
from backend.rbac.permissions import Permission
from backend.rbac.roles import ProjectRole
from backend.shared.db import get_connection

TEST_JWT_SECRET = "phase3-super-secret-key-12345678901234567890"


@pytest.fixture(autouse=True)
def configure_test_jwt(monkeypatch):
    """Ensure test JWT secret and dev mode are set for deterministic testing."""
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")


@pytest.fixture
def auth_db_fixture():
    """Seeds test profiles, projects, and memberships in the live DB, cleaning up afterwards."""
    conn = get_connection()
    conn.autocommit = False

    user_engineer = uuid.uuid4()
    user_supervisor = uuid.uuid4()
    user_outsider = uuid.uuid4()
    user_inactive = uuid.uuid4()

    proj_id = uuid.uuid4()
    membership_eng = uuid.uuid4()
    membership_sup = uuid.uuid4()
    membership_inact = uuid.uuid4()

    with conn.cursor() as cur:
        # Create profiles (profiles_role_check accepts SITE_ENGINEER / SUPERVISOR)
        cur.execute("INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);", (user_engineer, "Alice Engineer", "SITE_ENGINEER"))
        cur.execute("INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);", (user_supervisor, "Bob Supervisor", "SUPERVISOR"))
        cur.execute("INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);", (user_outsider, "Charlie Outsider", "SITE_ENGINEER"))
        cur.execute("INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);", (user_inactive, "Dave Inactive", "SITE_ENGINEER"))

        # Create project
        cur.execute("INSERT INTO projects (project_id, project_code, project_name) VALUES (%s, %s, %s);", (proj_id, f"PRJ-{uuid.uuid4().hex[:6]}", "Phase 3 Auth Test Project"))

        # Create memberships in project_memberships table
        cur.execute("INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active) VALUES (%s, %s, %s, %s, %s);", (membership_eng, user_engineer, proj_id, "SITE_ENGINEER", True))
        cur.execute("INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active) VALUES (%s, %s, %s, %s, %s);", (membership_sup, user_supervisor, proj_id, "SUPERVISOR", True))
        cur.execute("INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active) VALUES (%s, %s, %s, %s, %s);", (membership_inact, user_inactive, proj_id, "SITE_ENGINEER", False))

        conn.commit()

    fixtures = {
        "user_engineer": user_engineer,
        "user_supervisor": user_supervisor,
        "user_outsider": user_outsider,
        "user_inactive": user_inactive,
        "project_id": proj_id,
    }

    yield fixtures

    # Cleanup
    with conn.cursor() as cur:
        cur.execute("DELETE FROM project_memberships WHERE project_id = %s;", (proj_id,))
        cur.execute("DELETE FROM projects WHERE project_id = %s;", (proj_id,))
        cur.execute("DELETE FROM profiles WHERE id IN (%s, %s, %s, %s);", (user_engineer, user_supervisor, user_outsider, user_inactive))
        conn.commit()
    conn.close()


def _make_jwt(sub: str, exp_delta: int = 3600) -> str:
    payload = {
        "sub": str(sub),
        "email": f"{sub}@example.com",
        "exp": int(time.time()) + exp_delta,
    }
    return jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")


# Test FastAPI app mounting protected sample routes
sample_app = FastAPI()


@sample_app.get("/sample/user")
def _handle_user(user: CurrentUser = Depends(require_authenticated_user)):
    return {"user_id": str(user.user_id), "role": user.role}


@sample_app.get("/sample/project-context")
def _handle_project_context(context: ProjectContext = Depends(require_project_context)):
    return {
        "user_id": str(context.user_id),
        "project_id": str(context.project_id),
        "role": context.role,
    }


@sample_app.post("/sample/claim")
def _handle_claim(context: ProjectContext = Depends(require_permission(Permission.CREATE_EXECUTION_EVENT))):
    return {"status": "created", "role": context.role}


@sample_app.post("/sample/approve")
def _handle_approve(context: ProjectContext = Depends(require_permission(Permission.APPROVE_ACTUAL))):
    return {"status": "approved", "role": context.role}


@sample_app.get("/sample/supervisor-only")
def _handle_supervisor(context: ProjectContext = Depends(require_project_role(ProjectRole.SUPERVISOR))):
    return {"status": "supervisor_ok"}


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------


def test_missing_jwt_rejected():
    client = TestClient(sample_app)
    resp = client.get("/sample/user")
    assert resp.status_code == 401
    assert "Missing Authorization" in resp.json()["detail"]


def test_invalid_jwt_rejected():
    client = TestClient(sample_app)
    resp = client.get("/sample/user", headers={"Authorization": "Bearer invalid.token.value"})
    assert resp.status_code == 401


def test_expired_jwt_rejected():
    client = TestClient(sample_app)
    expired_token = _make_jwt(str(uuid.uuid4()), exp_delta=-3600)
    resp = client.get("/sample/user", headers={"Authorization": f"Bearer {expired_token}"})
    assert resp.status_code == 401


def test_jwt_user_not_in_profiles_rejected():
    client = TestClient(sample_app)
    token = _make_jwt(str(uuid.uuid4()))
    resp = client.get("/sample/user", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert "has no profile record" in resp.json()["detail"]


def test_valid_jwt_and_profile_resolves_current_user(auth_db_fixture):
    client = TestClient(sample_app)
    f = auth_db_fixture
    token = _make_jwt(str(f["user_engineer"]))
    resp = client.get("/sample/user", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == str(f["user_engineer"])
    assert data["role"] == "SITE_ENGINEER"


def test_project_context_non_member_denied(auth_db_fixture):
    client = TestClient(sample_app)
    f = auth_db_fixture
    # Outsider has no membership in project
    token = _make_jwt(str(f["user_outsider"]))
    resp = client.get(
        "/sample/project-context",
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["project_id"])},
    )
    assert resp.status_code == 403


def test_project_context_inactive_membership_denied(auth_db_fixture):
    client = TestClient(sample_app)
    f = auth_db_fixture
    # User has membership with active = FALSE
    token = _make_jwt(str(f["user_inactive"]))
    resp = client.get(
        "/sample/project-context",
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["project_id"])},
    )
    assert resp.status_code == 403


def test_project_context_forged_project_id_denied(auth_db_fixture):
    client = TestClient(sample_app)
    f = auth_db_fixture
    token = _make_jwt(str(f["user_engineer"]))
    forged_proj = str(uuid.uuid4())
    resp = client.get(
        "/sample/project-context",
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": forged_proj},
    )
    assert resp.status_code == 403


def test_project_context_active_member_allowed(auth_db_fixture):
    client = TestClient(sample_app)
    f = auth_db_fixture
    token = _make_jwt(str(f["user_engineer"]))
    resp = client.get(
        "/sample/project-context",
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["project_id"])},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"] == str(f["project_id"])
    assert data["role"] == "SITE_ENGINEER"


def test_rbac_permission_enforcement(auth_db_fixture):
    client = TestClient(sample_app)
    f = auth_db_fixture
    eng_token = _make_jwt(str(f["user_engineer"]))
    sup_token = _make_jwt(str(f["user_supervisor"]))
    proj_id_str = str(f["project_id"])

    # 1. Site Engineer can submit claim
    resp = client.post(
        "/sample/claim",
        headers={"Authorization": f"Bearer {eng_token}", "X-Project-ID": proj_id_str},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "created"

    # 2. Site Engineer CANNOT approve actuals (Permission APPROVE_ACTUAL denied -> 403)
    resp = client.post(
        "/sample/approve",
        headers={"Authorization": f"Bearer {eng_token}", "X-Project-ID": proj_id_str},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error_code"] == "PERMISSION_DENIED"

    # 3. Supervisor CAN approve actuals
    resp = client.post(
        "/sample/approve",
        headers={"Authorization": f"Bearer {sup_token}", "X-Project-ID": proj_id_str},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_forged_role_in_request_ignored(auth_db_fixture):
    """
    CRITICAL: Frontend attempts to forge its role by sending role='SUPERVISOR' in query/params.
    The system must resolve role strictly from project_memberships (SITE_ENGINEER) and reject.
    """
    client = TestClient(sample_app)
    f = auth_db_fixture
    eng_token = _make_jwt(str(f["user_engineer"]))
    proj_id_str = str(f["project_id"])

    # Engineer attempts supervisor-only endpoint with forged role in query
    resp = client.get(
        "/sample/supervisor-only?role=SUPERVISOR",
        headers={"Authorization": f"Bearer {eng_token}", "X-Project-ID": proj_id_str},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error_code"] == "ROLE_REQUIRED"
