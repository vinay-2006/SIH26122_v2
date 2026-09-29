"""
SETUAI V7 Phase 7 Tests — Reopen Workflow (Challenge, RBAC Authorization, and State Invariants)
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
def phase7_reopen_fixture():
    conn = get_connection()
    conn.autocommit = False

    user_pm = uuid.uuid4()
    user_site_eng = uuid.uuid4()
    proj_id = uuid.uuid4()
    sched_id = f"SCH-P7-{uuid.uuid4().hex[:6]}"
    act_comp_id = "ACT-COMP-001"
    act_prog_id = "ACT-PROG-002"
    actual_id = f"ACTUAL-{uuid.uuid4().hex[:8]}"
    dec_id = f"DEC-{uuid.uuid4().hex[:8]}"
    ev_id = f"EV-{uuid.uuid4().hex[:8]}"

    with conn.cursor() as cur:
        # 1. Profiles
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_pm, "Phase 7 PM", "SUPERVISOR"),
        )
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_site_eng, "Phase 7 Site Eng", "SITE_ENGINEER"),
        )

        # 2. Project
        proj_code = f"PRJ-P7-{uuid.uuid4().hex[:6]}"
        cur.execute(
            """
            INSERT INTO projects (project_id, project_code, project_name, status, created_by)
            VALUES (%s, %s, %s, 'ACTIVE', %s);
            """,
            (proj_id, proj_code, "Phase 7 Test Project", user_pm),
        )

        # 3. Memberships: PM -> PROJECT_MANAGER, Site Eng -> SITE_ENGINEER
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

        # 4. Schedule Version
        cur.execute(
            """
            INSERT INTO schedules (schedule_id, project_name, project_id, version_code, active)
            VALUES (%s, %s, %s, 'V1.0', TRUE);
            """,
            (sched_id, "Phase 7 Test Project", proj_id),
        )

        # 5. Schedule Activities: 1 completed, 1 in-progress
        cur.execute(
            """
            INSERT INTO schedule_activities (
                activity_id, schedule_id, project_id, activity_name, discipline, location,
                planned_start, planned_finish, planned_quantity, baseline_pct_complete
            ) VALUES
            (%s, %s, %s, 'Completed Foundation', 'Civil', 'Zone 1', '2026-08-01', '2026-08-15', 100.0, 100.0),
            (%s, %s, %s, 'In-Progress Wall', 'Civil', 'Zone 1', '2026-08-10', '2026-08-25', 50.0, 20.0);
            """,
            (act_comp_id, sched_id, proj_id, act_prog_id, sched_id, proj_id),
        )

        # 6. Initial execution event & approved actual for completed activity
        cur.execute(
            """
            INSERT INTO execution_events (
                event_id, schedule_id, project_id, event_date,
                raw_claim_text, input_channel, status
            ) VALUES (%s, %s, %s, '2026-08-15', 'Foundation complete', 'MANUAL', 'APPROVED');
            """,
            (ev_id, sched_id, proj_id),
        )
        cur.execute(
            """
            INSERT INTO planner_decisions (
                decision_id, event_id, selected_activity_id, action,
                approved_pct, approved_qty, planner_id, justification
            ) VALUES (%s, %s, %s, 'APPROVE', 100.0, 100.0, %s, 'Initial completion approval');
            """,
            (dec_id, ev_id, act_comp_id, user_pm),
        )
        cur.execute(
            """
            INSERT INTO approved_actuals (
                actual_id, decision_id, event_id, schedule_id, activity_id,
                project_id, actual_start, actual_finish, actual_pct_complete,
                actual_quantity, is_reopened
            ) VALUES (%s, %s, %s, %s, %s, %s, '2026-08-01', '2026-08-15', 100.0, 100.0, FALSE);
            """,
            (actual_id, dec_id, ev_id, sched_id, act_comp_id, proj_id),
        )

    conn.commit()
    conn.close()

    yield {
        "user_pm": user_pm,
        "user_site_eng": user_site_eng,
        "project_id": proj_id,
        "schedule_id": sched_id,
        "act_comp_id": act_comp_id,
        "act_prog_id": act_prog_id,
        "actual_id": actual_id,
    }

    # Teardown
    cleanup_conn = get_connection()
    with cleanup_conn.cursor() as cur:
        cur.execute("DELETE FROM audit_logs WHERE project_id = %s;", (proj_id,))
        cur.execute("DELETE FROM approved_actuals WHERE project_id = %s;", (proj_id,))
        cur.execute("DELETE FROM planner_decisions WHERE planner_id = %s;", (user_pm,))
        cur.execute("DELETE FROM execution_events WHERE project_id = %s;", (proj_id,))
        cur.execute("DELETE FROM schedule_activities WHERE project_id = %s;", (proj_id,))
        cur.execute("DELETE FROM schedules WHERE project_id = %s;", (proj_id,))
        cur.execute("DELETE FROM project_memberships WHERE project_id = %s;", (proj_id,))
        cur.execute("DELETE FROM projects WHERE project_id = %s;", (proj_id,))
        cur.execute("DELETE FROM profiles WHERE id IN (%s, %s);", (user_pm, user_site_eng))
    cleanup_conn.commit()
    cleanup_conn.close()


def test_reopen_request_success_site_engineer(phase7_reopen_fixture):
    """Site engineer can challenge completed activity with justification."""
    client = TestClient(app)
    f = phase7_reopen_fixture
    token = _make_jwt(str(f["user_site_eng"]))

    payload = {
        "reason": "QUALITY_FAILURE",
        "justification": "Ultrasound testing revealed honeycombing voids inside the pour.",
        "evidence_event_ids": [],
    }

    resp = client.post(
        f"/api/v1/projects/{f['project_id']}/schedules/{f['schedule_id']}/activities/{f['act_comp_id']}/reopen",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["activity_id"] == f["act_comp_id"]
    assert data["canonical_state"] == "COMPLETED"
    assert data["workflow_condition"] == "REOPEN_REQUESTED"
    assert data["reopen_status"] == "REQUESTED"
    assert "Ultrasound testing" in data["justification"]


def test_reopen_request_rejected_on_in_progress(phase7_reopen_fixture):
    """Cannot reopen an activity that is not legitimately completed."""
    client = TestClient(app)
    f = phase7_reopen_fixture
    token = _make_jwt(str(f["user_site_eng"]))

    payload = {
        "reason": "INCORRECT_COMPLETION",
        "justification": "Challenging in-progress wall activity incorrectly.",
    }

    resp = client.post(
        f"/api/v1/projects/{f['project_id']}/schedules/{f['schedule_id']}/activities/{f['act_prog_id']}/reopen",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text


def test_reopen_request_duplicate_rejected(phase7_reopen_fixture):
    """Duplicate reopen request while one is pending returns 409 Conflict."""
    client = TestClient(app)
    f = phase7_reopen_fixture
    token = _make_jwt(str(f["user_site_eng"]))

    payload = {
        "reason": "QUALITY_FAILURE",
        "justification": "Initial honeycombing challenge justification.",
    }

    # 1. First request
    resp1 = client.post(
        f"/api/v1/projects/{f['project_id']}/schedules/{f['schedule_id']}/activities/{f['act_comp_id']}/reopen",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp1.status_code == 201

    # 2. Second duplicate request
    resp2 = client.post(
        f"/api/v1/projects/{f['project_id']}/schedules/{f['schedule_id']}/activities/{f['act_comp_id']}/reopen",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp2.status_code == 409, resp2.text


def test_reopen_decision_site_engineer_forbidden(phase7_reopen_fixture):
    """Site engineer is forbidden from deciding on a reopen request."""
    client = TestClient(app)
    f = phase7_reopen_fixture
    eng_token = _make_jwt(str(f["user_site_eng"]))

    # 1. Site Eng creates request
    client.post(
        f"/api/v1/projects/{f['project_id']}/schedules/{f['schedule_id']}/activities/{f['act_comp_id']}/reopen",
        json={"reason": "QUALITY_FAILURE", "justification": "Testing honeycombing failure."},
        headers={"Authorization": f"Bearer {eng_token}"},
    )

    # 2. Site Eng tries to approve their own request
    resp = client.post(
        f"/api/v1/projects/{f['project_id']}/schedules/{f['schedule_id']}/activities/{f['act_comp_id']}/reopen/decide",
        json={"decision": "APPROVED", "rework_instructions": "Self-approved rework."},
        headers={"Authorization": f"Bearer {eng_token}"},
    )
    assert resp.status_code == 403, resp.text


def test_reopen_decision_pm_reject(phase7_reopen_fixture):
    """PM rejects reopen request: workflow condition returns to NONE; actual is untouched."""
    client = TestClient(app)
    f = phase7_reopen_fixture
    eng_token = _make_jwt(str(f["user_site_eng"]))
    pm_token = _make_jwt(str(f["user_pm"]))

    # 1. Request
    client.post(
        f"/api/v1/projects/{f['project_id']}/schedules/{f['schedule_id']}/activities/{f['act_comp_id']}/reopen",
        json={"reason": "OTHER", "justification": "Minor surface flaw detected on inspection."},
        headers={"Authorization": f"Bearer {eng_token}"},
    )

    # 2. PM rejects
    resp = client.post(
        f"/api/v1/projects/{f['project_id']}/schedules/{f['schedule_id']}/activities/{f['act_comp_id']}/reopen/decide",
        json={"decision": "REJECTED", "notes": "Flaw is purely cosmetic, structurally sound."},
        headers={"Authorization": f"Bearer {pm_token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["reopen_status"] == "REJECTED"
    assert data["workflow_condition"] == "NONE"
    assert data["canonical_state"] == "COMPLETED"


def test_reopen_decision_pm_approve(phase7_reopen_fixture):
    """PM approves reopen request: workflow condition becomes REWORK_IN_PROGRESS; is_reopened=True."""
    client = TestClient(app)
    f = phase7_reopen_fixture
    eng_token = _make_jwt(str(f["user_site_eng"]))
    pm_token = _make_jwt(str(f["user_pm"]))

    # 1. Request
    client.post(
        f"/api/v1/projects/{f['project_id']}/schedules/{f['schedule_id']}/activities/{f['act_comp_id']}/reopen",
        json={"reason": "QUALITY_FAILURE", "justification": "Core testing failed specifications."},
        headers={"Authorization": f"Bearer {eng_token}"},
    )

    # 2. PM approves
    resp = client.post(
        f"/api/v1/projects/{f['project_id']}/schedules/{f['schedule_id']}/activities/{f['act_comp_id']}/reopen/decide",
        json={"decision": "APPROVED", "rework_instructions": "Chipping gun demolition and re-grout."},
        headers={"Authorization": f"Bearer {pm_token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["reopen_status"] == "APPROVED"
    assert data["workflow_condition"] == "REWORK_IN_PROGRESS"
    assert data["canonical_state"] == "COMPLETED"
    assert "re-grout" in (data.get("rework_instructions") or "")
