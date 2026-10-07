"""Role separation enforced in the database (second wall behind the backend): PM / Supervisor / Site Engineer."""
from datetime import timedelta

import pytest

from v2world import TODAY, approve_qty, as_actor, claim, decide, doc, fails, make_member, make_user, one, sysmode, u


def boom(conn, fn, match):
    with pytest.raises(Exception, match=match):
        with conn.transaction():
            fn()


# ------------------------------------------------------------------ claims, decisions, documents, issues
def test_only_site_engineers_can_file_claims(w, conn):
    sysmode(conn)
    for who in (w.pm, w.sup):
        boom(conn, lambda who=who: claim(conn, w, w.a2, by=who), "only be filed by a SITE_ENGINEER")
    boom(conn, lambda: claim(conn, w, w.a2, by=w.outsider), "only be filed by a SITE_ENGINEER")
    claim(conn, w, w.a2, by=w.se)


def test_only_supervisors_can_decide(w, conn):
    sysmode(conn)
    ev = claim(conn, w, w.a2)
    for who in (w.pm, w.se, w.outsider):
        boom(conn, lambda who=who: decide(conn, w, ev, w.a2, by=who), "only a SUPERVISOR")
    decide(conn, w, ev, w.a2, by=w.sup)


def test_schedule_files_only_from_project_managers(w, conn):
    sysmode(conn)
    boom(conn, lambda: doc(conn, w, "SCHEDULE_FILE", by=w.se), "only be uploaded by a PROJECT_MANAGER")
    boom(conn, lambda: doc(conn, w, "SCHEDULE_FILE", by=w.sup), "only be uploaded by a PROJECT_MANAGER")
    doc(conn, w, "SCHEDULE_FILE", by=w.pm)


def test_site_engineers_upload_reports_and_evidence_not_the_pm(w, conn):
    sysmode(conn)
    for kind in ("DAILY_REPORT", "SITE_REPORT", "PHOTO", "EVIDENCE", "ISSUE_REPORT"):
        doc(conn, w, kind, by=w.se)
    for kind in ("DAILY_REPORT", "SITE_REPORT", "PHOTO"):
        boom(conn, lambda kind=kind: doc(conn, w, kind, by=w.pm), "only be uploaded by a SITE_ENGINEER")
        boom(conn, lambda kind=kind: doc(conn, w, kind, by=w.sup), "only be uploaded by a SITE_ENGINEER")
    boom(conn, lambda: doc(conn, w, "EVIDENCE", by=w.pm), "SITE_ENGINEER or SUPERVISOR")


def test_upload_batches_only_by_site_engineers(w, conn):
    sysmode(conn)
    conn.execute("insert into upload_batches (project_id,uploaded_by) values (%s,%s)", (w.project, w.se))
    boom(conn, lambda: conn.execute("insert into upload_batches (project_id,uploaded_by) values (%s,%s)", (w.project, w.pm)),
         "only be created by a SITE_ENGINEER")


def test_issue_reporting_and_resolution_roles(w, conn):
    sysmode(conn)
    ins = ("insert into issues (issue_id,project_id,activity_uid,category_code,title,reported_date,reported_by) "
           "values (%s,%s,%s,'MATERIAL_DELIVERY_DELAY','Line pipe delayed at port',%s,%s)")
    boom(conn, lambda: conn.execute(ins, (u(), w.project, w.a1.uid, TODAY, w.pm)), "SITE_ENGINEER or SUPERVISOR")
    iid = u()
    conn.execute(ins, (iid, w.project, w.a1.uid, TODAY, w.se))
    boom(conn, lambda: conn.execute("update issues set status='RESOLVED', resolved_by=%s, resolved_at=now() where issue_id=%s", (w.se, iid)),
         "only a SUPERVISOR")
    boom(conn, lambda: conn.execute("update issues set status='RESOLVED', resolved_by=%s, resolved_at=now() where issue_id=%s", (w.pm, iid)),
         "only a SUPERVISOR")
    conn.execute("update issues set status='RESOLVED', resolved_by=%s, resolved_at=now(), resolution_notes='Cleared' where issue_id=%s", (w.sup, iid))
    fails(conn, "update issues set status='ACTIVE', resolved_by=null, resolved_at=null where issue_id=%s", (iid,), match="cannot be reopened")
    fails(conn, "delete from issues where issue_id=%s", (iid,), match="append-only")
    fails(conn, "insert into issues (project_id,category_code,title,reported_date,reported_by) values (%s,'OTHER','no target',%s,%s)",
          (w.project, TODAY, w.se), match="issue_target_chk|check")


def test_root_causes_and_memory_are_supervisor_managed(w, conn):
    sysmode(conn)
    rc = "insert into root_causes (project_id,category_code,title,identified_by) values (%s,'WEATHER','Monsoon flooding of ROW',%s)"
    boom(conn, lambda: conn.execute(rc, (w.project, w.pm)), "managed by a SUPERVISOR")
    boom(conn, lambda: conn.execute(rc, (w.project, w.se)), "managed by a SUPERVISOR")
    conn.execute(rc, (w.project, w.sup))
    mem = "insert into institutional_memory (project_id,title,lessons_learned,recorded_by) values (%s,'Flood lesson','Pre-position pumps',%s)"
    boom(conn, lambda: conn.execute(mem, (w.project, w.pm)), "managed by a SUPERVISOR")
    conn.execute(mem, (w.project, w.sup))


# ------------------------------------------------------------------ tripwire: schedule / project management is PM-only
def test_schedule_and_project_writes_need_a_project_manager_actor(conn):
    from v2world import build_world
    w = build_world(conn, do_activate=False)
    sysmode(conn)
    v = u()
    ver = ("insert into schedule_versions (version_id,project_id,version_no,kind,baseline_name,data_date,planned_start_date,"
           "planned_finish_date,created_by,parent_version_id) values (%s,%s,2,'REVISION','Rev 1',%s,%s,%s,%s,%s)")
    args = (v, w.project, TODAY, TODAY, TODAY + timedelta(days=5), w.pm, w.v1)
    for who in (w.se, w.sup, w.outsider, w.pm2):        # pm2 is a PM, but of ANOTHER project
        as_actor(conn, who)
        boom(conn, lambda: conn.execute(ver, args), "requires role")
        boom(conn, lambda: conn.execute("update baseline_activities set activity_name='x' where version_id=%s", (w.v1,)), "requires role")
        boom(conn, lambda: conn.execute("update projects set project_name='Hijacked' where project_id=%s", (w.project,)), "requires role")
        boom(conn, lambda: conn.execute("update project_settings set over_baseline_tolerance_pct=99 where project_id=%s", (w.project,)), "requires role")
    conn.execute("select set_config('app.system','off',true), set_config('app.actor_id','',true)")   # no actor at all
    boom(conn, lambda: conn.execute(ver, args), "authenticated actor")
    as_actor(conn, w.pm)
    conn.execute(ver, args)                                                       # the project's own PM can
    conn.execute("update projects set project_name='Renamed by PM' where project_id=%s", (w.project,))
    conn.execute("update project_settings set over_baseline_tolerance_pct=15 where project_id=%s", (w.project,))
    conn.execute("update baseline_activities set activity_name='Edited in draft' where version_id=%s", (w.v1,))


def test_project_creation_requires_the_platform_grant(conn):
    from v2world import build_world
    w = build_world(conn)
    ins = "insert into projects (project_code,project_name,created_by) values (%s,%s,%s)"
    sysmode(conn)
    nobody = make_user(conn, "nogrant")
    as_actor(conn, nobody)
    boom(conn, lambda: conn.execute(ins, ("NEW-1", "Project without grant", nobody)), "CREATE_PROJECT")
    as_actor(conn, w.se)
    boom(conn, lambda: conn.execute(ins, ("NEW-1", "SE tries to create", w.se)), "CREATE_PROJECT")
    as_actor(conn, w.pm)                                          # has the grant, but cannot create on someone else's behalf
    boom(conn, lambda: conn.execute(ins, ("NEW-2", "Spoofed creator", w.sup)), "CREATE_PROJECT")
    pid = one(conn, ins + " returning project_id", ("NEW-3", "Created by granted PM", w.pm))["project_id"] if False else None
    row = conn.execute(ins + " returning project_id", ("NEW-3", "Created by granted PM", w.pm)).fetchone()
    pid = row["project_id"]
    assert one(conn, "select count(*) from project_settings where project_id=%s", (pid,)) == 1      # defaults auto-created
    # creator bootstraps as PM of their new project; nobody else can claim that seat
    conn.execute("insert into project_memberships (project_id,user_id,role) values (%s,%s,'PROJECT_MANAGER')", (pid, w.pm))
    as_actor(conn, w.sup)
    boom(conn, lambda: conn.execute("insert into project_memberships (project_id,user_id,role) values (%s,%s,'PROJECT_MANAGER')", (pid, w.sup)),
         "only a platform admin")
    # revoking the grant removes the authority
    sysmode(conn)
    conn.execute("update platform_grants set revoked_at=now(), revoked_by=%s where user_id=%s and capability='CREATE_PROJECT'", (w.admin, w.pm))
    as_actor(conn, w.pm)
    boom(conn, lambda: conn.execute(ins, ("NEW-4", "After revocation", w.pm)), "CREATE_PROJECT")


def test_platform_grants_are_admin_only(w, conn):
    ins = "insert into platform_grants (user_id,capability,granted_by) values (%s,'CREATE_PROJECT',%s)"
    as_actor(conn, w.pm)
    boom(conn, lambda: conn.execute(ins, (w.sup, w.pm)), "requires PLATFORM_ADMIN")
    as_actor(conn, w.admin)
    conn.execute(ins, (w.sup, w.admin))


# ------------------------------------------------------------------ memberships
def test_pm_manages_se_and_supervisor_members_only(w, conn):
    newse = make_user(conn, "newse")
    as_actor(conn, w.pm)
    conn.execute("insert into project_memberships (project_id,user_id,role,added_by) values (%s,%s,'SITE_ENGINEER',%s)", (w.project, newse, w.pm))
    conn.execute("update project_memberships set role='SUPERVISOR' where project_id=%s and user_id=%s", (w.project, newse))
    conn.execute("update project_memberships set status='REMOVED' where project_id=%s and user_id=%s", (w.project, newse))
    assert one(conn, "select project_role_of(%s,%s)", (w.project, newse)) is None               # removed => no access
    boom(conn, lambda: conn.execute("insert into project_memberships (project_id,user_id,role) values (%s,%s,'PROJECT_MANAGER')",
                                    (w.project, w.outsider)), "only a platform admin")
    boom(conn, lambda: conn.execute("update project_memberships set role='PROJECT_MANAGER' where project_id=%s and user_id=%s",
                                    (w.project, w.se)), "only a platform admin")


def test_other_people_cannot_manage_members(w, conn):
    new = make_user(conn, "candidate")
    ins = "insert into project_memberships (project_id,user_id,role) values (%s,%s,'SITE_ENGINEER')"
    for who in (w.se, w.sup, w.outsider, w.pm2):
        as_actor(conn, who)
        boom(conn, lambda: conn.execute(ins, (w.project, new)), "only a platform admin|authenticated actor")


def test_last_project_manager_cannot_be_removed_or_demoted(w, conn):
    as_actor(conn, w.admin)
    for sql in ("update project_memberships set status='REMOVED' where project_id=%s and user_id=%s",
                "update project_memberships set status='SUSPENDED' where project_id=%s and user_id=%s",
                "update project_memberships set role='SUPERVISOR' where project_id=%s and user_id=%s",
                "delete from project_memberships where project_id=%s and user_id=%s"):
        fails(conn, sql, (w.project, w.pm), match="last active PROJECT_MANAGER")
    conn.execute("insert into project_memberships (project_id,user_id,role) values (%s,%s,'PROJECT_MANAGER')", (w.project, w.outsider))
    conn.execute("update project_memberships set status='REMOVED' where project_id=%s and user_id=%s", (w.project, w.pm))    # now allowed


def test_invitations_are_pm_only_for_se_and_supervisor_roles(w, conn):
    ins = ("insert into project_invitations (project_id,email,role,token_hash,invited_by,expires_at) "
           "values (%s,%s,%s,%s,%s, now() + interval '3 days')")
    as_actor(conn, w.se)
    boom(conn, lambda: conn.execute(ins, (w.project, "x@test.local", "SITE_ENGINEER", "h1", w.se)), "requires role")
    as_actor(conn, w.pm)
    conn.execute(ins, (w.project, "new.engineer@test.local", "SITE_ENGINEER", "h2", w.pm))
    fails(conn, ins, (w.project, "x@test.local", "PROJECT_MANAGER", "h3", w.pm), match="check")
    fails(conn, ins, (w.project, "NEW.engineer@test.local", "SUPERVISOR", "h4", w.pm), match="uq_invitation_open|duplicate")
    fails(conn, ins, (w.project, "y@test.local", "SUPERVISOR", "h2", w.pm), match="token_hash|duplicate")
