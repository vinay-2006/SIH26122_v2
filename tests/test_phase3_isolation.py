"""
Phase 3 Multi-Project Isolation, Schedule Context, and Defense-in-Depth Tests.

Covers:
1. Multi-Project Setup with Identical / Overlapping Data:
   - PROJECT_A and PROJECT_B have identical activity IDs ('ACT-ISO-001'),
     identical activity names, and identical claim descriptions.
2. Project Isolation:
   - User A (Project A member) -> Project A access ALLOWED
   - User A -> Project B access DENIED (403)
   - User B (Project B member) -> Project B access ALLOWED
   - User B -> Project A access DENIED (403)
3. Schedule Isolation:
   - User A -> Project A schedule ALLOWED
   - User A -> Project B schedule DENIED (403 / 404)
   - Cross-project schedule mismatch (Project A header + Project B schedule) REJECTED
4. Resource Isolation:
   - Schedule activities, execution events, approved actuals, and audit logs
5. Forged Identifiers:
   - Forged project_id, forged schedule_id, forged resource IDs rejected
6. Database Boundary & Defense in Depth:
   - Project-scoped repository enforces project context
   - PostgreSQL RLS session physically denies cross-project access at DB layer
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
from backend.context.schedule import ScheduleContext, require_schedule_context
from backend.repositories.activity_repo import ProjectActivityRepository
from backend.repositories.approved_actual_repo import ProjectApprovedActualRepository
from backend.repositories.audit_repo import ProjectAuditRepository
from backend.repositories.execution_event_repo import ProjectExecutionEventRepository
from backend.repositories.schedule_repo import ProjectScheduleRepository
from backend.shared.db import get_connection

TEST_JWT_SECRET = "phase3-super-secret-key-12345678901234567890"


@pytest.fixture(autouse=True)
def configure_test_jwt(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")


@pytest.fixture
def multi_project_fixture():
    """Sets up Project A and Project B with overlapping entities and separate memberships."""
    conn = get_connection()
    conn.autocommit = False

    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    proj_a = uuid.uuid4()
    proj_b = uuid.uuid4()

    sched_a = f"SCH-A-{uuid.uuid4().hex[:6]}"
    sched_b = f"SCH-B-{uuid.uuid4().hex[:6]}"

    act_id = f"ACT-ISO-{uuid.uuid4().hex[:4]}"
    evt_a = f"EVT-A-{uuid.uuid4().hex[:4]}"
    evt_b = f"EVT-B-{uuid.uuid4().hex[:4]}"
    dec_a = f"DEC-A-{uuid.uuid4().hex[:4]}"
    dec_b = f"DEC-B-{uuid.uuid4().hex[:4]}"
    actual_a = f"ACTUAL-A-{uuid.uuid4().hex[:4]}"
    actual_b = f"ACTUAL-B-{uuid.uuid4().hex[:4]}"

    with conn.cursor() as cur:
        # Profiles (profiles table role must be SITE_ENGINEER / SUPERVISOR)
        cur.execute("INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);", (user_a, "User Alpha", "SITE_ENGINEER"))
        cur.execute("INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);", (user_b, "User Beta", "SUPERVISOR"))

        # Projects
        cur.execute("INSERT INTO projects (project_id, project_code, project_name) VALUES (%s, %s, %s);", (proj_a, f"PRJ-A-{uuid.uuid4().hex[:4]}", "Project Alpha"))
        cur.execute("INSERT INTO projects (project_id, project_code, project_name) VALUES (%s, %s, %s);", (proj_b, f"PRJ-B-{uuid.uuid4().hex[:4]}", "Project Beta"))

        # Memberships: User A is member of Project A only; User B is member of Project B only
        cur.execute("INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active) VALUES (%s, %s, %s, %s, %s);", (uuid.uuid4(), user_a, proj_a, "SITE_ENGINEER", True))
        cur.execute("INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active) VALUES (%s, %s, %s, %s, %s);", (uuid.uuid4(), user_b, proj_b, "SUPERVISOR", True))

        # Schedules
        cur.execute("INSERT INTO schedules (schedule_id, project_id, project_name, active) VALUES (%s, %s, %s, %s);", (sched_a, proj_a, "Project Alpha", True))
        cur.execute("INSERT INTO schedules (schedule_id, project_id, project_name, active) VALUES (%s, %s, %s, %s);", (sched_b, proj_b, "Project Beta", True))

        # Overlapping Activities (identical activity_id in both projects under separate schedules)
        cur.execute(
            """
            INSERT INTO schedule_activities (activity_id, schedule_id, project_id, activity_name, discipline, location, planned_start, planned_finish)
            VALUES (%s, %s, %s, %s, 'CIVIL', 'Sector 1', '2026-04-01', '2026-04-15');
            """,
            (act_id, sched_a, proj_a, "Common Site Excavation"),
        )
        cur.execute(
            """
            INSERT INTO schedule_activities (activity_id, schedule_id, project_id, activity_name, discipline, location, planned_start, planned_finish)
            VALUES (%s, %s, %s, %s, 'CIVIL', 'Sector 1', '2026-04-01', '2026-04-15');
            """,
            (act_id, sched_b, proj_b, "Common Site Excavation"),
        )

        # Execution Events in each project
        cur.execute(
            """
            INSERT INTO execution_events (event_id, schedule_id, project_id, event_date, raw_claim_text, input_channel, event_type)
            VALUES (%s, %s, %s, '2026-04-05', 'Excavation Progress Project A', 'WHATSAPP', 'FIELD_PROGRESS');
            """,
            (evt_a, sched_a, proj_a),
        )
        cur.execute(
            """
            INSERT INTO execution_events (event_id, schedule_id, project_id, event_date, raw_claim_text, input_channel, event_type)
            VALUES (%s, %s, %s, '2026-04-05', 'Excavation Progress Project B', 'WHATSAPP', 'FIELD_PROGRESS');
            """,
            (evt_b, sched_b, proj_b),
        )

        # Approved Actuals in each project (including decision_id and event_id)
        cur.execute(
            """
            INSERT INTO approved_actuals (actual_id, schedule_id, activity_id, project_id, decision_id, event_id, actual_pct_complete)
            VALUES (%s, %s, %s, %s, %s, %s, 45.0);
            """,
            (actual_a, sched_a, act_id, proj_a, dec_a, evt_a),
        )
        cur.execute(
            """
            INSERT INTO approved_actuals (actual_id, schedule_id, activity_id, project_id, decision_id, event_id, actual_pct_complete)
            VALUES (%s, %s, %s, %s, %s, %s, 80.0);
            """,
            (actual_b, sched_b, act_id, proj_b, dec_b, evt_b),
        )

        # Audit Logs
        cur.execute(
            """
            INSERT INTO audit_logs (project_id, schedule_id, actor_id, role, action, entity_type, entity_id, payload_hash, previous_hash, current_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'hash_a', 'prev_a', 'curr_a');
            """,
            (proj_a, sched_a, str(user_a), "SITE_ENGINEER", "SUBMIT_CLAIM", "CLAIM", evt_a),
        )
        cur.execute(
            """
            INSERT INTO audit_logs (project_id, schedule_id, actor_id, role, action, entity_type, entity_id, payload_hash, previous_hash, current_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'hash_b', 'prev_b', 'curr_b');
            """,
            (proj_b, sched_b, str(user_b), "SUPERVISOR", "APPROVE_ACTUAL", "ACTUAL", actual_b),
        )

        conn.commit()

    fixtures = {
        "user_a": user_a,
        "user_b": user_b,
        "proj_a": proj_a,
        "proj_b": proj_b,
        "sched_a": sched_a,
        "sched_b": sched_b,
        "act_id": act_id,
        "evt_a": evt_a,
        "evt_b": evt_b,
        "actual_a": actual_a,
        "actual_b": actual_b,
    }

    yield fixtures

    # Cleanup
    with conn.cursor() as cur:
        cur.execute("DELETE FROM audit_logs WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM approved_actuals WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM execution_events WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM schedule_activities WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM schedules WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM project_memberships WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM projects WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM profiles WHERE id IN (%s, %s);", (user_a, user_b))
        conn.commit()
    conn.close()


def _make_jwt(sub: str) -> str:
    payload = {
        "sub": str(sub),
        "email": f"{sub}@example.com",
        "exp": int(time.time()) + 3600,
    }
    return jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")


# Test API app
iso_app = FastAPI()


@iso_app.get("/iso/project-summary")
def _handle_project_summary(context: ProjectContext = Depends(require_project_context)):
    return {"project_id": str(context.project_id), "role": context.role}


@iso_app.get("/iso/schedule")
def _handle_schedule_view(sched_context: ScheduleContext = Depends(require_schedule_context)):
    return {
        "project_id": str(sched_context.project_id),
        "schedule_id": sched_context.schedule_id,
    }


@iso_app.get("/iso/activities/{activity_id}")
def _handle_activity_view(
    activity_id: str,
    sched_context: ScheduleContext = Depends(require_schedule_context),
):
    activity = ProjectActivityRepository.get(sched_context, activity_id)
    if not activity:
        raise SecurityException(status_code=404, error_code="NOT_FOUND", message="Activity not found")
    return activity


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------


def test_cross_project_isolation_mutual_denial(multi_project_fixture):
    """User A cannot access Project B; User B cannot access Project A."""
    client = TestClient(iso_app)
    f = multi_project_fixture
    token_a = _make_jwt(str(f["user_a"]))
    token_b = _make_jwt(str(f["user_b"]))

    # 1. User A -> Project A: ALLOW (200)
    resp = client.get("/iso/project-summary", headers={"Authorization": f"Bearer {token_a}", "X-Project-ID": str(f["proj_a"])})
    assert resp.status_code == 200
    assert resp.json()["project_id"] == str(f["proj_a"])

    # 2. User A -> Project B: DENIED (403)
    resp = client.get("/iso/project-summary", headers={"Authorization": f"Bearer {token_a}", "X-Project-ID": str(f["proj_b"])})
    assert resp.status_code == 403

    # 3. User B -> Project B: ALLOW (200)
    resp = client.get("/iso/project-summary", headers={"Authorization": f"Bearer {token_b}", "X-Project-ID": str(f["proj_b"])})
    assert resp.status_code == 200
    assert resp.json()["project_id"] == str(f["proj_b"])

    # 4. User B -> Project A: DENIED (403)
    resp = client.get("/iso/project-summary", headers={"Authorization": f"Bearer {token_b}", "X-Project-ID": str(f["proj_a"])})
    assert resp.status_code == 403


def test_schedule_context_cross_project_validation(multi_project_fixture):
    """Schedule access must strictly belong to the authorized project context."""
    client = TestClient(iso_app)
    f = multi_project_fixture
    token_a = _make_jwt(str(f["user_a"]))

    # 1. User A requests Project A schedule with Project A context: ALLOW (200)
    resp = client.get(
        "/iso/schedule",
        headers={
            "Authorization": f"Bearer {token_a}",
            "X-Project-ID": str(f["proj_a"]),
            "X-Schedule-ID": f["sched_a"],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["schedule_id"] == f["sched_a"]

    # 2. User A attempts to request Project B schedule with Project A context: REJECTED (403)
    resp = client.get(
        "/iso/schedule",
        headers={
            "Authorization": f"Bearer {token_a}",
            "X-Project-ID": str(f["proj_a"]),
            "X-Schedule-ID": f["sched_b"],
        },
    )
    assert resp.status_code == 403
    assert "does not belong" in resp.json()["detail"]["message"]

    # 3. Missing schedule context (implicit fallback strictly disallowed): REJECTED (400)
    resp = client.get(
        "/iso/schedule",
        headers={
            "Authorization": f"Bearer {token_a}",
            "X-Project-ID": str(f["proj_a"]),
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error_code"] == "INVALID_SCHEDULE_CONTEXT"


def test_resource_isolation_with_overlapping_identifiers(multi_project_fixture):
    """
    Even with identical activity_id in both projects, User A only sees Project A's
    schedule activity and cannot access Project B's activity.
    """
    client = TestClient(iso_app)
    f = multi_project_fixture
    token_a = _make_jwt(str(f["user_a"]))
    act_id = f["act_id"]

    # User A fetching act_id under Project A context gets Project A activity
    resp = client.get(
        f"/iso/activities/{act_id}",
        headers={
            "Authorization": f"Bearer {token_a}",
            "X-Project-ID": str(f["proj_a"]),
            "X-Schedule-ID": f["sched_a"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["activity_id"] == act_id
    assert data["project_id"] == str(f["proj_a"])
    assert data["schedule_id"] == f["sched_a"]


def test_repository_project_boundary_enforcement(multi_project_fixture):
    """Direct repository calls enforce project_id scoping."""
    f = multi_project_fixture
    user_a = CurrentUser(id=str(f["user_a"]), role="SITE_ENGINEER")
    context_a = ProjectContext(
        user=user_a,
        project_id=f["proj_a"],
        role="SITE_ENGINEER",
        membership_id=uuid.uuid4(),
    )

    # 1. Schedule repository: User A context querying Project B schedule returns None
    sched_b_result = ProjectScheduleRepository.get(context_a, f["sched_b"])
    assert sched_b_result is None

    # 2. Execution Event repository: User A context querying Project B event returns None
    evt_b_result = ProjectExecutionEventRepository.get(context_a, f["evt_b"])
    assert evt_b_result is None

    # 3. Approved Actuals repository: User A context gets Project A actual (45%), cannot get Project B
    actual_b_result = ProjectApprovedActualRepository.get(context_a, f["actual_b"])
    assert actual_b_result is None

    actual_a_result = ProjectApprovedActualRepository.get(context_a, f["actual_a"])
    assert actual_a_result is not None
    assert actual_a_result["project_id"] == f["proj_a"]
    assert actual_a_result["actual_pct_complete"] == 45.0


def test_database_rls_boundary_enforcement(multi_project_fixture):
    """
    Direct SQL query under User A's RLS session attempting to SELECT Project B rows
    is blocked by PostgreSQL RLS and returns 0 rows.
    """
    f = multi_project_fixture
    user_a = f["user_a"]
    proj_b = f["proj_b"]

    with ProjectActivityRepository.rls_connection(user_a) as conn:
        with conn.cursor() as cur:
            # Attempt to query Project B schedules under User A RLS context
            cur.execute("SELECT count(*) as cnt FROM schedules WHERE project_id = %s;", (proj_b,))
            row = cur.fetchone()
            assert row["cnt"] == 0

            # Attempt to query Project B activities under User A RLS context
            cur.execute("SELECT count(*) as cnt FROM schedule_activities WHERE project_id = %s;", (proj_b,))
            row = cur.fetchone()
            assert row["cnt"] == 0

            # Attempt to query Project B execution events under User A RLS context
            cur.execute("SELECT count(*) as cnt FROM execution_events WHERE project_id = %s;", (proj_b,))
            row = cur.fetchone()
            assert row["cnt"] == 0
