import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.context import gates
from backend.context.schedule import ScheduleContext
from backend.shared.db import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/schedule", tags=["schedule"])


@router.get("/health")
def health():
    return {"router": "schedule", "status": "ok"}


def _parse_date(val: Any) -> Optional[date]:
    """Parse a date from string, date, or datetime object."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


from backend.shared.actuals import get_execution_state
from backend.shared.schemas import ExecutionState
from backend.shared.impact import (
    compare_candidate_impacts,
    evaluate_constraint,
    evaluate_successor_impact,
    parse_date,
    select_controlling_constraint,
)


MAX_IMPACT_HOPS = 3


def query_impact_preview(
    activity_id: str,
    delay_days: int,
    schedule_id: Optional[str] = None,
    conn: Optional[Any] = None,
) -> dict:
    """
    Downstream schedule impact preview for an activity and hypothetical delay (Phase 2 A1).

    Features:
    1. Validates delay_days >= 0.
    2. Resolves target activity across schedule_activities scoped by schedule_id.
    3. Bounded iterative frontier traversal up to MAX_IMPACT_HOPS = 3.
    4. Frontier-batched queries for outgoing edges, incoming edges, activities, and actuals (strictly avoids N+1).
    5. Reconciles constraints onto common REQUIRED SUCCESSOR START dimension.
    6. Selects controlling predecessor with deterministic 4-tier tie-breaking.
    7. Applies canonical execution-state gating (COMPLETED successor -> 0 impact and no downstream propagation).
    8. Handles Float NULL contract (NULL -> UNKNOWN, halts numeric propagation along branch).
    9. Propagates remaining net_delay downstream after float absorption (without repeatedly re-adding target delay).
    10. Cycle protection via visited tracking preventing infinite loops.
    11. Converging paths reconciled through full A1 constraint evaluation (strongest schedule impact retained).
    12. Preserves backward-compatible response fields while exposing structured multi-hop path trace.
    """
    if delay_days is None or delay_days < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="delay_days must be a non-negative integer",
        )

    def _fetch_one(query: str, params: tuple):
        if conn is not None:
            return conn.execute(query, params).fetchone()
        with get_connection() as c:
            return c.execute(query, params).fetchone()

    def _fetch_all(query: str, params: tuple):
        if conn is not None:
            return conn.execute(query, params).fetchall()
        with get_connection() as c:
            return c.execute(query, params).fetchall()

    target_query = """
        SELECT activity_id, schedule_id, activity_name, planned_start, planned_finish,
               total_float, is_critical
        FROM schedule_activities
        WHERE activity_id = %s
    """
    target_params: List[Any] = [activity_id]
    if schedule_id:
        target_query += " AND schedule_id = %s"
        target_params.append(schedule_id)

    try:
        target_rows = _fetch_all(target_query, tuple(target_params))
    except Exception:
        fallback_query = """
            SELECT activity_id, schedule_id, activity_name, planned_start, planned_finish
            FROM schedule_activities
            WHERE activity_id = %s
        """
        if schedule_id:
            fallback_query += " AND schedule_id = %s"
        target_rows = _fetch_all(fallback_query, tuple(target_params))

    if not target_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Activity '{activity_id}' not found",
        )

    if not schedule_id and len(target_rows) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"Activity '{activity_id}' exists in multiple schedules; "
                    "provide schedule_id."),
        )

    target_dict = dict(target_rows[0])
    resolved_schedule_id = str(target_dict["schedule_id"])
    schedule_id = resolved_schedule_id

    # Traversal state
    activities_cache: Dict[str, Dict[str, Any]] = {activity_id: target_dict}
    actuals_cache: Dict[str, Dict[str, Any]] = {}

    propagated_delays: Dict[str, int] = {activity_id: delay_days}
    paths: Dict[str, List[str]] = {activity_id: [activity_id]}
    impacts_by_activity: Dict[str, Dict[str, Any]] = {}
    visited_depths: Dict[str, int] = {activity_id: 0}

    def _ensure_activities_and_actuals(act_ids: List[str]):
        missing_ids = [aid for aid in act_ids if aid not in activities_cache]
        if not missing_ids:
            return
        placeholders = ", ".join(["%s"] * len(missing_ids))
        try:
            q = f"""
                SELECT activity_id, schedule_id, activity_name, planned_start, planned_finish,
                       total_float, is_critical
                FROM schedule_activities
                WHERE schedule_id = %s
                  AND activity_id IN ({placeholders})
            """
            rows = _fetch_all(q, tuple([schedule_id] + missing_ids))
        except Exception:
            q = f"""
                SELECT activity_id, schedule_id, activity_name, planned_start, planned_finish
                FROM schedule_activities
                WHERE schedule_id = %s
                  AND activity_id IN ({placeholders})
            """
            rows = _fetch_all(q, tuple([schedule_id] + missing_ids))
        for r in rows:
            activities_cache[str(r["activity_id"])] = dict(r)

        try:
            q_act = f"""
                SELECT activity_id, schedule_id, actual_start, actual_finish, actual_pct_complete
                FROM approved_actuals
                WHERE schedule_id = %s
                  AND activity_id IN ({placeholders})
            """
            act_rows = _fetch_all(q_act, tuple([schedule_id] + missing_ids))
            for r in act_rows:
                actuals_cache[str(r["activity_id"])] = dict(r)
        except Exception:
            pass

    current_frontier = [activity_id]

    for hop in range(1, MAX_IMPACT_HOPS + 1):
        if not current_frontier:
            break

        # 1. Batch query outgoing dependencies for current frontier
        frontier_placeholders = ", ".join(["%s"] * len(current_frontier))
        try:
            outgoing_query = f"""
                SELECT dependency_id, schedule_id, predecessor_activity_id, successor_activity_id,
                       relationship_type, lag_days
                FROM schedule_dependencies
                WHERE schedule_id = %s
                  AND predecessor_activity_id IN ({frontier_placeholders})
            """
            outgoing_rows = _fetch_all(outgoing_query, tuple([schedule_id] + current_frontier))
        except Exception:
            outgoing_query = f"""
                SELECT dependency_id, schedule_id, predecessor_activity_id, successor_activity_id,
                       relationship_type
                FROM schedule_dependencies
                WHERE schedule_id = %s
                  AND predecessor_activity_id IN ({frontier_placeholders})
            """
            outgoing_rows = _fetch_all(outgoing_query, tuple([schedule_id] + current_frontier))

        if not outgoing_rows:
            break

        # 2. Collect discovered candidate successors (excluding target)
        candidate_succ_ids = sorted(list(set(
            str(r["successor_activity_id"]) for r in outgoing_rows
            if str(r["successor_activity_id"]) != activity_id
        )))
        if not candidate_succ_ids:
            break

        # 3. Batch query incoming dependencies for all candidate successors
        succ_placeholders = ", ".join(["%s"] * len(candidate_succ_ids))
        try:
            incoming_query = f"""
                SELECT dependency_id, schedule_id, predecessor_activity_id, successor_activity_id,
                       relationship_type, lag_days
                FROM schedule_dependencies
                WHERE schedule_id = %s
                  AND successor_activity_id IN ({succ_placeholders})
                ORDER BY successor_activity_id ASC, predecessor_activity_id ASC
            """
            incoming_rows = _fetch_all(incoming_query, tuple([schedule_id] + candidate_succ_ids))
        except Exception:
            incoming_query = f"""
                SELECT dependency_id, schedule_id, predecessor_activity_id, successor_activity_id,
                       relationship_type
                FROM schedule_dependencies
                WHERE schedule_id = %s
                  AND successor_activity_id IN ({succ_placeholders})
                ORDER BY successor_activity_id ASC, predecessor_activity_id ASC
            """
            incoming_rows = _fetch_all(incoming_query, tuple([schedule_id] + candidate_succ_ids))

        # 4. Batch query activities and actuals
        all_needed = set(candidate_succ_ids)
        for r in incoming_rows:
            all_needed.add(str(r["predecessor_activity_id"]))
        _ensure_activities_and_actuals(sorted(list(all_needed)))

        # 5. Group incoming dependencies by successor_id
        succ_incoming_map: Dict[str, List[Dict[str, Any]]] = {sid: [] for sid in candidate_succ_ids}
        for dep in incoming_rows:
            sid = str(dep["successor_activity_id"])
            if sid in succ_incoming_map:
                succ_incoming_map[sid].append(dict(dep))

        next_frontier = []

        # 6. Evaluate each candidate successor
        for succ_id in candidate_succ_ids:
            succ_act = activities_cache.get(succ_id)
            if not succ_act:
                continue

            succ_actual = actuals_cache.get(succ_id)
            succ_state = get_execution_state(activity=succ_act, approved_actual=succ_actual)

            constraints_list = []
            for dep in succ_incoming_map.get(succ_id, []):
                pred_id = str(dep["predecessor_activity_id"])
                pred_act = activities_cache.get(pred_id, {})
                pred_delay = propagated_delays.get(pred_id, 0)

                c_eval = evaluate_constraint(
                    predecessor_id=pred_id,
                    predecessor_start=parse_date(pred_act.get("planned_start")),
                    predecessor_finish=parse_date(pred_act.get("planned_finish")),
                    successor_id=succ_id,
                    successor_planned_start=parse_date(succ_act.get("planned_start")),
                    successor_planned_finish=parse_date(succ_act.get("planned_finish")),
                    relationship_type=str(dep.get("relationship_type") or "FS"),
                    lag_days=float(dep.get("lag_days") or 0.0),
                    is_target_predecessor=(pred_id == activity_id),
                    simulated_delay_days=pred_delay,
                )
                constraints_list.append(c_eval)

            # Trace path
            controlling_pred = None
            controlling_c = select_controlling_constraint(constraints_list)
            if controlling_c:
                controlling_pred = controlling_c.predecessor_activity_id

            if controlling_pred and controlling_pred in paths:
                resolved_path = paths[controlling_pred] + [succ_id]
            else:
                frontier_parent = next((str(r["predecessor_activity_id"]) for r in outgoing_rows if str(r["successor_activity_id"]) == succ_id and str(r["predecessor_activity_id"]) in paths), None)
                if frontier_parent:
                    resolved_path = paths[frontier_parent] + [succ_id]
                else:
                    resolved_path = [activity_id, succ_id]

            impact_result = evaluate_successor_impact(
                successor=succ_act,
                execution_state=succ_state,
                constraints=constraints_list,
                target_activity_id=activity_id,
                propagation_depth=hop,
                target_path=resolved_path,
            )

            # Converging-path deduplication: retain stronger schedule impact via full A1 semantics
            if succ_id in impacts_by_activity:
                existing = impacts_by_activity[succ_id]
                if compare_candidate_impacts(impact_result, existing) > 0:
                    impacts_by_activity[succ_id] = impact_result
            else:
                impacts_by_activity[succ_id] = impact_result


            # 7. Next frontier eligibility
            final_eval = impacts_by_activity[succ_id]
            net_delay = final_eval.get("net_delay_days")
            float_st = final_eval.get("float_status")
            is_comp = (succ_state == ExecutionState.COMPLETED.value or succ_state == "COMPLETED")

            can_propagate = (
                hop < MAX_IMPACT_HOPS
                and not is_comp
                and float_st == "KNOWN"
                and net_delay is not None
                and net_delay > 0
            )

            if can_propagate:
                propagated_delays[succ_id] = net_delay
                paths[succ_id] = final_eval.get("target_path", [activity_id, succ_id])
                if succ_id not in visited_depths:
                    visited_depths[succ_id] = hop
                    next_frontier.append(succ_id)

        current_frontier = next_frontier

    target_act = activities_cache.get(activity_id, {})
    target_p_start = parse_date(target_act.get("planned_start"))
    target_p_finish = parse_date(target_act.get("planned_finish"))
    shifted_target_finish = (target_p_finish + timedelta(days=delay_days)).isoformat() if target_p_finish else ""

    for imp in impacts_by_activity.values():
        s_id = imp["successor_activity_id"]
        act_info = activities_cache.get(s_id, {})
        imp["activity_name"] = act_info.get("activity_name") or s_id
        orig_fin = act_info.get("planned_finish")
        imp["original_planned_finish"] = str(orig_fin)[:10] if orig_fin else ""
        net_d = imp.get("net_delay_days")
        if orig_fin and net_d is not None and imp.get("float_status") != "UNKNOWN":
            p_fin = parse_date(orig_fin)
            if p_fin:
                imp["shifted_earliest_finish"] = (p_fin + timedelta(days=net_d)).isoformat()
            else:
                imp["shifted_earliest_finish"] = ""
        else:
            imp["shifted_earliest_finish"] = ""

    final_impacts = list(impacts_by_activity.values())
    final_impacts.sort(key=lambda x: (x.get("propagation_depth", 1), x["successor_activity_id"]))

    return {
        "activity_id": activity_id,
        "activity_name": target_act.get("activity_name") or activity_id,
        "planned_start": target_p_start.isoformat() if target_p_start else "",
        "planned_finish": target_p_finish.isoformat() if target_p_finish else "",
        "shifted_finish": shifted_target_finish,
        "schedule_id": resolved_schedule_id,
        "delay_days": delay_days,
        "propagation_depth_limit": MAX_IMPACT_HOPS,
        "impacts": final_impacts,
    }


@router.get("/{activity_id}/impact-preview")
def get_impact_preview(
    activity_id: str,
    delay_days: int = Query(..., ge=0, description="Hypothetical delay in days"),
    schedule_id: Optional[str] = Query(None, description="Required (or X-Schedule-ID); validated against the project"),
    schedule_context: ScheduleContext = Depends(gates.claim_review_schedule),
):
    """
    LEGACY quick impact preview (V6 shape) within an EXPLICIT schedule of the caller's project.
    The V7 compound-impact engine is /projects/{project_id}/schedules/{schedule_id}/impact/*.
    """
    try:
        return query_impact_preview(activity_id=activity_id, delay_days=delay_days, schedule_id=schedule_context.schedule_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to generate impact preview for '%s': %s", activity_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate impact preview for '{activity_id}'",
        )
