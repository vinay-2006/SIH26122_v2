"""Row Level Security as the `anon` / `authenticated` roles (what direct PostgREST access would see). Deny by default."""
import psycopg
import pytest

from v2world import TODAY, approve_qty, build_world, claim, decide, doc, make_member, make_user, one, sysmode, u


def become(conn, uid, role="authenticated"):
    conn.execute("reset role")
    conn.execute(f"set local role {role}")
    conn.execute("select set_config('request.jwt.claim.sub', %s, true)", (str(uid) if uid else "",))


def n(conn, sql, params=None):
    return conn.execute(f"select count(*) c from ({sql}) q", params).fetchone()["c"]


@pytest.fixture
def seeded(w, conn):
    """Two SEs, claims from each, a decision + notification, ledger rows, an issue, org + project memories, an audit row."""
    sysmode(conn)
    w.se2 = make_user(conn, "se2")
    make_member(conn, w.project, w.se2, "SITE_ENGINEER", w.pm)
    w.c1 = claim(conn, w, w.a2, by=w.se)
    w.c2 = claim(conn, w, w.a2, by=w.se2)
    approve_qty(conn, w, w.a2, 0, 100)
    ev = claim(conn, w, w.a1, by=w.se)
    w.dec = decide(conn, w, ev, w.a1)
    decide(conn, w, w.c2, w.a2)                                       # a decision on the OTHER engineer's claim
    conn.execute("insert into notifications (project_id,recipient_id,notification_type,decision_id,event_id,title) values (%s,%s,'CLAIM_DECISION',%s,%s,'Approved')",
                 (w.project, w.se, w.dec, ev))
    conn.execute("insert into institutional_memory (project_id,title,lessons_learned,visibility,recorded_by) values (%s,'Org lesson','Share this',%s,%s)",
                 (w.project, "ORGANISATION", w.sup))
    conn.execute("insert into institutional_memory (project_id,title,lessons_learned,visibility,recorded_by) values (%s,'Private lesson','Keep here',%s,%s)",
                 (w.project, "PROJECT", w.sup))
    conn.execute("insert into audit_logs (project_id,actor_id,action,entity_type,entity_id,payload_hash,previous_hash,current_hash) "
                 "values (%s,%s,'X','Y','1','h','p',%s)", (w.project, w.sup, "h" + u().hex))
    conn.execute("insert into project_invitations (project_id,email,role,token_hash,invited_by,expires_at) values (%s,'i@test.local','SUPERVISOR',%s,%s,now()+interval '1 day')",
                 (w.project, "t" + u().hex, w.pm))
    return w


def test_anon_has_no_access_at_all(seeded, conn):
    become(conn, None, "anon")
    for t in ("projects", "execution_events", "baseline_activities", "profiles", "v_project_progress", "disciplines"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute(f"select * from {t}")


def test_authenticated_cannot_write_anything_directly(seeded, conn):
    w = seeded
    become(conn, w.pm)
    for sql, params in (("insert into projects (project_code,project_name,created_by) values ('HAX-1','hax',%s)", (w.pm,)),
                        ("update projects set project_name='x' where project_id=%s", (w.project,)),
                        ("update baseline_activities set activity_name='x'", None),
                        ("insert into project_memberships (project_id,user_id,role) values (%s,%s,'PROJECT_MANAGER')", (w.project, w.pm)),
                        ("delete from execution_events", None)):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute(sql, params)


def test_project_isolation_between_members_and_outsiders(seeded, conn):
    w = seeded
    become(conn, w.outsider)
    for t in ("projects", "project_memberships", "baseline_activities", "schedule_versions", "execution_events", "approved_resource_progress",
              "source_documents", "issues", "notifications", "audit_logs", "v_project_progress", "v_activity_progress", "v_wbs_progress"):
        assert n(conn, f"select * from {t}") == 0, t
    become(conn, w.pm2)                                              # a PM of ANOTHER project
    assert n(conn, "select * from projects") == 1
    assert one(conn, "select project_id from projects") == w.project_b
    assert n(conn, "select * from baseline_activities") == 0
    assert n(conn, "select * from v_project_progress") == 0
    become(conn, w.pm)
    assert n(conn, "select * from projects") == 1 and one(conn, "select project_id from projects") == w.project


def test_claim_visibility_by_role(seeded, conn):
    w = seeded
    become(conn, w.se)
    own = {r["filed_by"] for r in conn.execute("select filed_by from execution_events").fetchall()}
    assert own == {w.se}                                              # a site engineer sees only their own claims
    become(conn, w.se2)
    assert {r["filed_by"] for r in conn.execute("select filed_by from execution_events").fetchall()} == {w.se2}
    become(conn, w.sup)
    assert {r["filed_by"] for r in conn.execute("select filed_by from execution_events").fetchall()} == {w.se, w.se2}
    become(conn, w.pm)
    assert n(conn, "select * from execution_events") == 0              # the PM cannot read field claims or decisions
    assert n(conn, "select * from planner_decisions") == 0
    become(conn, w.se)
    assert n(conn, "select * from planner_decisions") == 2             # the SE sees decisions on their OWN claims only (2 of 3)
    become(conn, w.se2)
    assert n(conn, "select * from planner_decisions") == 1
    become(conn, w.sup)
    assert n(conn, "select * from planner_decisions") == 3


def test_members_read_schedule_and_progress_but_pm_only_sees_imports_and_invitations(seeded, conn):
    w = seeded
    for who in (w.se, w.sup, w.pm):
        become(conn, who)
        assert n(conn, "select * from baseline_activities") == 3, who
        assert n(conn, "select * from approved_resource_progress") == 1
        assert one(conn, "select physical_pct from v_project_progress") > 0
    become(conn, w.pm);  assert n(conn, "select * from project_invitations") == 1
    become(conn, w.se);  assert n(conn, "select * from project_invitations") == 0
    become(conn, w.sup); assert n(conn, "select * from project_invitations") == 0


def test_notifications_are_recipient_only(seeded, conn):
    w = seeded
    become(conn, w.se);  assert n(conn, "select * from notifications") == 1
    become(conn, w.se2); assert n(conn, "select * from notifications") == 0
    become(conn, w.sup); assert n(conn, "select * from notifications") == 0


def test_audit_log_visible_to_supervisor_and_pm_only(seeded, conn):
    w = seeded
    become(conn, w.sup); assert n(conn, "select * from audit_logs") == 1
    become(conn, w.pm);  assert n(conn, "select * from audit_logs") == 1
    become(conn, w.se);  assert n(conn, "select * from audit_logs") == 0


def test_institutional_memory_sharing(seeded, conn):
    w = seeded
    become(conn, w.pm2)                                               # member of another project
    assert [r["title"] for r in conn.execute("select title from institutional_memory").fetchall()] == ["Org lesson"]
    become(conn, w.outsider)                                          # member of nothing
    assert n(conn, "select * from institutional_memory") == 0
    become(conn, w.se)
    assert n(conn, "select * from institutional_memory") == 2


def test_profiles_visible_only_to_self_and_project_colleagues(seeded, conn):
    w = seeded
    become(conn, w.se)
    ids = {r["id"] for r in conn.execute("select id from profiles").fetchall()}
    assert w.se in ids and w.sup in ids and w.pm in ids and w.outsider not in ids and w.pm2 not in ids


def test_removed_or_suspended_members_lose_access_immediately(seeded, conn):
    w = seeded
    sysmode(conn)
    conn.execute("update project_memberships set status='SUSPENDED' where project_id=%s and user_id=%s", (w.project, w.se))
    become(conn, w.se)
    assert n(conn, "select * from projects") == 0 and n(conn, "select * from baseline_activities") == 0 and n(conn, "select * from v_project_progress") == 0
