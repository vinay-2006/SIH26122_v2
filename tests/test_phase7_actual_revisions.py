"""
SETUAI V7 Phase 7 Tests — Actuals Revision & Reconstructible Audit Timeline
Verifies that historical approved actuals are never silently destroyed,
and full lifecycle history is 100% reconstructible.
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
def phase7_revision_fixture():
    conn = get_connection()
    conn.autocommit = False

    user_pm = uuid.uuid4()
    proj_id = uuid.uuid4()
    sched_id = f"SCH-REV-{uuid.uuid4().hex[:6]}"
    act_id = "ACT-REV-001"
    actual_id = f"ACTUAL-REV-{uuid.uuid4().hex[:8]}"
    dec_id = f"DEC-{uuid.uuid4().hex[:8]}"
    ev_id = f"EV-{uuid.uuid4().hex[:8]}"

    with conn.cursor() as cur:
        # Profiles
        cur.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, %s);",
            (user_pm, "Revision PM", "SUPERVISOR"),
        )
        # Project
        proj_code = f"PRJ-REV-{uuid.uuid4().hex[:6]}"
        cur.execute(
            """
            INSERT INTO projects (project_id, project_code, project_name, status, created_by)
            VALUES (%s, %s, %s, 'ACTIVE', %s);
            """,
            (proj_id, proj_code, "Phase 7 Revision Project", user_pm),
        )
        # Memberships: PM -> PROJECT_MANAGER
        cur.execute(
            """
            INSERT INTO project_memberships (membership_id, user_id, project_id, assigned_role, active, status)
            VALUES (%s, %s, %s, 'PROJECT_MANAGER', TRUE, 'ACTIVE');
            """,
            (uuid.uuid4(), user_pm, proj_id),
        )
        # Schedule Version
        cur.execute(
            """
            INSERT INTO schedules (schedule_id, project_name, project_id, version_code, active)
            VALUES (%s, %s, %s, 'V1.0', TRUE);
            """,
            (sched_id, "Phase 7 Revision Project", proj_id),
        )
        # Activity
        cur.execute(
            """
            INSERT INTO schedule_activities (
                activity_id, schedule_id, project_id, activity_name, discipline, location,
                planned_start, planned_finish, planned_quantity, baseline_pct_complete
            ) VALUES (%s, %s, %s, 'Tunnel Lining Segment 4', 'Civil', 'Sector 4', '2026-08-01', '2026-08-20', 500.0, 100.0);
            """,
            (act_id, sched_id, proj_id),
        )
        # Initial Execution Event
        cur.execute(
            """
            INSERT INTO execution_events (
                event_id, schedule_id, project_id, event_date,
                raw_claim_text, input_channel, status
            ) VALUES (%s, %s, %s, '2026-08-20', 'Tunnel lining segment 4 100 percent complete', 'MANUAL', 'APPROVED');
            """,
            (ev_id, sched_id, proj_id),
        )
        # Initial Decision
        cur.execute(
            """
            INSERT INTO planner_decisions (
                decision_id, event_id, selected_activity_id, action,
                approved_pct, approved_qty, planner_id, justification
            ) VALUES (%s, %s, %s, 'APPROVE', 100.0, 500.0, %s, 'Original segment completion approval');
            """,
            (dec_id, ev_id, act_id, user_pm),
        )
        # Initial Approved Actual (Already Authorized for Rework)
        cur.execute(
            """
            INSERT INTO approved_actuals (
                actual_id, decision_id, event_id, schedule_id, activity_id,
                project_id, actual_start, actual_finish, actual_pct_complete,
                actual_quantity, is_reopened, rework_notes
            ) VALUES (%s, %s, %s, %s, %s, %s, '2026-08-01', '2026-08-20', 100.0, 500.0, TRUE, 'Authorized rework for cracking');
            """,
            (actual_id, dec_id, ev_id, sched_id, act_id, proj_id),
        )

    conn.commit()
    conn.close()

    yield {
        "user_pm": user_pm,
        "project_id": proj_id,
        "schedule_id": sched_id,
        "activity_id": act_id,
        "actual_id": actual_id,
    }

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
        cur.execute("DELETE FROM profiles WHERE id = %s;", (user_pm,))
    cleanup_conn.commit()
    cleanup_conn.close()


def test_actual_revision_happy_path(phase7_revision_fixture):
    """
    Submitting an actual revision:
    - Atomically updates approved_actuals with corrected values.
    - Resets is_reopened to False.
    - Transitions canonical execution state to IN_PROGRESS (if pct < 100).
    - Preserves previous actual in audit log.
    """
    client = TestClient(app)
    f = phase7_revision_fixture
    token = _make_jwt(str(f["user_pm"]))

    payload = {
        "actual_start": "2026-08-01",
        "actual_pct_complete": 85.0,
        "actual_quantity": 425.0,
        "revision_notes": "15% of lining spalled off; removed and prepared for re-casting.",
    }

    resp = client.post(
        f"/api/v1/projects/{f['project_id']}/schedules/{f['schedule_id']}/activities/{f['activity_id']}/actuals/revision",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["canonical_state"] == "IN_PROGRESS"
    assert data["workflow_condition"] == "NONE"

    # Prior actual preserved intact
    prev = data["previous_actual"]
    assert float(prev["actual_pct_complete"]) == 100.0
    assert float(prev["actual_quantity"]) == 500.0

    # Current actual updated
    curr = data["current_actual"]
    assert float(curr["actual_pct_complete"]) == 85.0
    assert float(curr["actual_quantity"]) == 425.0
    assert curr["is_reopened"] is False


def test_reconstructible_history_endpoint(phase7_revision_fixture):
    """
    GET /history endpoint reconstructs full timeline:
    - Current approved actual
    - Historical approved actuals (from audit logs)
    - Audit timeline
    """
    client = TestClient(app)
    f = phase7_revision_fixture
    token = _make_jwt(str(f["user_pm"]))

    # 1. Execute revision
    revision_payload = {
        "actual_start": "2026-08-01",
        "actual_pct_complete": 85.0,
        "actual_quantity": 425.0,
        "revision_notes": "First revision post-rework.",
    }
    client.post(
        f"/api/v1/projects/{f['project_id']}/schedules/{f['schedule_id']}/activities/{f['activity_id']}/actuals/revision",
        json=revision_payload,
        headers={"Authorization": f"Bearer {token}"},
    )

    # 2. Query history
    resp = client.get(
        f"/api/v1/projects/{f['project_id']}/schedules/{f['schedule_id']}/activities/{f['activity_id']}/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    history = resp.json()

    assert history["activity_id"] == f["activity_id"]
    assert history["canonical_state"] == "IN_PROGRESS"
    assert history["current_approved_actual"] is not None
    assert float(history["current_approved_actual"]["actual_pct_complete"]) == 85.0

    # Historical record contains original 100% record
    hist_actuals = history["historical_approved_actuals"]
    assert len(hist_actuals) >= 1
    assert float(hist_actuals[0]["actual_pct_complete"]) == 100.0
    assert float(hist_actuals[0]["actual_quantity"]) == 500.0


def test_revision_rejected_when_not_in_rework(phase7_revision_fixture):
    """Cannot approve an actual revision if activity is not currently in rework."""
    client = TestClient(app)
    f = phase7_revision_fixture
    token = _make_jwt(str(f["user_pm"]))

    # First revise successfully (which clears rework flag)
    client.post(
        f"/api/v1/projects/{f['project_id']}/schedules/{f['schedule_id']}/activities/{f['activity_id']}/actuals/revision",
        json={"actual_pct_complete": 85.0, "revision_notes": "First valid revision."},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Attempt second revision immediately without reopening
    resp = client.post(
        f"/api/v1/projects/{f['project_id']}/schedules/{f['schedule_id']}/activities/{f['activity_id']}/actuals/revision",
        json={"actual_pct_complete": 90.0, "revision_notes": "Attempting revision without rework authorization."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text
