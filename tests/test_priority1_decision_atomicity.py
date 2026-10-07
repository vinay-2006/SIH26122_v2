"""
Regression test suite for Priority 1: Decision -> approved_actual Atomicity.

Verifies:
1. Happy path: APPROVE persists planner_decisions, execution_events status, and approved_actuals atomically.
2. Failure injection in upsert: Rollback ensures NO partial state (no decision created, status unchanged, no actuals row).
3. Downstream adapter isolation: Post-commit adapter failure (P6 / CSV export) does not compromise or roll back approved_actuals.
"""

import uuid
import pytest
from unittest.mock import patch

import backend.main  # noqa: F401  (import order: avoids a pre-existing repositories<->services circular import)
from backend.auth.models import CurrentUser
from backend.context.errors import SecurityException
from backend.context.project import ProjectContext
from backend.shared.audit import verify_audit_chain
from backend.shared.db import get_connection
from backend.routers.decisions import _record_decision


@pytest.fixture
def clean_db():
    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                # Clean up previous test runs if any
                cur.execute("DELETE FROM approved_actuals WHERE schedule_id LIKE 'ATOM-%'")
                cur.execute("DELETE FROM planner_decisions WHERE event_id IN (SELECT event_id FROM execution_events WHERE schedule_id LIKE 'ATOM-%')")
                cur.execute("DELETE FROM execution_events WHERE schedule_id LIKE 'ATOM-%'")
                cur.execute("DELETE FROM schedule_activities WHERE schedule_id LIKE 'ATOM-%'")
                cur.execute("DELETE FROM schedules WHERE schedule_id LIKE 'ATOM-%'")
                _drop_atomicity_projects(cur)
                cur.execute("DELETE FROM profiles WHERE full_name = 'Atomicity Test Planner'")
    yield
    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("DELETE FROM approved_actuals WHERE schedule_id LIKE 'ATOM-%'")
                cur.execute("DELETE FROM planner_decisions WHERE event_id IN (SELECT event_id FROM execution_events WHERE schedule_id LIKE 'ATOM-%')")
                cur.execute("DELETE FROM execution_events WHERE schedule_id LIKE 'ATOM-%'")
                cur.execute("DELETE FROM schedule_activities WHERE schedule_id LIKE 'ATOM-%'")
                cur.execute("DELETE FROM schedules WHERE schedule_id LIKE 'ATOM-%'")
                _drop_atomicity_projects(cur)
                cur.execute("DELETE FROM profiles WHERE full_name = 'Atomicity Test Planner'")


def _drop_atomicity_projects(cur):
    cur.execute("DELETE FROM audit_logs WHERE project_id IN (SELECT project_id FROM projects WHERE project_code LIKE 'V7-INTEG-ATOM-%')")
    cur.execute("DELETE FROM project_memberships WHERE project_id IN (SELECT project_id FROM projects WHERE project_code LIKE 'V7-INTEG-ATOM-%')")
    cur.execute("DELETE FROM projects WHERE project_code LIKE 'V7-INTEG-ATOM-%'")


def _new_planner_profile() -> str:
    """planner_decisions.planner_id REFERENCES profiles(id): the planner must be a real profile.
    (These tests used a random uuid, which only worked before that FK existed.)"""
    planner = str(uuid.uuid4())
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO profiles (id, full_name, role) VALUES (%s, 'Atomicity Test Planner', 'SUPERVISOR')",
            (planner,),
        )
        conn.commit()
    return planner


def _setup_schedule_and_claim(sched_id: str, act_id: str, event_id: str, planner_id: str = None,
                              actual_pct: float = None):
    """Project + membership (SUPERVISOR) + schedule + activity + VALIDATED claim, all owned by one project.
    Returns the caller's ProjectContext. An existing approved actual can be pre-seeded (actual_pct)."""
    planner_id = planner_id or _new_planner_profile()
    project_id = str(uuid.uuid4())
    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO projects (project_id, project_code, project_name, status) VALUES (%s, %s, 'Atomicity Test Project', 'ACTIVE')",
                    (project_id, f"V7-INTEG-ATOM-{uuid.uuid4().hex[:6]}"),
                )
                cur.execute(
                    "INSERT INTO project_memberships (user_id, project_id, assigned_role, active, status) VALUES (%s, %s, 'SUPERVISOR', TRUE, 'ACTIVE')",
                    (planner_id, project_id),
                )
                cur.execute(
                    """
                    INSERT INTO schedules (schedule_id, project_name, data_date, source_format, project_id)
                    VALUES (%s, 'Atomicity Test Project', '2026-01-01', 'P6_CSV', %s)
                    """,
                    (sched_id, project_id),
                )
                cur.execute(
                    """
                    INSERT INTO schedule_activities (
                        schedule_id, activity_id, activity_name, discipline, location,
                        planned_start, planned_finish, project_id
                    )
                    VALUES (%s, %s, 'Foundation Pouring', 'CIVIL', 'BLOCK_A', '2026-03-01', '2026-03-15', %s)
                    """,
                    (sched_id, act_id, project_id),
                )
                cur.execute(
                    """
                    INSERT INTO execution_events (
                        event_id, schedule_id, matched_activity_id, event_date,
                        raw_claim_text, input_channel, event_type, claim_mode, claimed_pct, status, project_id
                    )
                    VALUES (%s, %s, %s, '2026-03-05', 'Foundation pour 45 pct', 'TYPED_TEXT', 'PROGRESS_UPDATE', 'CUMULATIVE_PCT', 45.0, 'VALIDATED', %s)
                    """,
                    (event_id, sched_id, act_id, project_id),
                )
                if actual_pct is not None:
                    seed_ev, seed_dec = f"EV-SEED-{uuid.uuid4().hex[:6]}", f"DEC-SEED-{uuid.uuid4().hex[:6]}"
                    cur.execute(
                        "INSERT INTO execution_events (event_id, schedule_id, event_date, raw_claim_text, input_channel, status, project_id) "
                        "VALUES (%s, %s, '2026-03-01', 'seed', 'TYPED_TEXT', 'APPROVED', %s)", (seed_ev, sched_id, project_id))
                    cur.execute(
                        "INSERT INTO planner_decisions (decision_id, event_id, selected_activity_id, action, approved_pct, planner_id, justification) "
                        "VALUES (%s, %s, %s, 'APPROVE', %s, %s, 'seed')", (seed_dec, seed_ev, act_id, actual_pct, planner_id))
                    cur.execute(
                        "INSERT INTO approved_actuals (actual_id, decision_id, event_id, schedule_id, activity_id, actual_pct_complete, project_id) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)", (f"ACT-SEED-{uuid.uuid4().hex[:6]}", seed_dec, seed_ev, sched_id, act_id, actual_pct, project_id))
    return ProjectContext(
        user=CurrentUser(id=planner_id, full_name="Atomicity Test Planner", role="SUPERVISOR"),
        project_id=uuid.UUID(project_id), role="SUPERVISOR", membership_id=uuid.uuid4(),
    )


def test_decision_atomicity_happy_path(clean_db):
    sched_id = f"ATOM-{uuid.uuid4().hex[:6]}"
    act_id = "ACT-100"
    event_id = f"EV-{uuid.uuid4().hex[:6]}"
    ctx = _setup_schedule_and_claim(sched_id, act_id, event_id)

    planner_uuid = str(ctx.user.id)
    res = _record_decision(
        project_context=ctx,
        event_id=event_id,
        action="APPROVE",
        planner_id=planner_uuid,
        justification="Verified progress on site",
        selected_activity_id=act_id,
        approved_pct=45.0,
        approved_qty=None,
    )

    assert res["status"] == "APPROVED"
    assert res["approved_actual"] is not None

    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Verify planner_decisions
            cur.execute("SELECT * FROM planner_decisions WHERE event_id = %s", (event_id,))
            dec_row = cur.fetchone()
            assert dec_row is not None
            assert dec_row["action"] == "APPROVE"
            assert float(dec_row["approved_pct"]) == 45.0

            # 2. Verify execution_events
            cur.execute("SELECT status FROM execution_events WHERE event_id = %s", (event_id,))
            ev_row = cur.fetchone()
            assert ev_row["status"] == "APPROVED"

            # 3. Verify approved_actuals
            cur.execute("SELECT * FROM approved_actuals WHERE schedule_id = %s AND activity_id = %s", (sched_id, act_id))
            act_row = cur.fetchone()
            assert act_row is not None
            assert act_row["decision_id"] == dec_row["decision_id"]
            assert float(act_row["actual_pct_complete"]) == 45.0


def test_decision_atomicity_rollback_on_upsert_failure(clean_db):
    sched_id = f"ATOM-{uuid.uuid4().hex[:6]}"
    act_id = "ACT-100"
    event_id = f"EV-{uuid.uuid4().hex[:6]}"
    ctx = _setup_schedule_and_claim(sched_id, act_id, event_id)

    # Mock _execute_upsert to raise an error mid-transaction
    planner_uuid = str(ctx.user.id)
    with patch("backend.routers.decisions._execute_upsert", side_effect=RuntimeError("Simulated DB failure during actuals upsert")):
        with pytest.raises(RuntimeError, match="Simulated DB failure during actuals upsert"):
            _record_decision(
                project_context=ctx,
                event_id=event_id,
                action="APPROVE",
                planner_id=planner_uuid,
                justification="Should roll back",
                selected_activity_id=act_id,
                approved_pct=50.0,
                approved_qty=None,
            )

    # Verify complete rollback: NO decision, status remains VALIDATED, NO approved_actual
    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Verify planner_decisions has no record
            cur.execute("SELECT * FROM planner_decisions WHERE event_id = %s", (event_id,))
            assert cur.fetchone() is None

            # 2. Verify execution_events status is STILL 'VALIDATED'
            cur.execute("SELECT status FROM execution_events WHERE event_id = %s", (event_id,))
            ev_row = cur.fetchone()
            assert ev_row["status"] == "VALIDATED"

            # 3. Verify approved_actuals has no record
            cur.execute("SELECT * FROM approved_actuals WHERE schedule_id = %s AND activity_id = %s", (sched_id, act_id))
            assert cur.fetchone() is None


def test_decision_atomicity_adapter_failure_isolation(clean_db):
    sched_id = f"ATOM-{uuid.uuid4().hex[:6]}"
    act_id = "ACT-100"
    event_id = f"EV-{uuid.uuid4().hex[:6]}"
    ctx = _setup_schedule_and_claim(sched_id, act_id, event_id)

    # Downstream adapters failing must NOT roll back the committed decision or approved_actual
    planner_uuid = str(ctx.user.id)
    with patch("backend.routers.export.trigger_auto_export", side_effect=RuntimeError("CSV Export service down")), \
         patch("backend.shared.p6.trigger_p6_actual_push", side_effect=RuntimeError("P6 gateway timeout")):
        res = _record_decision(
            project_context=ctx,
            event_id=event_id,
            action="APPROVE",
            planner_id=planner_uuid,
            justification="Adapters failing downstream must not abort commit",
            selected_activity_id=act_id,
            approved_pct=60.0,
            approved_qty=None,
        )

    assert res["status"] == "APPROVED"
    assert res["approved_actual"] is not None

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM planner_decisions WHERE event_id = %s", (event_id,))
            assert cur.fetchone() is not None

            cur.execute("SELECT status FROM execution_events WHERE event_id = %s", (event_id,))
            assert cur.fetchone()["status"] == "APPROVED"

            cur.execute("SELECT * FROM approved_actuals WHERE schedule_id = %s AND activity_id = %s", (sched_id, act_id))
            act_row = cur.fetchone()
            assert act_row is not None
            assert float(act_row["actual_pct_complete"]) == 60.0


# --------------------------------------------------------------------------- V7 governance additions

def test_approval_stamps_project_and_audits_in_the_same_transaction(clean_db):
    sched_id, act_id, event_id = f"ATOM-{uuid.uuid4().hex[:6]}", "ACT-100", f"EV-{uuid.uuid4().hex[:6]}"
    ctx = _setup_schedule_and_claim(sched_id, act_id, event_id)
    res = _record_decision(project_context=ctx, event_id=event_id, action="APPROVE", planner_id=str(ctx.user.id),
                           justification="ok", selected_activity_id=act_id, approved_pct=45.0, approved_qty=None)
    with get_connection() as conn:
        actual = conn.execute("SELECT project_id FROM approved_actuals WHERE schedule_id = %s AND activity_id = %s", (sched_id, act_id)).fetchone()
        assert str(actual["project_id"]) == str(ctx.project_id), "approved actual must be owned by the claim's project"
        logs = [dict(r) for r in conn.execute("SELECT * FROM audit_logs WHERE project_id = %s ORDER BY log_id", (ctx.project_id,)).fetchall()]
    assert [l["action"] for l in logs] == ["APPROVE"] and logs[0]["role"] == "SUPERVISOR"
    assert verify_audit_chain(logs, allow_subchain=False) == (True, None)


def test_rollback_leaves_no_audit_row(clean_db):
    sched_id, act_id, event_id = f"ATOM-{uuid.uuid4().hex[:6]}", "ACT-100", f"EV-{uuid.uuid4().hex[:6]}"
    ctx = _setup_schedule_and_claim(sched_id, act_id, event_id)
    with patch("backend.routers.decisions._execute_upsert", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            _record_decision(project_context=ctx, event_id=event_id, action="APPROVE", planner_id=str(ctx.user.id),
                             justification="x", selected_activity_id=act_id, approved_pct=10.0, approved_qty=None)
    with get_connection() as conn:
        n = conn.execute("SELECT count(*) AS n FROM audit_logs WHERE project_id = %s", (ctx.project_id,)).fetchone()["n"]
    assert n == 0, "no approval, therefore no audit entry (they commit together)"


def test_a_claim_of_another_project_cannot_be_decided(clean_db):
    sched_a, sched_b = f"ATOM-{uuid.uuid4().hex[:6]}", f"ATOM-{uuid.uuid4().hex[:6]}"
    ev_a, ev_b = f"EV-{uuid.uuid4().hex[:6]}", f"EV-{uuid.uuid4().hex[:6]}"
    ctx_a = _setup_schedule_and_claim(sched_a, "ACT-100", ev_a)
    _setup_schedule_and_claim(sched_b, "ACT-100", ev_b)
    with pytest.raises(SecurityException) as exc:
        _record_decision(project_context=ctx_a, event_id=ev_b, action="APPROVE", planner_id=str(ctx_a.user.id),
                         justification="cross-project", selected_activity_id="ACT-100", approved_pct=99.0, approved_qty=None)
    assert exc.value.status_code == 403
    with get_connection() as conn:
        assert conn.execute("SELECT status FROM execution_events WHERE event_id = %s", (ev_b,)).fetchone()["status"] == "VALIDATED"
        assert conn.execute("SELECT count(*) AS n FROM approved_actuals WHERE schedule_id = %s", (sched_b,)).fetchone()["n"] == 0


def test_planner_cannot_redirect_an_approval_onto_a_completed_activity(clean_db):
    """Completed work changes only through the governed reopen/revision workflow."""
    sched_id, event_id = f"ATOM-{uuid.uuid4().hex[:6]}", f"EV-{uuid.uuid4().hex[:6]}"
    ctx = _setup_schedule_and_claim(sched_id, "ACT-100", event_id, actual_pct=100.0)
    with pytest.raises(SecurityException) as exc:
        _record_decision(project_context=ctx, event_id=event_id, action="APPROVE", planner_id=str(ctx.user.id),
                         justification="try to overwrite completed", selected_activity_id="ACT-100", approved_pct=30.0, approved_qty=None)
    assert exc.value.status_code == 409 and exc.value.detail["error_code"] == "REOPEN_NOT_ALLOWED"
    with get_connection() as conn:
        pct = conn.execute("SELECT actual_pct_complete FROM approved_actuals WHERE schedule_id = %s AND activity_id = 'ACT-100'", (sched_id,)).fetchone()["actual_pct_complete"]
    assert float(pct) == 100.0


def test_target_activity_must_exist_in_the_claims_schedule(clean_db):
    sched_id, event_id = f"ATOM-{uuid.uuid4().hex[:6]}", f"EV-{uuid.uuid4().hex[:6]}"
    ctx = _setup_schedule_and_claim(sched_id, "ACT-100", event_id)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _record_decision(project_context=ctx, event_id=event_id, action="APPROVE", planner_id=str(ctx.user.id),
                         justification="bad target", selected_activity_id="NO-SUCH-ACT", approved_pct=10.0, approved_qty=None)
    assert exc.value.status_code == 422
