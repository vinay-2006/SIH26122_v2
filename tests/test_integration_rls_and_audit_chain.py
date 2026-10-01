"""
Runtime RLS + audit-chain tests for the ISOLATED integration database.

Classification: DB_WRITE + INTEGRATION (backend/testing/classification.py). The root conftest guard
refuses to run these unless SETUAI_ALLOW_DB_TESTS=1, SETUAI_TEST_ENV=integration and DATABASE_URL is
an isolated local DB carrying the _setuai_env marker. All rows use fresh UUIDs / "V7-INTEG-" codes and
are removed at module teardown.

RLS is exercised at runtime the way Supabase does it: `SET ROLE authenticated` (or anon) plus
`request.jwt.claim.sub` (what auth.uid() reads). Setup/teardown use the superuser connection.
"""
import os
import threading
import uuid

import psycopg
import psycopg.errors
import psycopg.rows
import pytest

import backend.main  # noqa: F401  (import order: avoids a pre-existing repositories<->services circular import)
from backend.repositories.audit_repo import ProjectAuditRepository
from backend.shared.audit import GENESIS_HASH, verify_audit_chain, write_audit_log

pytestmark = [pytest.mark.integration]


def _connect():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=psycopg.rows.dict_row, autocommit=True)


@pytest.fixture(scope="module")
def world():
    """Two projects with one member each, plus a user with no membership. Superuser-created."""
    su = _connect()
    w = {"su": su, "created": []}
    tag = uuid.uuid4().hex[:8]
    w["A"], w["B"] = str(uuid.uuid4()), str(uuid.uuid4())
    w["uA"], w["uB"], w["uN"] = (str(uuid.uuid4()) for _ in range(3))
    w["sA"], w["sB"] = f"SCH-A-{tag}", f"SCH-B-{tag}"
    for u, name in ((w["uA"], "Member A"), (w["uB"], "Member B"), (w["uN"], "Nobody")):
        su.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (u, f"{name}-{tag}@integ.test"))
        su.execute("INSERT INTO profiles (id, full_name, role) VALUES (%s, %s, 'SITE_ENGINEER')", (u, name))
    for p, label, user in ((w["A"], "A", w["uA"]), (w["B"], "B", w["uB"])):
        su.execute(
            "INSERT INTO projects (project_id, project_code, project_name, status) VALUES (%s, %s, %s, 'ACTIVE')",
            (p, f"V7-INTEG-{label}-{tag}", f"V7-INTEG Project {label}"),
        )
        su.execute(
            "INSERT INTO project_memberships (user_id, project_id, assigned_role, active, status) "
            "VALUES (%s, %s, 'SUPERVISOR', TRUE, 'ACTIVE')",
            (user, p),
        )
    for p, s in ((w["A"], w["sA"]), (w["B"], w["sB"])):
        su.execute(
            "INSERT INTO schedules (schedule_id, project_name, project_id, active) VALUES (%s, %s, %s, TRUE)",
            (s, "V7-INTEG schedule", p),
        )
        su.execute(
            "INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, "
            "planned_start, planned_finish, project_id) VALUES ('ACT-1', %s, 'a', 'CIVIL', 'x', "
            "'2026-01-01', '2026-02-01', %s)",
            (s, p),
        )
    # events (two in A so an evidence link can exist; one in B)
    w["evA1"], w["evA2"], w["evB1"] = (str(uuid.uuid4()) for _ in range(3))
    w["docA"], w["docB"], w["docOwn"] = (f"DOC-{uuid.uuid4().hex[:8]}" for _ in range(3))
    for d, up in ((w["docA"], None), (w["docB"], None), (w["docOwn"], w["uN"])):
        su.execute(
            "INSERT INTO source_documents (document_id, file_name, document_type, uploader_id, file_hash) "
            "VALUES (%s, 'f.txt', 'TEXT', %s, %s)",
            (d, up, uuid.uuid4().hex),
        )
    for ev, s, p, doc in ((w["evA1"], w["sA"], w["A"], w["docA"]), (w["evA2"], w["sA"], w["A"], None),
                          (w["evB1"], w["sB"], w["B"], w["docB"])):
        su.execute(
            "INSERT INTO execution_events (event_id, schedule_id, project_id, raw_claim_text, event_date, "
            "input_channel, document_id, status) VALUES (%s, %s, %s, 'claim', '2026-01-05', 'TYPED_TEXT', %s, 'EXTRACTED')",
            (ev, s, p, doc),
        )
    su.execute(
        "INSERT INTO claim_activity_splits (split_id, event_id, activity_id, schedule_id, split_pct, split_basis) "
        "VALUES (%s, %s, 'ACT-1', %s, 100, 'EQUAL')",
        (str(uuid.uuid4()), w["evA1"], w["sA"]),
    )
    su.execute(
        "INSERT INTO evidence_links (link_id, event_id_a, event_id_b, relation_type, rationale) "
        "VALUES (%s, %s, %s, 'CORROBORATES', 'integ')",
        (str(uuid.uuid4()), w["evA1"], w["evA2"]),
    )
    su.execute(
        "INSERT INTO execution_summaries (summary_id, period_start, period_end, summary_text) "
        "VALUES (%s, '2026-01-01', '2026-01-31', 'global summary')",
        (f"SUM-{tag}",),
    )
    w["sum_id"] = f"SUM-{tag}"
    # legacy NULL-project row that must be invisible to every authenticated member
    su.execute(
        "INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, "
        "planned_start, planned_finish, project_id) VALUES ('ACT-NULL', %s, 'n', 'CIVIL', 'x', "
        "'2026-01-01', '2026-02-01', NULL)",
        (w["sA"],),
    )
    yield w
    for stmt, args in (
        ("DELETE FROM audit_logs WHERE project_id IN (%s, %s)", (w["A"], w["B"])),
        ("DELETE FROM source_references WHERE event_id IN (SELECT event_id FROM execution_events WHERE project_id IN (%s, %s))", (w["A"], w["B"])),
        ("DELETE FROM source_documents WHERE uploader_id IN (%s, %s, %s)", (w["uA"], w["uB"], w["uN"])),
        ("DELETE FROM evidence_links WHERE event_id_a IN (%s, %s)", (w["evA1"], w["evA2"])),
        ("DELETE FROM claim_activity_splits WHERE event_id = %s", (w["evA1"],)),
        ("DELETE FROM execution_events WHERE project_id IN (%s, %s)", (w["A"], w["B"])),
        ("DELETE FROM source_documents WHERE document_id IN (%s, %s, %s)", (w["docA"], w["docB"], w["docOwn"])),
        ("DELETE FROM execution_summaries WHERE summary_id = %s", (w["sum_id"],)),
        ("DELETE FROM schedule_activities WHERE schedule_id IN (%s, %s)", (w["sA"], w["sB"])),
        ("DELETE FROM schedules WHERE schedule_id IN (%s, %s)", (w["sA"], w["sB"])),
        ("DELETE FROM project_memberships WHERE project_id IN (%s, %s)", (w["A"], w["B"])),
        ("DELETE FROM projects WHERE project_id IN (%s, %s)", (w["A"], w["B"])),
        ("DELETE FROM profiles WHERE id IN (%s, %s, %s)", (w["uA"], w["uB"], w["uN"])),
        ("DELETE FROM auth.users WHERE id IN (%s, %s, %s)", (w["uA"], w["uB"], w["uN"])),
    ):
        su.execute(stmt, args)
    su.close()


def _as(role, sub=None):
    """A fresh connection acting as a Supabase role (RLS applies; superuser bypass is dropped)."""
    c = _connect()
    c.execute(f"SET ROLE {role}")
    if sub:
        c.execute("SELECT set_config('request.jwt.claim.sub', %s, false)", (sub,))
    return c


def _count(c, sql, args=()):
    return c.execute(sql, args).fetchone()["n"]


# --------------------------------------------------------------------------- RLS: isolation

def test_member_sees_only_own_project_rows(world):
    a, b = _as("authenticated", world["uA"]), _as("authenticated", world["uB"])
    for c, own, other in ((a, world["A"], world["B"]), (b, world["B"], world["A"])):
        assert _count(c, "SELECT count(*) n FROM projects WHERE project_id = %s", (own,)) == 1
        assert _count(c, "SELECT count(*) n FROM projects WHERE project_id = %s", (other,)) == 0
        assert _count(c, "SELECT count(*) n FROM schedules WHERE project_id = %s", (other,)) == 0
        assert _count(c, "SELECT count(*) n FROM schedule_activities WHERE project_id = %s", (other,)) == 0
        assert _count(c, "SELECT count(*) n FROM execution_events WHERE project_id = %s", (other,)) == 0


def test_member_cannot_see_other_projects_by_direct_id_lookup(world):
    b = _as("authenticated", world["uB"])
    assert _count(b, "SELECT count(*) n FROM schedules WHERE schedule_id = %s", (world["sA"],)) == 0
    assert _count(b, "SELECT count(*) n FROM execution_events WHERE event_id = %s", (world["evA1"],)) == 0


def test_null_project_rows_invisible_to_members(world):
    a = _as("authenticated", world["uA"])
    assert _count(a, "SELECT count(*) n FROM schedule_activities WHERE activity_id = 'ACT-NULL'") == 0
    assert _count(world["su"], "SELECT count(*) n FROM schedule_activities WHERE activity_id = 'ACT-NULL'") == 1


def test_no_membership_user_sees_nothing(world):
    n = _as("authenticated", world["uN"])
    assert _count(n, "SELECT count(*) n FROM projects WHERE project_code LIKE 'V7-INTEG-%%'") == 0
    assert _count(n, "SELECT count(*) n FROM schedules WHERE project_name = 'V7-INTEG schedule'") == 0


def test_authenticated_without_identity_sees_nothing(world):
    c = _as("authenticated")  # role but no request.jwt.claim.sub -> auth.uid() is NULL
    assert _count(c, "SELECT count(*) n FROM projects WHERE project_code LIKE 'V7-INTEG-%%'") == 0
    assert _count(c, "SELECT count(*) n FROM execution_events WHERE project_id IN (%s, %s)", (world["A"], world["B"])) == 0


def test_anon_has_no_access(world):
    c = _as("anon")
    for table in ("projects", "schedules", "execution_events", "approved_actuals", "audit_logs"):
        try:
            assert _count(c, f"SELECT count(*) n FROM {table}") == 0
        except psycopg.errors.InsufficientPrivilege:
            pass  # acceptable: no grant at all


def test_inactive_membership_loses_access(world):
    su = world["su"]
    su.execute("UPDATE project_memberships SET active = FALSE WHERE user_id = %s", (world["uB"],))
    try:
        b = _as("authenticated", world["uB"])
        assert _count(b, "SELECT count(*) n FROM projects WHERE project_id = %s", (world["B"],)) == 0
    finally:
        su.execute("UPDATE project_memberships SET active = TRUE WHERE user_id = %s", (world["uB"],))


# --------------------------------------------------------------------------- RLS: write side / project_id substitution

def test_cannot_insert_into_other_project(world):
    b = _as("authenticated", world["uB"])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        b.execute(
            "INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, "
            "planned_start, planned_finish, project_id) VALUES ('EVIL', %s, 'x', 'CIVIL', 'x', "
            "'2026-01-01', '2026-02-01', %s)",
            (world["sA"], world["A"]),
        )


def test_cannot_move_row_to_other_project_or_update_foreign_row(world):
    b = _as("authenticated", world["uB"])
    cur = b.execute("UPDATE schedule_activities SET activity_name = 'pwn' WHERE schedule_id = %s", (world["sA"],))
    assert cur.rowcount == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        b.execute("UPDATE schedule_activities SET project_id = %s WHERE schedule_id = %s", (world["A"], world["sB"]))
    cur = b.execute("DELETE FROM schedule_activities WHERE schedule_id = %s", (world["sA"],))
    assert cur.rowcount == 0


def test_null_project_insert_rejected(world):
    a = _as("authenticated", world["uA"])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        a.execute(
            "INSERT INTO schedules (schedule_id, project_name, project_id) VALUES (%s, 'n', NULL)",
            (f"SCH-NULL-{uuid.uuid4().hex[:6]}",),
        )


# --------------------------------------------------------------------------- Migration 013: the four blockers

def test_claim_activity_splits_project_scoped(world):
    assert _count(_as("authenticated", world["uA"]), "SELECT count(*) n FROM claim_activity_splits WHERE event_id = %s", (world["evA1"],)) == 1
    assert _count(_as("authenticated", world["uB"]), "SELECT count(*) n FROM claim_activity_splits WHERE event_id = %s", (world["evA1"],)) == 0


def test_evidence_links_project_scoped(world):
    q = "SELECT count(*) n FROM evidence_links WHERE event_id_a = %s"
    assert _count(_as("authenticated", world["uA"]), q, (world["evA1"],)) == 1
    assert _count(_as("authenticated", world["uB"]), q, (world["evA1"],)) == 0
    assert _count(_as("authenticated", world["uN"]), q, (world["evA1"],)) == 0


def test_execution_summaries_not_readable_by_authenticated(world):
    q = "SELECT count(*) n FROM execution_summaries WHERE summary_id = %s"
    assert _count(world["su"], q, (world["sum_id"],)) == 1
    for u in ("uA", "uB", "uN"):
        assert _count(_as("authenticated", world[u]), q, (world["sum_id"],)) == 0


def test_source_documents_project_scoped(world):
    q = "SELECT count(*) n FROM source_documents WHERE document_id = %s"
    assert _count(_as("authenticated", world["uA"]), q, (world["docA"],)) == 1
    assert _count(_as("authenticated", world["uA"]), q, (world["docB"],)) == 0
    assert _count(_as("authenticated", world["uB"]), q, (world["docA"],)) == 0
    assert _count(_as("authenticated", world["uB"]), q, (world["docB"],)) == 1
    # uploader can read their own upload even without a referencing record
    assert _count(_as("authenticated", world["uN"]), q, (world["docOwn"],)) == 1
    assert _count(_as("authenticated", world["uA"]), q, (world["docOwn"],)) == 0


def test_no_true_policies_left_and_init_db_does_not_recreate_them(world):
    from backend.shared.db import init_db

    init_db()  # re-runs schema.sql, which used to recreate the USING (true) policies at every startup
    rows = world["su"].execute(
        "SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'public' AND cmd = 'SELECT' "
        "AND replace(coalesce(qual, ''), ' ', '') = 'true' AND tablename NOT IN ('profiles')"
    ).fetchall()
    assert rows == [], f"unscoped SELECT policies present: {rows}"


# --------------------------------------------------------------------------- audit chain

def _ctx(world, project_key, user_key):
    from backend.auth.models import CurrentUser
    from backend.context.project import ProjectContext

    return ProjectContext(
        user=CurrentUser(id=world[user_key], full_name="t", role="SITE_ENGINEER"),
        project_id=uuid.UUID(world[project_key]),
        role="SUPERVISOR",
        membership_id=uuid.uuid4(),
    )


def _chain(su, project_id):
    return [dict(r) for r in su.execute(
        "SELECT * FROM audit_logs WHERE project_id = %s ORDER BY log_id ASC", (project_id,)).fetchall()]


def test_chain_links_from_genesis_and_verifies(world):
    for i in range(5):
        write_audit_log("T", f"e{i}", "ACT", world["uA"], {"n": i}, {"n": i + 1}, project_id=world["A"])
    logs = _chain(world["su"], world["A"])
    assert logs[0]["previous_hash"] == GENESIS_HASH
    for prev, cur in zip(logs, logs[1:]):
        assert cur["previous_hash"] == prev["current_hash"]
    assert verify_audit_chain(logs, allow_subchain=False) == (True, None)


def test_chains_are_independent_per_project(world):
    write_audit_log("T", "x", "ACT", world["uB"], None, {"k": 1}, project_id=world["B"])
    write_audit_log("T", "y", "ACT", world["uA"], None, {"k": 1}, project_id=world["A"])
    write_audit_log("T", "z", "ACT", world["uB"], {"k": 1}, {"k": 2}, project_id=world["B"])
    lb = _chain(world["su"], world["B"])
    assert lb[0]["previous_hash"] == GENESIS_HASH
    assert verify_audit_chain(lb, allow_subchain=False)[0]
    assert verify_audit_chain(_chain(world["su"], world["A"]), allow_subchain=False)[0]


def test_concurrent_appends_do_not_fork_the_chain(world):
    before = len(_chain(world["su"], world["A"]))
    errors = []

    def worker(n):
        try:
            for i in range(5):
                write_audit_log("T", f"c{n}-{i}", "CONCURRENT", world["uA"], None, {"n": n, "i": i}, project_id=world["A"])
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    logs = _chain(world["su"], world["A"])
    assert len(logs) == before + 40
    assert len({l["previous_hash"] for l in logs}) == len(logs), "two rows share a previous_hash = fork"
    assert verify_audit_chain(logs, allow_subchain=False) == (True, None)


def test_rolled_back_append_leaves_no_row_and_no_gap(world):
    from backend.shared.audit import append_audit_record
    from backend.shared.db import get_connection

    before = _chain(world["su"], world["A"])
    with pytest.raises(RuntimeError):
        with get_connection() as conn:
            with conn.transaction():
                append_audit_record(conn, entity_type="T", entity_id="rb", action="ROLLBACK", actor_id=world["uA"],
                                    before_state=None, after_state={"x": 1}, project_id=world["A"])
                raise RuntimeError("boom")
    after = _chain(world["su"], world["A"])
    assert [l["log_id"] for l in after] == [l["log_id"] for l in before]
    write_audit_log("T", "after-rb", "OK", world["uA"], None, {"ok": 1}, project_id=world["A"])
    assert verify_audit_chain(_chain(world["su"], world["A"]), allow_subchain=False)[0]


def test_repository_writes_as_authenticated_member_and_stays_valid(world):
    ctx = _ctx(world, "A", "uA")
    ProjectAuditRepository.log(ctx, "REPO_ACTION", "T", "repo-1", schedule_id=world["sA"], old_state=None, new_state={"a": 1})
    ProjectAuditRepository.log(ctx, "REPO_ACTION", "T", "repo-2", schedule_id=world["sA"], old_state={"a": 1}, new_state={"a": 2})
    logs = _chain(world["su"], world["A"])
    assert logs[-1]["action"] == "REPO_ACTION" and logs[-1]["role"] == "SUPERVISOR"
    assert verify_audit_chain(logs, allow_subchain=False) == (True, None)


def test_repository_cannot_append_to_a_project_the_user_is_not_member_of(world):
    ctx = _ctx(world, "A", "uB")
    before = len(_chain(world["su"], world["A"]))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ProjectAuditRepository.log(ctx, "FORGED", "T", "forged", old_state=None, new_state={"x": 1})
    assert len(_chain(world["su"], world["A"])) == before


def test_history_is_not_mutated_by_new_appends(world):
    snapshot = {l["log_id"]: (l["current_hash"], l["previous_hash"], l["payload_hash"], l["after_state"])
                for l in _chain(world["su"], world["B"])}
    write_audit_log("T", "later", "LATER", world["uB"], None, {"later": True}, project_id=world["B"])
    now = {l["log_id"]: (l["current_hash"], l["previous_hash"], l["payload_hash"], l["after_state"])
           for l in _chain(world["su"], world["B"])}
    for log_id, vals in snapshot.items():
        assert now[log_id] == vals


def test_tamper_missing_and_bad_linkage_are_detected_not_repaired(world):
    su = world["su"]
    logs = _chain(su, world["B"])
    assert len(logs) >= 3 and verify_audit_chain(logs, allow_subchain=False)[0]
    victim = logs[1]["log_id"]
    original = logs[1]["after_state"]
    try:
        su.execute("UPDATE audit_logs SET after_state = %s WHERE log_id = %s", ('{"tampered":true}', victim))
        ok, reason = verify_audit_chain(_chain(su, world["B"]), allow_subchain=False)
        assert not ok and "mismatch" in reason.lower()
        su.execute("UPDATE audit_logs SET after_state = %s WHERE log_id = %s", (original, victim))
        assert verify_audit_chain(_chain(su, world["B"]), allow_subchain=False)[0]
        # wrong previous hash
        su.execute("UPDATE audit_logs SET previous_hash = %s WHERE log_id = %s", ("f" * 64, victim))
        ok, reason = verify_audit_chain(_chain(su, world["B"]), allow_subchain=False)
        assert not ok and "linkage" in reason.lower()
        su.execute("UPDATE audit_logs SET previous_hash = %s WHERE log_id = %s", (logs[1]["previous_hash"], victim))
        # a deleted record inside the chain
        su.execute("ALTER TABLE audit_logs DISABLE TRIGGER ALL")
        row = su.execute("SELECT * FROM audit_logs WHERE log_id = %s", (victim,)).fetchone()
        su.execute("DELETE FROM audit_logs WHERE log_id = %s", (victim,))
        ok, reason = verify_audit_chain(_chain(su, world["B"]), allow_subchain=False)
        assert not ok and "linkage" in reason.lower()
        cols = list(row.keys())
        su.execute(
            f"INSERT INTO audit_logs ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))})",
            [row[c] if not isinstance(row[c], dict) else psycopg.types.json.Json(row[c]) for c in cols],
        )
    finally:
        su.execute("ALTER TABLE audit_logs ENABLE TRIGGER ALL")
    assert verify_audit_chain(_chain(su, world["B"]), allow_subchain=False)[0]
