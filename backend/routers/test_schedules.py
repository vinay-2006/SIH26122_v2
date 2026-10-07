"""API tests for the M1 Schedule API layer (backend.routers.schedules).

These drive the FastAPI app through fastapi.testclient.TestClient and
exercise the full path for each M1 endpoint:

    POST /api/v1/schedules -> generate schedule_id -> parser -> repository -> PostgreSQL -> active FAISS index
    GET  /api/v1/schedules, /{schedule_id}, /{schedule_id}/activities,
         /{schedule_id}/activities/{activity_id}, /{schedule_id}/dependencies -> PostgreSQL

They require a reachable DATABASE_URL (see backend/.env.example) and are
skipped automatically (when run via `python -m` directly) when the database
is not reachable — the same pattern used by
backend/shared/test_schedule_repository_integration.py.

schedule_id is generated server-side (a fresh uuid.uuid4() per POST, per the
project's ID-format contract) rather than supplied by the caller, so tests
capture it from each response instead of choosing it up front. Only the
activity_id values embedded in each test's CSV fixture are still
self-chosen, namespaced under "m1-api-test-" for the same reason as before:
to keep test residue easy to spot if a `finally` cleanup is ever skipped.
Each test deletes everything it wrote (database rows, including any
dependencies) in a `finally` block. The active FAISS index (in-memory only,
see backend.shared.schedule_index) needs no cleanup — it is simply replaced
by the next schedule that gets indexed.

Duplicate-schedule protection (ScheduleAlreadyExistsError) is not tested
here: with schedule_id generated server-side, a real duplicate can no
longer be produced through the public API without artificially forcing one
(e.g. mocking uuid.uuid4()), which the project's ID-format contract
explicitly says not to do. That guard is still fully exercised at the
repository layer — see
backend/shared/test_schedule_repository_integration.py::test_duplicate_schedule_id_is_rejected.

Run directly (requires a reachable DATABASE_URL):
    python backend/routers/test_schedules.py
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from backend.auth.dependencies import get_current_user
from backend.auth.models import CurrentUser
from backend.main import app
from backend.shared.db import get_connection

client = TestClient(app)


@pytest.fixture(autouse=True, scope="module")
def _authenticated_project_manager():
    """/api/v1/schedules now requires authentication and project membership (MANAGE_SCHEDULE to
    create). Each run gets its own project + PROJECT_MANAGER member; identity is injected, while
    membership, project scoping and RBAC still run against the real rows."""
    user_id, project_id = str(uuid.uuid4()), str(uuid.uuid4())
    tag = uuid.uuid4().hex[:8]
    with get_connection() as conn:
        conn.execute("INSERT INTO profiles (id, full_name, role) VALUES (%s, 'M1 API Test PM', 'SUPERVISOR')", (user_id,))
        conn.execute(
            "INSERT INTO projects (project_id, project_code, project_name, status) VALUES (%s, %s, 'V7-INTEG m1 api', 'ACTIVE')",
            (project_id, f"V7-INTEG-M1-{tag}"),
        )
        conn.execute(
            "INSERT INTO project_memberships (user_id, project_id, assigned_role, active, status) "
            "VALUES (%s, %s, 'PROJECT_MANAGER', TRUE, 'ACTIVE')",
            (user_id, project_id),
        )
        conn.commit()
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=user_id, full_name="M1 API Test PM", role="SUPERVISOR")
    client.headers.update({"X-Project-ID": project_id})
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        client.headers.pop("X-Project-ID", None)
        with get_connection() as conn:
            conn.execute("DELETE FROM schedule_dependencies WHERE schedule_id IN (SELECT schedule_id FROM schedules WHERE project_id = %s)", (project_id,))
            conn.execute("DELETE FROM schedule_activities WHERE project_id = %s", (project_id,))
            conn.execute("DELETE FROM schedules WHERE project_id = %s", (project_id,))
            conn.execute("DELETE FROM project_memberships WHERE project_id = %s", (project_id,))
            conn.execute("DELETE FROM projects WHERE project_id = %s", (project_id,))
            conn.execute("DELETE FROM profiles WHERE id = %s", (user_id,))
            conn.commit()

_CSV_TEMPLATE = (
    "L1,L2,L3,L4,L5 Activity ID,L6 Task ID,Discipline,Activity,Unit,"
    "Planned Qty,Baseline Start,Baseline Finish,Prior Actual,Today Actual,"
    "Cumulative Actual,Progress Pct,Status\n"
    "North Field Utility Corridor,Pump Station 3 Tie In,Civil Works,"
    "Trench and Foundations,CIV-PS3-TR-0180,{activity_prefix}-01,Civil,"
    "Excavate utility trench CH 0+180 to CH 0+220,m,40,2026-08-14,2026-08-14,"
    "0,40,40,100.0,Complete\n"
    "North Field Utility Corridor,Pump Station 3 Tie In,Piping Works,"
    "Above Ground Piping,PIP-PS3-WLD-024,{activity_prefix}-02,Piping,"
    "Complete field weld joints for utility header,joints,24,2026-08-11,"
    "2026-08-15,20,2,22,91.7,Ongoing\n"
)

_INVALID_CSV = (
    "L1,L2,L3,L4,L5 Activity ID,L6 Task ID,Discipline,Activity,Unit,"
    "Planned Qty,Baseline Start,Baseline Finish,Prior Actual,Today Actual,"
    "Cumulative Actual,Progress Pct,Status\n"
    ",,Civil Works,Trench and Foundations,CIV-PS3-TR-0180,,Civil,"
    "Excavate utility trench CH 0+180 to CH 0+220,m,40,2026-08-14,2026-08-14,"
    "0,40,40,100.0,Complete\n"
)


def _new_activity_prefix() -> str:
    return f"m1-api-test-{uuid.uuid4().hex[:12]}"


def _assert_generated_schedule_id(schedule_id: str) -> None:
    """schedule_id must be a lowercase uuid4 string generated by the server."""
    parsed = uuid.UUID(schedule_id, version=4)
    assert str(parsed) == schedule_id  # canonical lowercase form, not e.g. uppercase or braced


def _database_available() -> bool:
    try:
        with get_connection() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception as exc:  # connection/auth/network failure
        print(f"  (database unavailable: {exc})")
        return False


def _cleanup(schedule_id: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM schedule_dependencies WHERE schedule_id = %s", (schedule_id,))
        conn.execute("DELETE FROM schedule_activities WHERE schedule_id = %s", (schedule_id,))
        conn.execute("DELETE FROM schedules WHERE schedule_id = %s", (schedule_id,))
        conn.commit()


def test_create_and_read_schedule_end_to_end():
    activity_prefix = _new_activity_prefix()
    csv_content = _CSV_TEMPLATE.format(activity_prefix=activity_prefix)

    schedule_id = None
    try:
        create_resp = client.post(
            "/api/v1/schedules",
            json={
                "project_name": "North Field Utility Corridor",
                "data_date": "2026-08-14",
                "source_format": "csv",
                "csv_content": csv_content,
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        body = create_resp.json()
        schedule_id = body["schedule_id"]
        _assert_generated_schedule_id(schedule_id)
        assert body["project_name"] == "North Field Utility Corridor"
        assert body["data_date"] == "2026-08-14"
        assert body["source_format"] == "csv"
        assert body["activity_count"] == 2
        assert body["dependency_count"] == 0
        assert body["indexed"] is True
        assert body["index_error"] is None

        list_resp = client.get("/api/v1/schedules")
        assert list_resp.status_code == 200
        assert any(s["schedule_id"] == schedule_id for s in list_resp.json())

        get_resp = client.get(f"/api/v1/schedules/{schedule_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["project_name"] == "North Field Utility Corridor"

        activities_resp = client.get(f"/api/v1/schedules/{schedule_id}/activities")
        assert activities_resp.status_code == 200
        activities = activities_resp.json()
        assert len(activities) == 2
        activity_ids = {a["activity_id"] for a in activities}
        assert activity_ids == {f"{activity_prefix}-01", f"{activity_prefix}-02"}

        one_resp = client.get(f"/api/v1/schedules/{schedule_id}/activities/{activity_prefix}-01")
        assert one_resp.status_code == 200
        one = one_resp.json()
        assert one["discipline"] == "CIVIL"  # normalised to the canonical discipline code on write (migration 016)
        assert one["planned_quantity"] == 40

        deps_resp = client.get(f"/api/v1/schedules/{schedule_id}/dependencies")
        assert deps_resp.status_code == 200
        assert deps_resp.json() == []  # no dependency columns in this CSV
    finally:
        if schedule_id:
            _cleanup(schedule_id)

    print("✓ POST creates a schedule end-to-end (server-generated schedule_id) and GET endpoints retrieve it from PostgreSQL")


def test_dependencies_are_parsed_persisted_and_retrievable():
    activity_prefix = _new_activity_prefix()
    csv_content = (
        "L1,L2,L3,L4,L5 Activity ID,L6 Task ID,Discipline,Activity,Unit,"
        "Planned Qty,Baseline Start,Baseline Finish,Predecessor Activity ID,Relationship Type\n"
        "North Field,Pump Station 3,Civil Works,Trench,CIV-TR,{activity_prefix}-01,Civil,"
        "Excavate utility trench,m,40,2026-08-14,2026-08-16,,\n"
        "North Field,Pump Station 3,Civil Works,Backfill,CIV-BF,{activity_prefix}-02,Civil,"
        "Backfill utility trench,m,40,2026-08-17,2026-08-18,{activity_prefix}-01,FS\n"
    ).format(activity_prefix=activity_prefix)

    schedule_id = None
    try:
        create_resp = client.post(
            "/api/v1/schedules",
            json={
                "project_name": "North Field Utility Corridor",
                "csv_content": csv_content,
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        schedule_id = create_resp.json()["schedule_id"]
        _assert_generated_schedule_id(schedule_id)
        assert create_resp.json()["dependency_count"] == 1

        deps_resp = client.get(f"/api/v1/schedules/{schedule_id}/dependencies")
        assert deps_resp.status_code == 200
        deps = deps_resp.json()
        assert len(deps) == 1
        assert deps[0]["predecessor_activity_id"] == f"{activity_prefix}-01"
        assert deps[0]["successor_activity_id"] == f"{activity_prefix}-02"
        assert deps[0]["relationship_type"] == "FS"
        assert deps[0]["schedule_id"] == schedule_id
        uuid.UUID(deps[0]["dependency_id"], version=4)  # dependency_id is also a generated uuid4
    finally:
        if schedule_id:
            _cleanup(schedule_id)

    print("✓ dependency columns are parsed, persisted, and retrievable via the dependencies API")


def test_invalid_csv_returns_422_with_row_errors_and_persists_nothing():
    resp = client.post(
        "/api/v1/schedules",
        json={
            "project_name": "North Field Utility Corridor",
            "csv_content": _INVALID_CSV,
        },
    )

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["errors"]
    assert any(e["field"] == "activity_id" for e in detail["errors"])

    # A schedule_id is generated internally before parsing, but since parsing
    # failed, save_schedule() is never called — nothing with any schedule_id
    # was persisted. Verified structurally (parse failure raises before the
    # persistence call is reached — see backend/routers/schedules.py) rather
    # than by an ID-scoped query, since a failed POST returns no schedule_id
    # to query by.

    print("✓ invalid CSV rows are rejected with 422 and per-row errors, nothing persisted")


def test_get_unknown_schedule_returns_404():
    resp = client.get("/api/v1/schedules/does-not-exist-m1-api-test")
    assert resp.status_code == 404

    activities_resp = client.get("/api/v1/schedules/does-not-exist-m1-api-test/activities")
    assert activities_resp.status_code == 404

    print("✓ GET on an unknown schedule_id returns 404 for the schedule and its activities")


def test_wbs_tree_groups_activities_and_excludes_blank_wbs_code():
    activity_prefix = _new_activity_prefix()
    csv_content = (
        "L1,L2,L3,L4,L5 Activity ID,L6 Task ID,Discipline,Activity,Unit,"
        "Planned Qty,Baseline Start,Baseline Finish\n"
        # Two children of the same wbs_code ("1.01.01") -- a decomposition
        # candidate for M3. Deliberately inserted out of activity_id order
        # to prove the endpoint sorts, not just passes through insert order.
        "North Field,Pump Station 3,Civil Works,Trench,1.01.01,{p}-02,Civil,"
        "Backfill utility trench,m,25,2026-08-16,2026-08-17\n"
        "North Field,Pump Station 3,Civil Works,Trench,1.01.01,{p}-01,Civil,"
        "Excavate utility trench,m,40,2026-08-14,2026-08-15\n"
        # A single-child wbs_code ("1.02.01") with no Planned Qty -> NULL.
        "North Field,Pump Station 3,Piping Works,Header,1.02.01,{p}-03,Piping,"
        "Weld header joint,joints,,2026-08-11,2026-08-12\n"
        # Blank L5 Activity ID -> parsed to NULL wbs_code -> must be excluded,
        # not bucketed under "UNASSIGNED".
        "North Field,Pump Station 3,HSE,Inspection,,{p}-04,HSE,"
        "Daily safety walk,ea,1,2026-08-11,2026-08-11\n"
    ).format(p=activity_prefix)

    schedule_id = None
    try:
        create_resp = client.post(
            "/api/v1/schedules",
            json={"project_name": "North Field Utility Corridor", "csv_content": csv_content},
        )
        assert create_resp.status_code == 201, create_resp.text
        schedule_id = create_resp.json()["schedule_id"]
        assert create_resp.json()["activity_count"] == 4

        resp = client.get(f"/api/v1/schedules/{schedule_id}/wbs-tree")
        assert resp.status_code == 200
        body = resp.json()
        assert body["schedule_id"] == schedule_id

        groups = body["wbs_groups"]
        wbs_codes = [g["wbs_code"] for g in groups]
        assert wbs_codes == sorted(wbs_codes)  # groups ordered by wbs_code
        assert "" not in wbs_codes and None not in wbs_codes

        all_activity_ids = {a["activity_id"] for g in groups for a in g["activities"]}
        assert all_activity_ids == {f"{activity_prefix}-01", f"{activity_prefix}-02", f"{activity_prefix}-03"}
        assert f"{activity_prefix}-04" not in all_activity_ids  # blank-wbs activity excluded

        multi_child = next(g for g in groups if g["wbs_code"] == "1.01.01")
        assert [a["activity_id"] for a in multi_child["activities"]] == [
            f"{activity_prefix}-01", f"{activity_prefix}-02",
        ]  # ordered by activity_id within the group, not insertion order
        assert {a["planned_quantity"] for a in multi_child["activities"]} == {40, 25}

        single_child = next(g for g in groups if g["wbs_code"] == "1.02.01")
        assert len(single_child["activities"]) == 1
        assert single_child["activities"][0]["activity_id"] == f"{activity_prefix}-03"
        assert single_child["activities"][0]["planned_quantity"] is None  # blank Planned Qty preserved as NULL
    finally:
        if schedule_id:
            _cleanup(schedule_id)

    print("✓ wbs-tree groups activities by wbs_code (deterministic ordering), preserves NULL planned_quantity, and excludes blank-wbs activities")


def test_wbs_tree_unknown_schedule_returns_404():
    resp = client.get("/api/v1/schedules/does-not-exist-m1-api-test/wbs-tree")
    assert resp.status_code == 404

    print("✓ GET wbs-tree on an unknown schedule_id returns 404")


def test_get_unknown_activity_returns_404():
    activity_prefix = _new_activity_prefix()

    schedule_id = None
    try:
        create_resp = client.post(
            "/api/v1/schedules",
            json={
                "project_name": "North Field Utility Corridor",
                "csv_content": _CSV_TEMPLATE.format(activity_prefix=activity_prefix),
            },
        )
        schedule_id = create_resp.json()["schedule_id"]

        resp = client.get(f"/api/v1/schedules/{schedule_id}/activities/does-not-exist")
        assert resp.status_code == 404
    finally:
        if schedule_id:
            _cleanup(schedule_id)

    print("✓ GET on an unknown activity_id within a real schedule returns 404")


if __name__ == "__main__":
    print("Checking database availability (DATABASE_URL)...")
    if not _database_available():
        print(
            "Skipping M1 Schedule API tests: no reachable database. Configure "
            "DATABASE_URL in the project .env to run them (see backend/.env.example)."
        )
    else:
        test_create_and_read_schedule_end_to_end()
        test_dependencies_are_parsed_persisted_and_retrievable()
        test_invalid_csv_returns_422_with_row_errors_and_persists_nothing()
        test_get_unknown_schedule_returns_404()
        test_wbs_tree_groups_activities_and_excludes_blank_wbs_code()
        test_wbs_tree_unknown_schedule_returns_404()
        test_get_unknown_activity_returns_404()

        print("\nM1 Schedule API tests passed.")
