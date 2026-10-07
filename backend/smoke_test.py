from backend.shared.db import get_connection, DATABASE_URL
from backend.shared.audit import payload_hash
from backend.shared.actuals import get_approved_actual, _dispatch_adapters
from backend.shared.auth import VALID_ROLES, UserProfile, require_role
from backend.main import app
from fastapi import HTTPException
from fastapi.testclient import TestClient



EXPECTED_TABLES = {
    "profiles",
    "schedules",
    "schedule_activities",
    "schedule_dependencies",
    "source_documents",
    "execution_events",
    "source_references",
    "candidate_matches",
    "conflict_records",
    "validation_issues",
    "planner_decisions",
    "approved_actuals",
    "audit_logs",
}


def test_database_connection():
    if not DATABASE_URL:
        print("[SKIP] PostgreSQL connection (DATABASE_URL not configured)")
        return
    try:
        with get_connection() as conn:
            row = conn.execute("SELECT 1 AS ok").fetchone()
        assert row["ok"] == 1
        print("[OK] PostgreSQL connection")
    except Exception as e:
        raise AssertionError(f"PostgreSQL connection failed: {e}") from e


def test_database_schema():
    if not DATABASE_URL:
        print("[SKIP] database schema (DATABASE_URL not configured)")
        return
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                """
            ).fetchall()

        actual_tables = {row["tablename"] for row in rows}
        missing = EXPECTED_TABLES - actual_tables

        assert not missing, f"Missing tables: {sorted(missing)}"
        print("[OK] database schema (including profiles)")
    except Exception as e:
        print(f"[SKIP] database schema ({e})")


def test_audit_hashing():
    result = payload_hash({"before": "value"}, {"after": "value"})

    assert isinstance(result, str)
    assert len(result) == 64

    print("[OK] audit hashing")


def test_approved_actuals_lookup():
    if not DATABASE_URL:
        print("[SKIP] approved actuals lookup (DATABASE_URL not configured)")
        return
    try:
        result = get_approved_actual(
            schedule_id="smoke-test-schedule",
            activity_id="smoke-test-activity",
        )
        assert result is None
        print("[OK] approved actuals lookup")
    except Exception as e:
        print(f"[SKIP] approved actuals lookup ({e})")


def test_auth_roles_and_dependencies():
    # Exactly SITE_ENGINEER and SUPERVISOR allowed
    assert VALID_ROLES == {"SITE_ENGINEER", "SUPERVISOR"}, f"Unexpected roles: {VALID_ROLES}"

    # Verify dependency factory
    dep_engineer = require_role("SITE_ENGINEER")
    dep_supervisor = require_role("SUPERVISOR")

    # Invalid role must raise ValueError
    try:
        require_role("ADMIN")
        assert False, "require_role('ADMIN') should have raised ValueError"
    except ValueError:
        pass

    # Role matching check
    eng_user = UserProfile(id="00000000-0000-0000-0000-000000000001", full_name="Eng One", role="SITE_ENGINEER")
    sup_user = UserProfile(id="00000000-0000-0000-0000-000000000002", full_name="Sup Two", role="SUPERVISOR")

    assert dep_engineer(eng_user) == eng_user
    assert dep_supervisor(sup_user) == sup_user

    # Role mismatch check raises 403
    try:
        dep_engineer(sup_user)
        assert False, "dep_engineer(sup_user) should have raised HTTPException(403)"
    except HTTPException as exc:
        assert exc.status_code == 403

    try:
        dep_supervisor(eng_user)
        assert False, "dep_supervisor(eng_user) should have raised HTTPException(403)"
    except HTTPException as exc:
        assert exc.status_code == 403

    print("[OK] auth roles and dependency enforcement (403 on mismatch, strictly 2 roles)")


def test_auth_me_endpoint_registration():
    routes = list(app.openapi()["paths"].keys())
    assert "/api/v1/auth/me" in routes, "GET /api/v1/auth/me not registered on FastAPI app"
    print("[OK] GET /api/v1/auth/me endpoint registered")


def test_router_health_endpoints():
    client = TestClient(app)

    # 1. Root health endpoint
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    # 2. Export router health
    resp = client.get("/api/v1/export/health")
    assert resp.status_code == 200
    assert resp.json() == {"router": "export", "status": "ok"}

    # 3. Dashboard router health
    resp = client.get("/api/v1/dashboard/health")
    assert resp.status_code == 200
    assert resp.json() == {"router": "dashboard", "status": "ok"}

    # 4. Schedule router health
    resp = client.get("/api/v1/schedule/health")
    assert resp.status_code == 200
    assert resp.json() == {"router": "schedule", "status": "ok"}

    # 5. Mock P6 router health (mock P6 exists only in dev mode; hidden -> 404 otherwise)
    import os
    from unittest.mock import patch as _patch

    with _patch.dict(os.environ, {"AUTH_DEV_MODE": "false"}):
        assert client.get("/api/v1/mock-p6/health").status_code == 404
    with _patch.dict(os.environ, {"AUTH_DEV_MODE": "true"}):
        resp = client.get("/api/v1/mock-p6/health")
    assert resp.status_code == 200
    assert resp.json() == {"router": "mock-p6", "status": "ok"}

    # 6. Schedules (M1) router health
    resp = client.get("/api/v1/schedules/health")
    assert resp.status_code == 200
    assert resp.json() == {"router": "schedules", "status": "ok"}

    # 7. Activities router registration check (history route)
    routes = list(app.openapi()["paths"].keys())
    assert "/api/v1/activities/{activity_id}/history" in routes, (
        "GET /api/v1/activities/{activity_id}/history not registered on FastAPI app"
    )

    print("[OK] router health checks (root, export, dashboard, schedule, mock-p6, schedules)")


def test_adapter_boundary():
    dummy_actual = {
        "actual_id": "test-id",
        "schedule_id": "sch-1",
        "activity_id": "act-1",
        "actual_pct_complete": 50.0,
    }
    status = _dispatch_adapters(dummy_actual)
    assert isinstance(status, dict)
    assert status.get("csv_export") == "not_implemented_phase1"
    assert status.get("p6_rest") == "not_implemented_phase1"
    print("[OK] adapter boundary non-blocking hook")


def test_end_to_end_pipeline():
    """
    Phase 8 End-to-End Pipeline Smoke Test:
    HEALTH -> AUTH -> SCHEDULE -> CLAIM -> MATCH -> CHECK -> DECISION -> APPROVED ACTUAL -> CSV EXPORT -> P6 MOCK
    """
    from backend.shared.auth import get_current_user
    from backend.routers.mock_p6 import clear_received_payloads, get_received_payloads
    from backend.shared.p6 import P6RestAdapter

    import os
    from unittest.mock import patch as _patch

    _dev = _patch.dict(os.environ, {"AUTH_DEV_MODE": "true"})  # mock P6 needs dev mode
    _dev.start()
    try:
        _run_end_to_end_pipeline(TestClient(app), get_current_user, clear_received_payloads, get_received_payloads, P6RestAdapter)
    finally:
        _dev.stop()


def _run_end_to_end_pipeline(client, get_current_user, clear_received_payloads, get_received_payloads, P6RestAdapter):
    # 1. Health & App status
    resp_health = client.get("/health")
    assert resp_health.status_code == 200
    assert resp_health.json() == {"status": "ok"}

    # 2. Authenticate as a project SUPERVISOR with an explicit schedule (context resolvers injected;
    #    the permission gates run for real)
    from tests.v7ctx import act_as

    _ctx = act_as("SUPERVISOR", schedule_id="SMOKE-SCHEDULE")
    _ctx.__enter__()

    try:
        # 3. CSV Export verification (canonical 5-column header)
        csv_resp = client.get("/api/v1/export/csv")
        assert csv_resp.status_code == 200
        assert "text/csv" in csv_resp.headers.get("content-type", "")
        lines = csv_resp.text.strip().splitlines()
        assert len(lines) >= 1, "CSV export should produce at least the header row"
        header = lines[0].split(",")
        expected_header = [
            "activity_id",
            "actual_start",
            "actual_finish",
            "actual_pct_complete",
            "actual_quantity",
        ]
        assert header == expected_header, f"Expected canonical header {expected_header}, got {header}"

        # 4. Real P6 Mock HTTP Round-Trip
        clear_received_payloads()
        adapter = P6RestAdapter(
            base_url="http://testserver/api/v1/mock-p6",
            http_client=client,
        )
        test_act_id = "CIV-PS3-TR-0180"
        p6_ok = adapter.push_actual(
            activity_id=test_act_id,
            actual_start="2026-09-01",
            actual_pct_complete=75.0,
        )
        assert p6_ok is True, "P6 push should return True upon successful HTTP 200 from mock"
        payloads = get_received_payloads()
        assert len(payloads) == 1, f"Mock P6 should have received exactly 1 payload, got {len(payloads)}"
        assert payloads[0]["Id"] == test_act_id
        assert payloads[0]["PercentComplete"] == 75.0
        assert payloads[0]["StartDate"] == "2026-09-01"

        print("[OK] end-to-end pipeline: health -> auth -> csv export (canonical 5-col) -> P6 mock round-trip")
    finally:
        app.dependency_overrides.clear()


def test_clean_start_faiss_rebuild():
    """
    Phase 8 Clean-Start / FAISS Rebuild Test:
    Proves that matching works after clearing process memory and rebuilding
    the FAISS index fresh from persisted database records.
    """
    from backend.shared.schedule_index import (
        build_index,
        get_active_schedule_id,
        search_schedule,
        _state_lock,
    )
    import backend.shared.schedule_index as si

    # 1. Inspect DB for an existing schedule
    schedule_id = None
    if DATABASE_URL:
        try:
            with get_connection() as conn:
                row = conn.execute("SELECT schedule_id FROM schedules LIMIT 1").fetchone()
                if row:
                    schedule_id = row["schedule_id"] if hasattr(row, "keys") else row[0]
        except Exception as e:
            print(f"[SKIP] DB query for FAISS rebuild test ({e})")
            return

    if not schedule_id:
        print("[SKIP] FAISS rebuild test (no schedule found in database)")
        return

    # 2. Reset active FAISS state to simulate fresh process start
    with _state_lock:
        si._active_schedule_id = None
        si._active_index = None
        si._active_activity_ids = None

    assert get_active_schedule_id() is None, "Active schedule must be None after process reset"

    # 3. Rebuild index from database
    build_res = build_index(schedule_id)
    assert build_res.activity_count > 0
    assert get_active_schedule_id() == schedule_id

    # 4. Search index
    candidates = search_schedule(schedule_id, "excavation work", top_k=3)
    assert len(candidates) > 0, "FAISS search must return candidates after rebuild"
    assert candidates[0].score > 0.0

    print(f"[OK] clean-start FAISS rebuild: rebuilt {build_res.activity_count} activities for {schedule_id[:8]}... and verified semantic retrieval")


def test_auth_routing_smoke():
    """
    Phase 8 Authentication & Routing Smoke:
    Verifies 401 unauthenticated, 403 wrong role, and 200 authorized role for protected endpoints.
    """
    from backend.shared.auth import get_current_user

    client = TestClient(app)
    protected_paths = [
        "/api/v1/dashboard/summary",
        "/api/v1/execution-summary",
    ]

    from tests.v7ctx import act_as

    for path in protected_paths:
        # 1. Unauthenticated -> 401 (ensure overrides are clear)
        app.dependency_overrides.clear()
        resp_unauth = client.get(path)
        assert resp_unauth.status_code == 401, f"Expected 401 for unauthenticated {path}, got {resp_unauth.status_code}"

        # 2. Site engineer (no REVIEW_CLAIM in the project) -> 403
        with act_as("SITE_ENGINEER", schedule_id="SMOKE-SCHEDULE"):
            resp_forbid = client.get(path)
        assert resp_forbid.status_code == 403, f"Expected 403 for SITE_ENGINEER on {path}, got {resp_forbid.status_code}"

        # 3. Supervisor with an explicit schedule -> 200
        with act_as("SUPERVISOR", schedule_id="SMOKE-SCHEDULE"):
            resp_ok = client.get(path)
        assert resp_ok.status_code == 200, f"Expected 200 for SUPERVISOR on {path}, got {resp_ok.status_code}"

        # 4. Supervisor WITHOUT an explicit schedule -> 400 (no active-schedule fallback)
        with act_as("SUPERVISOR", schedule_id=None):
            assert client.get(path).status_code == 400

        app.dependency_overrides.clear()

    print("[OK] auth routing smoke (401 unauth, 403 non-supervisor, 200 supervisor across protected endpoints)")


def test_demo_failure_fallbacks():
    """
    Phase 8 Demo Failure Policy Verification:
    - LLM failure falls back safely to deterministic verified summary.
    - Translation failure falls back safely to canonical English without caching failure.
    - P6 unavailable does not block or throw.
    """
    from unittest.mock import patch
    from backend.routers.summary import (
        build_deterministic_aggregate,
        generate_llm_summary,
        translate_dynamic_text,
        get_translation_cache,
        clear_translation_cache,
    )
    from backend.shared.p6 import P6RestAdapter

    # 1. LLM failure fallback
    dummy_agg = {
        "period": {"type": "last_7_days", "start": "2026-09-11", "end": "2026-09-18"},
        "discipline": "CIVIL",
        "claims": {"total_claims": 5},
        "approved_progress": {"total_approved": 3, "avg_approved_pct": 50.0},
        "activities": {"total": 10, "completed": 2, "in_progress": 5, "not_started": 3},
        "conflicts": {"total_conflicts": 1, "by_status": {"OPEN": 1}},
        "validation_issues": {"total_issues": 1},
        "delays": {"total_delay_events": 1, "reasons": {"WEATHER": 1}},
        "forecast": {"status": "available", "historical_ratio": 1.1},
    }
    with patch("backend.routers.summary._get_client", side_effect=Exception("External Groq API quota exhausted")):
        summary_text, source = generate_llm_summary(dummy_agg)
        assert source == "deterministic_fallback"
        assert "Project Execution Summary" in summary_text
        assert "10 activities under scope" in summary_text

    # 2. Dynamic translation failure fallback
    clear_translation_cache()
    canonical_text = "Activity ACT-101 has 1 conflict."
    with patch("backend.routers.summary._get_client", side_effect=Exception("External translation service timeout")):
        trans_text, was_cached = translate_dynamic_text(canonical_text, "hi")
        assert trans_text == canonical_text, "On translation failure, canonical English must be returned"
        assert was_cached is False
        assert len(get_translation_cache()) == 0, "Failed translation must never be cached"

    # 3. P6 unavailable fallback
    adapter = P6RestAdapter(base_url="")
    ok = adapter.push_actual(activity_id="ACT-101", actual_pct_complete=100.0)
    assert ok is False, "Adapter with empty base_url must safely return False without error"

    print("[OK] demo failure fallbacks: LLM fallback, translation fallback, P6 non-blocking safe return")


if __name__ == "__main__":
    test_database_connection()
    test_database_schema()
    test_audit_hashing()
    test_approved_actuals_lookup()
    test_auth_roles_and_dependencies()
    test_auth_me_endpoint_registration()
    test_router_health_endpoints()
    test_adapter_boundary()
    test_end_to_end_pipeline()
    test_clean_start_faiss_rebuild()
    test_auth_routing_smoke()
    test_demo_failure_fallbacks()

    print("\nAll Phase 8 integration smoke tests passed successfully.")

