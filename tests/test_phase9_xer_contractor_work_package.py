"""
Phase 9 Focused Tests — XER Ingestion Pipeline, Contractor Domain, Work Package Domain, Activity Attribution, and Project Isolation.
"""

import time
import uuid
import jwt
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.shared.db import get_connection

TEST_JWT_SECRET = "phase3-super-secret-key-12345678901234567890"

VALID_XER_CONTENT = """ERMHDR\t18.8\t2026-09-01\tP6 Export
%T\tPROJECT
%F\tproj_id\tproj_short_name
%R\t1\tPRJ-XER-1
%T\tPROJWBS
%F\twbs_id\tproj_id\twbs_short_name\twbs_name
%R\t10\t1\tWBS-CIVIL\tCivil Works
%R\t20\t1\tWBS-PIPING\tPiping Works
%T\tTASK
%F\ttask_id\ttask_code\ttask_name\twbs_id\tstatus_code\ttarget_start_date\ttarget_end_date\ttarget_qty_cnt\ttotal_float_hr_cnt
%R\t101\tACT-X1\tSite Mobilization\t10\tTK_NotStart\t2026-09-01 08:00\t2026-09-15 17:00\t100.0\t0.0
%R\t102\tACT-X2\tExcavation\t10\tTK_Active\t2026-09-16 08:00\t2026-09-30 17:00\t200.0\t16.0
%R\t103\tACT-X3\tPipe Laying\t20\tTK_NotStart\t2026-10-01 08:00\t2026-10-15 17:00\t300.0\t40.0
%T\tTASKPRED
%F\ttask_pred_id\ttask_id\tpred_task_id\tpred_type\tlag_hr_cnt
%R\t1\t102\t101\tPR_FS\t0.0
%R\t2\t103\t102\tPR_FS\t8.0
%E"""

INVALID_XER_HEADER = "NOT_A_VALID_XER_HEADER\nSome random content"

INVALID_XER_DUPLICATE_TASKS = """ERMHDR\t18.8\t2026-09-01\tP6 Export
%T\tTASK
%F\ttask_id\ttask_code\ttask_name
%R\t101\tACT-DUP\tTask 1
%R\t102\tACT-DUP\tTask 2
%E"""


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
def phase9_fixture():
    conn = get_connection()
    conn.autocommit = False

    user_pm_a = uuid.uuid4()
    user_pm_b = uuid.uuid4()
    proj_a = uuid.uuid4()
    proj_b = uuid.uuid4()

    with conn.cursor() as cur:
        # Profiles
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_pm_a, "PM Project Alpha", "SUPERVISOR"),
        )
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_pm_b, "PM Project Beta", "SUPERVISOR"),
        )

        # Projects
        cur.execute(
            "INSERT INTO projects (project_id, project_code, project_name, status) VALUES (%s, %s, %s, 'ACTIVE');",
            (proj_a, f"PRJ-A-{uuid.uuid4().hex[:6]}", "Project Alpha Phase 9"),
        )
        cur.execute(
            "INSERT INTO projects (project_id, project_code, project_name, status) VALUES (%s, %s, %s, 'ACTIVE');",
            (proj_b, f"PRJ-B-{uuid.uuid4().hex[:6]}", "Project Beta Phase 9"),
        )

        # Project Memberships
        cur.execute(
            """
            INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active, status)
            VALUES (%s, %s, %s, 'PROJECT_MANAGER', TRUE, 'ACTIVE');
            """,
            (uuid.uuid4(), user_pm_a, proj_a),
        )
        cur.execute(
            """
            INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active, status)
            VALUES (%s, %s, %s, 'PROJECT_MANAGER', TRUE, 'ACTIVE');
            """,
            (uuid.uuid4(), user_pm_b, proj_b),
        )

        conn.commit()

    fixtures = {
        "user_pm_a": user_pm_a,
        "user_pm_b": user_pm_b,
        "proj_a": proj_a,
        "proj_b": proj_b,
    }

    yield fixtures

    # Cleanup
    with conn.cursor() as cur:
        cur.execute("DELETE FROM audit_logs WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM schedule_activities WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM schedule_dependencies WHERE schedule_id IN (SELECT schedule_id FROM schedules WHERE project_id IN (%s, %s));", (proj_a, proj_b))
        cur.execute("DELETE FROM schedules WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM work_packages WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM contractors WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM project_memberships WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM projects WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM profiles WHERE id IN (%s, %s);", (user_pm_a, user_pm_b))
        conn.commit()
    conn.close()


# 1. XER parser/import succeeds with valid fixture
def test_1_xer_import_succeeds(phase9_fixture):
    f = phase9_fixture
    token = _make_jwt(str(f["user_pm_a"]))
    client = TestClient(app)

    payload = {
        "version_code": "XER-V1",
        "xer_content": VALID_XER_CONTENT,
        "activate_immediately": True,
    }
    resp = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules/xer",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["version_code"] == "XER-V1"
    assert data["activity_count"] == 3
    assert data["dependency_count"] == 2


# 2. Invalid XER is rejected
def test_2_invalid_xer_rejected(phase9_fixture):
    f = phase9_fixture
    token = _make_jwt(str(f["user_pm_a"]))
    client = TestClient(app)

    payload = {
        "version_code": "XER-BAD",
        "xer_content": INVALID_XER_HEADER,
        "activate_immediately": False,
    }
    resp = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules/xer",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp.status_code in (422, 400)


# 3. Import is transactional (failed import leaves no partially imported schedule)
def test_3_import_is_transactional(phase9_fixture):
    f = phase9_fixture
    token = _make_jwt(str(f["user_pm_a"]))
    client = TestClient(app)

    payload = {
        "version_code": "XER-DUP-FAIL",
        "xer_content": INVALID_XER_DUPLICATE_TASKS,
    }
    resp = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules/xer",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp.status_code in (422, 400)

    # Verify no orphaned schedule or activities in DB
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM schedules WHERE version_code = 'XER-DUP-FAIL';")
            row = cur.fetchone()
            assert list(row.values())[0] == 0


# 4. Schedule version is created correctly
def test_4_schedule_version_created_correctly(phase9_fixture):
    f = phase9_fixture
    token = _make_jwt(str(f["user_pm_a"]))
    client = TestClient(app)

    payload = {
        "version_code": "XER-V2",
        "xer_content": VALID_XER_CONTENT,
        "activate_immediately": True,
    }
    resp = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules/xer",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp.status_code == 201
    data = resp.json()

    # Check schedule attributes
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT active, source_format FROM schedules WHERE schedule_id = %s;", (data["schedule_id"],))
            row = cur.fetchone()
            assert row["active"] is True
            assert row["source_format"] == "xer"


# 5. Existing schedule version is not silently overwritten
def test_5_existing_schedule_version_not_overwritten(phase9_fixture):
    f = phase9_fixture
    token = _make_jwt(str(f["user_pm_a"]))
    client = TestClient(app)

    payload = {
        "version_code": "XER-DUP-CODE",
        "xer_content": VALID_XER_CONTENT,
    }
    resp1 = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules/xer",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp1.status_code == 201

    # Second attempt with same version_code must be rejected
    resp2 = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules/xer",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp2.status_code == 409


# 6. Project isolation works for schedules
def test_6_project_isolation_schedules(phase9_fixture):
    f = phase9_fixture
    token_a = _make_jwt(str(f["user_pm_a"]))
    token_b = _make_jwt(str(f["user_pm_b"]))
    client = TestClient(app)

    resp1 = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules/xer",
        json={"version_code": "XER-A", "xer_content": VALID_XER_CONTENT},
        headers={"Authorization": f"Bearer {token_a}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp1.status_code == 201
    sch_a_id = resp1.json()["schedule_id"]

    # User B cannot view Project A's schedule
    resp_denied = client.get(
        f"/api/v1/projects/{f['proj_a']}/schedules/{sch_a_id}",
        headers={"Authorization": f"Bearer {token_b}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_denied.status_code == 403


# 7. Contractor creation works
def test_7_contractor_creation(phase9_fixture):
    f = phase9_fixture
    token = _make_jwt(str(f["user_pm_a"]))
    client = TestClient(app)

    payload = {
        "contractor_code": "CON-001",
        "company_name": "Larsen & Toubro Ltd",
        "type_or_category": "MAIN_CONTRACTOR",
        "contract_reference": "CNT/2026/001",
        "contact_email": "contact@lt.example.com",
    }
    resp = client.post(
        f"/api/v1/projects/{f['proj_a']}/contractors",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["contractor_code"] == "CON-001"
    assert data["company_name"] == "Larsen & Toubro Ltd"
    assert data["project_id"] == str(f["proj_a"])


# 8. Duplicate contractor code within one project is rejected
def test_8_duplicate_contractor_code_rejected(phase9_fixture):
    f = phase9_fixture
    token = _make_jwt(str(f["user_pm_a"]))
    client = TestClient(app)

    payload = {"contractor_code": "CON-DUP", "company_name": "Contractor Alpha"}

    resp1 = client.post(
        f"/api/v1/projects/{f['proj_a']}/contractors",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp1.status_code == 201

    resp2 = client.post(
        f"/api/v1/projects/{f['proj_a']}/contractors",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp2.status_code == 409


# 9. Same contractor code across different projects is allowed
def test_9_same_contractor_code_across_projects_allowed(phase9_fixture):
    f = phase9_fixture
    token_a = _make_jwt(str(f["user_pm_a"]))
    token_b = _make_jwt(str(f["user_pm_b"]))
    client = TestClient(app)

    payload = {"contractor_code": "SHARED-CODE", "company_name": "Shared Infra Corp"}

    resp_a = client.post(
        f"/api/v1/projects/{f['proj_a']}/contractors",
        json=payload,
        headers={"Authorization": f"Bearer {token_a}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_a.status_code == 201

    resp_b = client.post(
        f"/api/v1/projects/{f['proj_b']}/contractors",
        json=payload,
        headers={"Authorization": f"Bearer {token_b}", "X-Project-ID": str(f["proj_b"])},
    )
    assert resp_b.status_code == 201


# 10. Work package creation works
def test_10_work_package_creation(phase9_fixture):
    f = phase9_fixture
    token = _make_jwt(str(f["user_pm_a"]))
    client = TestClient(app)

    payload = {
        "package_code": "WP-CIV-01",
        "package_name": "Earthworks & Foundation",
        "discipline": "CIVIL",
    }
    resp = client.post(
        f"/api/v1/projects/{f['proj_a']}/work-packages",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["package_code"] == "WP-CIV-01"
    assert data["package_name"] == "Earthworks & Foundation"


# 11. Work package contractor relationship works
def test_11_work_package_contractor_relationship(phase9_fixture):
    f = phase9_fixture
    token = _make_jwt(str(f["user_pm_a"]))
    client = TestClient(app)

    # 1. Create contractor
    resp_c = client.post(
        f"/api/v1/projects/{f['proj_a']}/contractors",
        json={"contractor_code": "CON-REL", "company_name": "Rel Subcontractor"},
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    c_id = resp_c.json()["contractor_id"]

    # 2. Create work package linking to contractor
    resp_wp = client.post(
        f"/api/v1/projects/{f['proj_a']}/work-packages",
        json={
            "package_code": "WP-REL-01",
            "package_name": "Piping Installation Package",
            "contractor_id": c_id,
        },
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_wp.status_code == 201
    assert resp_wp.json()["contractor_id"] == c_id


# 12. Activity attribution works
def test_12_activity_attribution(phase9_fixture):
    f = phase9_fixture
    token = _make_jwt(str(f["user_pm_a"]))
    client = TestClient(app)

    # Import schedule
    resp_sch = client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules/xer",
        json={"version_code": "XER-ATTR", "xer_content": VALID_XER_CONTENT, "activate_immediately": True},
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_sch.status_code == 201

    # Create contractor & work package
    resp_c = client.post(
        f"/api/v1/projects/{f['proj_a']}/contractors",
        json={"contractor_code": "CON-ATTR", "company_name": "Attributed Cont Ltd"},
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    c_id = resp_c.json()["contractor_id"]

    resp_wp = client.post(
        f"/api/v1/projects/{f['proj_a']}/work-packages",
        json={"package_code": "WP-ATTR-01", "package_name": "Site Prep Package", "contractor_id": c_id},
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    wp_id = resp_wp.json()["work_package_id"]

    # Update activity attribution for ACT-X1
    resp_attr = client.patch(
        f"/api/v1/projects/{f['proj_a']}/activities/ACT-X1/attribution",
        json={"contractor_id": c_id, "work_package_id": wp_id},
        headers={"Authorization": f"Bearer {token}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_attr.status_code == 200, resp_attr.text
    data = resp_attr.json()
    assert data["contractor_id"] == c_id
    assert data["work_package_id"] == wp_id


# 13. Cross-project contractor access is denied
def test_13_cross_project_contractor_access_denied(phase9_fixture):
    f = phase9_fixture
    token_a = _make_jwt(str(f["user_pm_a"]))
    token_b = _make_jwt(str(f["user_pm_b"]))
    client = TestClient(app)

    # Create contractor in Project A
    resp_c = client.post(
        f"/api/v1/projects/{f['proj_a']}/contractors",
        json={"contractor_code": "CON-CROSS", "company_name": "Alpha Only Cont"},
        headers={"Authorization": f"Bearer {token_a}", "X-Project-ID": str(f["proj_a"])},
    )
    c_id = resp_c.json()["contractor_id"]

    # User B tries to view contractor in Project A -> 403
    resp_denied = client.get(
        f"/api/v1/projects/{f['proj_a']}/contractors/{c_id}",
        headers={"Authorization": f"Bearer {token_b}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_denied.status_code == 403


# 14. Cross-project work-package access is denied
def test_14_cross_project_work_package_access_denied(phase9_fixture):
    f = phase9_fixture
    token_a = _make_jwt(str(f["user_pm_a"]))
    token_b = _make_jwt(str(f["user_pm_b"]))
    client = TestClient(app)

    resp_wp = client.post(
        f"/api/v1/projects/{f['proj_a']}/work-packages",
        json={"package_code": "WP-CROSS", "package_name": "Alpha Only Package"},
        headers={"Authorization": f"Bearer {token_a}", "X-Project-ID": str(f["proj_a"])},
    )
    wp_id = resp_wp.json()["work_package_id"]

    # User B tries to view work package in Project A -> 403
    resp_denied = client.get(
        f"/api/v1/projects/{f['proj_a']}/work-packages/{wp_id}",
        headers={"Authorization": f"Bearer {token_b}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_denied.status_code == 403


# 15. Cross-project activity attribution is denied
def test_15_cross_project_activity_attribution_denied(phase9_fixture):
    f = phase9_fixture
    token_a = _make_jwt(str(f["user_pm_a"]))
    token_b = _make_jwt(str(f["user_pm_b"]))
    client = TestClient(app)

    # Import schedule in Project A
    client.post(
        f"/api/v1/projects/{f['proj_a']}/schedules/xer",
        json={"version_code": "XER-CROSS", "xer_content": VALID_XER_CONTENT, "activate_immediately": True},
        headers={"Authorization": f"Bearer {token_a}", "X-Project-ID": str(f["proj_a"])},
    )

    # User B tries to update attribution on Project A's activity
    resp_denied = client.patch(
        f"/api/v1/projects/{f['proj_a']}/activities/ACT-X1/attribution",
        json={"stage_id": str(uuid.uuid4())},
        headers={"Authorization": f"Bearer {token_b}", "X-Project-ID": str(f["proj_a"])},
    )
    assert resp_denied.status_code == 403
