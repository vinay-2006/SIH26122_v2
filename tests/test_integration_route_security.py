"""
Route-level security for the previously unauthenticated legacy routes, against the ISOLATED
integration database (DB_WRITE + INTEGRATION; guarded by the root conftest).

Authentication is exercised for real (no token -> 401). For authorization, the identity is injected via
dependency override but membership, project derivation and RBAC run against real rows, so the checks
are the production code paths.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

import backend.main  # noqa: F401  (import order: avoids a pre-existing repositories<->services circular import)
from backend.auth.dependencies import get_current_user
from backend.auth.models import CurrentUser
from backend.main import app
from backend.routers.test_schedules import _CSV_TEMPLATE
from tests.test_integration_rls_and_audit_chain import world  # noqa: F401  (shared fixture)

pytestmark = [pytest.mark.integration]


@pytest.fixture()
def anon():
    app.dependency_overrides.pop(get_current_user, None)
    return TestClient(app, raise_server_exceptions=False)


_HOME = {"uA": "A", "uB": "B", "uN": "A"}  # the project each identity would select in the UI


def _as(world, key, project=None, role="SITE_ENGINEER"):
    """Authenticated as `key` (identity injected; membership/RBAC/context are the real code paths).
    Sends the explicit X-Project-ID a client must always send (project=False sends none)."""
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=world[key], full_name=key, role=role)
    c = TestClient(app, raise_server_exceptions=False)
    if project is not False:
        c.headers.update({"X-Project-ID": world[project or _HOME[key]]})
    return c


@pytest.fixture(autouse=True)
def _cleanup_override():
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _code(resp):
    d = resp.json().get("detail")
    return d.get("error_code") if isinstance(d, dict) else None


# ------------------------------------------------------------------ 401: no credentials

@pytest.mark.parametrize("method,path", [
    ("get", "/api/v1/schedules"),
    ("get", "/api/v1/schedules/active"),
    ("get", "/api/v1/schedules/SCH-ANY"),
    ("get", "/api/v1/schedules/SCH-ANY/activities"),
    ("get", "/api/v1/schedules/SCH-ANY/activities/ACT-1"),
    ("get", "/api/v1/schedules/SCH-ANY/dependencies"),
    ("get", "/api/v1/schedules/SCH-ANY/wbs-tree"),
    ("post", "/api/v1/schedules"),
    ("get", "/api/v1/claims/EVT-ANY/candidates"),
])
def test_unauthenticated_is_401(anon, world, method, path):
    kwargs = {"json": {"project_name": "x", "csv_content": "x"}} if method == "post" else {}
    resp = getattr(anon, method)(path, **kwargs)
    assert resp.status_code == 401, (path, resp.status_code, resp.text)


def test_invalid_token_is_401(anon, world):
    resp = anon.get("/api/v1/schedules", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


# ------------------------------------------------------------------ schedules: project isolation

def test_member_reads_own_schedule_but_not_another_projects(world):
    a = _as(world, "uA")
    assert a.get(f"/api/v1/schedules/{world['sA']}/activities").status_code == 200
    assert a.get(f"/api/v1/schedules/{world['sA']}").status_code == 200
    for suffix in ("", "/activities", "/activities/ACT-1", "/dependencies", "/wbs-tree"):
        r = a.get(f"/api/v1/schedules/{world['sB']}{suffix}")
        assert r.status_code == 403, (suffix, r.status_code)
        assert _code(r) == "SCHEDULE_ACCESS_DENIED"


def test_user_without_membership_is_denied(world):
    n = _as(world, "uN")
    assert n.get(f"/api/v1/schedules/{world['sA']}/activities").status_code == 403
    assert n.get("/api/v1/schedules").status_code == 403


def test_unknown_schedule_is_404_for_a_member(world):
    a = _as(world, "uA")
    assert a.get("/api/v1/schedules/NO-SUCH-SCHEDULE/activities").status_code == 404


def test_header_cannot_substitute_the_path_schedule(world):
    """Authorize own schedule via X-Schedule-ID while the path names another project's schedule."""
    a = _as(world, "uA")
    r = a.get(f"/api/v1/schedules/{world['sB']}/activities", headers={"X-Schedule-ID": world["sA"]})
    assert r.status_code == 400 and _code(r) == "INVALID_SCHEDULE_CONTEXT", r.text
    r = a.get(f"/api/v1/schedules/{world['sB']}/activities?schedule_id={world['sA']}")
    assert r.status_code == 400 and _code(r) == "INVALID_SCHEDULE_CONTEXT", r.text


def test_header_cannot_substitute_the_path_project(world):
    a = _as(world, "uA")
    # path names project B, header claims project A (which uA IS a member of)
    r = a.get(f"/api/v1/projects/{world['B']}/schedules", headers={"X-Project-ID": world["A"]})
    assert r.status_code in (400, 403) and r.status_code != 200, r.text
    assert _code(r) in ("INVALID_PROJECT_CONTEXT", "PROJECT_ACCESS_DENIED"), r.text


def test_list_schedules_is_scoped_to_callers_project(world):
    a, b = _as(world, "uA"), None
    ids_a = {s["schedule_id"] for s in a.get("/api/v1/schedules").json()}
    assert world["sA"] in ids_a and world["sB"] not in ids_a
    b = _as(world, "uB")
    ids_b = {s["schedule_id"] for s in b.get("/api/v1/schedules").json()}
    assert world["sB"] in ids_b and world["sA"] not in ids_b


def test_explicit_foreign_project_context_is_denied(world):
    a = _as(world, "uA")
    r = a.get("/api/v1/schedules", headers={"X-Project-ID": world["B"]})
    assert r.status_code == 403 and _code(r) == "PROJECT_ACCESS_DENIED"


def test_no_project_context_is_rejected_even_for_a_single_project_member(world):
    """There is no implicit 'the only project you belong to'. Every project-owned route needs an explicit project."""
    a = _as(world, "uA", project=False)
    for path in ("/api/v1/schedules", "/api/v1/schedules/active", f"/api/v1/schedules/{world['sA']}/activities",
                 f"/api/v1/activities?schedule_id={world['sA']}", f"/api/v1/claims/{world['evA1']}/candidates",
                 f"/api/v1/claims?schedule_id={world['sA']}", "/api/v1/audit", "/api/v1/decisions"):
        r = a.get(path)
        assert r.status_code == 400 and _code(r) == "INVALID_PROJECT_CONTEXT", (path, r.status_code, r.text)


# ------------------------------------------------------------------ /schedules/active: no silent pick

def test_active_schedule_is_the_projects_and_ambiguity_is_a_conflict(world):
    su = world["su"]
    a = _as(world, "uA")
    r = a.get("/api/v1/schedules/active")
    assert r.status_code == 200 and r.json()["schedule_id"] == world["sA"]
    extra = f"SCH-A2-{uuid.uuid4().hex[:6]}"
    su.execute("INSERT INTO schedules (schedule_id, project_name, project_id, active) VALUES (%s, 'dup', %s, TRUE)", (extra, world["A"]))
    try:
        r = a.get("/api/v1/schedules/active")
        assert r.status_code == 409, "two active schedules must be reported, never silently picked"
    finally:
        su.execute("DELETE FROM schedules WHERE schedule_id = %s", (extra,))
    su.execute("UPDATE schedules SET active = FALSE WHERE schedule_id = %s", (world["sA"],))
    try:
        assert a.get("/api/v1/schedules/active").status_code == 404
    finally:
        su.execute("UPDATE schedules SET active = TRUE WHERE schedule_id = %s", (world["sA"],))


# ------------------------------------------------------------------ POST /schedules: role + project stamping

def test_create_schedule_requires_manage_permission_and_stamps_project(world, monkeypatch):
    su = world["su"]
    monkeypatch.setattr("backend.routers.schedules.schedule_index.build_index", lambda *_a, **_k: None)
    body = {"project_name": "V7-INTEG created", "csv_content": _CSV_TEMPLATE.format(activity_prefix="v7integ")}
    a = _as(world, "uA")  # SUPERVISOR: has no MANAGE_SCHEDULE
    r = a.post("/api/v1/schedules", json=body)
    assert r.status_code == 403 and _code(r) == "PERMISSION_DENIED", r.text

    su.execute("UPDATE project_memberships SET assigned_role = 'PROJECT_MANAGER' WHERE user_id = %s", (world["uA"],))
    created = None
    try:
        r = a.post("/api/v1/schedules", json=body)
        assert r.status_code == 201, r.text
        created = r.json()["schedule_id"]
        row = su.execute("SELECT project_id, active FROM schedules WHERE schedule_id = %s", (created,)).fetchone()
        assert str(row["project_id"]) == world["A"]
        assert row["active"] is False, "project already has an active schedule; the new one must not displace it"
        acts = su.execute("SELECT DISTINCT project_id FROM schedule_activities WHERE schedule_id = %s", (created,)).fetchall()
        assert [str(x["project_id"]) for x in acts] == [world["A"]]
        assert _as(world, "uB").get(f"/api/v1/schedules/{created}/activities").status_code == 403
    finally:
        su.execute("UPDATE project_memberships SET assigned_role = 'SUPERVISOR' WHERE user_id = %s", (world["uA"],))
        if created:
            su.execute("DELETE FROM schedule_dependencies WHERE schedule_id = %s", (created,))
            su.execute("DELETE FROM schedule_activities WHERE schedule_id = %s", (created,))
            su.execute("DELETE FROM schedules WHERE schedule_id = %s", (created,))


# ------------------------------------------------------------------ /claims/{id}/candidates

def test_candidates_authorization(world):
    su = world["su"]
    url = f"/api/v1/claims/{world['evA1']}/candidates"
    assert _as(world, "uA").get(url).status_code == 200
    r = _as(world, "uB").get(url)
    assert r.status_code == 403 and _code(r) == "PROJECT_ACCESS_DENIED"
    assert _as(world, "uN").get(url).status_code == 403
    assert _as(world, "uA").get("/api/v1/claims/NO-SUCH-EVENT/candidates").status_code == 404
    null_ev = str(uuid.uuid4())
    su.execute(
        "INSERT INTO execution_events (event_id, schedule_id, project_id, raw_claim_text, event_date, input_channel, status) "
        "VALUES (%s, %s, NULL, 'legacy', '2026-01-05', 'TYPED_TEXT', 'EXTRACTED')", (null_ev, world["sA"]))
    try:
        assert _as(world, "uA").get(f"/api/v1/claims/{null_ev}/candidates").status_code == 403, "project-less legacy events are unreachable"
    finally:
        su.execute("DELETE FROM execution_events WHERE event_id = %s", (null_ev,))


def test_candidates_suspended_membership_denied(world):
    su = world["su"]
    su.execute("UPDATE project_memberships SET active = FALSE WHERE user_id = %s", (world["uA"],))
    try:
        assert _as(world, "uA").get(f"/api/v1/claims/{world['evA1']}/candidates").status_code == 403
    finally:
        su.execute("UPDATE project_memberships SET active = TRUE WHERE user_id = %s", (world["uA"],))


# ------------------------------------------------------------------ /activities: no silent schedule selection

def _as_supervisor(world, key, project=None):
    return _as(world, key, project=project, role="SUPERVISOR")


def test_activities_require_explicit_schedule(world):
    a = _as_supervisor(world, "uA")
    for path in ("/api/v1/activities", "/api/v1/activities/ACT-1/history"):
        r = a.get(path)
        assert r.status_code == 400 and _code(r) == "INVALID_SCHEDULE_CONTEXT", (path, r.status_code, r.text)


def test_activities_are_project_scoped(world):
    a = _as_supervisor(world, "uA")
    assert a.get(f"/api/v1/activities?schedule_id={world['sA']}").status_code == 200
    assert a.get(f"/api/v1/activities/ACT-1/history?schedule_id={world['sA']}").status_code == 200
    # the same activity id exists in B's schedule; A must not reach it by any route
    for path in (f"/api/v1/activities?schedule_id={world['sB']}",
                 f"/api/v1/activities/ACT-1/history?schedule_id={world['sB']}"):
        r = a.get(path)
        assert r.status_code == 403 and _code(r) == "SCHEDULE_ACCESS_DENIED", (path, r.status_code)
    n = _as_supervisor(world, "uN")
    assert n.get(f"/api/v1/activities?schedule_id={world['sA']}").status_code == 403


def test_activity_history_no_cross_schedule_lookup_by_activity_id(world):
    """Old behaviour: an activity_id found in exactly one OTHER schedule was returned anyway."""
    su = world["su"]
    only_b = f"ONLY-B-{uuid.uuid4().hex[:6]}"
    su.execute(
        "INSERT INTO schedule_activities (activity_id, schedule_id, activity_name, discipline, location, "
        "planned_start, planned_finish, project_id) VALUES (%s, %s, 'b only', 'CIVIL', 'x', '2026-01-01', '2026-02-01', %s)",
        (only_b, world["sB"], world["B"]))
    try:
        r = _as_supervisor(world, "uA").get(f"/api/v1/activities/{only_b}/history?schedule_id={world['sA']}")
        assert r.status_code == 404, "an activity that lives in another project's schedule must not resolve"
    finally:
        su.execute("DELETE FROM schedule_activities WHERE activity_id = %s", (only_b,))


# ------------------------------------------------------------------ activity attribution (schedule-scoped, validated)

def _attr_url(world, schedule, activity="ACT-1", project="A"):
    return f"/api/v1/projects/{world[project]}/schedules/{world[schedule]}/activities/{activity}/attribution"


def test_attribution_requires_manage_schedule_and_is_validated(world):
    su = world["su"]
    con_a, con_b = str(uuid.uuid4()), str(uuid.uuid4())
    su.execute("INSERT INTO contractors (contractor_id, project_id, contractor_code, company_name) VALUES (%s, %s, 'C-A', 'A Co')", (con_a, world["A"]))
    su.execute("INSERT INTO contractors (contractor_id, project_id, contractor_code, company_name) VALUES (%s, %s, 'C-B', 'B Co')", (con_b, world["B"]))
    wp_a = str(uuid.uuid4())
    su.execute("INSERT INTO work_packages (work_package_id, project_id, contractor_id, package_name) VALUES (%s, %s, %s, 'WP A')", (wp_a, world["A"], con_a))
    try:
        # SUPERVISOR has no MANAGE_SCHEDULE
        r = _as(world, "uA").patch(_attr_url(world, "sA"), json={"contractor_id": con_a})
        assert r.status_code == 403 and _code(r) == "PERMISSION_DENIED"
        su.execute("UPDATE project_memberships SET assigned_role = 'PLANNER' WHERE user_id = %s", (world["uA"],))
        a = _as(world, "uA")
        # another project's contractor cannot be referenced (would be a cross-project link)
        assert a.patch(_attr_url(world, "sA"), json={"contractor_id": con_b}).status_code == 404
        assert a.patch(_attr_url(world, "sA"), json={"work_package_id": str(uuid.uuid4())}).status_code == 404
        assert a.patch(_attr_url(world, "sA"), json={"stage_id": str(uuid.uuid4())}).status_code == 404
        # a work package alone derives its contractor
        r = a.patch(_attr_url(world, "sA"), json={"work_package_id": wp_a})
        assert r.status_code == 200 and r.json()["contractor_id"] == con_a
        # work package and contractor must agree
        con_a2 = str(uuid.uuid4())
        su.execute("INSERT INTO contractors (contractor_id, project_id, contractor_code, company_name) VALUES (%s, %s, 'C-A2', 'A2 Co')", (con_a2, world["A"]))
        assert a.patch(_attr_url(world, "sA"), json={"work_package_id": wp_a, "contractor_id": con_a2}).status_code == 409
        # explicit null clears; omitted fields are untouched
        r = a.patch(_attr_url(world, "sA"), json={"work_package_id": None})
        assert r.status_code == 200 and r.json()["work_package_id"] is None and r.json()["contractor_id"] == con_a
        # another project's schedule / activity in another schedule
        assert a.patch(_attr_url(world, "sB"), json={"contractor_id": con_a}).status_code == 403
        assert a.patch(_attr_url(world, "sA", "NO-SUCH"), json={"contractor_id": con_a}).status_code == 404
        # the change is audited in the project chain and changed ONLY this schedule's activity
        assert su.execute("SELECT count(*) AS n FROM audit_logs WHERE project_id = %s AND action = 'ATTRIBUTION_UPDATED'", (world["A"],)).fetchone()["n"] >= 2
        assert su.execute("SELECT contractor_id FROM schedule_activities WHERE schedule_id = %s AND activity_id = 'ACT-1'", (world["sB"],)).fetchone()["contractor_id"] is None
    finally:
        su.execute("UPDATE project_memberships SET assigned_role = 'SUPERVISOR' WHERE user_id = %s", (world["uA"],))
        su.execute("UPDATE schedule_activities SET contractor_id = NULL, work_package_id = NULL WHERE schedule_id = %s", (world["sA"],))
        su.execute("DELETE FROM work_packages WHERE project_id IN (%s, %s)", (world["A"], world["B"]))
        su.execute("DELETE FROM contractors WHERE project_id IN (%s, %s)", (world["A"], world["B"]))
