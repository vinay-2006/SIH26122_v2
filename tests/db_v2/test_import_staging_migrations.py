"""Migrations 0009-0011: import staging, discarding unlocked versions, invitation acceptance, function privileges, audit outliving entities."""
from datetime import timedelta

import psycopg
import pytest

from v2world import TODAY, as_actor, build_world, doc, fails, make_member, make_user, one, sysmode, u

HELPERS = {"is_project_member", "project_role_of", "can_read_claim", "can_read_claim_by_id", "is_pm", "is_supervisor_or_pm",
           "is_member_of_any_project", "shares_project_with"}


def make_import(conn, w, status="PARSED"):
    d = doc(conn, w, "SCHEDULE_FILE", by=w.pm)
    return conn.execute("insert into schedule_imports (project_id, source_document_id, format, status, uploaded_by) values (%s,%s,'CSV',%s,%s) returning import_id",
                        (w.project, d, status, w.pm)).fetchone()["import_id"]


# ---------------------------------------------------------------------------------------------------- 0009 import staging
def test_import_status_values_columns_and_uniqueness(w, conn):
    sysmode(conn)
    d = doc(conn, w, "SCHEDULE_FILE", by=w.pm)
    ins = "insert into schedule_imports (project_id, source_document_id, format, status, uploaded_by) values (%s,%s,'CSV',%s,%s)"
    conn.execute(ins, (w.project, d, "BUILT", w.pm))
    fails(conn, ins, (w.project, d, "PARSED", w.pm), match="uq_import_document|duplicate")      # one import per uploaded file
    d2 = doc(conn, w, "SCHEDULE_FILE", by=w.pm)
    fails(conn, ins, (w.project, d2, "INVALID", w.pm), match="check")                           # only PARSED / BUILT / DISCARDED exist
    row = conn.execute("select decisions, parsed_payload, built_at from schedule_imports").fetchone()
    assert row["decisions"] == {} and row["parsed_payload"] is None and row["built_at"] is None


def test_an_unlocked_validated_version_can_be_discarded_with_its_identities_but_a_locked_one_cannot(conn):
    d = build_world(conn, do_activate=False)
    sysmode(conn)
    conn.execute("update schedule_versions set status = 'VALIDATED' where version_id = %s", (d.v1,))      # built, reviewed, not yet locked
    n_act = one(conn, "select count(*) from activities where first_version_id = %s", (d.v1,))
    assert n_act == 3 and one(conn, "select count(*) from assignments where project_id = %s", (d.project,)) == 6
    conn.execute("delete from schedule_versions where version_id = %s", (d.v1,))
    for t in ("schedule_wbs", "baseline_activities", "baseline_resources"):
        assert one(conn, f"select count(*) from {t} where project_id = %s", (d.project,)) == 0, t
    assert one(conn, "select count(*) from activities where project_id = %s", (d.project,)) == 0           # identities created in the draft go with it
    assert one(conn, "select count(*) from assignments where project_id = %s", (d.project,)) == 0
    a = build_world(conn)                                                                                  # ACTIVE + locked: history
    sysmode(conn)
    fails(conn, "delete from schedule_versions where version_id = %s", (a.v1,), match="cannot be deleted")
    conn.execute("update schedule_versions set status = 'SUPERSEDED' where version_id = %s", (a.v1,))
    fails(conn, "delete from schedule_versions where version_id = %s", (a.v1,), match="cannot be deleted")


def test_discarding_never_removes_identities_that_earlier_versions_still_use(w, conn):
    """a revision reuses activity identities created in the baseline; discarding the revision must leave them intact"""
    sysmode(conn)
    v2 = u()
    conn.execute("insert into schedule_versions (version_id, project_id, version_no, kind, baseline_name, data_date, planned_start_date, planned_finish_date, "
                 "created_by, parent_version_id) values (%s,%s,2,'REVISION','Rev 1',%s,%s,%s,%s,%s)", (v2, w.project, TODAY, TODAY, TODAY + timedelta(days=9), w.pm, w.v1))
    root = u()
    conn.execute("insert into schedule_wbs (wbs_id, project_id, version_id, wbs_code, wbs_name, node_type) values (%s,%s,%s,'R','R','PROJECT')", (root, w.project, v2))
    conn.execute("insert into baseline_activities (project_id, version_id, activity_uid, external_activity_id, wbs_id, activity_name, discipline_code, "
                 "baseline_duration, baseline_start, baseline_finish) values (%s,%s,%s,'A1000',%s,'x','PIPING',1,%s,%s)", (w.project, v2, w.a1.uid, root, TODAY, TODAY))
    conn.execute("delete from schedule_versions where version_id = %s", (v2,))
    assert one(conn, "select count(*) from activities where project_id = %s", (w.project,)) == 3
    assert one(conn, "select count(*) from baseline_activities where version_id = %s", (w.v1,)) == 3


# ---------------------------------------------------------------------------------------------------- 0010 invitation acceptance + privileges
def invite(conn, w, email, role="SUPERVISOR", token="t" * 40, **over):
    sysmode(conn)
    conn.execute("insert into project_invitations (project_id, email, role, token_hash, invited_by, expires_at) values (%s,%s,%s,%s,%s, now() + interval '1 day')",
                 (w.project, email, role, token, w.pm))
    for k, v in over.items():
        conn.execute(f"update project_invitations set {k} = {v} where token_hash = %s", (token,))


def accept(conn, token, user):
    return conn.execute("select accept_project_invitation(%s, %s) m", (token, user)).fetchone()["m"]


def test_accepting_an_invitation_creates_exactly_the_invited_membership(w, conn):
    new = make_user(conn, "joiner")
    email = one(conn, "select email from profiles where id = %s", (new,))
    invite(conn, w, email.upper(), "SITE_ENGINEER")
    before = one(conn, "select coalesce(current_setting('app.system', true), '')")
    mid = accept(conn, "t" * 40, new)
    m = conn.execute("select * from project_memberships where membership_id = %s", (mid,)).fetchone()
    assert (m["project_id"], m["user_id"], m["role"], m["status"], m["added_by"]) == (w.project, new, "SITE_ENGINEER", "ACTIVE", w.pm)
    assert one(conn, "select accepted_by from project_invitations") == new
    assert one(conn, "select coalesce(current_setting('app.system', true), '')") == before              # the bypass is not left switched on
    as_actor(conn, w.se)                                                                                # ...and the guards still bite afterwards
    fails(conn, "insert into project_memberships (project_id, user_id, role) values (%s,%s,'SUPERVISOR')", (w.project, w.outsider), match="only a platform admin|requires")
    fails(conn, "update project_invitations set revoked_at = now()", match="requires role")


def test_invitation_acceptance_rules(w, conn):
    joiner, other = make_user(conn, "joiner2"), make_user(conn, "stranger")
    email = one(conn, "select email from profiles where id = %s", (joiner,))
    invite(conn, w, email, token="a" * 40)
    for bad_token, user, code in (("nope", joiner, "22023"), ("a" * 40, other, "42501")):
        err = fails(conn, "select accept_project_invitation(%s,%s)", (bad_token, user))
        assert err.sqlstate == code
    invite(conn, w, "late@test.local", token="b" * 40, expires_at="now() - interval '1 second'")
    invite(conn, w, "gone@test.local", token="c" * 40, revoked_at="now()")
    assert fails(conn, "select accept_project_invitation(%s,%s)", ("b" * 40, joiner)).sqlstate == "22023"
    assert fails(conn, "select accept_project_invitation(%s,%s)", ("c" * 40, joiner)).sqlstate == "22023"
    accept(conn, "a" * 40, joiner)
    assert fails(conn, "select accept_project_invitation(%s,%s)", ("a" * 40, joiner)).sqlstate == "22023"      # single use
    invite(conn, w, email, token="d" * 40)
    assert fails(conn, "select accept_project_invitation(%s,%s)", ("d" * 40, joiner)).sqlstate == "23505"      # already an active member


def test_database_clients_cannot_call_privileged_functions(w, conn):
    for role in ("anon", "authenticated"):
        conn.execute(f"set local role {role}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("select accept_project_invitation('x', %s)", (w.se,))
        conn.execute("reset role")
    rows = conn.execute("select p.proname, has_function_privilege('anon', p.oid, 'EXECUTE') a, has_function_privilege('authenticated', p.oid, 'EXECUTE') au, "
                        "has_function_privilege('public', p.oid, 'EXECUTE') pu from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
                        "where n.nspname = 'public'").fetchall()
    assert rows and not [r["proname"] for r in rows if r["a"] or r["pu"]]                            # nothing is executable by anon / PUBLIC
    assert {r["proname"] for r in rows if r["au"]} == HELPERS                                         # authenticated: only the RLS helpers


# ---------------------------------------------------------------------------------------------------- 0011 audit outlives entities
def test_audit_records_survive_the_deletion_of_the_version_they_describe_and_stay_immutable(conn):
    d = build_world(conn, do_activate=False)
    sysmode(conn)
    conn.execute("insert into audit_logs (project_id, actor_id, action, entity_type, entity_id, schedule_version_id, payload_hash, previous_hash, current_hash) "
                 "values (%s,%s,'SCHEDULE_VERSION_BUILT','SCHEDULE_VERSION',%s,%s,'h','p',%s)", (d.project, d.pm, str(d.v1), d.v1, "h" + u().hex))
    conn.execute("delete from schedule_versions where version_id = %s", (d.v1,))
    row = conn.execute("select schedule_version_id, entity_id from audit_logs").fetchone()
    assert row["schedule_version_id"] == d.v1 and row["entity_id"] == str(d.v1)
    fails(conn, "update audit_logs set action = 'FORGED'", match="append-only")
    fails(conn, "delete from audit_logs", match="append-only")
