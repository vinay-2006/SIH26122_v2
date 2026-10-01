import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from backend.context import gates
from backend.context.schedule import ScheduleContext
from backend.shared.db import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/activities", tags=["activities"])


def _execute(conn: Optional[Any], query: str, params: tuple = ()):
    if conn is not None:
        return conn.execute(query, params)
    with get_connection() as c:
        return c.execute(query, params)


def _resolve_activity(activity_id: str, schedule_id: Optional[str], conn: Optional[Any]) -> Dict[str, Any]:
    """
    ISS-23: activity metadata (and whether the activity exists at all) comes
    from schedule_activities, never inferred only from execution_events -- an
    activity with zero events must still 200 with its real metadata, and a
    genuinely nonexistent activity_id must 404 rather than a silent empty
    timeline.

    The schedule is ALWAYS explicit: there is no active/latest fallback and no cross-schedule lookup
    by activity_id alone (that would resolve an activity belonging to another project's schedule).
    """
    if not schedule_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="schedule_id is required; implicit active/latest schedule selection is not allowed.",
        )

    rows = _execute(
        conn,
        "SELECT * FROM schedule_activities WHERE activity_id = %s AND schedule_id = %s",
        (activity_id, schedule_id),
    ).fetchall()
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Activity '{activity_id}' not found",
        )
    return dict(rows[0])


def query_activity_history(
    activity_id: str,
    schedule_id: Optional[str] = None,
    conn: Optional[Any] = None,
) -> dict:
    """
    Retrieve chronological activity history timeline for an activity.

    Core business rules:
    0. The activity itself must exist in schedule_activities (see
       _resolve_activity) -- 404 for a genuinely unknown activity_id, 409 if
       activity_id is ambiguous across schedules and no schedule_id was given.
       Its schedule_id then scopes every query below, so a same-named
       activity_id in a different schedule can never leak into this timeline.
    1. For the specified activity_id, retrieve all linked execution_events:
       - Where the event's latest planner_decision has selected_activity_id = activity_id, OR
       - Where no planner_decision exists, but matched_activity_id or reported_activity_id = activity_id.
    2. For every linked execution_event, retrieve all linked source_references.
    3. Retrieve the single final planner_decision for the activity:
       - Ordered by decided_at DESC, decision_id DESC LIMIT 1.
    4. Assemble a unified timeline ordered chronologically:
       - timestamp ASC
       - type == "execution_event" before "planner_decision"
       - event_id / decision_id deterministic tie-breaker.
    5. Return activity metadata + {"timeline": [...]} -- an empty timeline is
       a legitimate "no history yet" outcome for a real activity, distinct
       from the activity not existing at all.
    """
    activity_row = _resolve_activity(activity_id, schedule_id, conn)
    resolved_schedule_id = str(activity_row["schedule_id"])

    events_query = """
        SELECT
            ee.event_id,
            ee.document_id,
            ee.schedule_id,
            ee.event_date,
            ee.raw_claim_text,
            ee.input_channel,
            ee.language_detected,
            ee.reported_activity_id,
            ee.matched_activity_id,
            ee.discipline,
            ee.action,
            ee.event_type,
            ee.claim_mode,
            ee.asset_tag,
            ee.location,
            ee.claimed_quantity,
            ee.claimed_uom,
            ee.claimed_pct,
            ee.delay_reason,
            ee.supervisor_id,
            ee.photo_path,
            ee.status,
            ee.created_at
        FROM execution_events ee
        WHERE ee.schedule_id = %s
          AND (
            ee.event_id IN (
                SELECT pd.event_id
                FROM planner_decisions pd
                WHERE pd.selected_activity_id = %s
                  AND pd.decision_id = (
                      SELECT pd2.decision_id
                      FROM planner_decisions pd2
                      WHERE pd2.event_id = pd.event_id
                      ORDER BY pd2.decided_at DESC, pd2.decision_id DESC
                      LIMIT 1
                  )
            )
            OR (
                NOT EXISTS (
                    SELECT 1 FROM planner_decisions pd3 WHERE pd3.event_id = ee.event_id
                )
                AND (ee.matched_activity_id = %s OR ee.reported_activity_id = %s)
            )
        )
        ORDER BY ee.event_date ASC, ee.created_at ASC, ee.event_id ASC
    """

    # planner_decisions has no schedule_id column -- scope through its event's
    # schedule_id instead, so a decision on another schedule's same-named
    # activity_id can never appear here.
    decision_query = """
        SELECT
            pd.decision_id,
            pd.event_id,
            pd.selected_activity_id,
            pd.action,
            pd.approved_pct,
            pd.approved_qty,
            pd.planner_id,
            pd.justification,
            pd.decided_at
        FROM planner_decisions pd
        JOIN execution_events ee ON ee.event_id = pd.event_id
        WHERE pd.selected_activity_id = %s AND ee.schedule_id = %s
        ORDER BY pd.decided_at ASC, pd.decision_id ASC
    """

    actuals_query = """
        SELECT
            actual_id,
            decision_id,
            event_id,
            schedule_id,
            activity_id,
            actual_start,
            actual_finish,
            actual_pct_complete,
            actual_quantity,
            created_at
        FROM approved_actuals
        WHERE activity_id = %s AND schedule_id = %s
        ORDER BY created_at ASC, actual_id ASC
    """

    event_rows = _execute(
        conn, events_query, (resolved_schedule_id, activity_id, activity_id, activity_id)
    ).fetchall()
    decision_rows = _execute(conn, decision_query, (activity_id, resolved_schedule_id)).fetchall()
    try:
        actual_rows = _execute(conn, actuals_query, (activity_id, resolved_schedule_id)).fetchall()
    except Exception:
        actual_rows = []


    event_ids = [str(r["event_id"]) for r in event_rows]

    refs_by_event: Dict[str, List[dict]] = {}
    if event_ids:
        placeholders = ", ".join(["%s"] * len(event_ids))
        refs_query = f"""
            SELECT
                reference_id,
                event_id,
                file_name,
                sheet_name,
                row_cell_ref,
                message_id,
                raw_snippet
            FROM source_references
            WHERE event_id IN ({placeholders})
            ORDER BY reference_id ASC
        """
        if conn is not None:
            ref_rows = conn.execute(refs_query, tuple(event_ids)).fetchall()
        else:
            with get_connection() as c:
                ref_rows = c.execute(refs_query, tuple(event_ids)).fetchall()

        for ref in ref_rows:
            e_id = str(ref["event_id"])
            if e_id not in refs_by_event:
                refs_by_event[e_id] = []
            refs_by_event[e_id].append(
                {
                    "reference_id": str(ref["reference_id"]),
                    "file_name": ref["file_name"] if ref["file_name"] is not None else None,
                    "sheet_name": ref["sheet_name"] if ref["sheet_name"] is not None else None,
                    "row_cell_ref": ref["row_cell_ref"] if ref["row_cell_ref"] is not None else None,
                    "message_id": ref["message_id"] if ref["message_id"] is not None else None,
                    "raw_snippet": str(ref["raw_snippet"] or ""),
                }
            )

    timeline: List[dict] = []

    for event in event_rows:
        ts = None
        if event["created_at"] is not None:
            ts = str(event["created_at"])
        elif event["event_date"] is not None:
            ts = str(event["event_date"])

        e_id = str(event["event_id"])
        timeline.append(
            {
                "type": "execution_event",
                "event_id": e_id,
                "schedule_id": str(event["schedule_id"]) if event["schedule_id"] is not None else None,
                "event_date": str(event["event_date"]) if event["event_date"] is not None else None,
                "raw_claim_text": str(event["raw_claim_text"] or ""),
                "claim_mode": str(event["claim_mode"]) if event["claim_mode"] is not None else "CUMULATIVE_PCT",
                "claimed_pct": float(event["claimed_pct"]) if event["claimed_pct"] is not None else None,
                "claimed_quantity": float(event["claimed_quantity"]) if event["claimed_quantity"] is not None else None,
                "delay_reason": str(event["delay_reason"]) if event["delay_reason"] is not None else None,
                "status": str(event["status"]) if event["status"] is not None else None,
                "photo_path": str(event["photo_path"]) if event["photo_path"] else None,
                "document_id": str(event["document_id"]) if event["document_id"] else None,
                "timestamp": ts,
                "source_references": refs_by_event.get(e_id, []),
            }
        )

    decisions_list: List[dict] = []
    for d in decision_rows:
        dec_ts = str(d["decided_at"]) if d["decided_at"] is not None else None
        d_dict = {
            "type": "planner_decision",
            "decision_id": str(d["decision_id"]),
            "event_id": str(d["event_id"]),
            "selected_activity_id": str(d["selected_activity_id"]),
            "action": str(d["action"]),
            "approved_pct": float(d["approved_pct"]) if d["approved_pct"] is not None else None,
            "approved_qty": float(d["approved_qty"]) if d["approved_qty"] is not None else None,
            "planner_id": str(d["planner_id"]) if d["planner_id"] is not None else None,
            "justification": str(d["justification"]) if d["justification"] is not None else "",
            "timestamp": dec_ts,
        }
        decisions_list.append(d_dict)
        timeline.append(d_dict)

    actuals_list: List[dict] = []
    for a in actual_rows:
        act_ts = str(a["created_at"]) if a["created_at"] is not None else (
            str(a["actual_finish"]) if a["actual_finish"] is not None else (
                str(a["actual_start"]) if a["actual_start"] is not None else None
            )
        )
        a_dict = {
            "type": "approved_actual",
            "actual_id": str(a["actual_id"]),
            "decision_id": str(a["decision_id"]) if a["decision_id"] is not None else None,
            "event_id": str(a["event_id"]) if a["event_id"] is not None else None,
            "schedule_id": str(a["schedule_id"]) if a["schedule_id"] is not None else None,
            "activity_id": str(a["activity_id"]),
            "actual_start": str(a["actual_start"]) if a["actual_start"] is not None else None,
            "actual_finish": str(a["actual_finish"]) if a["actual_finish"] is not None else None,
            "actual_pct_complete": float(a["actual_pct_complete"]) if a["actual_pct_complete"] is not None else None,
            "actual_quantity": float(a["actual_quantity"]) if a["actual_quantity"] is not None else None,
            "timestamp": act_ts,
        }
        actuals_list.append(a_dict)
        timeline.append(a_dict)

    timeline.sort(
        key=lambda item: (
            str(item.get("timestamp") or ""),
            0 if item.get("type") == "execution_event" else (1 if item.get("type") == "planner_decision" else 2),
            str(item.get("event_id") or item.get("decision_id") or item.get("actual_id") or ""),
        )
    )

    return {
        "activity_id": str(activity_id),
        "schedule_id": resolved_schedule_id,
        "activity_name": str(activity_row["activity_name"]) if activity_row.get("activity_name") is not None else None,
        "discipline": str(activity_row["discipline"]) if activity_row.get("discipline") is not None else None,
        "location": str(activity_row["location"]) if activity_row.get("location") is not None else None,
        "wbs_code": str(activity_row["wbs_code"]) if activity_row.get("wbs_code") is not None else None,
        "planned_start": str(activity_row["planned_start"]) if activity_row.get("planned_start") is not None else None,
        "planned_finish": str(activity_row["planned_finish"]) if activity_row.get("planned_finish") is not None else None,
        "timeline": timeline,
    }



class ActivitySummary(BaseModel):
    activity_id: str
    activity_name: str
    schedule_id: str
    discipline: str
    location: str
    asset_tag: Optional[str] = None
    wbs_code: Optional[str] = None
    planned_start: Optional[str] = None
    planned_finish: Optional[str] = None
    planned_quantity: Optional[float] = None
    uom: Optional[str] = None
    baseline_pct_complete: Optional[float] = 0.0
    actual_start: Optional[str] = None
    actual_finish: Optional[str] = None
    actual_pct_complete: Optional[float] = None
    execution_state: str
    is_critical: Optional[bool] = None
    total_float: Optional[float] = None
    has_changes: bool
    event_count: int
    last_changed_at: Optional[str] = None


class ActivityMetrics(BaseModel):
    total: int
    in_progress: int
    completed: int
    not_started: int
    critical: int
    changed: int


class ActivityListResponse(BaseModel):
    items: List[ActivitySummary]
    total: int
    page: int
    page_size: int
    schedule_id: str
    metrics: ActivityMetrics


_BASE_ACTIVITIES_CTE = """
WITH lifecycle_events AS (
    SELECT schedule_id, COALESCE(matched_activity_id, reported_activity_id) as activity_id, COALESCE(created_at, event_date) as ts
    FROM execution_events
    WHERE matched_activity_id IS NOT NULL OR reported_activity_id IS NOT NULL
    UNION ALL
    SELECT ee.schedule_id, pd.selected_activity_id as activity_id, pd.decided_at as ts
    FROM planner_decisions pd
    JOIN execution_events ee ON ee.event_id = pd.event_id
    UNION ALL
    SELECT schedule_id, activity_id, created_at as ts
    FROM approved_actuals
),
lifecycle_agg AS (
    SELECT schedule_id, activity_id, COUNT(*) as event_count, MAX(ts) as last_changed_at
    FROM lifecycle_events
    GROUP BY schedule_id, activity_id
),
latest_actuals AS (
    SELECT schedule_id, activity_id, actual_start, actual_finish, actual_pct_complete
    FROM (
        SELECT schedule_id, activity_id, actual_start, actual_finish, actual_pct_complete,
               ROW_NUMBER() OVER (PARTITION BY schedule_id, activity_id ORDER BY created_at DESC, actual_id DESC) as rn
        FROM approved_actuals
    ) ranked
    WHERE rn = 1
),
base_activities AS (
    SELECT
        sa.activity_id,
        sa.activity_name,
        sa.schedule_id,
        sa.discipline,
        sa.location,
        sa.asset_tag,
        sa.wbs_code,
        sa.planned_start,
        sa.planned_finish,
        sa.planned_quantity,
        sa.uom,
        sa.baseline_pct_complete,
        sa.is_critical,
        sa.total_float,
        la.actual_start,
        la.actual_finish,
        la.actual_pct_complete,
        CASE
            WHEN la.actual_pct_complete >= 100.0 THEN 'COMPLETED'
            WHEN la.actual_start IS NOT NULL AND TRIM(CAST(la.actual_start AS TEXT)) != '' THEN 'IN_PROGRESS'
            ELSE 'NOT_STARTED'
        END AS execution_state,
        COALESCE(lag.event_count, 0) AS event_count,
        CASE WHEN COALESCE(lag.event_count, 0) > 0 THEN TRUE ELSE FALSE END AS has_changes,
        lag.last_changed_at
    FROM schedule_activities sa
    LEFT JOIN latest_actuals la ON la.schedule_id = sa.schedule_id AND la.activity_id = sa.activity_id
    LEFT JOIN lifecycle_agg lag ON lag.schedule_id = sa.schedule_id AND lag.activity_id = sa.activity_id
    WHERE sa.schedule_id = %s
)
"""


def _format_iso(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, (datetime, )):
        return val.isoformat()
    return str(val)


def query_activities(
    schedule_id: Optional[str] = None,
    search: Optional[str] = None,
    discipline: Optional[str] = None,
    location: Optional[str] = None,
    wbs_code: Optional[str] = None,
    execution_state: Optional[str] = None,
    is_critical: Optional[str] = None,
    float_range: Optional[str] = None,
    has_changes: Optional[bool] = None,
    change_recency: Optional[str] = None,
    page: int = 1,
    page_size: int = 25,
    sort_by: str = "activity_id",
    sort_order: str = "asc",
    conn: Optional[Any] = None,
) -> dict:
    if not schedule_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="schedule_id is required; implicit active/latest schedule selection is not allowed.",
        )

    empty_metrics = {"total": 0, "in_progress": 0, "completed": 0, "not_started": 0, "critical": 0, "changed": 0}
    if not schedule_id:
        return {
            "items": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "schedule_id": "",
            "metrics": empty_metrics,
        }

    where_clauses = ["1=1"]
    params: List[Any] = [schedule_id]

    if search and search.strip():
        s_term = f"%{search.strip()}%"
        where_clauses.append(
            "(LOWER(ba.activity_id) LIKE LOWER(%s) OR LOWER(ba.activity_name) LIKE LOWER(%s) OR (ba.asset_tag IS NOT NULL AND LOWER(ba.asset_tag) LIKE LOWER(%s)))"
        )
        params.extend([s_term, s_term, s_term])

    if discipline and discipline.upper() != "ALL":
        where_clauses.append("UPPER(ba.discipline) = UPPER(%s)")
        params.append(discipline)

    if location and location.upper() != "ALL":
        where_clauses.append("LOWER(ba.location) = LOWER(%s)")
        params.append(location)

    if wbs_code and wbs_code.upper() != "ALL":
        where_clauses.append("ba.wbs_code LIKE %s")
        params.append(f"{wbs_code}%")

    if execution_state and execution_state.upper() != "ALL":
        where_clauses.append("ba.execution_state = %s")
        params.append(execution_state.upper())

    if is_critical and is_critical.upper() != "ALL":
        crit_val = is_critical.upper()
        if crit_val in ("CRITICAL", "TRUE", "1"):
            where_clauses.append("ba.is_critical IS TRUE")
        elif crit_val in ("NON_CRITICAL", "FALSE", "0"):
            where_clauses.append("ba.is_critical IS FALSE")
        elif crit_val in ("UNKNOWN", "NULL"):
            where_clauses.append("ba.is_critical IS NULL")

    if float_range and float_range.upper() != "ALL":
        fr = float_range.upper()
        if fr in ("ZERO", "0"):
            where_clauses.append("ba.total_float = 0")
        elif fr in ("1_TO_5", "1-5"):
            where_clauses.append("(ba.total_float > 0 AND ba.total_float <= 5)")
        elif fr in ("GT_5", ">5"):
            where_clauses.append("ba.total_float > 5")
        elif fr in ("UNKNOWN", "NULL"):
            where_clauses.append("ba.total_float IS NULL")

    if has_changes is not None:
        if has_changes:
            where_clauses.append("ba.has_changes IS TRUE")
        else:
            where_clauses.append("ba.has_changes IS FALSE")

    if change_recency and change_recency.upper() != "ALL":
        cr = change_recency.upper()
        now_utc = datetime.now(timezone.utc)
        if cr == "TODAY":
            start_of_today = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            where_clauses.append("ba.last_changed_at >= %s")
            params.append(start_of_today)
        elif cr == "LAST_7_DAYS":
            seven_days_ago = (now_utc - timedelta(days=7)).isoformat()
            where_clauses.append("ba.last_changed_at >= %s")
            params.append(seven_days_ago)
        elif cr == "LAST_30_DAYS":
            thirty_days_ago = (now_utc - timedelta(days=30)).isoformat()
            where_clauses.append("ba.last_changed_at >= %s")
            params.append(thirty_days_ago)
        elif cr == "NO_CHANGES":
            where_clauses.append("ba.has_changes IS FALSE")

    where_str = " AND ".join(where_clauses)

    # 1. Compute scope-aware metrics
    metrics_query = f"""
    {_BASE_ACTIVITIES_CTE}
    SELECT
        COUNT(*) as total,
        COALESCE(SUM(CASE WHEN ba.execution_state = 'IN_PROGRESS' THEN 1 ELSE 0 END), 0) as in_progress,
        COALESCE(SUM(CASE WHEN ba.execution_state = 'COMPLETED' THEN 1 ELSE 0 END), 0) as completed,
        COALESCE(SUM(CASE WHEN ba.execution_state = 'NOT_STARTED' THEN 1 ELSE 0 END), 0) as not_started,
        COALESCE(SUM(CASE WHEN ba.is_critical IS TRUE THEN 1 ELSE 0 END), 0) as critical,
        COALESCE(SUM(CASE WHEN ba.has_changes IS TRUE THEN 1 ELSE 0 END), 0) as changed
    FROM base_activities ba
    WHERE {where_str}
    """
    m_row = _execute(conn, metrics_query, tuple(params)).fetchone()
    if m_row:
        metrics = {
            "total": int(m_row["total"] if isinstance(m_row, dict) else m_row[0]),
            "in_progress": int(m_row["in_progress"] if isinstance(m_row, dict) else m_row[1]),
            "completed": int(m_row["completed"] if isinstance(m_row, dict) else m_row[2]),
            "not_started": int(m_row["not_started"] if isinstance(m_row, dict) else m_row[3]),
            "critical": int(m_row["critical"] if isinstance(m_row, dict) else m_row[4]),
            "changed": int(m_row["changed"] if isinstance(m_row, dict) else m_row[5]),
        }
    else:
        metrics = empty_metrics

    # 2. Build sorting & pagination
    SORT_EXPRESSIONS = {
        "activity_id": "ba.activity_id",
        "activity_name": "ba.activity_name",
        "planned_start": "ba.planned_start",
        "planned_finish": "ba.planned_finish",
        "actual_pct_complete": "COALESCE(ba.actual_pct_complete, 0)",
        "baseline_pct_complete": "COALESCE(ba.baseline_pct_complete, 0)",
        "total_float": "ba.total_float",
    }
    dir_str = "DESC" if str(sort_order).lower() == "desc" else "ASC"
    if sort_by == "last_changed_at":
        if dir_str == "DESC":
            order_clause = "CASE WHEN ba.last_changed_at IS NULL THEN 1 ELSE 0 END, ba.last_changed_at DESC, ba.activity_id ASC"
        else:
            order_clause = "CASE WHEN ba.last_changed_at IS NULL THEN 0 ELSE 1 END, ba.last_changed_at ASC, ba.activity_id ASC"
    else:
        col_expr = SORT_EXPRESSIONS.get(sort_by, "ba.activity_id")
        order_clause = f"{col_expr} {dir_str}, ba.activity_id ASC"

    valid_page = max(1, page)
    valid_page_size = max(1, min(page_size, 500))
    offset = (valid_page - 1) * valid_page_size

    items_query = f"""
    {_BASE_ACTIVITIES_CTE}
    SELECT ba.*
    FROM base_activities ba
    WHERE {where_str}
    ORDER BY {order_clause}
    LIMIT %s OFFSET %s
    """
    item_rows = _execute(conn, items_query, tuple(params + [valid_page_size, offset])).fetchall()

    items: List[dict] = []
    for r in item_rows:
        row_dict = dict(r)
        items.append({
            "activity_id": str(row_dict["activity_id"]),
            "activity_name": str(row_dict["activity_name"]),
            "schedule_id": str(row_dict["schedule_id"]),
            "discipline": str(row_dict["discipline"]),
            "location": str(row_dict["location"]),
            "asset_tag": str(row_dict["asset_tag"]) if row_dict.get("asset_tag") is not None else None,
            "wbs_code": str(row_dict["wbs_code"]) if row_dict.get("wbs_code") is not None else None,
            "planned_start": _format_iso(row_dict.get("planned_start")),
            "planned_finish": _format_iso(row_dict.get("planned_finish")),
            "planned_quantity": float(row_dict["planned_quantity"]) if row_dict.get("planned_quantity") is not None else None,
            "uom": str(row_dict["uom"]) if row_dict.get("uom") is not None else None,
            "baseline_pct_complete": float(row_dict["baseline_pct_complete"]) if row_dict.get("baseline_pct_complete") is not None else 0.0,
            "actual_start": _format_iso(row_dict.get("actual_start")),
            "actual_finish": _format_iso(row_dict.get("actual_finish")),
            "actual_pct_complete": float(row_dict["actual_pct_complete"]) if row_dict.get("actual_pct_complete") is not None else None,
            "execution_state": str(row_dict.get("execution_state") or "NOT_STARTED"),
            "is_critical": bool(row_dict["is_critical"]) if row_dict.get("is_critical") is not None else None,
            "total_float": float(row_dict["total_float"]) if row_dict.get("total_float") is not None else None,
            "has_changes": bool(row_dict.get("has_changes")),
            "event_count": int(row_dict.get("event_count") or 0),
            "last_changed_at": _format_iso(row_dict.get("last_changed_at")),
        })

    return {
        "items": items,
        "total": metrics["total"],
        "page": valid_page,
        "page_size": valid_page_size,
        "schedule_id": schedule_id,
        "metrics": metrics,
    }


@router.get("", response_model=ActivityListResponse)
def list_activities(
    schedule_id: Optional[str] = None,
    search: Optional[str] = None,
    discipline: Optional[str] = None,
    location: Optional[str] = None,
    wbs_code: Optional[str] = None,
    execution_state: Optional[str] = None,
    is_critical: Optional[str] = None,
    float_range: Optional[str] = None,
    has_changes: Optional[bool] = None,
    change_recency: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=500),
    sort_by: str = Query("activity_id"),
    sort_order: str = Query("asc"),
    schedule_context: ScheduleContext = Depends(gates.claim_review_schedule),
):
    """
    List schedule activities with canonical execution state, multi-source lifecycle aggregation,
    and characteristic filters. Restricted to SUPERVISOR role.
    """
    try:
        return query_activities(
            schedule_id=schedule_context.schedule_id,
            search=search,
            discipline=discipline,
            location=location,
            wbs_code=wbs_code,
            execution_state=execution_state,
            is_critical=is_critical,
            float_range=float_range,
            has_changes=has_changes,
            change_recency=change_recency,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to list activities: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list activities",
        )


@router.get("/{activity_id}/history")
def get_activity_history(
    activity_id: str,
    schedule_id: Optional[str] = None,
    schedule_context: ScheduleContext = Depends(gates.claim_review_schedule),
):
    """
    Chronological activity history timeline for an activity, plus the
    activity's own metadata (so "exists with zero events" and "does not
    exist" are distinguishable -- see query_activity_history/ISS-23).
    Restricted to SUPERVISOR role.
    """
    try:
        return query_activity_history(activity_id=activity_id, schedule_id=schedule_context.schedule_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to query activity history for '%s': %s", activity_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve activity history for '{activity_id}'",
        )
