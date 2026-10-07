"""Every route, every persona. The table below is the authoritative inventory of who can reach what; the test fails if a route exists that is not in
the table (or the table lists a route that does not exist), so a new endpoint cannot ship without its authorization being stated and tested."""
import uuid

import pytest

from apikit import url
from v2api import connect

# M = Project Manager, S = Supervisor, E = Site Engineer. The set is who passes the first (permission) gate; the domain services and database guards
# then apply their own, finer rules (tested in tests/v2_domain and the workflow tests).
ROUTES = {
    ("GET", "/projects/{p}"): "MSE", ("PATCH", "/projects/{p}"): "M", ("POST", "/projects/{p}/archive"): "M", ("POST", "/projects/{p}/restore"): "M",
    ("GET", "/projects/{p}/settings"): "M", ("PATCH", "/projects/{p}/settings"): "M",
    ("GET", "/projects/{p}/members"): "MS", ("POST", "/projects/{p}/members"): "M", ("PATCH", "/projects/{p}/members/{i}"): "M", ("DELETE", "/projects/{p}/members/{i}"): "M",
    ("GET", "/projects/{p}/invitations"): "M", ("POST", "/projects/{p}/invitations"): "M", ("DELETE", "/projects/{p}/invitations/{i}"): "M",
    ("POST", "/projects/{p}/schedule-imports"): "M", ("GET", "/projects/{p}/schedule-imports/{i}"): "M", ("DELETE", "/projects/{p}/schedule-imports/{i}"): "M",
    ("PUT", "/projects/{p}/schedule-imports/{i}/decisions"): "M", ("POST", "/projects/{p}/schedule-imports/{i}/build"): "M",
    ("GET", "/projects/{p}/schedule-versions"): "MSE", ("GET", "/projects/{p}/schedule-versions/compare"): "M", ("GET", "/projects/{p}/schedule-versions/{i}"): "MSE",
    ("DELETE", "/projects/{p}/schedule-versions/{i}"): "M", ("POST", "/projects/{p}/schedule-versions/{i}/activate"): "M",
    ("GET", "/projects/{p}/schedule-versions/{i}/wbs"): "MSE", ("GET", "/projects/{p}/schedule-versions/{i}/activities"): "MSE",
    ("GET", "/projects/{p}/activities"): "MSE", ("GET", "/projects/{p}/activities/{i}/timeline"): "MSE",
    ("POST", "/projects/{p}/documents"): "SE", ("GET", "/projects/{p}/documents"): "MSE", ("GET", "/projects/{p}/documents/{i}"): "MSE",
    ("GET", "/projects/{p}/documents/{i}/content"): "MSE", ("POST", "/projects/{p}/documents/{i}/extract"): "E",
    ("POST", "/projects/{p}/claims"): "E", ("GET", "/projects/{p}/my-claims"): "E", ("GET", "/projects/{p}/claims/{i}"): "MSE",
    ("POST", "/projects/{p}/claims/{i}/withdraw"): "E", ("POST", "/projects/{p}/claims/{i}/clarification-answer"): "E", ("POST", "/projects/{p}/claims/{i}/evidence"): "E",
    ("POST", "/projects/{p}/claims/{i}/correction"): "E",
    ("GET", "/projects/{p}/review-queue"): "S", ("POST", "/projects/{p}/claims/{i}/rematch"): "S", ("POST", "/projects/{p}/claims/{i}/bind-quantities"): "S",
    ("POST", "/projects/{p}/claims/{i}/decision-preview"): "S", ("POST", "/projects/{p}/claims/{i}/decision"): "S", ("POST", "/projects/{p}/claims/{i}/clarification-request"): "S",
    ("GET", "/projects/{p}/claim-counts"): "MS",
    ("POST", "/projects/{p}/issues"): "SE", ("GET", "/projects/{p}/issues"): "MSE", ("GET", "/projects/{p}/issues/{i}"): "MSE", ("GET", "/projects/{p}/blockers"): "MSE",
    ("POST", "/projects/{p}/issues/{i}/evidence"): "SE", ("POST", "/projects/{p}/issues/{i}/resolve"): "S", ("POST", "/projects/{p}/issues/{i}/root-cause"): "S",
    ("POST", "/projects/{p}/issues/{i}/memory"): "S", ("GET", "/projects/{p}/root-causes"): "MS", ("POST", "/projects/{p}/root-causes"): "S", ("GET", "/projects/{p}/memory"): "MSE",
    ("GET", "/projects/{p}/dashboard/summary"): "MSE", ("GET", "/projects/{p}/dashboard/wbs"): "MSE", ("GET", "/projects/{p}/dashboard/stages"): "MSE",
    ("GET", "/projects/{p}/dashboard/disciplines"): "MSE", ("GET", "/projects/{p}/dashboard/activities"): "MSE", ("GET", "/projects/{p}/dashboard/timeline"): "MSE",
    ("GET", "/projects/{p}/notifications"): "MSE", ("POST", "/projects/{p}/notifications/{i}/read"): "MSE",
    ("GET", "/projects/{p}/knowledge"): "MSE", ("POST", "/projects/{p}/knowledge"): "M", ("PUT", "/projects/{p}/knowledge/{i}"): "M", ("POST", "/projects/{p}/knowledge/{i}/retire"): "M",
    ("GET", "/projects/{p}/dashboard/compare"): "MS", ("GET", "/projects/{p}/audit"): "MS", ("GET", "/projects/{p}/audit/verify"): "MS",
}
GLOBAL = {("GET", "/health"): "public", ("GET", "/auth/config"): "public", ("POST", "/auth/local-login"): "public", ("GET", "/me"): "user", ("GET", "/projects"): "user", ("POST", "/projects"): "grant", ("POST", "/invitations/accept"): "user",
          ("POST", "/platform/grants"): "admin", ("DELETE", "/platform/grants/{user_id}/{capability}"): "admin", ("POST", "/platform/users/{user_id}/revoke-sessions"): "admin"}
GATE_CODES = {"PERMISSION_DENIED", "NOT_A_MEMBER", "UNAUTHENTICATED"}


def canonical(path: str) -> str:
    import re
    p = path.replace("/api/v2", "").replace("{project_id}", "{p}")
    parts = p.split("/")
    out = []
    for seg in parts:
        out.append("{i}" if (seg.startswith("{") and seg != "{p}") else seg)
    return "/".join(out)


def openapi_routes(client):
    o = client.get("/openapi.json").json()
    return {(m.upper(), canonical(p)) for p, v in o["paths"].items() for m in v}


def test_the_role_table_covers_exactly_the_routes_the_api_exposes(client):
    exposed = openapi_routes(client)
    listed = set(ROUTES) | {(m, canonical(p)) for (m, p) in GLOBAL}
    assert exposed - listed == set(), f"routes without an authorization entry: {sorted(exposed - listed)}"
    assert listed - exposed == set(), f"table entries for routes that do not exist: {sorted(listed - exposed)}"
    assert len(ROUTES) >= 60


@pytest.fixture
def personas(kit):
    w = kit.world
    from v2api import make_user
    suspended = make_user("suspended")
    with connect(system=True) as c:
        c.execute("insert into project_memberships (project_id, user_id, role, status) values (%s,%s,'SUPERVISOR','SUSPENDED')", (w.project, suspended.id))
    return {"M": w.pm, "S": w.sup, "E": w.se}, {"outsider": w.outsider, "other_project_pm": w.pm2, "suspended": suspended}


def call(api, method, path, kit, user):
    real = path.replace("{p}", str(kit.project)).replace("{i}", str(uuid.uuid4()))
    kw = {}
    if method in ("POST", "PUT", "PATCH"):
        kw["json"] = {}
    return api.call(method, "/api/v2" + real, user, **kw)


def code_of(r):
    try:
        return r.json().get("error", {}).get("code")
    except Exception:
        return None


def test_each_role_reaches_exactly_its_routes(kit, api, personas):
    roles, _ = personas
    for (method, path), allowed in ROUTES.items():
        for letter, user in roles.items():
            r = call(api, method, path, kit, user)
            if letter in allowed:
                assert code_of(r) not in GATE_CODES and r.status_code != 401, f"{letter} should reach {method} {path}: {r.status_code} {r.text[:150]}"
            else:
                assert r.status_code == 403 and code_of(r) == "PERMISSION_DENIED", f"{letter} must not reach {method} {path}: {r.status_code} {r.text[:150]}"


def test_non_members_suspended_users_and_anonymous_are_refused_everywhere(kit, api, personas):
    _, outsiders = personas
    for (method, path) in ROUTES:
        for who, user in outsiders.items():
            r = call(api, method, path, kit, user)
            assert r.status_code == 403 and code_of(r) == "NOT_A_MEMBER", f"{who} reached {method} {path}: {r.status_code} {r.text[:150]}"
        r = call(api, method, path, kit, None)
        assert r.status_code == 401 and code_of(r) == "UNAUTHENTICATED", f"anonymous reached {method} {path}"
    assert api.get("/health").status_code == 200
    for (method, path), kind in GLOBAL.items():
        if kind != "public":
            r = api.call(method, "/api/v2" + path.replace("{user_id}", str(uuid.uuid4())).replace("{capability}", "CREATE_PROJECT"))
            assert r.status_code == 401, (method, path)


def test_a_valid_token_for_a_deleted_membership_stops_working_immediately(kit, api):
    w = kit.world
    assert api.get(url(kit, "/dashboard/summary"), w.sup).status_code == 200
    with connect(system=True) as c:
        c.execute("update project_memberships set status = 'REMOVED' where project_id = %s and user_id = %s", (w.project, w.sup.id))
    r = api.get(url(kit, "/dashboard/summary"), w.sup)
    assert r.status_code == 403 and code_of(r) == "NOT_A_MEMBER"


def test_roles_do_not_carry_across_projects(kit, api):
    w = kit.world
    r = api.get(f"/api/v2/projects/{w.project2}/dashboard/summary", w.pm)
    assert r.status_code == 403 and code_of(r) == "NOT_A_MEMBER"
    r = api.get(f"/api/v2/projects/{w.project}/dashboard/summary", w.pm2)
    assert r.status_code == 403 and code_of(r) == "NOT_A_MEMBER"
