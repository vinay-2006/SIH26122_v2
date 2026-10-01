import logging
import uuid
from collections import deque
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.context import gates
from backend.context.schedule import ScheduleContext
from backend.shared.db import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["graph"])


def _serialize_row(row: Any) -> Dict[str, Any]:
    """Convert database row to JSON-serializable dictionary."""
    data = dict(row) if row else {}
    serialized = {}
    for k, v in data.items():
        if isinstance(v, (datetime, date, uuid.UUID)):
            serialized[k] = str(v)
        else:
            serialized[k] = v
    return serialized


def build_activity_graph(
    activity_id: str,
    depth: int = 1,
    schedule_id: Optional[str] = None,
    conn: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Dynamically construct a read-time execution graph centered on `activity_id`.

    Traverses up to `depth` dependency hops.
    Permitted node types:
      - activity
      - execution_event
      - decision
      - approved_actual

    Edge types:
      - has_execution_event (activity -> execution_event)
      - produces_decision (execution_event -> decision)
      - produces_actual (decision -> approved_actual)
      - dependency (activity -> activity)
    """
    clean_depth = max(0, int(depth))

    # Helper function to execute queries against conn or pooled connection
    def _execute_query(query: str, params: Tuple[Any, ...]) -> List[Dict[str, Any]]:
        if conn is not None:
            cur = conn.execute(query, params)
            rows = cur.fetchall()
            return [_serialize_row(r) for r in rows]
        with get_connection() as c:
            cur = c.execute(query, params)
            rows = cur.fetchall()
            return [_serialize_row(r) for r in rows]

    # 1. Fetch root activity
    root_query = "SELECT * FROM schedule_activities WHERE activity_id = %s"
    root_params: List[Any] = [activity_id]
    if schedule_id:
        root_query += " AND schedule_id = %s"
        root_params.append(schedule_id)

    root_rows = _execute_query(root_query, tuple(root_params))
    if not root_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Activity '{activity_id}' not found",
        )

    if not schedule_id and len(root_rows) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"Activity '{activity_id}' exists in multiple schedules; "
                    "provide schedule_id."),
        )

    resolved_schedule_id = str(root_rows[0]["schedule_id"])
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    visited_node_ids: Set[str] = set()
    visited_edge_keys: Set[Tuple[str, str, str]] = set()

    def add_node(node_id: str, node_type: str, data: Dict[str, Any]) -> None:
        if node_id not in visited_node_ids:
            visited_node_ids.add(node_id)
            nodes.append({
                "id": node_id,
                "type": node_type,
                "data": data,
            })

    def add_edge(source_id: str, target_id: str, edge_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        key = (source_id, target_id, edge_type)
        if key not in visited_edge_keys:
            visited_edge_keys.add(key)
            edge_obj: Dict[str, Any] = {
                "id": f"{edge_type}:{source_id}->{target_id}",
                "source": source_id,
                "target": target_id,
                "type": edge_type,
            }
            if data:
                edge_obj["data"] = data
            edges.append(edge_obj)

    # BFS queue for activity dependency traversal: (schedule_id, activity_id, current_hop)
    visited_activity_hops: Dict[Tuple[str, str], int] = {(resolved_schedule_id, activity_id): 0}
    queue: deque = deque([(resolved_schedule_id, activity_id, 0)])

    while queue:
        curr_sched_id, curr_act_id, curr_hop = queue.popleft()

        # Fetch activity details
        act_rows = _execute_query(
            "SELECT * FROM schedule_activities WHERE schedule_id = %s AND activity_id = %s",
            (resolved_schedule_id, curr_act_id),
        )
        act_data = act_rows[0] if act_rows else {"activity_id": curr_act_id}
        act_node_id = f"activity:{resolved_schedule_id}:{curr_act_id}"
        add_node(act_node_id, "activity", act_data)

        # 2. Execution path for this activity:
        # a) Execution events
        events_query = """
            SELECT
                ee.event_id,
                ee.schedule_id,
                ee.event_date,
                ee.raw_claim_text,
                ee.input_channel,
                ee.matched_activity_id,
                ee.reported_activity_id,
                ee.discipline,
                ee.claim_mode,
                ee.claimed_quantity,
                ee.claimed_pct,
                ee.delay_reason,
                ee.photo_path,
                ee.status,
                ee.created_at
            FROM execution_events ee
            WHERE ee.schedule_id = %s
              AND (ee.matched_activity_id = %s
               OR ee.reported_activity_id = %s
               OR ee.event_id IN (
                    SELECT pd.event_id FROM planner_decisions pd
                    JOIN execution_events pdee ON pdee.event_id = pd.event_id
                    WHERE pd.selected_activity_id = %s AND pdee.schedule_id = %s
               ))
            ORDER BY ee.event_date ASC, ee.created_at ASC, ee.event_id ASC
        """
        try:
            event_rows = _execute_query(events_query, (resolved_schedule_id, curr_act_id, curr_act_id, curr_act_id, resolved_schedule_id))
        except Exception:
            # Fallback for minimal test DB setups lacking delay_reason or photo_path
            fallback_events_query = """
                SELECT
                    ee.event_id,
                    ee.schedule_id,
                    ee.event_date,
                    ee.raw_claim_text,
                    ee.input_channel,
                    ee.matched_activity_id,
                    ee.reported_activity_id,
                    ee.discipline,
                    ee.claim_mode,
                    ee.claimed_quantity,
                    ee.claimed_pct,
                    ee.status,
                    ee.created_at
                FROM execution_events ee
                WHERE ee.schedule_id = %s
                  AND (ee.matched_activity_id = %s
                   OR ee.reported_activity_id = %s
                   OR ee.event_id IN (
                        SELECT pd.event_id FROM planner_decisions pd
                        JOIN execution_events pdee ON pdee.event_id = pd.event_id
                        WHERE pd.selected_activity_id = %s AND pdee.schedule_id = %s
                   ))
                ORDER BY ee.event_date ASC, ee.created_at ASC, ee.event_id ASC
            """
            event_rows = _execute_query(fallback_events_query, (resolved_schedule_id, curr_act_id, curr_act_id, curr_act_id, resolved_schedule_id))
        for evt in event_rows:
            evt_node_id = f"event:{evt['event_id']}"
            add_node(evt_node_id, "execution_event", evt)
            add_edge(act_node_id, evt_node_id, "has_execution_event")

        # b) Decisions
        decisions_query = """
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
            JOIN execution_events decision_event ON decision_event.event_id = pd.event_id
            WHERE decision_event.schedule_id = %s
              AND (pd.selected_activity_id = %s
               OR pd.event_id IN (
                    SELECT event_id FROM execution_events
                    WHERE schedule_id = %s AND (matched_activity_id = %s OR reported_activity_id = %s)
               ))
            ORDER BY pd.decided_at ASC, pd.decision_id ASC
        """
        decision_rows = _execute_query(decisions_query, (resolved_schedule_id, curr_act_id, resolved_schedule_id, curr_act_id, curr_act_id))
        for dec in decision_rows:
            dec_node_id = f"decision:{dec['decision_id']}"
            add_node(dec_node_id, "decision", dec)
            evt_node_id = f"event:{dec['event_id']}"
            if evt_node_id in visited_node_ids:
                add_edge(evt_node_id, dec_node_id, "produces_decision")
            else:
                add_edge(act_node_id, dec_node_id, "has_decision")

        # c) Approved Actuals
        try:
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
                WHERE schedule_id = %s AND activity_id = %s
                ORDER BY created_at ASC, actual_id ASC
            """
            actual_rows = _execute_query(actuals_query, (resolved_schedule_id, curr_act_id))
            for actl in actual_rows:
                actl_node_id = f"actual:{actl['actual_id']}"
                add_node(actl_node_id, "approved_actual", actl)
                dec_node_id = f"decision:{actl['decision_id']}"
                if dec_node_id in visited_node_ids:
                    add_edge(dec_node_id, actl_node_id, "produces_actual")
                else:
                    evt_node_id = f"event:{actl['event_id']}"
                    if evt_node_id in visited_node_ids:
                        add_edge(evt_node_id, actl_node_id, "produces_actual")
                    else:
                        add_edge(act_node_id, actl_node_id, "produces_actual")
        except Exception as e:
            logger.debug("approved_actuals query skipped or failed: %s", e)

        # 3. Traverse dependencies if within depth limit
        if curr_hop < clean_depth:
            dep_query = """
                SELECT
                    dependency_id,
                    schedule_id,
                    predecessor_activity_id,
                    successor_activity_id,
                    relationship_type,
                    lag_days
                FROM schedule_dependencies
                WHERE schedule_id = %s
                  AND (predecessor_activity_id = %s OR successor_activity_id = %s)
            """
            dep_rows = _execute_query(dep_query, (resolved_schedule_id, curr_act_id, curr_act_id))

            for dep in dep_rows:
                pred = dep["predecessor_activity_id"]
                succ = dep["successor_activity_id"]
                neighbor = succ if pred == curr_act_id else pred

                next_hop = curr_hop + 1
                neighbor_key = (resolved_schedule_id, neighbor)
                if neighbor_key not in visited_activity_hops:
                    visited_activity_hops[neighbor_key] = next_hop
                    queue.append((resolved_schedule_id, neighbor, next_hop))

                # Add dependency edge if neighbor is within depth
                if visited_activity_hops[neighbor_key] <= clean_depth:
                    pred_node_id = f"activity:{resolved_schedule_id}:{pred}"
                    succ_node_id = f"activity:{resolved_schedule_id}:{succ}"
                    dep_data = {
                        "relationship_type": dep.get("relationship_type", "FS"),
                        "lag_days": float(dep.get("lag_days") or 0.0),
                    }
                    add_edge(pred_node_id, succ_node_id, "dependency", dep_data)

    return {
        "nodes": nodes,
        "edges": edges,
    }


@router.get("/graph/activity/{activity_id}")
def get_activity_graph_endpoint(
    activity_id: str,
    depth: int = Query(default=1, ge=0, le=10, description="Graph traversal depth"),
    schedule_id: Optional[str] = Query(default=None, description="Required (or X-Schedule-ID); validated against the project"),
    schedule_context: ScheduleContext = Depends(gates.events_view),
) -> Dict[str, Any]:
    """
    Read-time knowledge execution graph centered on activity_id, within an EXPLICIT schedule of the
    caller's project (VIEW_EXECUTION_EVENTS).
    Returns:
      {
        "nodes": [...],
        "edges": [...]
      }
    """
    return build_activity_graph(
        activity_id=activity_id,
        depth=depth,
        schedule_id=schedule_context.schedule_id,
    )
