import logging
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.routers.graph import build_activity_graph
from backend.context import gates
from backend.context.schedule import ScheduleContext
from backend.shared.db import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/investigation", tags=["investigation"])


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


def build_investigation_context(
    activity_id: str,
    depth: int = 1,
    schedule_id: Optional[str] = None,
    conn: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Construct structured investigation context for an activity (Phase 6 Ask Why).

    Reuses Phase 5 build_activity_graph for deterministic, bounded traversal,
    cycle protection, and deduplication.
    Aggregates connected:
      - activity details
      - execution events
      - validation issues
      - conflict records
      - schedule impact preview
      - source evidence (references)
      - planner decisions
      - approved actuals
      - schedule dependencies
    Exposes only genuine database records; does not invent causal statements.
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

    # 1. Reuse Phase 5 graph traversal (validates root exists or raises 404)
    graph_data = build_activity_graph(
        activity_id=activity_id,
        depth=clean_depth,
        schedule_id=schedule_id,
        conn=conn,
    )

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    # Extract all entity IDs traversed within depth bounds
    activity_ids: List[str] = []
    root_act_data: Dict[str, Any] = {}
    execution_events: List[Dict[str, Any]] = []
    decisions: List[Dict[str, Any]] = []
    approved_actuals: List[Dict[str, Any]] = []

    for node in nodes:
        ntype = node.get("type")
        ndata = node.get("data", {})
        if ntype == "activity":
            aid = ndata.get("activity_id")
            if aid:
                activity_ids.append(aid)
                if aid == activity_id:
                    root_act_data = ndata
        elif ntype == "execution_event":
            execution_events.append(ndata)
        elif ntype == "decision":
            decisions.append(ndata)
        elif ntype == "approved_actual":
            approved_actuals.append(ndata)

    event_ids = [e.get("event_id") for e in execution_events if e.get("event_id")]

    # 2. Extract dependencies within graph
    dependencies: List[Dict[str, Any]] = []
    for edge in edges:
        if edge.get("type") == "dependency":
            src = edge.get("source", "").split(":")[-1]
            tgt = edge.get("target", "").split(":")[-1]
            dep_data = edge.get("data", {})
            dependencies.append({
                "predecessor_activity_id": src,
                "successor_activity_id": tgt,
                "relationship_type": dep_data.get("relationship_type", "FS"),
                "lag_days": dep_data.get("lag_days", 0.0),
            })

    # 3. Validation issues for traversed execution events
    validations: List[Dict[str, Any]] = []
    if event_ids:
        try:
            placeholders = ", ".join(["%s"] * len(event_ids))
            val_query = f"""
                SELECT issue_id, event_id, rule_code, severity, description
                FROM validation_issues
                WHERE event_id IN ({placeholders})
                ORDER BY issue_id ASC
            """
            validations = _execute_query(val_query, tuple(event_ids))
        except Exception as e:
            logger.debug("validation_issues query skipped or failed: %s", e)

    # 4. Conflict records for traversed activities or events (scoped to schedule)
    conflicts: List[Dict[str, Any]] = []
    resolved_sched_id = str(root_act_data.get("schedule_id") or schedule_id or "")
    try:
        conf_clauses = []
        conf_params: List[Any] = []
        if activity_ids:
            act_placeholders = ", ".join(["%s"] * len(activity_ids))
            conf_clauses.append(f"activity_id IN ({act_placeholders})")
            conf_params.extend(activity_ids)
        if event_ids:
            evt_placeholders = ", ".join(["%s"] * len(event_ids))
            conf_clauses.append(f"event_id_a IN ({evt_placeholders}) OR event_id_b IN ({evt_placeholders})")
            conf_params.extend(event_ids)
            conf_params.extend(event_ids)

        if conf_clauses:
            where_clause = " OR ".join(conf_clauses)
            if resolved_sched_id:
                where_clause = f"schedule_id = %s AND ({where_clause})"
                conf_params = [resolved_sched_id] + conf_params
            conf_query = f"""
                SELECT
                    conflict_id,
                    schedule_id,
                    activity_id,
                    reporting_period,
                    event_id_a,
                    event_id_b,
                    value_a,
                    value_b,
                    variance_pct,
                    status
                FROM conflict_records
                WHERE {where_clause}
                ORDER BY reporting_period DESC, conflict_id ASC
            """
            conflicts = _execute_query(conf_query, tuple(conf_params))
    except Exception as e:
        logger.debug("conflict_records query skipped or failed: %s", e)

    # 5. Evidence / source references
    evidence: List[Dict[str, Any]] = []
    if event_ids:
        try:
            placeholders = ", ".join(["%s"] * len(event_ids))
            ref_query = f"""
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
            evidence = _execute_query(ref_query, tuple(event_ids))
        except Exception as e:
            logger.debug("source_references query skipped or failed: %s", e)

    # Also capture photo evidence from events if present
    for ev in execution_events:
        photo = ev.get("photo_path")
        if photo:
            evidence.append({
                "reference_id": f"photo:{ev.get('event_id')}",
                "event_id": ev.get("event_id"),
                "file_name": photo,
                "raw_snippet": f"Attached photographic evidence: {photo}",
            })

    # 6. Schedule impact preview for root activity (Phase 2 constraint engine)
    impacts: List[Dict[str, Any]] = []
    try:
        from backend.routers.schedule import query_impact_preview
        impact_result = query_impact_preview(
            activity_id=activity_id,
            delay_days=0,
            conn=conn,
        )
        impacts = impact_result.get("impacts", [])
    except Exception as e:
        logger.debug("query_impact_preview skipped or failed in investigation: %s", e)

    # 7. Build structured summary without inventing causal claims
    summary = {
        "conflict_status": "present" if conflicts else "not_present",
        "validation_status": "present" if validations else "not_present",
        "impact_status": "present" if impacts else "not_present",
        "evidence_status": "present" if evidence else "not_present",
        "approved_actual_status": "present" if approved_actuals else "not_present",
        "dependencies_count": len(dependencies),
        "execution_events_count": len(execution_events),
        "decisions_count": len(decisions),
    }

    return {
        "root_activity_id": activity_id,
        "depth": clean_depth,
        "context": {
            "activity": root_act_data,
            "execution_events": execution_events,
            "validations": validations,
            "conflicts": conflicts,
            "impacts": impacts,
            "evidence": evidence,
            "decisions": decisions,
            "approved_actuals": approved_actuals,
            "dependencies": dependencies,
        },
        "summary": summary,
        "graph": graph_data,
    }


@router.get("/activity/{activity_id}")
def get_activity_investigation(
    activity_id: str,
    depth: int = Query(default=1, ge=0, le=10, description="Investigation graph depth"),
    schedule_id: Optional[str] = Query(default=None, description="Required (or X-Schedule-ID); validated against the project"),
    schedule_context: ScheduleContext = Depends(gates.events_view),
) -> Dict[str, Any]:
    """
    Supervisor Ask Why / Investigation Context Endpoint, within an EXPLICIT schedule of the caller's
    project (VIEW_EXECUTION_EVENTS).
    Returns:
      {
        "root_activity_id": "ACT-001",
        "depth": 1,
        "context": { ... },
        "summary": { ... },
        "graph": { ... }
      }
    """
    return build_investigation_context(
        activity_id=activity_id,
        depth=depth,
        schedule_id=schedule_context.schedule_id,
    )
