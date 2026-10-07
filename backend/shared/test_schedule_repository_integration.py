"""Live-database integration tests for M1 schedule persistence.

Opt-in: these tests write to and read from the actual PostgreSQL/Supabase
database configured via DATABASE_URL (see backend/.env.example) and are
skipped automatically when that database is not reachable. They are
intentionally separate from test_schedule_repository.py, which covers the
same module's guard logic without requiring any database.

Each test uses a schedule_id namespaced under "m1-integration-test-" and
deletes everything it wrote in a `finally` block, so runs never leave
residue in the database, whether or not they pass.

Run directly (requires a reachable DATABASE_URL):
    python backend/shared/test_schedule_repository_integration.py
"""

import uuid

import psycopg

from backend.shared.db import get_connection
from backend.shared.schedule import ScheduleParseResult, parse_schedule_csv
from backend.shared.schedule_repository import (
    ScheduleAlreadyExistsError,
    SchedulePersistenceError,
    list_schedule_dependencies,
    list_schedule_wbs_activities,
    save_schedule,
)
from backend.shared.schemas import Schedule, ScheduleActivity

_CSV_TEMPLATE = (
    "L1,L2,L3,L4,L5 Activity ID,L6 Task ID,Discipline,Activity,Unit,"
    "Planned Qty,Baseline Start,Baseline Finish,Prior Actual,Today Actual,"
    "Cumulative Actual,Progress Pct,Status\n"
    "North Field Utility Corridor,Pump Station 3 Tie In,Civil Works,"
    "Trench and Foundations,CIV-PS3-TR-0180,{activity_id}-01,Civil,"
    "Excavate utility trench CH 0+180 to CH 0+220,m,40,2026-08-14,2026-08-14,"
    "0,40,40,100.0,Complete\n"
    "North Field Utility Corridor,Pump Station 3 Tie In,Piping Works,"
    "Above Ground Piping,PIP-PS3-WLD-024,{activity_id}-02,Piping,"
    "Complete field weld joints for utility header,joints,24,2026-08-11,"
    "2026-08-15,20,2,22,91.7,Ongoing\n"
)


def _new_schedule_id() -> str:
    return f"m1-integration-test-{uuid.uuid4().hex[:12]}"


def _valid_case(schedule_id: str) -> tuple[Schedule, ScheduleParseResult]:
    csv_text = _CSV_TEMPLATE.format(activity_id=schedule_id)
    parse_result = parse_schedule_csv(csv_text, schedule_id=schedule_id)
    assert parse_result.is_valid, [e.describe() for e in parse_result.errors]
    schedule = Schedule(
        schedule_id=schedule_id,
        project_name="North Field Utility Corridor",
        data_date="2026-08-14",
        source_format="csv",
    )
    return schedule, parse_result


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


def test_persists_schedule_and_activities_with_correct_field_mapping():
    schedule_id = _new_schedule_id()
    schedule, parse_result = _valid_case(schedule_id)

    try:
        count = save_schedule(schedule, parse_result)
        assert count == 2

        with get_connection() as conn:
            schedule_row = conn.execute(
                "SELECT * FROM schedules WHERE schedule_id = %s", (schedule_id,)
            ).fetchone()
            activity_rows = conn.execute(
                "SELECT * FROM schedule_activities WHERE schedule_id = %s ORDER BY activity_id",
                (schedule_id,),
            ).fetchall()

        assert schedule_row["project_name"] == "North Field Utility Corridor"
        assert str(schedule_row["data_date"]) == "2026-08-14"
        assert schedule_row["source_format"] == "csv"

        assert len(activity_rows) == 2
        first = activity_rows[0]
        expected = parse_result.activities[0]
        assert first["activity_id"] == expected.activity_id
        assert first["activity_name"] == expected.activity_name
        assert first["wbs_code"] == expected.wbs_code
        # the stored discipline is the canonical code (migration 016 normalises spellings on write)
        assert first["discipline"] == expected.discipline.upper()
        assert first["location"] == expected.location
        assert first["asset_tag"] is None
        assert str(first["planned_start"]) == str(expected.planned_start)
        assert str(first["planned_finish"]) == str(expected.planned_finish)
        assert float(first["planned_quantity"]) == expected.planned_quantity
        assert first["uom"] == expected.uom
        assert float(first["baseline_pct_complete"]) == expected.baseline_pct_complete
    finally:
        _cleanup(schedule_id)

    print("✓ persists schedule + multiple activities with correct field mapping")


def test_duplicate_schedule_id_is_rejected():
    schedule_id = _new_schedule_id()
    schedule, parse_result = _valid_case(schedule_id)

    try:
        save_schedule(schedule, parse_result)

        try:
            save_schedule(schedule, parse_result)
            raised = False
        except ScheduleAlreadyExistsError:
            raised = True

        assert raised

        with get_connection() as conn:
            activity_rows = conn.execute(
                "SELECT COUNT(*) AS n FROM schedule_activities WHERE schedule_id = %s",
                (schedule_id,),
            ).fetchone()
        assert activity_rows["n"] == 2  # not duplicated by the rejected second import
    finally:
        _cleanup(schedule_id)

    print("✓ re-importing an existing schedule_id is rejected, not overwritten")


def test_transaction_rolls_back_on_activity_failure():
    schedule_id = _new_schedule_id()
    schedule, parse_result = _valid_case(schedule_id)

    # Bypass the parser's own duplicate-activity_id check to force a
    # database-level primary key violation on the second activity insert,
    # simulating a mid-import failure.
    broken_activity = parse_result.activities[0].model_copy(
        update={"activity_id": parse_result.activities[1].activity_id}
    )
    broken_result = ScheduleParseResult(
        schedule_id=schedule_id,
        activities=[parse_result.activities[1], broken_activity],
        errors=[],
    )

    try:
        try:
            save_schedule(schedule, broken_result)
            raised = False
        except SchedulePersistenceError:
            raised = True

        assert raised

        with get_connection() as conn:
            schedule_row = conn.execute(
                "SELECT 1 FROM schedules WHERE schedule_id = %s", (schedule_id,)
            ).fetchone()
            activity_row = conn.execute(
                "SELECT 1 FROM schedule_activities WHERE schedule_id = %s", (schedule_id,)
            ).fetchone()

        assert schedule_row is None  # schedule insert was rolled back too
        assert activity_row is None  # no partially-imported activities remain
    finally:
        _cleanup(schedule_id)

    print("✓ a mid-import failure rolls back the schedule row and all activities")


def test_persists_dependencies_and_they_are_retrievable():
    schedule_id = _new_schedule_id()
    csv_text = (
        "L1,L2,L3,L4,L5 Activity ID,L6 Task ID,Discipline,Activity,Unit,"
        "Planned Qty,Baseline Start,Baseline Finish,Predecessor Activity ID,Relationship Type\n"
        "North Field Utility Corridor,Pump Station 3 Tie In,Civil Works,Trench and Foundations,"
        "CIV-PS3-TR-0180,{activity_id}-01,Civil,Excavate utility trench,m,40,"
        "2026-08-14,2026-08-15,,\n"
        "North Field Utility Corridor,Pump Station 3 Tie In,Civil Works,Trench and Foundations,"
        "CIV-PS3-BF-0180,{activity_id}-02,Civil,Backfill utility trench,m,40,"
        "2026-08-16,2026-08-17,{activity_id}-01,FS\n"
    ).format(activity_id=schedule_id)

    parse_result = parse_schedule_csv(csv_text, schedule_id=schedule_id)
    assert parse_result.is_valid, [e.describe() for e in parse_result.errors]
    assert len(parse_result.dependencies) == 1
    schedule = Schedule(schedule_id=schedule_id, project_name="North Field Utility Corridor")

    try:
        save_schedule(schedule, parse_result)

        dependencies = list_schedule_dependencies(schedule_id)
        assert len(dependencies) == 1
        dep = dependencies[0]
        assert dep.predecessor_activity_id == f"{schedule_id}-01"
        assert dep.successor_activity_id == f"{schedule_id}-02"
        assert dep.relationship_type == "FS"
    finally:
        _cleanup(schedule_id)

    print("✓ dependencies are persisted transactionally with the schedule and retrievable from PostgreSQL")


_EXTRA_ACTIVITY_SQL = """
    INSERT INTO schedule_activities (
        schedule_id, activity_id, activity_name, wbs_code, discipline,
        location, planned_start, planned_finish
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""


def test_wbs_activities_excludes_null_empty_and_whitespace_wbs_code():
    """list_schedule_wbs_activities() (M1 Feature #30 WBS tree) must exclude
    NULL, empty-string, and whitespace-only wbs_code -- not just NULL. The
    normal CSV ingestion path (backend.shared.schedule) already normalizes a
    blank wbs_code column to NULL before it reaches this table, so an
    empty-string or whitespace-only value can only occur via a different
    ingestion path -- inserted directly here to prove the repository query
    itself (not just the parser) enforces the exclusion.
    """
    schedule_id = _new_schedule_id()
    schedule, parse_result = _valid_case(schedule_id)

    try:
        save_schedule(schedule, parse_result)

        with get_connection() as conn:
            conn.execute(
                _EXTRA_ACTIVITY_SQL,
                (schedule_id, f"{schedule_id}-empty", "Blank wbs_code activity", "",
                 "Civil", "North Field", "2026-08-14", "2026-08-14"),
            )
            conn.execute(
                _EXTRA_ACTIVITY_SQL,
                (schedule_id, f"{schedule_id}-whitespace", "Whitespace wbs_code activity", "   ",
                 "Civil", "North Field", "2026-08-14", "2026-08-14"),
            )
            conn.commit()

        rows = list_schedule_wbs_activities(schedule_id)
        activity_ids = {row["activity_id"] for row in rows}
        assert f"{schedule_id}-empty" not in activity_ids
        assert f"{schedule_id}-whitespace" not in activity_ids
        assert activity_ids == {f"{schedule_id}-01", f"{schedule_id}-02"}

        wbs_codes = [row["wbs_code"] for row in rows]
        assert wbs_codes == sorted(wbs_codes)  # deterministic ordering by wbs_code, then activity_id
    finally:
        _cleanup(schedule_id)

    print("✓ list_schedule_wbs_activities excludes NULL, empty-string, and whitespace-only wbs_code")


if __name__ == "__main__":
    print("Checking database availability (DATABASE_URL)...")
    if not _database_available():
        print(
            "Skipping M1 schedule persistence integration tests: no reachable "
            "database. Configure DATABASE_URL in the project .env to run them "
            "(see backend/.env.example)."
        )
    else:
        test_persists_schedule_and_activities_with_correct_field_mapping()
        test_duplicate_schedule_id_is_rejected()
        test_transaction_rolls_back_on_activity_failure()
        test_persists_dependencies_and_they_are_retrievable()
        test_wbs_activities_excludes_null_empty_and_whitespace_wbs_code()

        print("\nM1 schedule persistence integration tests passed.")
