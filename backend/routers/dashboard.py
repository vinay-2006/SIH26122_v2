import logging
from datetime import date, datetime, timedelta
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.context import gates
from backend.context.project import ProjectContext
from backend.context.schedule import ScheduleContext
from backend.shared.db import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


def query_delay_reason_aggregates(conn: Optional[Any] = None, schedule_id: Optional[str] = None) -> dict:
    """
    Query execution_events joined with planner_decisions to aggregate delay reasons
    for approved claims.

    Core business rules:
    1. For every execution_event, find its latest planner_decisions row using:
       decided_at DESC, decision_id DESC
    2. Only APPROVE and EDIT count. REJECT and HOLD do not count.
    3. Exclude NULL, empty string, and whitespace-only delay reasons.
    4. Group using TRIM(delay_reason).
    5. Sort by count DESC, delay_reason ASC.

    schedule_id: when given, restricts to that schedule's claims only (see
    module docstring / ISS-05 — a caller with no schedule_id argument at all
    gets the pre-existing unscoped aggregate, used by unit tests that seed a
    single-schedule fixture; the live /summary and /delay-reasons endpoints
    always pass the resolved active schedule so results never mix schedules).
    """
    query = """
        SELECT
            TRIM(ee.delay_reason) AS delay_reason,
            COUNT(*) AS count
        FROM execution_events ee
        JOIN planner_decisions pd ON pd.event_id = ee.event_id
        WHERE pd.decision_id = (
            SELECT pd2.decision_id
            FROM planner_decisions pd2
            WHERE pd2.event_id = ee.event_id
            ORDER BY pd2.decided_at DESC, pd2.decision_id DESC
            LIMIT 1
        )
        AND pd.action IN ('APPROVE', 'EDIT')
        AND ee.delay_reason IS NOT NULL
        AND TRIM(ee.delay_reason) != ''
    """
    params: tuple = ()
    if schedule_id is not None:
        query += " AND ee.schedule_id = %s"
        params = (schedule_id,)
    query += " GROUP BY TRIM(ee.delay_reason) ORDER BY count DESC, delay_reason ASC"

    if conn is not None:
        rows = conn.execute(query, params).fetchall()
    else:
        with get_connection() as c:
            rows = c.execute(query, params).fetchall()

    delay_reasons: List[dict] = [
        {
            "delay_reason": str(row["delay_reason"]),
            "count": int(row["count"]),
        }
        for row in rows
    ]

    total = sum(item["count"] for item in delay_reasons)

    return {
        "delay_reasons": delay_reasons,
        "total_approved_delay_claims": total,
    }


@router.get("/health")
def health():
    return {"router": "dashboard", "status": "ok"}


def query_dashboard_summary(conn: Optional[Any] = None, schedule_id: Optional[str] = None) -> dict:
    """
    Phase 3 Live Operational Dashboard Summary.

    Consolidated database-derived operational KPIs and discipline breakdown.

    Source-of-truth KPI definitions:
    1. Total Claims: Count of all submitted execution claims from execution_events.
    2. Pending Review: Count of claims awaiting Supervisor decision
       (status NOT IN ('APPROVED', 'EDITED', 'REJECTED') or status IS NULL).
    3. Actuals: Count of authoritative records committed in approved_actuals.
       Raw claims do not count as actuals.
    4. Conflicts: Count of open conflict records in conflict_records
       (status = 'OPEN' or status IS NULL or status != 'RESOLVED').
    5. Discipline Breakdown: Distribution of schedule_activities grouped by discipline,
       providing live volume distribution for the discipline chart.
    6. claims_trend_pct: total_claims (by event_date) in the last 7 days vs. the 7
       days before that, both windows scoped to the same schedule. None (not 0)
       when the prior window has no claims at all -- a percentage change against
       zero is meaningless, so the caller must show "no previous-period data"
       rather than a fabricated number (ISS-11).

    schedule_id: when given, every KPI above is restricted to that schedule.
    A caller with no schedule_id argument at all gets the pre-existing
    unscoped aggregate (used by unit tests seeding a single-schedule
    fixture); the live /summary endpoint always passes the resolved active
    schedule so results never mix schedules (ISS-05).
    """
    def _execute(query: str, params: tuple = ()):
        if conn is not None:
            return conn.execute(query, params)
        with get_connection() as c:
            return c.execute(query, params)

    scope_sql = "" if schedule_id is None else " AND schedule_id = %s"
    scope_params: tuple = () if schedule_id is None else (schedule_id,)

    # 1. Total Claims
    total_claims = 0
    try:
        row = _execute(
            "SELECT COUNT(*) AS total FROM execution_events WHERE 1=1" + scope_sql,
            scope_params,
        ).fetchone()
        if row:
            total_claims = int(row["total"] if isinstance(row, dict) or hasattr(row, "keys") else row[0])
    except Exception as e:
        logger.warning("Could not count execution_events: %s", e)

    # 2. Pending Review
    pending_review = 0
    try:
        row = _execute(
            """
            SELECT COUNT(*) AS pending
            FROM execution_events
            WHERE (status IS NULL
               OR UPPER(TRIM(status)) NOT IN ('APPROVED', 'EDITED', 'REJECTED'))
            """
            + scope_sql,
            scope_params,
        ).fetchone()
        if row:
            pending_review = int(row["pending"] if isinstance(row, dict) or hasattr(row, "keys") else row[0])
    except Exception as e:
        logger.warning("Could not count pending execution_events: %s", e)

    # 3. Actuals
    actuals = 0
    try:
        row = _execute(
            "SELECT COUNT(*) AS total FROM approved_actuals WHERE 1=1" + scope_sql,
            scope_params,
        ).fetchone()
        if row:
            actuals = int(row["total"] if isinstance(row, dict) or hasattr(row, "keys") else row[0])
    except Exception as e:
        logger.warning("Could not count approved_actuals: %s", e)

    # 4. Conflicts
    conflicts = 0
    try:
        row = _execute(
            """
            SELECT COUNT(*) AS total
            FROM conflict_records
            WHERE (status IS NULL
               OR UPPER(TRIM(status)) = 'OPEN'
               OR UPPER(TRIM(status)) != 'RESOLVED')
            """
            + scope_sql,
            scope_params,
        ).fetchone()
        if row:
            conflicts = int(row["total"] if isinstance(row, dict) or hasattr(row, "keys") else row[0])
    except Exception as e:
        logger.warning("Could not count conflict_records: %s", e)

    # 5. Discipline Breakdown
    discipline_breakdown: List[dict] = []
    try:
        rows = _execute(
            """
            SELECT
                UPPER(TRIM(discipline)) AS discipline,
                COUNT(*) AS count
            FROM schedule_activities
            WHERE discipline IS NOT NULL
              AND TRIM(discipline) != ''
            """
            + scope_sql
            + " GROUP BY UPPER(TRIM(discipline)) ORDER BY count DESC, discipline ASC",
            scope_params,
        ).fetchall()
        for r in rows:
            disc = str(r["discipline"] if isinstance(r, dict) or hasattr(r, "keys") else r[0])
            cnt = int(r["count"] if isinstance(r, dict) or hasattr(r, "keys") else r[1])
            discipline_breakdown.append({
                "discipline": disc,
                "name": disc,
                "count": cnt,
                "value": cnt,
            })
    except Exception as e:
        logger.warning("Could not aggregate discipline breakdown: %s", e)

    # 6. Week-over-week claims trend (real data only -- None, never a guess).
    # Date bounds are computed in Python (not SQL FILTER/INTERVAL, which are
    # Postgres-only and silently no-op under the SQLite fixtures some unit
    # tests use) so the same plain >=/< comparison works against both.
    claims_trend_pct: Optional[float] = None
    try:
        today = date.today()
        current_start = (today - timedelta(days=7)).isoformat()
        previous_start = (today - timedelta(days=14)).isoformat()

        current_row = _execute(
            "SELECT COUNT(*) AS n FROM execution_events WHERE event_date >= %s" + scope_sql,
            (current_start,) + scope_params,
        ).fetchone()
        previous_row = _execute(
            "SELECT COUNT(*) AS n FROM execution_events WHERE event_date >= %s AND event_date < %s" + scope_sql,
            (previous_start, current_start) + scope_params,
        ).fetchone()

        def _n(row):
            return int(row["n"] if isinstance(row, dict) or hasattr(row, "keys") else row[0])

        if current_row is not None and previous_row is not None:
            current_week = _n(current_row)
            previous_week = _n(previous_row)
            if previous_week > 0:
                claims_trend_pct = round((current_week - previous_week) / previous_week * 100, 1)
    except Exception as e:
        logger.warning("Could not compute claims trend: %s", e)

    return {
        "total_claims": total_claims,
        "pending_review": pending_review,
        "actuals": actuals,
        "conflicts": conflicts,
        "discipline_breakdown": discipline_breakdown,
        "claims_trend_pct": claims_trend_pct,
    }


@router.get("/summary")
def get_dashboard_summary(
    schedule_id: Optional[str] = Query(default=None, description="Required (or X-Schedule-ID); validated against the project"),
    schedule_context: ScheduleContext = Depends(gates.claim_review_schedule),
):
    """
    Live dashboard summary KPIs and discipline volume breakdown, scoped to
    an EXPLICIT schedule of the caller's project (no active/latest fallback).
    Requires REVIEW_CLAIM (supervisor / planner / project manager / owner).
    """
    resolved_schedule_id = schedule_context.schedule_id
    try:
        return query_dashboard_summary(schedule_id=resolved_schedule_id)
    except Exception as e:
        logger.error("Failed to query dashboard summary: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve dashboard summary",
        )


@router.get("/delay-reasons")
def get_delay_reasons(
    schedule_id: Optional[str] = Query(default=None, description="Required (or X-Schedule-ID); validated against the project"),
    schedule_context: ScheduleContext = Depends(gates.claim_review_schedule),
):
    """
    Aggregate execution_events.delay_reason across APPROVED/EDIT claims,
    scoped to an EXPLICIT schedule of the caller's project.
    """
    resolved_schedule_id = schedule_context.schedule_id
    try:
        return query_delay_reason_aggregates(schedule_id=resolved_schedule_id)
    except Exception as e:
        logger.error("Failed to query delay reasons for dashboard: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve delay reasons",
        )


def _parse_date(val: Any) -> Optional[date]:
    """
    Safely parse date from date, datetime, or ISO date string (YYYY-MM-DD).
    Returns None if value is None, empty, or unparseable.
    """
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        try:
            return date.fromisoformat(val[:10])
        except (ValueError, TypeError):
            return None
    return None


def _calculate_duration_days(start_val: Any, finish_val: Any) -> Optional[int]:
    """
    Calculate duration in days between start and finish dates.
    Returns None if either date is missing or invalid.
    """
    start = _parse_date(start_val)
    finish = _parse_date(finish_val)
    if start is not None and finish is not None:
        return (finish - start).days
    return None


def _calculate_variance_days(
    planned_duration: Optional[int],
    actual_duration: Optional[int],
) -> Optional[int]:
    """
    Calculate variance in days: actual_duration - planned_duration.
    Positive variance indicates completion took longer than planned (delay).
    Negative variance indicates completion ahead of schedule.
    Returns None if either planned or actual duration is missing.
    """
    if planned_duration is not None and actual_duration is not None:
        return actual_duration - planned_duration
    return None


def query_institutional_memory(
    discipline: Optional[str] = None,
    conn: Optional[Any] = None,
    project_id: Optional[str] = None,
) -> dict:
    """
    Query schedule_activities joined with approved_actuals to provide
    historical comparison of planned duration vs actual duration.

    Core business rules:
    1. Canonical join on (schedule_id, activity_id).
    2. Planned duration = planned_finish - planned_start (in days).
    3. Actual duration = actual_finish - actual_start (in days) from approved_actuals only.
    4. Missing or incomplete actuals produce null actual_duration and null variance_days.
    5. Filterable by discipline (case-insensitive).
    6. Deterministic ordering: activity_id ASC, schedule_id ASC.
    """
    query = """
        SELECT
            sa.activity_id,
            sa.discipline,
            sa.planned_start,
            sa.planned_finish,
            aa.actual_start,
            aa.actual_finish
        FROM schedule_activities sa
        LEFT JOIN approved_actuals aa
            ON aa.schedule_id = sa.schedule_id
           AND aa.activity_id = sa.activity_id
    """
    params = []
    where = []
    if project_id is not None:  # operational callers always scope to the caller's project
        where.append("sa.project_id = %s")
        params.append(str(project_id))
    if discipline is not None and discipline.strip():
        where.append("UPPER(TRIM(sa.discipline)) = %s")
        params.append(discipline.strip().upper())
    if where:
        query += " WHERE " + " AND ".join(where)

    query += " ORDER BY sa.activity_id ASC, sa.schedule_id ASC"

    if conn is not None:
        rows = conn.execute(query, tuple(params) if params else None).fetchall()
    else:
        with get_connection() as c:
            rows = c.execute(query, tuple(params) if params else None).fetchall()

    activities: List[dict] = []
    for row in rows:
        planned_dur = _calculate_duration_days(row["planned_start"], row["planned_finish"])
        actual_dur = _calculate_duration_days(row["actual_start"], row["actual_finish"])
        variance = _calculate_variance_days(planned_dur, actual_dur)

        activities.append(
            {
                "activity_id": str(row["activity_id"]),
                "discipline": str(row["discipline"]),
                "planned_duration": planned_dur,
                "actual_duration": actual_dur,
                "variance_days": variance,
            }
        )

    return {
        "activities": activities,
        "total_activities": len(activities),
    }


@router.get("/institutional-memory")
def get_institutional_memory(
    discipline: Optional[str] = Query(
        default=None,
        description="Filter activities by discipline (e.g. CIVIL, PIPING)",
    ),
    project_context: ProjectContext = Depends(gates.project_review),
):
    """
    Historical comparison of planned vs actual duration per activity of THIS PROJECT
    (all of its schedule versions). Filterable by discipline. Requires REVIEW_CLAIM.
    """
    try:
        return query_institutional_memory(discipline=discipline, project_id=str(project_context.project_id))
    except Exception as e:
        logger.error("Failed to query institutional memory: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve institutional memory",
        )


def _calculate_historical_ratio_for_discipline(
    discipline: str,
    target_activity_id: Optional[str] = None,
    conn: Optional[Any] = None,
    project_id: Optional[str] = None,
) -> Optional[float]:
    """
    Calculate the average historical actual_duration / planned_duration ratio
    for completed activities matching the specified discipline.

    Rules:
    - Only activities with valid planned_start and planned_finish (planned_duration > 0).
    - Only activities with valid approved_actuals actual_start and actual_finish (actual_duration >= 0).
    - Excludes target_activity_id from history to prevent self-contamination.
    - No division by zero.
    - Returns None if no qualifying historical completed activities exist.
    """
    query = """
        SELECT
            sa.activity_id,
            sa.planned_start,
            sa.planned_finish,
            aa.actual_start,
            aa.actual_finish
        FROM schedule_activities sa
        JOIN approved_actuals aa
            ON aa.schedule_id = sa.schedule_id
           AND aa.activity_id = sa.activity_id
        WHERE UPPER(TRIM(sa.discipline)) = %s
          AND aa.actual_start IS NOT NULL
          AND aa.actual_finish IS NOT NULL
    """
    disc_norm = discipline.strip().upper()
    hist_params: tuple = (disc_norm,)
    if project_id is not None:  # history is this project's own; never another project's
        query += " AND sa.project_id = %s"
        hist_params = (disc_norm, str(project_id))
    if conn is not None:
        rows = conn.execute(query, hist_params).fetchall()
    else:
        with get_connection() as c:
            rows = c.execute(query, hist_params).fetchall()

    ratios: List[float] = []
    for row in rows:
        act_id = str(row["activity_id"])
        if target_activity_id and act_id == str(target_activity_id):
            continue

        p_dur = _calculate_duration_days(row["planned_start"], row["planned_finish"])
        a_dur = _calculate_duration_days(row["actual_start"], row["actual_finish"])

        if p_dur is not None and a_dur is not None and p_dur > 0 and a_dur >= 0:
            ratios.append(a_dur / p_dur)

    if not ratios:
        return None

    return sum(ratios) / len(ratios)


def query_forecast(
    activity_id: Optional[str] = None,
    discipline: Optional[str] = None,
    conn: Optional[Any] = None,
    project_id: Optional[str] = None,
    schedule_id: Optional[str] = None,
) -> dict:
    """
    Calculate historical-ratio forecast for an activity or a discipline.
    """
    if not activity_id and not discipline:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one of 'activity_id' or 'discipline' must be provided.",
        )

    # Mode 1: Activity-level forecast
    if activity_id:
        target_query = """
            SELECT
                activity_id,
                schedule_id,
                discipline,
                planned_start,
                planned_finish
            FROM schedule_activities
            WHERE activity_id = %s
        """
        target_params: tuple = (activity_id,)
        if schedule_id is not None:  # explicit schedule: never "the first schedule that has this id"
            target_query += " AND schedule_id = %s"
            target_params += (schedule_id,)
        if project_id is not None:
            target_query += " AND project_id = %s"
            target_params += (str(project_id),)
        target_query += " ORDER BY schedule_id ASC LIMIT 1"
        if conn is not None:
            target_row = conn.execute(target_query, target_params).fetchone()
        else:
            with get_connection() as c:
                target_row = c.execute(target_query, target_params).fetchone()
        if not target_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Activity '{activity_id}' not found",
            )

        act_discipline = str(target_row["discipline"])
        if discipline and act_discipline.strip().upper() != discipline.strip().upper():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Activity '{activity_id}' does not match discipline '{discipline}'",
            )

        planned_dur = _calculate_duration_days(target_row["planned_start"], target_row["planned_finish"])
        avg_ratio = _calculate_historical_ratio_for_discipline(
            discipline=act_discipline,
            target_activity_id=activity_id,
            conn=conn,
            project_id=project_id,
        )

        if avg_ratio is not None and planned_dur is not None and planned_dur > 0:
            forecast_dur = int(round(planned_dur * avg_ratio))
            hist_ratio = round(avg_ratio, 3)
        else:
            forecast_dur = None
            hist_ratio = None

        return {
            "activity_id": str(target_row["activity_id"]),
            "discipline": act_discipline,
            "planned_duration": planned_dur,
            "historical_ratio": hist_ratio,
            "forecast_duration": forecast_dur,
        }

    # Mode 2: Discipline-level forecast
    disc_norm = discipline.strip().upper()
    avg_ratio = _calculate_historical_ratio_for_discipline(
        discipline=disc_norm,
        conn=conn,
        project_id=project_id,
    )

    activities_query = """
        SELECT
            activity_id,
            schedule_id,
            discipline,
            planned_start,
            planned_finish
        FROM schedule_activities
        WHERE UPPER(TRIM(discipline)) = %s
    """
    act_params: tuple = (disc_norm,)
    if schedule_id is not None:
        activities_query += " AND schedule_id = %s"
        act_params += (schedule_id,)
    if project_id is not None:
        activities_query += " AND project_id = %s"
        act_params += (str(project_id),)
    activities_query += " ORDER BY activity_id ASC, schedule_id ASC"
    if conn is not None:
        rows = conn.execute(activities_query, act_params).fetchall()
    else:
        with get_connection() as c:
            rows = c.execute(activities_query, act_params).fetchall()

    activities: List[dict] = []
    hist_ratio = round(avg_ratio, 3) if avg_ratio is not None else None

    for r in rows:
        p_dur = _calculate_duration_days(r["planned_start"], r["planned_finish"])
        if avg_ratio is not None and p_dur is not None and p_dur > 0:
            f_dur = int(round(p_dur * avg_ratio))
        else:
            f_dur = None

        activities.append(
            {
                "activity_id": str(r["activity_id"]),
                "discipline": str(r["discipline"]),
                "planned_duration": p_dur,
                "historical_ratio": hist_ratio,
                "forecast_duration": f_dur,
            }
        )

    return {
        "discipline": disc_norm,
        "historical_ratio": hist_ratio,
        "activities": activities,
        "total_activities": len(activities),
    }


@router.get("/forecast")
def get_forecast(
    activity_id: Optional[str] = Query(
        default=None,
        description="Target activity ID for forecast",
    ),
    discipline: Optional[str] = Query(
        default=None,
        description="Target discipline for forecast",
    ),
    schedule_id: Optional[str] = Query(default=None, description="Required (or X-Schedule-ID)"),
    schedule_context: ScheduleContext = Depends(gates.claim_review_schedule),
):
    """
    Lightweight historical-ratio forecasting.
    Calculate actual_duration / planned_duration for completed historical activities
    of the same discipline IN THIS PROJECT, and apply that ratio to the target activity/schedule.
    Requires REVIEW_CLAIM and an explicit schedule.
    """
    try:
        return query_forecast(
            activity_id=activity_id,
            discipline=discipline,
            project_id=str(schedule_context.project_id),
            schedule_id=schedule_context.schedule_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to calculate forecast: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to calculate forecast",
        )


