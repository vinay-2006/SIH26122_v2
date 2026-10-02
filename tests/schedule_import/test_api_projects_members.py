"""Projects, settings, members, invitations and platform grants through the v2 API (real database, real authorization)."""
import datetime as dt

import pytest

from v2api import connect, make_user, token
from backend.v2 import audit

P = "/api/v2/projects"


def test_health_reports_the_isolated_v2_database(api):
    h = api.get("/health").json()
    assert h["database"].startswith("setuai_v2_") and h["schema"] == "sih-v2-baseline" and h["env"] == "integration" and h["migrations"] >= 10


def test_only_users_with_the_create_project_grant_can_create_projects(api):
    nobody, granted = make_user("nobody"), make_user("granted", ["CREATE_PROJECT"])
    body = {"project_code": "NEW-1", "project_name": "A brand new project"}
    r = api.post(P, nobody, json=body)
    assert r.status_code == 403 and r.json()["error"]["code"] == "CREATE_PROJECT_REQUIRED"
    assert api.post(P, None, json=body).status_code == 401
    r = api.post(P, granted, json=body)
    assert r.status_code == 201 and r.json()["created_by"] == str(granted.id)
    mine = api.get(P, granted).json()
    assert [(p["project_code"], p["my_role"]) for p in mine] == [("NEW-1", "PROJECT_MANAGER")]
    assert api.post(P, granted, json=body).status_code == 409                  # duplicate code
    assert api.post(P, granted, json={"project_code": "bad code", "project_name": "x"}).status_code == 422


def test_project_manager_role_alone_grants_no_creation_right(world, api):
    """pm2 only holds CREATE_PROJECT; a plain PM-by-membership (the supervisor-turned-PM case) cannot create."""
    r = api.post(P, world.sup, json={"project_code": "SUP-1", "project_name": "Supervisor tries to create"})
    assert r.status_code == 403


def test_admin_grants_and_revokes_project_creation(api):
    admin, user = make_user("root", ["PLATFORM_ADMIN"]), make_user("candidate")
    assert api.post("/api/v2/platform/grants", user, json={"email": user.email, "capability": "CREATE_PROJECT"}).status_code == 403
    r = api.post("/api/v2/platform/grants", admin, json={"email": user.email, "capability": "CREATE_PROJECT"})
    assert r.status_code == 201
    assert api.post(P, user, json={"project_code": "CAND-1", "project_name": "Candidate project"}).status_code == 201
    assert api.delete(f"/api/v2/platform/grants/{user.id}/CREATE_PROJECT", admin).status_code == 204
    assert api.post(P, user, json={"project_code": "CAND-2", "project_name": "After revocation"}).status_code == 403
    assert api.post("/api/v2/platform/grants", admin, json={"email": "ghost@test.local", "capability": "CREATE_PROJECT"}).status_code == 404
    assert api.post("/api/v2/platform/grants", admin, json={"email": user.email, "capability": "GOD_MODE"}).status_code == 422


def test_membership_not_role_name_controls_project_access(world, api):
    assert api.get(f"{P}/{world.project}", world.se).status_code == 200
    assert api.get(f"{P}/{world.project}", world.outsider).status_code == 403
    assert api.get(f"{P}/{world.project}", world.pm2).status_code == 403          # a PM, but of a different project
    assert api.get(f"{P}/{world.project}").status_code == 401
    assert api.get(f"{P}/{world.project}", world.pm).json()["my_role"] == "PROJECT_MANAGER"
    assert {p["project_code"] for p in api.get(P, world.pm2).json()} == {"OTHER-1"}


def test_only_the_pm_edits_project_details_and_settings(world, api):
    url = f"{P}/{world.project}"
    assert api.patch(url, world.pm, json={"description": "Pipeline spur", "lifecycle_status": "ONGOING"}).json()["lifecycle_status"] == "ONGOING"
    for who in (world.sup, world.se, world.outsider, world.pm2):
        assert api.patch(url, who, json={"description": "hijack"}).status_code == 403
        assert api.get(f"{url}/settings", who).status_code == 403
        assert api.patch(f"{url}/settings", who, json={"over_baseline_tolerance_pct": 99}).status_code == 403
    assert api.patch(url, world.pm, json={"project_name": "x"}).status_code == 422
    assert api.patch(url, world.pm, json={}).status_code == 422
    s = api.patch(f"{url}/settings", world.pm, json={"over_baseline_tolerance_pct": 15, "working_days_per_week": 5}).json()
    assert float(s["over_baseline_tolerance_pct"]) == 15 and s["working_days_per_week"] == 5
    assert api.patch(f"{url}/settings", world.pm, json={"over_baseline_tolerance_pct": 150}).status_code == 422
    assert float(api.get(f"{url}/settings", world.pm).json()["completion_threshold_pct"]) == 95


def test_archived_projects_are_read_only(world, api):
    url = f"{P}/{world.project}"
    assert api.post(f"{url}/archive", world.sup).status_code == 403
    assert api.post(f"{url}/archive", world.pm).json()["record_status"] == "ARCHIVED"
    assert api.patch(url, world.pm, json={"description": "x"}).json()["error"]["code"] == "PROJECT_ARCHIVED"
    assert api.post(f"{url}/members", world.pm, json={"email": world.outsider.email, "role": "SITE_ENGINEER"}).status_code == 409
    assert api.get(url, world.se).status_code == 200                               # still readable
    assert api.post(f"{url}/restore", world.pm).json()["record_status"] == "ACTIVE"
    assert api.patch(url, world.pm, json={"description": "x"}).status_code == 200


def test_pm_manages_site_engineers_and_supervisors(world, api):
    url = f"{P}/{world.project}/members"
    assert {m["role"] for m in api.get(url, world.pm).json()} == {"PROJECT_MANAGER", "SUPERVISOR", "SITE_ENGINEER"}
    assert api.get(url, world.sup).status_code == 200                              # supervisors can see who is on the project
    assert api.get(url, world.se).status_code == 403
    new = make_user("newbie")
    assert api.post(url, world.pm, json={"email": new.email, "role": "PROJECT_MANAGER"}).status_code == 422
    assert api.post(url, world.pm, json={"email": "nobody@test.local", "role": "SITE_ENGINEER"}).status_code == 404
    assert api.post(url, world.pm, json={"email": new.email, "role": "SITE_ENGINEER"}).status_code == 201
    assert api.post(url, world.pm, json={"email": new.email.upper(), "role": "SUPERVISOR"}).status_code == 409
    assert api.get(f"{P}/{world.project}", new).status_code == 200
    assert api.patch(f"{url}/{new.id}", world.pm, json={"role": "SUPERVISOR"}).json()["role"] == "SUPERVISOR"
    assert api.patch(f"{url}/{new.id}", world.pm, json={"status": "SUSPENDED"}).json()["status"] == "SUSPENDED"
    assert api.get(f"{P}/{world.project}", new).status_code == 403               # suspended: no access, immediately
    assert api.delete(f"{url}/{new.id}", world.pm).status_code == 204
    assert api.post(url, world.pm, json={"email": new.email, "role": "SITE_ENGINEER"}).status_code == 201   # re-added after removal
    assert api.get(f"{P}/{world.project}", new).status_code == 200
    for who in (world.sup, world.se, world.pm2, world.outsider):
        assert api.post(url, who, json={"email": new.email, "role": "SITE_ENGINEER"}).status_code == 403
        assert api.delete(f"{url}/{new.id}", who).status_code == 403


def test_the_pm_seat_cannot_be_changed_through_the_project_api(world, api):
    url = f"{P}/{world.project}/members/{world.pm.id}"
    calls = (lambda: api.patch(url, world.pm, json={"role": "SUPERVISOR"}), lambda: api.patch(url, world.pm, json={"status": "REMOVED"}),
             lambda: api.delete(url, world.pm))
    for call in calls:                                                              # sole PM: the last-PM rule refuses
        r = call()
        assert r.status_code == 409 and "last active PROJECT_MANAGER" in r.json()["error"]["message"]
    # with a second PM (seated by a platform admin, directly) a PM still cannot touch PM seats: only a platform admin may
    second = make_user("pm_second")
    with connect(system=True) as c:
        c.execute("insert into project_memberships (project_id, user_id, role) values (%s,%s,'PROJECT_MANAGER')", (world.project, second.id))
    for call in calls:
        assert call().status_code == 403
        assert api.patch(f"{P}/{world.project}/members/{second.id}", world.pm, json={"status": "REMOVED"}).status_code == 403
    assert sum(m["role"] == "PROJECT_MANAGER" and m["status"] == "ACTIVE" for m in api.get(f"{P}/{world.project}/members", world.pm).json()) == 2


# ---------------------------------------------------------------------------------------------------- invitations
def test_invitation_link_flow(world, api):
    invitee = make_user("invitee")
    url = f"{P}/{world.project}/invitations"
    assert api.post(url, world.se, json={"email": invitee.email, "role": "SITE_ENGINEER"}).status_code == 403
    assert api.post(url, world.pm, json={"email": invitee.email, "role": "PROJECT_MANAGER"}).status_code == 422
    r = api.post(url, world.pm, json={"email": invitee.email, "role": "SUPERVISOR"})
    assert r.status_code == 201
    tok = r.json()["token"]
    assert len(tok) >= 40 and r.json()["accept_path"].endswith(tok)
    listed = api.get(url, world.pm).json()
    assert len(listed) == 1 and listed[0]["state"] == "PENDING" and "token" not in listed[0] and "token_hash" not in listed[0]
    with connect() as c:                                                           # only a hash is stored
        row = c.execute("select token_hash from project_invitations").fetchone()
        assert row["token_hash"] != tok and tok not in str(row)
    assert api.post(url, world.pm, json={"email": invitee.email.upper(), "role": "SUPERVISOR"}).status_code == 409   # one open invitation per email
    wrong = api.post("/api/v2/invitations/accept", world.outsider, json={"token": tok})
    assert wrong.status_code == 403 and wrong.json()["error"]["code"] == "INVITATION_EMAIL_MISMATCH"
    assert api.post("/api/v2/invitations/accept", invitee, json={"token": "x" * 43}).status_code == 410
    ok = api.post("/api/v2/invitations/accept", invitee, json={"token": tok})
    assert ok.status_code == 200 and ok.json()["role"] == "SUPERVISOR"
    assert api.get(f"{P}/{world.project}", invitee).json()["my_role"] == "SUPERVISOR"
    assert api.post("/api/v2/invitations/accept", invitee, json={"token": tok}).status_code == 410     # single use
    assert api.get(url, world.pm).json()[0]["state"] == "ACCEPTED"


def test_revoked_and_expired_invitations_cannot_be_accepted(world, api):
    a, b = make_user("rev"), make_user("exp")
    url = f"{P}/{world.project}/invitations"
    ta = api.post(url, world.pm, json={"email": a.email, "role": "SITE_ENGINEER"}).json()
    tb = api.post(url, world.pm, json={"email": b.email, "role": "SITE_ENGINEER"}).json()
    assert api.delete(f"{url}/{ta['invitation_id']}", world.pm).status_code == 204
    assert api.post("/api/v2/invitations/accept", a, json={"token": ta["token"]}).status_code == 410
    with connect(system=True) as c:
        c.execute("update project_invitations set expires_at = now() - interval '1 minute' where invitation_id = %s", (tb["invitation_id"],))
    assert api.post("/api/v2/invitations/accept", b, json={"token": tb["token"]}).status_code == 410
    assert {i["state"] for i in api.get(url, world.pm).json()} == {"REVOKED", "EXPIRED"}


def test_invitations_cannot_be_used_to_become_a_project_manager_by_tampering(world, api):
    """the accept function only ever grants the role the PM invited for; and only to the matching email"""
    u = make_user("sneaky")
    tok = api.post(f"{P}/{world.project}/invitations", world.pm, json={"email": u.email, "role": "SITE_ENGINEER"}).json()["token"]
    api.post("/api/v2/invitations/accept", u, json={"token": tok})
    assert api.get(f"{P}/{world.project}", u).json()["my_role"] == "SITE_ENGINEER"


def test_the_invitation_accept_function_is_not_callable_by_database_clients(world):
    import psycopg
    with connect() as c:
        c.execute("set role authenticated")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("select accept_project_invitation('x', %s)", (world.se.id,))
        c.execute("reset role"); c.execute("set role anon")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("select accept_project_invitation('x', %s)", (world.se.id,))


def test_actions_are_audited_and_the_chain_verifies(world, api):
    api.patch(f"{P}/{world.project}", world.pm, json={"description": "audited change"})
    api.patch(f"{P}/{world.project}/settings", world.pm, json={"over_baseline_tolerance_pct": 12})
    api.patch(f"{P}/{world.project}/members/{world.se2.id}", world.pm, json={"status": "SUSPENDED"})
    with connect() as c:
        actions = [r["action"] for r in c.execute("select action from audit_logs order by log_id").fetchall()]
        assert {"PROJECT_CREATED", "MEMBER_ADDED", "PROJECT_UPDATED", "PROJECT_SETTINGS_UPDATED", "MEMBER_CHANGED"} <= set(actions)
        v = audit.verify_chain(c)
        assert v["valid"] and v["entries"] == len(actions)
        c.execute("select set_config('app.system','on',false)")
        import psycopg
        with pytest.raises(psycopg.errors.CheckViolation):                         # append-only even for a privileged connection
            c.execute("update audit_logs set action = 'FORGED' where log_id = 1")
