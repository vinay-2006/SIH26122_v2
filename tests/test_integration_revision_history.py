"""
Phase 7 revision-history CONTRACT (isolated integration DB; DB_WRITE + INTEGRATION).

Decision under test: no extra table is needed. The authoritative row lives in approved_actuals; every
prior state is preserved as audit_logs.before_state written in the SAME transaction as the UPDATE
(row-locked FOR UPDATE), each revision also appends a planner_decisions REVISION row, and the project's
audit chain over those rows verifies VALID.
"""
import json
import threading

import pytest
from fastapi.testclient import TestClient

import backend.main  # noqa: F401  (import order: avoids a pre-existing repositories<->services circular import)
from backend.main import app
from backend.shared.audit import verify_audit_chain
from backend.shared.db import get_connection
from tests.test_phase7_actual_revisions import _make_jwt, configure_test_jwt, phase7_revision_fixture  # noqa: F401

pytestmark = [pytest.mark.integration]


def _revise(client, f, token, pct, qty, notes):
    return client.post(
        f"/api/v1/projects/{f['project_id']}/schedules/{f['schedule_id']}/activities/{f['activity_id']}/actuals/revision",
        json={"actual_start": "2026-08-01", "actual_pct_complete": pct, "actual_quantity": qty, "revision_notes": notes},
        headers={"Authorization": f"Bearer {token}"},
    )


def _rows(sql, args):
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def test_revision_history_is_preserved_ordered_and_chain_valid(phase7_revision_fixture):
    f = phase7_revision_fixture
    client = TestClient(app)
    token = _make_jwt(str(f["user_pm"]))

    assert _revise(client, f, token, 85.0, 425.0, "first rework").status_code == 200
    # authorize a second rework cycle (simulated by the reopen flag; the reopen API path has its own tests)
    with get_connection() as conn:
        conn.execute("UPDATE approved_actuals SET is_reopened = TRUE WHERE actual_id = %s", (f["actual_id"],))
        conn.commit()
    assert _revise(client, f, token, 60.0, 300.0, "second rework").status_code == 200

    # 1. current authoritative actual = the latest revision (60%), single row, never 160% / 100%
    cur = _rows("SELECT actual_pct_complete, actual_quantity, is_reopened FROM approved_actuals WHERE actual_id = %s", (f["actual_id"],))
    assert len(cur) == 1 and float(cur[0]["actual_pct_complete"]) == 60.0 and cur[0]["is_reopened"] is False

    # 2. every prior state preserved, in order, as before_state of successive revision audit rows
    revs = _rows(
        "SELECT log_id, before_state, after_state FROM audit_logs WHERE project_id = %s AND action = 'ACTUAL_REVISION_APPROVED' ORDER BY log_id",
        (f["project_id"],),
    )
    assert len(revs) == 2
    b1, a1, b2, a2 = (json.loads(revs[0]["before_state"]), json.loads(revs[0]["after_state"]),
                      json.loads(revs[1]["before_state"]), json.loads(revs[1]["after_state"]))
    assert float(b1["actual_pct_complete"]) == 100.0 and float(b1["actual_quantity"]) == 500.0, "original actual preserved"
    assert float(a1["actual_pct_complete"]) == 85.0 and float(b2["actual_pct_complete"]) == 85.0
    assert float(a2["actual_pct_complete"]) == 60.0

    # 3. append-only decision trail: original APPROVE + two REVISION rows
    decs = _rows("SELECT action, approved_pct FROM planner_decisions WHERE planner_id = %s ORDER BY decided_at, decision_id", (f["user_pm"],))
    assert sorted(d["action"] for d in decs) == ["APPROVE", "REVISION", "REVISION"]

    # 4. the project's audit chain over these rows verifies with the authoritative verifier
    logs = _rows("SELECT * FROM audit_logs WHERE project_id = %s ORDER BY log_id", (f["project_id"],))
    assert verify_audit_chain(logs, allow_subchain=False) == (True, None)

    # 5. reconstruction endpoint agrees
    r = client.get(
        f"/api/v1/projects/{f['project_id']}/schedules/{f['schedule_id']}/activities/{f['activity_id']}/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text


def test_concurrent_revisions_cannot_corrupt_the_authoritative_actual(phase7_revision_fixture):
    """Fixture starts authorized for exactly ONE rework. Ten simultaneous revisions: one wins, the rest are refused."""
    f = phase7_revision_fixture
    token = _make_jwt(str(f["user_pm"]))
    statuses, lock = [], threading.Lock()

    def worker(i):
        code = _revise(TestClient(app), f, token, 50.0 + i, 100.0 + i, f"concurrent {i}").status_code
        with lock:
            statuses.append(code)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert statuses.count(200) == 1, statuses
    assert all(s in (200, 409, 422) for s in statuses), statuses
    rows = _rows("SELECT actual_pct_complete, is_reopened FROM approved_actuals WHERE actual_id = %s", (f["actual_id"],))
    assert len(rows) == 1 and rows[0]["is_reopened"] is False
    revs = _rows("SELECT 1 FROM audit_logs WHERE project_id = %s AND action = 'ACTUAL_REVISION_APPROVED'", (f["project_id"],))
    assert len(revs) == 1, "exactly one audit revision row for the one accepted revision"
    logs = _rows("SELECT * FROM audit_logs WHERE project_id = %s ORDER BY log_id", (f["project_id"],))
    assert verify_audit_chain(logs, allow_subchain=False)[0]
