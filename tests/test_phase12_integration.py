import os
from fastapi.testclient import TestClient

from backend.main import app
from backend.shared.p6 import P6RestAdapter
from backend.shared.seed import check_schedule_upload_endpoint, load_canonical_sample_data
from backend.smoke_test import (
    test_auth_me_endpoint_registration,
    test_database_connection,
    test_database_schema,
    test_approved_actuals_lookup,
    test_router_health_endpoints,
)


def test_root_health_endpoint():
    """Verify root GET /health returns 200 with {'status': 'ok'}."""
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_m6_router_health_endpoints(monkeypatch):
    """Verify all M6 router health endpoints respond with status 200 and their exact contract."""
    monkeypatch.setenv("AUTH_DEV_MODE", "true")  # mock P6 routes exist only in development mode
    client = TestClient(app)

    expected_health_endpoints = {
        "/api/v1/export/health": {"router": "export", "status": "ok"},
        "/api/v1/dashboard/health": {"router": "dashboard", "status": "ok"},
        "/api/v1/schedule/health": {"router": "schedule", "status": "ok"},
        "/api/v1/mock-p6/health": {"router": "mock-p6", "status": "ok"},
        "/api/v1/schedules/health": {"router": "schedules", "status": "ok"},
    }

    for path, expected_payload in expected_health_endpoints.items():
        resp = client.get(path)
        assert resp.status_code == 200, f"Health check failed for {path}: {resp.text}"
        assert resp.json() == expected_payload, f"Unexpected body for {path}: {resp.json()}"


def test_activities_and_auth_router_registration():
    """Verify authenticated / parameterized M6 routes are registered on the FastAPI app."""
    routes = list(app.openapi()["paths"].keys())
    assert "/api/v1/auth/me" in routes, "GET /api/v1/auth/me route is not registered"
    assert "/api/v1/activities/{activity_id}/history" in routes, (
        "GET /api/v1/activities/{activity_id}/history route is not registered"
    )
    assert "/api/v1/export/csv" in routes, "GET /api/v1/export/csv route is not registered"
    assert "/api/v1/dashboard/delay-reasons" in routes, "GET /api/v1/dashboard/delay-reasons is not registered"
    assert "/api/v1/dashboard/institutional-memory" in routes, (
        "GET /api/v1/dashboard/institutional-memory is not registered"
    )
    assert "/api/v1/dashboard/forecast" in routes, "GET /api/v1/dashboard/forecast is not registered"
    assert "/api/v1/schedule/{activity_id}/impact-preview" in routes, (
        "GET /api/v1/schedule/{activity_id}/impact-preview is not registered"
    )
    assert "/api/v1/mock-p6/activities/{activity_id}" in routes, (
        "POST /api/v1/mock-p6/activities/{activity_id} is not registered"
    )


def test_smoke_test_router_health_function_passes():
    """Verify backend.smoke_test.test_router_health_endpoints executes cleanly without error."""
    test_router_health_endpoints()
    test_auth_me_endpoint_registration()


def test_missing_database_url_handled_cleanly_in_smoke_test():
    """Verify absence of DATABASE_URL does not raise uncaught exceptions during smoke test checks."""
    orig_url = os.environ.get("DATABASE_URL")
    try:
        if "DATABASE_URL" in os.environ:
            del os.environ["DATABASE_URL"]

        # None of these should raise
        test_database_connection()
        test_database_schema()
        test_approved_actuals_lookup()
    finally:
        if orig_url is not None:
            os.environ["DATABASE_URL"] = orig_url


def test_missing_p6_base_url_causes_no_network_calls():
    """Verify P6RestAdapter with unset/empty base_url returns False without network activity."""
    adapter = P6RestAdapter(base_url="")
    result = adapter.push_actual(
        activity_id="PIP-PS3-WLD-024",
        actual_pct_complete=50.0,
    )
    assert result is False


def test_no_startup_seeding_side_effects():
    """Verify importing backend.main does not trigger sample seeding or create DB records."""
    # M1's real schedule router has merged, so production app now genuinely
    # has POST /api/v1/schedules -- the "no side effects on import" claim is
    # about the app never registering a startup seed handler, not about
    # whether the endpoint exists.
    assert check_schedule_upload_endpoint(app)
    # The application startup lifecycle has no seed event handlers
    startup_handlers = getattr(app, "on_startup", [])
    assert len(startup_handlers) == 0


def test_canonical_seed_runner_remains_blocked_without_m1_endpoint():
    """Verify load_canonical_sample_data halts cleanly with status BLOCKED when M1 endpoint is absent."""
    from fastapi import FastAPI

    absent_app = FastAPI()  # genuinely no schedules router, unlike `app`
    result = load_canonical_sample_data(app=absent_app)
    assert isinstance(result, dict)
    assert result.get("status") == "BLOCKED"
    assert "POST /api/v1/schedules" in result.get("reason", "")
    assert result.get("schedule_activities_seeded") == 0
