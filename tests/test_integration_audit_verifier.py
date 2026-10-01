"""
V7 audit-chain verifier against the isolated DB: legacy (pre-V7) history vs the V7 verified chain, tamper /
missing-record detection, cross-project isolation, concurrency without forks, determinism, and the API surface.
Nothing is ever repaired.
"""
import threading
import uuid

import pytest
from fastapi.testclient import TestClient

import backend.main  # noqa: F401  (import order: avoids a pre-existing repositories<->services circular import)
from backend.auth.dependencies import get_current_user
from backend.auth.models import CurrentUser
from backend.dossier.audit_verifier import AuditVerifier
from backend.main import app
from backend.shared.audit import write_audit_log
from tests.test_integration_rls_and_audit_chain import _ctx, world  # noqa: F401  (shared fixtures/helpers)

pytestmark = [pytest.mark.integration]


def _legacy(su, project, n):
    """A row exactly as the old defective writers produced it: previous_hash 'GENESIS', other hash scheme, no marker."""
    su.execute(
        "INSERT INTO audit_logs (project_id, actor_id, role, action, entity_type, entity_id, before_state, after_state, "
        "payload_hash, previous_hash, current_hash, entity_context) VALUES (%s, %s, 'SUPERVISOR', 'OLD_ACTION', 'T', %s, '{}', '{}', %s, 'GENESIS', %s, '{}'::jsonb)",
        (project, str(uuid.uuid4()), f"legacy-{n}", uuid.uuid4().hex, uuid.uuid4().hex + uuid.uuid4().hex[:32]),
    )


def _verify(world, project="A", user="uA"):
    return AuditVerifier.verify_project_chain(_ctx(world, project, user))


def test_v7_chain_is_verified_from_the_anchor_and_legacy_rows_are_only_counted(world):
    su = world["su"]
    su.execute("DELETE FROM audit_logs WHERE project_id = %s", (world["B"],))
    for n in range(3):
        _legacy(su, world["B"], n)
    res = _verify(world, "B", "uB")
    assert res.status == "LEGACY_ONLY" and res.legacy_records == 3 and res.v7_records == 0

    for n in range(4):
        write_audit_log("T", f"e{n}", "V7_ACTION", world["uB"], None, {"n": n}, project_id=world["B"])
    res = _verify(world, "B", "uB")
    assert res.status == "VALID" and res.legacy_records == 3 and res.v7_records == 4
    last_legacy = su.execute("SELECT max(log_id) AS m FROM audit_logs WHERE project_id = %s AND action = 'OLD_ACTION'", (world["B"],)).fetchone()["m"]
    assert res.anchor_log_id == last_legacy
    # legacy history is untouched by verification and by the new appends
    assert su.execute("SELECT count(*) AS n FROM audit_logs WHERE project_id = %s AND previous_hash = 'GENESIS'", (world["B"],)).fetchone()["n"] == 3


def test_tamper_and_missing_record_are_detected_never_repaired(world):
    su = world["su"]
    rows = su.execute("SELECT log_id, after_state, previous_hash FROM audit_logs WHERE project_id = %s AND action = 'V7_ACTION' ORDER BY log_id", (world["B"],)).fetchall()
    victim = rows[1]
    try:
        su.execute("UPDATE audit_logs SET after_state = '{\"n\":\"forged\"}' WHERE log_id = %s", (victim["log_id"],))
        res = _verify(world, "B", "uB")
        assert res.status == "BROKEN" and res.failure_type == "TAMPERED_CONTENT" and res.broken_at_log_id == victim["log_id"]
        # nothing was repaired: the forged value is still there and is still reported
        assert "forged" in su.execute("SELECT after_state FROM audit_logs WHERE log_id = %s", (victim["log_id"],)).fetchone()["after_state"]
        assert _verify(world, "B", "uB").broken_at_log_id == victim["log_id"]
    finally:
        su.execute("UPDATE audit_logs SET after_state = %s WHERE log_id = %s", (victim["after_state"], victim["log_id"]))
    assert _verify(world, "B", "uB").status == "VALID"

    saved = dict(su.execute("SELECT * FROM audit_logs WHERE log_id = %s", (victim["log_id"],)).fetchone())
    try:
        su.execute("DELETE FROM audit_logs WHERE log_id = %s", (victim["log_id"],))
        res = _verify(world, "B", "uB")
        assert res.status == "BROKEN" and res.failure_type == "BROKEN_LINK" and res.broken_at_log_id == rows[2]["log_id"]
    finally:
        cols = list(saved)
        import psycopg.types.json as pj
        su.execute(f"INSERT INTO audit_logs ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))})",
                   [pj.Json(saved[c]) if isinstance(saved[c], dict) else saved[c] for c in cols])
    assert _verify(world, "B", "uB").status == "VALID"


def test_projects_are_verified_independently(world):
    su = world["su"]
    write_audit_log("T", "a1", "A_ACTION", world["uA"], None, {"a": 1}, project_id=world["A"])
    assert _verify(world, "A", "uA").status == "VALID"
    victim = su.execute("SELECT log_id, after_state FROM audit_logs WHERE project_id = %s AND action = 'V7_ACTION' ORDER BY log_id LIMIT 1", (world["B"],)).fetchone()
    try:
        su.execute("UPDATE audit_logs SET after_state = '{}' WHERE log_id = %s", (victim["log_id"],))
        assert _verify(world, "B", "uB").status == "BROKEN"
        assert _verify(world, "A", "uA").status == "VALID", "tampering in project B must not affect project A"
    finally:
        su.execute("UPDATE audit_logs SET after_state = %s WHERE log_id = %s", (victim["after_state"], victim["log_id"]))


def test_concurrent_appends_never_fork_and_verification_is_deterministic(world):
    errors = []

    def worker(n):
        try:
            for i in range(4):
                write_audit_log("T", f"c{n}-{i}", "CONCURRENT", world["uA"], None, {"n": n, "i": i}, project_id=world["A"])
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    first, second = _verify(world, "A", "uA"), _verify(world, "A", "uA")
    assert first.status == "VALID"
    assert first.model_dump(exclude={"verified_at"}) == second.model_dump(exclude={"verified_at"})
    forks = world["su"].execute(
        "SELECT count(*) - count(DISTINCT previous_hash) AS f FROM audit_logs WHERE project_id = %s AND action = 'CONCURRENT'", (world["A"],)).fetchone()["f"]
    assert forks == 0


def test_api_reports_legacy_and_v7_separately(world):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=world["uB"], full_name="B", role="SUPERVISOR")
    try:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get(f"/api/v1/projects/{world['B']}/dossier/audit-verification", headers={"X-Project-ID": world["B"]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "VALID" and body["legacy_records"] == 3 and body["v7_records"] >= 4
        assert body["anchor_log_id"] and body["failure_type"] is None
        # another project's member cannot verify (or even see) this chain
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=world["uA"], full_name="A", role="SUPERVISOR")
        r = TestClient(app, raise_server_exceptions=False).get(f"/api/v1/projects/{world['B']}/dossier/audit-verification", headers={"X-Project-ID": world["B"]})
        assert r.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
