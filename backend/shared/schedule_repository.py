"""M1 baseline schedule persistence.

Writes a validated ScheduleParseResult (backend.shared.schedule) and its
Schedule metadata (backend.shared.schemas.Schedule) into the existing
`schedules`, `schedule_activities`, and `schedule_dependencies` tables
(backend/models/schema.sql). Also provides the read-side lookups the M1
Schedule API layer (backend.routers.schedules) needs to serve baseline
schedule data back out of PostgreSQL.

Reuses backend.shared.db.get_connection() — no second DB abstraction, no ORM.
"""

from __future__ import annotations

from typing import Optional

import psycopg

from backend.shared.db import get_connection
from backend.shared.schedule import ScheduleParseResult
from backend.shared.schemas import Schedule, ScheduleActivity, ScheduleDependency


class ScheduleAlreadyExistsError(Exception):
    """A schedule_id that already exists in `schedules`.

    Duplicate imports are rejected rather than silently overwritten or
    merged, so an existing schedule's activities can never be silently
    corrupted by a re-import.
    """

    def __init__(self, schedule_id: str) -> None:
        self.schedule_id = schedule_id
        super().__init__(
            f"schedule_id {schedule_id!r} already exists; duplicate schedule "
            "imports are rejected rather than overwritten"
        )


class SchedulePersistenceError(Exception):
    """A database error occurred while persisting a schedule. Nothing was committed."""


_INSERT_SCHEDULE_SQL = """
    INSERT INTO schedules (schedule_id, project_name, data_date, source_format, project_id, active)
    VALUES (%(schedule_id)s, %(project_name)s, %(data_date)s, %(source_format)s, %(project_id)s, %(active)s)
"""

_INSERT_ACTIVITY_SQL = """
    INSERT INTO schedule_activities (
        schedule_id, activity_id, activity_name, wbs_code, discipline,
        location, asset_tag, planned_start, planned_finish,
        planned_quantity, uom, baseline_pct_complete,
        total_float, is_critical, project_id
    ) VALUES (
        %(schedule_id)s, %(activity_id)s, %(activity_name)s, %(wbs_code)s, %(discipline)s,
        %(location)s, %(asset_tag)s, %(planned_start)s, %(planned_finish)s,
        %(planned_quantity)s, %(uom)s, %(baseline_pct_complete)s,
        %(total_float)s, %(is_critical)s, %(project_id)s
    )
"""

_INSERT_DEPENDENCY_SQL = """
    INSERT INTO schedule_dependencies (
        dependency_id, schedule_id, predecessor_activity_id,
        successor_activity_id, relationship_type, lag_days
    ) VALUES (
        %(dependency_id)s, %(schedule_id)s, %(predecessor_activity_id)s,
        %(successor_activity_id)s, %(relationship_type)s, %(lag_days)s
    )
"""


def save_schedule(
    schedule: Schedule,
    parse_result: ScheduleParseResult,
    project_id: Optional[str] = None,
    activate: bool = True,
) -> int:
    """Persist a schedule, its activities, and its dependencies in a single transaction.

    `parse_result` must already be valid (parse_result.is_valid) — this
    function re-validates nothing; validation is backend.shared.schedule's
    job (including that every dependency's predecessor/successor already
    refer to activities in this same import). All rows are written
    atomically: if any insert fails (including an already-existing
    schedule_id), nothing is committed. Dependencies are optional — a
    schedule with zero dependencies is perfectly valid.

    `project_id` stamps the schedule and its activities with their owning project (V7 isolation);
    None keeps the V6 project-less behaviour for direct callers. `activate` sets schedules.active.

    Returns the number of activities persisted.

    Raises:
        ValueError: schedule/parse_result mismatch, unvalidated or empty
            parse_result.
        ScheduleAlreadyExistsError: schedule.schedule_id already exists.
        SchedulePersistenceError: any other database error.
    """
    if schedule.schedule_id != parse_result.schedule_id:
        raise ValueError(
            f"schedule.schedule_id ({schedule.schedule_id!r}) does not match "
            f"parse_result.schedule_id ({parse_result.schedule_id!r})"
        )

    if not parse_result.is_valid:
        raise ValueError(
            "cannot persist a schedule with validation errors: "
            f"{[e.describe() for e in parse_result.errors]}"
        )

    if not parse_result.activities:
        raise ValueError("cannot persist a schedule with zero activities")

    try:
        with get_connection() as conn:
            existing = conn.execute(
                "SELECT 1 FROM schedules WHERE schedule_id = %s",
                (schedule.schedule_id,),
            ).fetchone()
            if existing:
                raise ScheduleAlreadyExistsError(schedule.schedule_id)

            conn.execute(
                _INSERT_SCHEDULE_SQL,
                {**schedule.model_dump(), "project_id": project_id, "active": activate},
            )

            for activity in parse_result.activities:
                conn.execute(_INSERT_ACTIVITY_SQL, {**activity.model_dump(), "project_id": project_id})

            for dependency in parse_result.dependencies:
                conn.execute(_INSERT_DEPENDENCY_SQL, dependency.model_dump())

            conn.commit()
    except ScheduleAlreadyExistsError:
        raise
    except psycopg.Error as exc:
        raise SchedulePersistenceError(str(exc)) from exc

    return len(parse_result.activities)


_SELECT_SCHEDULE_SQL = """
    SELECT schedule_id, project_name, data_date, source_format
    FROM schedules
    WHERE schedule_id = %s
"""

_LIST_SCHEDULES_SQL = """
    SELECT schedule_id, project_name, data_date, source_format
    FROM schedules
    ORDER BY schedule_id
"""

_SELECT_ACTIVITY_COLUMNS_SQL = """
    SELECT
        schedule_id, activity_id, activity_name, wbs_code, discipline,
        location, asset_tag, planned_start, planned_finish,
        planned_quantity, uom, baseline_pct_complete,
        total_float, is_critical
    FROM schedule_activities
"""


def get_schedule(schedule_id: str) -> Optional[Schedule]:
    """Look up one schedule's metadata by schedule_id, or None if it does not exist."""
    with get_connection() as conn:
        row = conn.execute(_SELECT_SCHEDULE_SQL, (schedule_id,)).fetchone()

    return Schedule(**row) if row is not None else None


def get_active_schedule() -> Optional[Schedule]:
    """The schedule new claims are matched against: the most recently created one
    (the same rule routers/intake.py uses), or None when no schedule exists."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT schedule_id, project_name, data_date, source_format
            FROM schedules
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()

    return Schedule(**row) if row is not None else None


def list_schedules(project_id: Optional[str] = None) -> list[Schedule]:
    """List schedules ordered by schedule_id. With project_id, only that project's schedules
    (operational callers must always pass it); without it, every schedule (internal/V6 use only)."""
    with get_connection() as conn:
        if project_id is None:
            rows = conn.execute(_LIST_SCHEDULES_SQL).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT schedule_id, project_name, data_date, source_format
                FROM schedules WHERE project_id = %s ORDER BY schedule_id
                """,
                (project_id,),
            ).fetchall()

    return [Schedule(**row) for row in rows]


def list_active_schedules_for_project(project_id: str) -> list[Schedule]:
    """Every schedule flagged active for one project. V7 expects exactly one; callers must treat
    0 as 'none' and >1 as an ambiguity error, never pick one silently."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT schedule_id, project_name, data_date, source_format
            FROM schedules WHERE project_id = %s AND active ORDER BY schedule_id
            """,
            (project_id,),
        ).fetchall()
    return [Schedule(**row) for row in rows]


def list_schedule_activities(schedule_id: str) -> list[ScheduleActivity]:
    """List every activity belonging to a schedule, ordered by activity_id.

    Returns an empty list if the schedule has no activities (including if
    the schedule_id itself does not exist) — callers that need to
    distinguish "unknown schedule" from "schedule with no activities" should
    check get_schedule() first.
    """
    with get_connection() as conn:
        rows = conn.execute(
            _SELECT_ACTIVITY_COLUMNS_SQL + " WHERE schedule_id = %s ORDER BY activity_id",
            (schedule_id,),
        ).fetchall()

    return [ScheduleActivity(**row) for row in rows]


def get_schedule_activity(schedule_id: str, activity_id: str) -> Optional[ScheduleActivity]:
    """Look up one activity by (schedule_id, activity_id), or None if it does not exist."""
    with get_connection() as conn:
        row = conn.execute(
            _SELECT_ACTIVITY_COLUMNS_SQL + " WHERE schedule_id = %s AND activity_id = %s",
            (schedule_id, activity_id),
        ).fetchone()

    return ScheduleActivity(**row) if row is not None else None


_LIST_DEPENDENCIES_SQL = """
    SELECT dependency_id, schedule_id, predecessor_activity_id,
           successor_activity_id, relationship_type, lag_days
    FROM schedule_dependencies
    WHERE schedule_id = %s
    ORDER BY dependency_id
"""


def list_schedule_dependencies(schedule_id: str) -> list[ScheduleDependency]:
    """List every dependency belonging to a schedule.

    Returns an empty list both for a schedule with no dependencies and for
    an unknown schedule_id — same convention as list_schedule_activities();
    callers that need to distinguish the two should check get_schedule()
    first.
    """
    with get_connection() as conn:
        rows = conn.execute(_LIST_DEPENDENCIES_SQL, (schedule_id,)).fetchall()

    return [ScheduleDependency(**row) for row in rows]


_LIST_WBS_ACTIVITIES_SQL = """
    SELECT wbs_code, activity_id, planned_quantity
    FROM schedule_activities
    WHERE schedule_id = %s
      AND wbs_code IS NOT NULL
      AND TRIM(wbs_code) <> ''
    ORDER BY wbs_code, activity_id
"""


def list_schedule_wbs_activities(schedule_id: str) -> list[dict]:
    """List (wbs_code, activity_id, planned_quantity) rows for a schedule,
    for the M1 half of Feature #30 (WBS Granularity Bridge) — a read-only
    grouping of existing activities by wbs_code for M3's decomposition
    analysis. Never mutates schedule_activities.

    Activities with a NULL, empty, or whitespace-only wbs_code are excluded
    at the SQL level (never bucketed as "unassigned") — this covers both the
    normal case (a blank wbs_code is normalized to NULL by
    backend.shared.schedule's CSV parser before it ever reaches this table)
    and any literal empty/whitespace string that might reach this table via
    a different ingestion path.

    Ordered by wbs_code then activity_id so a caller grouping these rows in
    Python (backend.routers.schedules) gets deterministic group and
    within-group ordering for free, without a second sort.

    Returns an empty list both for a schedule with no groupable activities
    and for an unknown schedule_id — same convention as the other list_*
    functions in this module; callers that need to distinguish the two
    should check get_schedule() first.
    """
    with get_connection() as conn:
        rows = conn.execute(_LIST_WBS_ACTIVITIES_SQL, (schedule_id,)).fetchall()

    return list(rows)
