"""
Phase 10 Focused Test Suite — Quality / ITP / Hold Points / Evidence / Status / Eligibility / RBAC / Audit & Isolation.
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
def phase10_fixture():
    conn = get_connection()
    conn.autocommit = False

    user_pm_a = uuid.uuid4()
    user_pm_b = uuid.uuid4()
    user_engineer_a = uuid.uuid4()
    user_inspector_a = uuid.uuid4()
    user_auditor_a = uuid.uuid4()

    proj_a = uuid.uuid4()
    proj_b = uuid.uuid4()

    contractor_a = uuid.uuid4()
    wp_a = uuid.uuid4()

    suffix = uuid.uuid4().hex[:6]
    code_a = f"PRJ-A-{suffix}"
    code_b = f"PRJ-B-{suffix}"
    sched_a = f"SCHED_A_{suffix}"
    sched_b = f"SCHED_B_{suffix}"
    con_code = f"CON-A_{suffix}"
    wp_code = f"WP-A_{suffix}"

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
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_engineer_a, "Engineer Alpha", "SITE_ENGINEER"),
        )
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_inspector_a, "Inspector Alpha", "SUPERVISOR"),
        )
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_auditor_a, "Auditor Alpha", "SUPERVISOR"),
        )

        # Projects
        cur.execute(
            "INSERT INTO projects (project_id, project_name, project_code, created_by) VALUES (%s, %s, %s, %s);",
            (proj_a, "Project Alpha", code_a, user_pm_a),
        )
        cur.execute(
            "INSERT INTO projects (project_id, project_name, project_code, created_by) VALUES (%s, %s, %s, %s);",
            (proj_b, "Project Beta", code_b, user_pm_b),
        )

        # Memberships
        cur.execute(
            "INSERT INTO project_memberships (project_id, user_id, assigned_role) VALUES (%s, %s, %s);",
            (proj_a, user_pm_a, "PROJECT_MANAGER"),
        )
        cur.execute(
            "INSERT INTO project_memberships (project_id, user_id, assigned_role) VALUES (%s, %s, %s);",
            (proj_b, user_pm_b, "PROJECT_MANAGER"),
        )
        cur.execute(
            "INSERT INTO project_memberships (project_id, user_id, assigned_role) VALUES (%s, %s, %s);",
            (proj_a, user_engineer_a, "SITE_ENGINEER"),
        )
        cur.execute(
            "INSERT INTO project_memberships (project_id, user_id, assigned_role) VALUES (%s, %s, %s);",
            (proj_a, user_inspector_a, "SUPERVISOR"),
        )
        cur.execute(
            "INSERT INTO project_memberships (project_id, user_id, assigned_role) VALUES (%s, %s, %s);",
            (proj_a, user_auditor_a, "SUPERVISOR"),
        )

        # Schedule Baseline for Project A
        cur.execute(
            "INSERT INTO schedules (schedule_id, project_id, project_name, version_code) VALUES (%s, %s, %s, %s);",
            (sched_a, proj_a, "Schedule Alpha Base", "v1.0"),
        )

        # Schedule Baseline for Project B
        cur.execute(
            "INSERT INTO schedules (schedule_id, project_id, project_name, version_code) VALUES (%s, %s, %s, %s);",
            (sched_b, proj_b, "Schedule Beta Base", "v1.0"),
        )

        # Activities for Project A
        cur.execute(
            """INSERT INTO schedule_activities 
               (activity_id, schedule_id, project_id, activity_name, discipline, location, planned_start, planned_finish, quality_gate_required) 
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);""",
            ("ACT-101", sched_a, proj_a, "Concrete Foundation Pour", "CIVIL", "Zone A", "2026-09-01", "2026-09-15", True),
        )
        cur.execute(
            """INSERT INTO schedule_activities 
               (activity_id, schedule_id, project_id, activity_name, discipline, location, planned_start, planned_finish, quality_gate_required) 
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);""",
            ("ACT-102", sched_a, proj_a, "Rebar Assembly", "CIVIL", "Zone A", "2026-09-16", "2026-09-30", False),
        )

        # Activity for Project B
        cur.execute(
            """INSERT INTO schedule_activities 
               (activity_id, schedule_id, project_id, activity_name, discipline, location, planned_start, planned_finish, quality_gate_required) 
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);""",
            ("ACT-201", sched_b, proj_b, "Beta Structural Steel", "CIVIL", "Zone B", "2026-09-01", "2026-09-15", True),
        )

        # Contractor & Work Package for Project A
        cur.execute(
            """INSERT INTO contractors (contractor_id, project_id, company_name, contractor_code)
               VALUES (%s, %s, %s, %s);""",
            (contractor_a, proj_a, "Alpha Quality Builders", con_code),
        )
        cur.execute(
            """INSERT INTO work_packages (work_package_id, project_id, contractor_id, package_name, package_code)
               VALUES (%s, %s, %s, %s, %s);""",
            (wp_a, proj_a, contractor_a, "Civil Foundation Package", wp_code),
        )

    conn.commit()

    token_pm_a = _make_jwt(str(user_pm_a))
    token_pm_b = _make_jwt(str(user_pm_b))
    token_engineer_a = _make_jwt(str(user_engineer_a))
    token_inspector_a = _make_jwt(str(user_inspector_a))
    token_auditor_a = _make_jwt(str(user_auditor_a))

    yield {
        "user_pm_a": user_pm_a,
        "user_pm_b": user_pm_b,
        "user_engineer_a": user_engineer_a,
        "user_inspector_a": user_inspector_a,
        "user_auditor_a": user_auditor_a,
        "proj_a": proj_a,
        "proj_b": proj_b,
        "contractor_a": contractor_a,
        "wp_a": wp_a,
        "headers_pm_a": {"Authorization": f"Bearer {token_pm_a}"},
        "headers_pm_b": {"Authorization": f"Bearer {token_pm_b}"},
        "headers_engineer_a": {"Authorization": f"Bearer {token_engineer_a}"},
        "headers_inspector_a": {"Authorization": f"Bearer {token_inspector_a}"},
        "headers_auditor_a": {"Authorization": f"Bearer {token_auditor_a}"},
    }

    # Cleanup
    with conn.cursor() as cur:
        # Scoped to this test's own projects: an unscoped DELETE wipes every project's quality data.
        cur.execute(
            "DELETE FROM quality_evidence WHERE quality_gate_id IN "
            "(SELECT quality_gate_id FROM quality_gates WHERE project_id IN (%s, %s));",
            (proj_a, proj_b),
        )
        cur.execute("DELETE FROM quality_gates WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM itps WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM audit_logs WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM work_packages WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM contractors WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM schedule_activities WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM schedules WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM project_memberships WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM projects WHERE project_id IN (%s, %s);", (proj_a, proj_b))
        cur.execute("DELETE FROM profiles WHERE id IN (%s, %s, %s, %s, %s);", (user_pm_a, user_pm_b, user_engineer_a, user_inspector_a, user_auditor_a))
    conn.commit()
    conn.close()


def test_1_itp_creation(phase10_fixture):
    client = TestClient(app)
    proj_a = phase10_fixture["proj_a"]
    headers = phase10_fixture["headers_pm_a"]

    payload = {
        "title": "Foundation ITP Plan",
        "description": "Inspection and test plan for foundation pouring",
        "discipline": "CIVIL",
        "responsible_party": "Quality Manager",
        "contractor_id": str(phase10_fixture["contractor_a"]),
        "work_package_id": str(phase10_fixture["wp_a"]),
        "status": "ACTIVE",
    }
    res = client.post(f"/api/v1/projects/{proj_a}/itps", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["title"] == "Foundation ITP Plan"
    assert data["project_id"] == str(proj_a)
    assert "itp_id" in data


def test_2_itp_retrieval(phase10_fixture):
    client = TestClient(app)
    proj_a = phase10_fixture["proj_a"]
    headers = phase10_fixture["headers_pm_a"]

    # Create ITP
    itp_res = client.post(
        f"/api/v1/projects/{proj_a}/itps",
        json={"title": "Piping Inspection Plan", "discipline": "PIPING"},
        headers=headers,
    )
    itp_id = itp_res.json()["itp_id"]

    # Get by ID
    res = client.get(f"/api/v1/projects/{proj_a}/itps/{itp_id}", headers=headers)
    assert res.status_code == 200
    assert res.json()["title"] == "Piping Inspection Plan"

    # List ITPs
    list_res = client.get(f"/api/v1/projects/{proj_a}/itps", headers=headers)
    assert list_res.status_code == 200
    assert len(list_res.json()) >= 1


def test_3_checkpoint_creation(phase10_fixture):
    client = TestClient(app)
    proj_a = phase10_fixture["proj_a"]
    headers = phase10_fixture["headers_pm_a"]

    payload = {
        "gate_name": "Concrete Slump Test",
        "gate_type": "TEST",
        "checkpoint_category": "QUALITY_CHECK",
        "activity_id": "ACT-101",
        "required": True,
        "remarks": "Must achieve 100mm slump",
    }
    res = client.post(f"/api/v1/projects/{proj_a}/quality-gates", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["gate_name"] == "Concrete Slump Test"
    assert data["status"] == "PENDING"
    assert "quality_gate_id" in data


def test_4_activity_quality_relationship(phase10_fixture):
    client = TestClient(app)
    proj_a = phase10_fixture["proj_a"]
    headers = phase10_fixture["headers_pm_a"]

    client.post(
        f"/api/v1/projects/{proj_a}/quality-gates",
        json={"gate_name": "Rebar Spacing Verification", "gate_type": "INSPECTION", "activity_id": "ACT-101"},
        headers=headers,
    )

    res = client.get(f"/api/v1/projects/{proj_a}/activities/ACT-101/quality-gates", headers=headers)
    assert res.status_code == 200
    assert len(res.json()) >= 1


def test_5_evidence_submission(phase10_fixture):
    client = TestClient(app)
    proj_a = phase10_fixture["proj_a"]
    headers = phase10_fixture["headers_pm_a"]

    gate_res = client.post(
        f"/api/v1/projects/{proj_a}/quality-gates",
        json={"gate_name": "NDT Weld Inspection", "gate_type": "NDT", "activity_id": "ACT-101"},
        headers=headers,
    )
    gate_id = gate_res.json()["quality_gate_id"]

    evidence_payload = {
        "evidence_type": "TEST_REPORT",
        "result": "PASS",
        "inspector_name": "John Doe, QC Lead",
        "inspection_date": "2026-09-29",
        "evidence_hash": "sha256-abc123xyz",
    }
    res = client.post(
        f"/api/v1/projects/{proj_a}/quality-gates/{gate_id}/evidence",
        json=evidence_payload,
        headers=headers,
    )
    assert res.status_code == 201, res.text
    assert res.json()["result"] == "PASS"

    # Verify gate transitioned to SUBMITTED
    gate_info = client.get(f"/api/v1/projects/{proj_a}/activities/ACT-101/quality-gates", headers=headers).json()
    matched_gate = next(g for g in gate_info if g["quality_gate_id"] == gate_id)
    assert matched_gate["status"] == "SUBMITTED"


def test_6_evidence_retrieval(phase10_fixture):
    from backend.repositories.quality_repo import QualityEvidenceRepository

    proj_a = phase10_fixture["proj_a"]
    client = TestClient(app)
    headers = phase10_fixture["headers_pm_a"]

    gate_res = client.post(
        f"/api/v1/projects/{proj_a}/quality-gates",
        json={"gate_name": "Material Cert Check", "gate_type": "MATERIAL_CERTIFICATE", "activity_id": "ACT-101"},
        headers=headers,
    )
    gate_id = gate_res.json()["quality_gate_id"]

    client.post(
        f"/api/v1/projects/{proj_a}/quality-gates/{gate_id}/evidence",
        json={"evidence_type": "MATERIAL_CERTIFICATE", "result": "PASS"},
        headers=headers,
    )

    records = QualityEvidenceRepository.list_by_gate(phase10_fixture["user_pm_a"], uuid.UUID(gate_id))
    assert len(records) == 1
    assert records[0]["evidence_type"] == "MATERIAL_CERTIFICATE"


def test_7_pending_quality_blocks_eligibility(phase10_fixture):
    client = TestClient(app)
    proj_a = phase10_fixture["proj_a"]
    headers = phase10_fixture["headers_pm_a"]

    client.post(
        f"/api/v1/projects/{proj_a}/quality-gates",
        json={"gate_name": "Pending Soil Test", "gate_type": "TEST", "activity_id": "ACT-101"},
        headers=headers,
    )

    res = client.get(f"/api/v1/projects/{proj_a}/activities/ACT-101/quality-status", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["is_eligible"] is False
    assert data["status"] in ("PENDING", "SUBMITTED")
    assert data["blocking_reason"] is not None


def test_8_submitted_quality_blocks_eligibility(phase10_fixture):
    client = TestClient(app)
    proj_a = phase10_fixture["proj_a"]
    headers = phase10_fixture["headers_pm_a"]

    gate_res = client.post(
        f"/api/v1/projects/{proj_a}/quality-gates",
        json={"gate_name": "Submitted Pour Card", "gate_type": "POUR_CARD", "activity_id": "ACT-101"},
        headers=headers,
    )
    gate_id = gate_res.json()["quality_gate_id"]

    client.post(
        f"/api/v1/projects/{proj_a}/quality-gates/{gate_id}/evidence",
        json={"evidence_type": "POUR_CARD", "result": "PASS"},
        headers=headers,
    )

    res = client.get(f"/api/v1/projects/{proj_a}/activities/ACT-101/quality-status", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["is_eligible"] is False
    assert data["status"] == "SUBMITTED"


def test_9_failed_quality_blocks_eligibility(phase10_fixture):
    client = TestClient(app)
    proj_a = phase10_fixture["proj_a"]
    headers = phase10_fixture["headers_pm_a"]

    gate_res = client.post(
        f"/api/v1/projects/{proj_a}/quality-gates",
        json={"gate_name": "Cube Strength Test", "gate_type": "TEST", "activity_id": "ACT-101"},
        headers=headers,
    )
    gate_id = gate_res.json()["quality_gate_id"]

    client.post(
        f"/api/v1/projects/{proj_a}/quality-gates/{gate_id}/fail",
        json={"remarks": "Cube strength below specification"},
        headers=headers,
    )

    res = client.get(f"/api/v1/projects/{proj_a}/activities/ACT-101/quality-status", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["is_eligible"] is False
    assert data["status"] == "FAILED"
    assert "failed quality gate" in data["blocking_reason"]


def test_10_passed_quality_permits_eligibility(phase10_fixture):
    client = TestClient(app)
    proj_a = phase10_fixture["proj_a"]
    headers = phase10_fixture["headers_pm_a"]

    gate_res = client.post(
        f"/api/v1/projects/{proj_a}/quality-gates",
        json={"gate_name": "Final Clearance", "gate_type": "CLEARANCE", "activity_id": "ACT-101"},
        headers=headers,
    )
    gate_id = gate_res.json()["quality_gate_id"]

    client.post(
        f"/api/v1/projects/{proj_a}/quality-gates/{gate_id}/pass",
        json={"remarks": "All specs satisfied"},
        headers=headers,
    )

    res = client.get(f"/api/v1/projects/{proj_a}/activities/ACT-101/quality-status", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["is_eligible"] is True
    assert data["status"] == "PASSED"
    assert data["blocking_reason"] is None


def test_11_waiver_follows_configured_rules(phase10_fixture):
    client = TestClient(app)
    proj_a = phase10_fixture["proj_a"]
    headers = phase10_fixture["headers_pm_a"]

    gate_res = client.post(
        f"/api/v1/projects/{proj_a}/quality-gates",
        json={"gate_name": "Optional Survey Check", "gate_type": "INSPECTION", "activity_id": "ACT-101"},
        headers=headers,
    )
    gate_id = gate_res.json()["quality_gate_id"]

    # Attempt waive without reason -> 422
    invalid_waive = client.post(
        f"/api/v1/projects/{proj_a}/quality-gates/{gate_id}/waive",
        json={},
        headers=headers,
    )
    assert invalid_waive.status_code in (422, 400)

    # Valid waive -> 200
    valid_waive = client.post(
        f"/api/v1/projects/{proj_a}/quality-gates/{gate_id}/waive",
        json={"waiver_reason": "Approved design modification waiver by Client PM"},
        headers=headers,
    )
    assert valid_waive.status_code == 200
    assert valid_waive.json()["status"] == "WAIVED"

    # Status shows eligible
    res = client.get(f"/api/v1/projects/{proj_a}/activities/ACT-101/quality-status", headers=headers)
    assert res.json()["is_eligible"] is True


def test_12_hold_point_blocks_progression(phase10_fixture):
    client = TestClient(app)
    proj_a = phase10_fixture["proj_a"]
    headers = phase10_fixture["headers_pm_a"]

    # Create HOLD point
    gate_res = client.post(
        f"/api/v1/projects/{proj_a}/quality-gates",
        json={
            "gate_name": "Client Hold Point before Pouring",
            "gate_type": "CLIENT_APPROVAL",
            "checkpoint_category": "HOLD",
            "activity_id": "ACT-101",
        },
        headers=headers,
    )
    assert gate_res.status_code == 201

    res = client.get(f"/api/v1/projects/{proj_a}/activities/ACT-101/quality-status", headers=headers)
    data = res.json()
    assert data["has_active_hold_point"] is True
    assert data["is_eligible"] is False


def test_13_satisfied_hold_point_clears_block(phase10_fixture):
    client = TestClient(app)
    proj_a = phase10_fixture["proj_a"]
    headers = phase10_fixture["headers_pm_a"]

    gate_res = client.post(
        f"/api/v1/projects/{proj_a}/quality-gates",
        json={
            "gate_name": "Client Hold Point before Pouring",
            "gate_type": "CLIENT_APPROVAL",
            "checkpoint_category": "HOLD",
            "activity_id": "ACT-101",
        },
        headers=headers,
    )
    gate_id = gate_res.json()["quality_gate_id"]

    # INTENTIONALLY CHANGED (evidence rule): a required HOLD point is released on a PASS inspection record, not on assertion.
    refused = client.post(f"/api/v1/projects/{proj_a}/quality-gates/{gate_id}/pass", headers=headers)
    assert refused.status_code == 409 and refused.json()["detail"]["error_code"] == "EVIDENCE_REQUIRED"
    still = client.get(f"/api/v1/projects/{proj_a}/activities/ACT-101/quality-status", headers=headers).json()
    assert still["has_active_hold_point"] is True

    ev = client.post(
        f"/api/v1/projects/{proj_a}/quality-gates/{gate_id}/evidence",
        json={"evidence_type": "CLIENT_APPROVAL", "result": "PASS", "inspector_name": "Client QA"},
        headers=headers,
    )
    assert ev.status_code == 201
    assert client.post(f"/api/v1/projects/{proj_a}/quality-gates/{gate_id}/pass", headers=headers).status_code == 200

    res = client.get(f"/api/v1/projects/{proj_a}/activities/ACT-101/quality-status", headers=headers)
    data = res.json()
    assert data["has_active_hold_point"] is False
    assert data["is_eligible"] is True


def test_14_multiple_gates(phase10_fixture):
    client = TestClient(app)
    proj_a = phase10_fixture["proj_a"]
    headers = phase10_fixture["headers_pm_a"]

    g1 = client.post(
        f"/api/v1/projects/{proj_a}/quality-gates",
        json={"gate_name": "Gate 1", "gate_type": "TEST", "activity_id": "ACT-101"},
        headers=headers,
    ).json()["quality_gate_id"]

    g2 = client.post(
        f"/api/v1/projects/{proj_a}/quality-gates",
        json={"gate_name": "Gate 2", "gate_type": "INSPECTION", "activity_id": "ACT-101"},
        headers=headers,
    ).json()["quality_gate_id"]

    # Pass G1, G2 remains pending
    client.post(f"/api/v1/projects/{proj_a}/quality-gates/{g1}/pass", headers=headers)

    res = client.get(f"/api/v1/projects/{proj_a}/activities/ACT-101/quality-status", headers=headers)
    data = res.json()
    assert data["total_gates"] == 2
    assert data["passed_gates"] == 1
    assert data["pending_gates"] == 1
    assert data["is_eligible"] is False


def test_15_invalid_evidence_reference_rejected(phase10_fixture):
    client = TestClient(app)
    proj_a = phase10_fixture["proj_a"]
    headers = phase10_fixture["headers_pm_a"]
    fake_id = str(uuid.uuid4())

    res = client.post(
        f"/api/v1/projects/{proj_a}/quality-gates/{fake_id}/evidence",
        json={"evidence_type": "PHOTO"},
        headers=headers,
    )
    assert res.status_code == 404


def test_16_invalid_activity_reference_rejected(phase10_fixture):
    client = TestClient(app)
    proj_a = phase10_fixture["proj_a"]
    headers = phase10_fixture["headers_pm_a"]

    res = client.post(
        f"/api/v1/projects/{proj_a}/quality-gates",
        json={"gate_name": "Bad Activity Gate", "gate_type": "TEST", "activity_id": "NON_EXISTENT_ACT"},
        headers=headers,
    )
    assert res.status_code == 404


def test_17_cross_project_reference_rejected(phase10_fixture):
    client = TestClient(app)
    proj_a = phase10_fixture["proj_a"]
    proj_b = phase10_fixture["proj_b"]
    headers_a = phase10_fixture["headers_pm_a"]

    # Create ITP in Project B
    itp_b = client.post(
        f"/api/v1/projects/{proj_b}/itps",
        json={"title": "Project B ITP"},
        headers=phase10_fixture["headers_pm_b"],
    ).json()["itp_id"]

    # Attempt to link Project A quality gate to Project B ITP -> 404/403
    res = client.post(
        f"/api/v1/projects/{proj_a}/quality-gates",
        json={"gate_name": "Cross Gate", "gate_type": "TEST", "itp_id": itp_b},
        headers=headers_a,
    )
    assert res.status_code in (404, 403)


def test_18_project_isolation_quality(phase10_fixture):
    client = TestClient(app)
    proj_a = phase10_fixture["proj_a"]
    proj_b = phase10_fixture["proj_b"]
    headers_b = phase10_fixture["headers_pm_b"]

    itp_a = client.post(
        f"/api/v1/projects/{proj_a}/itps",
        json={"title": "Project A Private ITP"},
        headers=phase10_fixture["headers_pm_a"],
    ).json()["itp_id"]

    # Project B user attempts to access Project A ITP
    res = client.get(f"/api/v1/projects/{proj_b}/itps/{itp_a}", headers=headers_b)
    assert res.status_code in (404, 403)


def test_19_unauthorized_user_cannot_approve_quality(phase10_fixture):
    client = TestClient(app)
    proj_a = phase10_fixture["proj_a"]
    headers_pm = phase10_fixture["headers_pm_a"]
    headers_engineer = phase10_fixture["headers_engineer_a"]

    gate_id = client.post(
        f"/api/v1/projects/{proj_a}/quality-gates",
        json={"gate_name": "Gate for Approval Test", "gate_type": "TEST", "activity_id": "ACT-101"},
        headers=headers_pm,
    ).json()["quality_gate_id"]

    # Site Engineer attempts to PASS quality gate -> 403
    res = client.post(f"/api/v1/projects/{proj_a}/quality-gates/{gate_id}/pass", headers=headers_engineer)
    assert res.status_code == 403


def test_20_unauthorized_user_cannot_waive_quality(phase10_fixture):
    client = TestClient(app)
    proj_a = phase10_fixture["proj_a"]
    headers_pm = phase10_fixture["headers_pm_a"]
    headers_engineer = phase10_fixture["headers_engineer_a"]

    gate_id = client.post(
        f"/api/v1/projects/{proj_a}/quality-gates",
        json={"gate_name": "Gate for Waiver Test", "gate_type": "TEST", "activity_id": "ACT-101"},
        headers=headers_pm,
    ).json()["quality_gate_id"]

    # Site Engineer attempts to WAIVE quality gate -> 403 (Requires WAIVE_QUALITY, held by PM/SUPERVISOR)
    res = client.post(
        f"/api/v1/projects/{proj_a}/quality-gates/{gate_id}/waive",
        json={"waiver_reason": "Engineer attempts waiver"},
        headers=headers_engineer,
    )
    assert res.status_code == 403


def test_21_audit_event_created_for_transitions(phase10_fixture):
    client = TestClient(app)
    proj_a = phase10_fixture["proj_a"]
    headers = phase10_fixture["headers_pm_a"]

    gate_id = client.post(
        f"/api/v1/projects/{proj_a}/quality-gates",
        json={"gate_name": "Audit Tracked Gate", "gate_type": "TEST", "activity_id": "ACT-101"},
        headers=headers,
    ).json()["quality_gate_id"]

    client.post(f"/api/v1/projects/{proj_a}/quality-gates/{gate_id}/pass", headers=headers)

    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT action, entity_type FROM audit_logs WHERE project_id = %s AND entity_id = %s;",
            (str(proj_a), str(gate_id)),
        )
        rows = cur.fetchall()
        actions = [r["action"] for r in rows]
        assert "CREATE_QUALITY_GATE" in actions
        assert "PASS_QUALITY_GATE" in actions
    conn.close()


def test_22_transaction_rollback_and_deterministic_status(phase10_fixture):
    from backend.services.quality_service import QualityService
    from backend.context.project import ProjectContext
    from backend.auth.models import CurrentUser

    proj_a = phase10_fixture["proj_a"]
    user_pm_a = phase10_fixture["user_pm_a"]

    user_obj = CurrentUser(id=str(user_pm_a), email="pm@example.com")
    ctx = ProjectContext(
        user=user_obj,
        project_id=proj_a,
        role="PROJECT_MANAGER",
        membership_id=uuid.uuid4(),
        project_name="Project Alpha",
    )

    # Deterministic status read 1
    s1 = QualityService.get_quality_status(ctx, "ACT-102")
    assert s1["is_eligible"] is True
    assert s1["status"] == "NOT_REQUIRED"

    # Deterministic status read 2
    s2 = QualityService.get_quality_status(ctx, "ACT-102")
    assert s1 == s2
