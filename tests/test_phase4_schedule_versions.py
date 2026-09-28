"""
Phase 4B Tests — Schedule Version Domain, Transactional Activation, Supersession,
Historical Preservation, Metadata Comparison, and Cross-Project Isolation.
"""

import time
import uuid
import jwt
import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from backend.auth.dependencies import get_current_user
from backend.context.project import ProjectContext, require_project_context
from backend.context.schedule import ScheduleContext, require_schedule_context
from backend.main import app
from backend.shared.db import get_connection

TEST_JWT_SECRET = "phase3-super-secret-key-12345678901234567890"

CSV_V1 = (
    "L6 Task ID,L5 Activity ID,Activity,Discipline,Unit,Planned Qty,Baseline Start,Baseline Finish,L1,L2\n"
    "ACT-101,WBS-1,Site Mobilization,Civil,m3,100.0,2026-06-01,2026-06-15,Zone 1,Area A\n"
    "ACT-102,WBS-1,Bulk Excavation,Civil,m3,250.0,2026-06-16,2026-06-30,Zone 1,Area A\n"
)

CSV_V2 = (
    "L6 Task ID,L5 Activity ID,Activity,Discipline,Unit,Planned Qty,Baseline Start,Baseline Finish,L1,L2,Predecessor Activity ID,Relationship Type\n"
    "ACT-101,WBS-1,Site Mobilization,Civil,m3,100.0,2026-06-01,2026-06-15,Zone 1,Area A,,\n"
    "ACT-102,WBS-1,Bulk Excavation,Civil,m3,250.0,2026-06-16,2026-06-30,Zone 1,Area A,ACT-101,FS\n"
    "ACT-103,WBS-2,Retaining Wall Pour,Structural,m3,80.0,2026-07-01,2026-07-20,Zone 1,Area B,ACT-102,FS\n"
)


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
def phase4_schedule_fixture():
    conn = get_connection()
    conn.autocommit = False

    user_pm = uuid.uuid4()
    user_planner = uuid.uuid4()
    user_site_eng = uuid.uuid4()
    user_other = uuid.uuid4()

    proj_a = uuid.uuid4()
    proj_b = uuid.uuid4()

    with conn.cursor() as cur:
        # Profiles
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_pm, "Alpha PM", "SUPERVISOR"),
        )
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_planner, "Alpha Planner", "SUPERVISOR"),
        )
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_site_eng, "Alpha Site Eng", "SITE_ENGINEER"),
        )
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_other, "Beta PM", "SUPERVISOR"),
        )

        # Projects
        cur.execute(
            "INSERT INTO projects (project_id, project_code, project_name, status) VALUES (%s, %s, %s, 'ACTIVE');",
            (proj_a, f"PRJ-A-{uuid.uuid4().hex[:6]}", "Project Alpha Schedules"),
        )
        cur.execute(
            "INSERT INTO projects (project_id, project_code, project_name, status) VALUES (%s, %s, %s, 'ACTIVE');",
            (proj_b, f"PRJ-B-{uuid.uuid4().hex[:6]}", "Project Beta Schedules"),
        )

        # Project Memberships
        cur.execute(
            """
            INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active, status)
            VALUES (%s, %s, %s, 'PROJECT_MANAGER', TRUE, 'ACTIVE');
            """,
            (uuid.uuid4(), user_pm, proj_a),
        )
        cur.execute(
            """
            INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active, status)
            VALUES (%s, %s, %s, 'PLANNER', TRUE, 'ACTIVE');
            """,
            (uuid.uuid4(), user_planner, proj_a),
        )
        cur.execute(
            """
            INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active, status)
            VALUES (%s, %s, %s, 'SITE_ENGINEER', TRUE, 'ACTIVE');
            """,
            (uuid.uuid4(), user_site_eng, proj_a),
        )
        cur.execute(
            """
            INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active, status)
            VALUES (%s, %s, %s, 'PROJECT_MANAGER', TRUE, 'ACTIVE');
            """,
            (uuid.uuid4(), user_other, proj_b),
        )

        conn.commit()

    fixtures = {
        "user_pm": user_pm,
        "user_planner": user_planner,
        "user_site_eng": user_site_eng,
        "user_other": user_other,
        "proj_a": proj_a,
        "proj_b": proj_b,
    }

    yield fixtures

    # Cleanup
    with conn.cursor() as cur:
        cur.execute("DELETE FROM audit_logs WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM approved_actuals WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM execution_events WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM schedule_activities WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM schedule_dependencies WHERE schedule_id IN (SELECT schedule_id FROM schedules WHERE project_id IN (%s, %s));", (proj_a, proj_b))
        cur.execute("DELETE FROM schedules WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM project_memberships WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM projects WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM profiles WHERE id IN (%s, %s, %s, %s);", (user_pm, user_planner, user_site_eng, user_other))
        conn.commit()
    conn.close()


def test_create_schedule_version_under_project(phase4_schedule_fixture):
    """Verify schedule version is created under explicit project_id with text ID, provenance, and audit."""
    f = phase4_schedule_fixture
    token = _make_jwt(str(f["user_planner"]))
    client = TestClient(app)

    payload = {
        "version_code": "V1",
        "csv_content": CSV_V1,
        "data_date": "2026-06-01",
        "source_format": "csv",
        "version_metadata": {"author": "Chief Planner", "baseline_status": "APPROVED"},
        "activate_immediately": True,
    }

    resp = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert isinstance(data["schedule_id"], str)
    assert data["project_id"] == str(f["proj_a"])
    assert data["version_code"] == "V1"
    assert data["active"] is True
    assert data["activity_count"] == 2
    assert data["source_hash"] is not None

    # Verify activities have project_id in DB
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) as count FROM schedule_activities WHERE schedule_id = %s AND project_id = %s;",
                (data["schedule_id"], f["proj_a"]),
            )
            assert cur.fetchone()["count"] == 2

            # Verify audit log
            cur.execute(
                "SELECT action, entity_type FROM audit_logs WHERE project_id = %s AND action = 'SCHEDULE_VERSION_CREATED';",
                (f["proj_a"],),
            )
            audit_row = cur.fetchone()
            assert audit_row is not None


def test_schedule_version_duplicate_code_conflict(phase4_schedule_fixture):
    """Creating duplicate version_code under the same project returns HTTP 409."""
    f = phase4_schedule_fixture
    token = _make_jwt(str(f["user_planner"]))
    client = TestClient(app)

    payload = {
        "version_code": "V1",
        "csv_content": CSV_V1,
        "activate_immediately": True,
    }

    # First creation
    resp1 = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp1.status_code == 201

    # Second creation with identical version_code in same project
    resp2 = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp2.status_code == 409
    assert "already exists" in resp2.json()["detail"].lower()


def test_transactional_activation_and_single_active_invariant(phase4_schedule_fixture):
    """
    Project has V1 active. Create V2 (inactive).
    Activate V2:
    - V2 becomes active
    - V1 becomes inactive
    - Invariant: Exactly one active version exists in the project
    - Both V1 and V2 remain queryable
    """
    f = phase4_schedule_fixture
    token = _make_jwt(str(f["user_planner"]))
    client = TestClient(app)

    # 1. Create V1 (active)
    resp_v1 = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules",
        json={"version_code": "V1", "csv_content": CSV_V1, "activate_immediately": True},
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_v1.status_code == 201
    v1_id = resp_v1.json()["schedule_id"]

    # 2. Create V2 (inactive)
    resp_v2 = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules",
        json={"version_code": "V2", "csv_content": CSV_V2, "activate_immediately": False},
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_v2.status_code == 201
    v2_id = resp_v2.json()["schedule_id"]
    assert resp_v2.json()["active"] is False

    # 3. Activate V2
    resp_act = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules/{v2_id}/activate",
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_act.status_code == 200
    act_data = resp_act.json()
    assert act_data["schedule_id"] == v2_id
    assert act_data["active"] is True
    assert act_data["previous_active_schedule_id"] == v1_id

    # 4. Verify Single-Active Invariant in DB
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT schedule_id, active FROM schedules WHERE project_id = %s;",
                (f["proj_a"],),
            )
            rows = cur.fetchall()
            active_rows = [r for r in rows if r["active"] is True]
            assert len(active_rows) == 1
            assert active_rows[0]["schedule_id"] == v2_id

            v1_row = [r for r in rows if r["schedule_id"] == v1_id][0]
            assert v1_row["active"] is False  # V1 is historical

    # 5. Historical version V1 remains queryable
    resp_get_v1 = client.get(
        f"/api/v1/projects/{f['proj_a']}/schedules/{v1_id}",
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_get_v1.status_code == 200
    assert resp_get_v1.json()["schedule_id"] == v1_id
    assert resp_get_v1.json()["active"] is False


def test_historical_claims_remain_attached_to_original_version(phase4_schedule_fixture):
    """
    CRITICAL INVARIANT:
    Claim C1 is created under Schedule V1.
    Approved actual A1 is created under Schedule V1.
    Schedule V2 is created and activated.
    Claim C1 and Actual A1 MUST remain attached to V1.
    Zero automatic migration to V2; zero automatic rematching.
    """
    f = phase4_schedule_fixture
    token = _make_jwt(str(f["user_planner"]))
    client = TestClient(app)

    # 1. Create and activate V1
    resp_v1 = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules",
        json={"version_code": "V1", "csv_content": CSV_V1, "activate_immediately": True},
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_v1.status_code == 201
    v1_id = resp_v1.json()["schedule_id"]

    # 2. Attach an execution event (Claim C1) and an approved actual to Schedule V1
    claim_id = f"CLAIM-{uuid.uuid4().hex[:6]}"
    actual_id = f"ACTUAL-{uuid.uuid4().hex[:6]}"
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO execution_events (
                    event_id, schedule_id, project_id, event_date, raw_claim_text, input_channel, event_type
                ) VALUES (%s, %s, %s, '2026-06-10', 'Site Mobilization 50 pct complete', 'PORTAL', 'FIELD_PROGRESS');
                """,
                (claim_id, v1_id, f["proj_a"]),
            )
            dec_id = f"DEC-{uuid.uuid4().hex[:6]}"
            cur.execute(
                """
                INSERT INTO approved_actuals (
                    actual_id, schedule_id, activity_id, project_id, decision_id, event_id, actual_pct_complete
                ) VALUES (%s, %s, 'ACT-101', %s, %s, %s, 50.0);
                """,
                (actual_id, v1_id, f["proj_a"], dec_id, claim_id),
            )
            conn.commit()

    # 3. Create and activate Schedule V2
    resp_v2 = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules",
        json={"version_code": "V2", "csv_content": CSV_V2, "activate_immediately": False},
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_v2.status_code == 201
    v2_id = resp_v2.json()["schedule_id"]

    # Activate V2
    resp_act = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules/{v2_id}/activate",
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_act.status_code == 200

    # 4. Invariant Assertion: Claim and Actual remain anchored to V1
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT schedule_id FROM execution_events WHERE event_id = %s;", (claim_id,))
            c_row = cur.fetchone()
            assert c_row["schedule_id"] == v1_id  # UNCHANGED! Not moved to V2!

            cur.execute("SELECT schedule_id FROM approved_actuals WHERE actual_id = %s;", (actual_id,))
            a_row = cur.fetchone()
            assert a_row["schedule_id"] == v1_id  # UNCHANGED! Not moved to V2!


def test_supersede_schedule_version(phase4_schedule_fixture):
    """Verify explicit supersession records provenance link while keeping older version queryable."""
    f = phase4_schedule_fixture
    token = _make_jwt(str(f["user_planner"]))
    client = TestClient(app)

    # Create V1
    resp_v1 = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules",
        json={"version_code": "V1", "csv_content": CSV_V1, "activate_immediately": True},
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    v1_id = resp_v1.json()["schedule_id"]

    # Create V2
    resp_v2 = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules",
        json={"version_code": "V2", "csv_content": CSV_V2, "activate_immediately": False},
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    v2_id = resp_v2.json()["schedule_id"]

    # Supersede V1 by V2
    resp_sup = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules/{v2_id}/supersede",
        json={"supersedes_schedule_id": v1_id},
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_sup.status_code == 200
    assert resp_sup.json()["supersedes_schedule_id"] == v1_id

    # Verify audit log recorded
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT action FROM audit_logs WHERE project_id = %s AND action = 'SCHEDULE_VERSION_SUPERSEDED';",
                (f["proj_a"],),
            )
            assert cur.fetchone() is not None


def test_compare_schedule_metadata(phase4_schedule_fixture):
    """Verify metadata comparison endpoint returns counts, date bounds, and version differences."""
    f = phase4_schedule_fixture
    token = _make_jwt(str(f["user_planner"]))
    client = TestClient(app)

    resp_v1 = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules",
        json={"version_code": "V1", "csv_content": CSV_V1, "activate_immediately": True},
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    v1_id = resp_v1.json()["schedule_id"]

    resp_v2 = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules",
        json={"version_code": "V2", "csv_content": CSV_V2, "activate_immediately": False},
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    v2_id = resp_v2.json()["schedule_id"]

    resp_cmp = client.get(
        f"/api/v1/projects/{f['proj_a']}/schedules/compare?schedule_a={v1_id}&schedule_b={v2_id}",
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_cmp.status_code == 200
    data = resp_cmp.json()
    assert data["schedule_a"]["version_code"] == "V1"
    assert data["schedule_b"]["version_code"] == "V2"
    assert data["schedule_a"]["activity_count"] == 2
    assert data["schedule_b"]["activity_count"] == 3
    assert data["differences"]["activity_count_diff"] == 1
    assert data["differences"]["version_code_changed"] is True


def test_cross_project_schedule_isolation(phase4_schedule_fixture):
    """Caller from Project B cannot access or modify schedules in Project A."""
    f = phase4_schedule_fixture
    token_b = _make_jwt(str(f["user_other"]))  # Member of Project B
    token_a = _make_jwt(str(f["user_planner"]))  # Member of Project A
    client = TestClient(app)

    # Create schedule in Project A
    resp_v1 = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules",
        json={"version_code": "V1", "csv_content": CSV_V1, "activate_immediately": True},
        headers={"Authorization": f"Bearer {token_a}", "X-Project-ID": str(f["proj_a"])},
    )
    v1_id = resp_v1.json()["schedule_id"]

    # User B tries to view Schedule in Project A
    resp_denied = client.get(
        f"/api/v1/projects/{f['proj_a']}/schedules/{v1_id}",
        headers={"Authorization": f"Bearer {token_b}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_denied.status_code == 403
    assert resp_denied.json()["detail"]["error_code"] == "PROJECT_ACCESS_DENIED"


def test_missing_schedule_context_rejected(phase4_schedule_fixture):
    """
    CRITICAL INVARIANT:
    Missing schedule context on a schedule-sensitive endpoint must return 400 INVALID_SCHEDULE_CONTEXT.
    No implicit fallback to latest or active schedule is permitted.
    """
    f = phase4_schedule_fixture
    token = _make_jwt(str(f["user_planner"]))

    test_app = FastAPI()

    @test_app.get("/test/schedule-sensitive")
    def _sensitive_endpoint(sched_ctx: ScheduleContext = Depends(require_schedule_context)):
        return {"schedule_id": sched_ctx.schedule_id}

    client = TestClient(test_app)

    # Calling with project context but omitting X-Schedule-ID
    resp = client.get(
        "/test/schedule-sensitive",
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"]["error_code"] == "INVALID_SCHEDULE_CONTEXT"


def test_schedule_version_rbac(phase4_schedule_fixture):
    """SITE_ENGINEER cannot create schedule version (needs MANAGE_SCHEDULE), but can view it."""
    f = phase4_schedule_fixture
    token_eng = _make_jwt(str(f["user_site_eng"]))
    token_planner = _make_jwt(str(f["user_planner"]))
    client = TestClient(app)

    # 1. SITE_ENGINEER attempts to create version -> 403 PERMISSION_DENIED
    resp_create = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules",
        json={"version_code": "V-ENG", "csv_content": CSV_V1},
        headers={"Authorization": f"Bearer {token_eng}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_create.status_code == 403
    assert resp_create.json()["detail"]["error_code"] == "PERMISSION_DENIED"

    # 2. PLANNER creates version
    resp_ok = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules",
        json={"version_code": "V-PLN", "csv_content": CSV_V1},
        headers={"Authorization": f"Bearer {token_planner}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_ok.status_code == 201

    # 3. SITE_ENGINEER can view versions (VIEW_SCHEDULE is allowed)
    resp_list = client.get(
        f"/api/v1/projects/{f['proj_a']}/schedules",
        headers={"Authorization": f"Bearer {token_eng}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_list.status_code == 200
    assert len(resp_list.json()) >= 1
