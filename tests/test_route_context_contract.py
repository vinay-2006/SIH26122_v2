"""
B2 contract (DB-free): every route of the app is either
  * PROJECT-scoped: depends on require_project_context / require_schedule_context (directly or via a gate),
  * or on the explicit allowlist below (health checks, user-level routes, the dev-only mock P6).
A new route that reads or writes project data without a project context fails this test.
"""
from fastapi.routing import APIRoute

from backend.main import app

USER_LEVEL = {  # authenticated, but not project-owned data
    ("GET", "/api/v1/auth/me"),
    ("GET", "/api/v1/projects"),      # the caller's own memberships
    ("POST", "/api/v1/projects"),     # creating a project makes the caller its owner
    ("POST", "/api/v1/reports/translate"),  # pure text translation, no stored data
}
PUBLIC = {
    ("GET", "/"), ("GET", "/health"), ("GET", "/health/db"), ("GET", "/health/ai"),
    ("GET", "/api/v1/intake/health"), ("GET", "/api/v1/claims/health"), ("GET", "/api/v1/claims/checks/health"),
    ("GET", "/api/v1/claims/matching/health"), ("GET", "/api/v1/dashboard/health"), ("GET", "/api/v1/decisions/health"),
    ("GET", "/api/v1/export/health"), ("GET", "/api/v1/schedule/health"), ("GET", "/api/v1/schedules/health"),
}
DEV_ONLY = {  # gated by AUTH_DEV_MODE, 404 otherwise (see routers/mock_p6.py)
    ("GET", "/api/v1/mock-p6/health"), ("GET", "/api/v1/mock-p6/received"), ("POST", "/api/v1/mock-p6/activities/{activity_id}"),
    # pre-authentication by nature; 404 unless SETUAI_LOCAL_DEMO_AUTH=1 AND the isolated-DB guard passes (tests/test_local_demo_auth.py)
    ("POST", "/api/v1/auth/local-login"),
}
PROJECT_DEPS = {"require_project_context", "require_schedule_context"}


def _routes():
    out = []

    def walk(router, prefix):
        for r in router.routes:
            if type(r).__name__ == "_IncludedRouter":
                walk(r.original_router, prefix + (getattr(r.include_context, "prefix", "") or ""))
            elif isinstance(r, APIRoute):
                out.append((prefix + r.path, r))

    walk(app.router, "")
    return out


def _dep_names(dependant, acc=None):
    acc = set() if acc is None else acc
    for d in dependant.dependencies:
        acc.add(getattr(d.call, "__qualname__", str(d.call)))
        _dep_names(d, acc)
    return acc


def test_every_route_is_project_scoped_or_explicitly_allowlisted():
    unscoped = []
    for path, r in _routes():
        deps = _dep_names(r.dependant)
        for method in r.methods - {"HEAD", "OPTIONS"}:
            key = (method, path)
            if deps & PROJECT_DEPS or key in USER_LEVEL or key in PUBLIC or key in DEV_ONLY:
                continue
            unscoped.append(key)
    assert unscoped == [], f"routes without a project context and not allowlisted: {sorted(unscoped)}"


def test_allowlists_have_no_stale_entries():
    live = {(m, p) for p, r in _routes() for m in r.methods - {"HEAD", "OPTIONS"}}
    stale = (USER_LEVEL | PUBLIC | DEV_ONLY) - live
    assert not stale, f"allowlisted routes that no longer exist: {sorted(stale)}"


def test_user_level_routes_are_authenticated():
    for path, r in _routes():
        for method in r.methods - {"HEAD", "OPTIONS"}:
            if (method, path) in USER_LEVEL:
                deps = _dep_names(r.dependant)
                assert any("get_current_user" in d for d in deps), (method, path)


def test_no_route_depends_on_the_removed_active_schedule_resolvers():
    import backend.routers.intake as intake

    assert not hasattr(intake, "_get_active_schedule_id")
    import importlib.util

    assert importlib.util.find_spec("backend.shared.schedule_context") is None
