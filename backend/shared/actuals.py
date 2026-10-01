import logging
import uuid
from datetime import date
from typing import Any, Optional, Union

from backend.shared.db import get_connection
from backend.shared.schemas import ExecutionState

logger = logging.getLogger(__name__)


def get_approved_actual(
    schedule_id: str,
    activity_id: str,
) -> Optional[dict]:
    """
    Return the latest approved actual for an activity.

    Approved actuals are the authoritative progress values.
    """
    with get_connection() as conn:
        row = conn.execute(
            """
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
                exported_at,
                created_at
            FROM approved_actuals
            WHERE schedule_id = %s
              AND activity_id = %s
            """,
            (schedule_id, activity_id),
        ).fetchone()

    return dict(row) if row else None


def get_approved_pct(
    schedule_id: str,
    activity_id: str,
) -> float:
    """
    Return approved percentage complete.

    Returns 0.0 when no approved actual exists.
    """
    actual = get_approved_actual(schedule_id, activity_id)

    if not actual:
        return 0.0

    return float(actual["actual_pct_complete"] or 0.0)


def get_execution_state(
    activity: Optional[Any] = None,
    approved_actual: Optional[Any] = None,
    *,
    actual_pct_complete: Optional[float] = None,
    actual_start: Optional[Any] = None,
) -> str:
    """
    Canonical execution-state interpretation for SIH26122 (Phase 1C Rule E).

    Priority:
        1. actual_pct_complete >= 100 -> COMPLETED (precedes actual_start)
        2. actual_start IS NOT NULL   -> IN_PROGRESS
        3. else                       -> NOT_STARTED

    Direct keyword arguments take precedence over attributes extracted from
    `approved_actual` or `activity`.
    """
    pct = actual_pct_complete
    start = actual_start

    # If not supplied as direct keyword arguments, inspect approved_actual then activity
    if pct is None or start is None:
        for source in (approved_actual, activity):
            if source is None:
                continue
            if isinstance(source, dict):
                if pct is None:
                    pct = source.get("actual_pct_complete")
                if start is None:
                    start = source.get("actual_start")
            else:
                if pct is None:
                    pct = getattr(source, "actual_pct_complete", None)
                if start is None:
                    start = getattr(source, "actual_start", None)

    # 1. actual_pct_complete >= 100 -> COMPLETED (takes precedence over actual_start)
    if pct is not None:
        try:
            if float(pct) >= 100.0:
                return ExecutionState.COMPLETED.value
        except (ValueError, TypeError):
            pass

    # 2. actual_start IS NOT NULL -> IN_PROGRESS
    if start is not None and str(start).strip() != "":
        return ExecutionState.IN_PROGRESS.value

    # 3. else -> NOT_STARTED
    return ExecutionState.NOT_STARTED.value


def _dispatch_adapters(actual: dict) -> dict:
    """
    Phase 1 adapter boundary hook.
    Establishes the shared actuals boundary required by subsequent phases.
    Later phases will invoke CSVExportAdapter and P6RestAdapter here.
    This hook is strictly non-blocking and isolated.
    """
    return {
        "csv_export": "not_implemented_phase1",
        "p6_rest": "not_implemented_phase1",
    }


def approved_split_quantity(conn: Any, schedule_id: Optional[str], activity_id: str) -> float:
    """
    Approved incremental quantity contributed to `activity_id` by WBS-split
    (decomposed) claims: for each split claim whose LATEST decision is
    APPROVE/EDIT, COALESCE(approved_qty, claimed_quantity) x split_pct.
    Source-decision based like every other quantity recalculation -- never a
    running total.
    """
    row = conn.execute(
        """
        SELECT COALESCE(SUM(COALESCE(pd.approved_qty, ee.claimed_quantity) * s.split_pct), 0.0) AS qty
        FROM execution_events ee
        JOIN planner_decisions pd ON pd.event_id = ee.event_id
        JOIN claim_activity_splits s ON s.event_id = ee.event_id AND s.activity_id = %s
        WHERE (CAST(%s AS TEXT) IS NULL OR ee.schedule_id = %s)
          AND ee.claim_mode = 'INCREMENTAL_QUANTITY'
          AND pd.action IN ('APPROVE', 'EDIT')
          AND pd.decision_id = (
            SELECT pd2.decision_id FROM planner_decisions pd2
            WHERE pd2.event_id = ee.event_id
            ORDER BY pd2.decided_at DESC, pd2.decision_id DESC
            LIMIT 1
          )
        """,
        (activity_id, schedule_id, schedule_id),
    ).fetchone()
    return float(row["qty"]) if row and row["qty"] is not None else 0.0


def _execute_upsert(
    conn: Any,
    schedule_id: str,
    activity_id: str,
    event_id: str,
    decision_id: str,
    **fields: Any,
) -> Optional[dict]:
    """
    Internal transactional implementation of upsert_approved_actual.
    """
    # 1. Inspect planner_decisions if present to verify authority and action
    dec_row = conn.execute(
        """
        SELECT decision_id, event_id, selected_activity_id, action, approved_pct, approved_qty
        FROM planner_decisions
        WHERE decision_id = %s
        """,
        (decision_id,),
    ).fetchone()

    pd_action = dec_row["action"] if dec_row else None
    pd_selected_activity = dec_row["selected_activity_id"] if dec_row else None
    pd_approved_pct = dec_row["approved_pct"] if dec_row else None
    pd_approved_qty = dec_row["approved_qty"] if dec_row else None

    # WBS split child (Feature 30): the claim is decomposed, so the caller names the child
    # activity and its share. The decision's own selected_activity_id is just the primary child.
    split_pct = fields.get("split_pct")
    if split_pct is not None:
        split_pct = float(split_pct)

    # Authority: After planner review, planner_decisions.selected_activity_id is authoritative
    if pd_selected_activity and split_pct is None:
        activity_id = pd_selected_activity

    action = fields.get("action") or pd_action
    if action in ("HOLD", "REJECT"):
        # REJECT and HOLD do not write to approved_actuals
        return None

    # 2. Inspect execution_events for claim metadata if present
    ev_row = conn.execute(
        """
        SELECT event_id, schedule_id, event_date, event_type, claim_mode,
               claimed_quantity, claimed_pct, matched_activity_id
        FROM execution_events
        WHERE event_id = %s
        """,
        (event_id,),
    ).fetchone()

    claim_mode = (
        fields.get("claim_mode")
        or (ev_row["claim_mode"] if ev_row else None)
        or "CUMULATIVE_PCT"
    )
    event_type = fields.get("event_type") or (ev_row["event_type"] if ev_row else None)
    event_date = fields.get("event_date") or (ev_row["event_date"] if ev_row else None)
    ev_claimed_pct = ev_row["claimed_pct"] if ev_row else None

    # 3. Lock and retrieve existing approved_actuals row for (schedule_id, activity_id)
    existing_row = conn.execute(
        """
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
            exported_at,
            created_at
        FROM approved_actuals
        WHERE schedule_id = %s
          AND activity_id = %s
        FOR UPDATE
        """,
        (schedule_id, activity_id),
    ).fetchone()

    existing = dict(existing_row) if existing_row else None

    # 4. Start/Finish date merging (Rule B)
    # Never erase existing start when updating finish; never erase finish when updating start
    incoming_start = fields.get("actual_start")
    incoming_finish = fields.get("actual_finish")

    if incoming_start is None and event_type == "ACTUAL_START":
        incoming_start = event_date
    if incoming_finish is None and event_type == "ACTUAL_FINISH":
        incoming_finish = event_date

    final_start = (
        incoming_start
        if incoming_start is not None
        else (existing["actual_start"] if existing else None)
    )
    final_finish = (
        incoming_finish
        if incoming_finish is not None
        else (existing["actual_finish"] if existing else None)
    )

    # 5. Cumulative percentage (Rule C)
    # Replaces the previous actual_pct_complete; does not add percentages
    if claim_mode == "CUMULATIVE_PCT" or "approved_pct" in fields or "actual_pct_complete" in fields:
        pct_candidate = None
        if fields.get("approved_pct") is not None:
            pct_candidate = fields["approved_pct"]
        elif pd_approved_pct is not None:
            pct_candidate = pd_approved_pct
        elif fields.get("actual_pct_complete") is not None:
            pct_candidate = fields["actual_pct_complete"]
        elif fields.get("claimed_pct") is not None:
            pct_candidate = fields["claimed_pct"]
        elif ev_claimed_pct is not None:
            pct_candidate = ev_claimed_pct

        if pct_candidate is not None:
            final_pct = float(pct_candidate)
            if split_pct is not None:
                final_pct = final_pct * split_pct  # contribution = claim value x split_pct
            final_pct = max(0.0, min(100.0, final_pct))
        else:
            final_pct = (
                float(existing["actual_pct_complete"])
                if existing and existing["actual_pct_complete"] is not None
                else None
            )
    else:
        final_pct = (
            float(existing["actual_pct_complete"])
            if existing and existing["actual_pct_complete"] is not None
            else fields.get("actual_pct_complete")
        )

    # 6. Incremental quantity (Rule D)
    # Recalculate from database source rows using canonical PRD v5 query
    if claim_mode == "INCREMENTAL_QUANTITY":
        recalc_row = conn.execute(
            """
            SELECT COALESCE(SUM(COALESCE(pd.approved_qty, ee.claimed_quantity)), 0.0) AS total_qty
            FROM execution_events ee
            JOIN planner_decisions pd ON pd.event_id = ee.event_id
            WHERE ee.schedule_id = %s
              AND pd.selected_activity_id = %s
              AND pd.action IN ('APPROVE', 'EDIT')
              AND pd.decision_id = (
                SELECT pd2.decision_id FROM planner_decisions pd2
                WHERE pd2.event_id = ee.event_id
                ORDER BY pd2.decided_at DESC, pd2.decision_id DESC
                LIMIT 1
              )
              AND NOT EXISTS (
                SELECT 1 FROM claim_activity_splits s WHERE s.event_id = ee.event_id
              )
            """,
            (schedule_id, activity_id),
        ).fetchone()

        recalculated_qty = (
            float(recalc_row["total_qty"])
            if recalc_row and recalc_row["total_qty"] is not None
            else 0.0
        )
        # Split claims are excluded above (their decision names only the primary
        # child); add every split child's share of every such approved claim.
        recalculated_qty += approved_split_quantity(conn, schedule_id, activity_id)

        if recalculated_qty > 0.0:
            final_quantity = recalculated_qty
        elif fields.get("actual_quantity") is not None:
            final_quantity = float(fields["actual_quantity"])
        elif fields.get("approved_qty") is not None:
            final_quantity = float(fields["approved_qty"])
        elif pd_approved_qty is not None:
            final_quantity = float(pd_approved_qty)
        else:
            final_quantity = recalculated_qty
    else:
        final_quantity = (
            float(fields["actual_quantity"])
            if fields.get("actual_quantity") is not None
            else (
                float(existing["actual_quantity"])
                if existing and existing["actual_quantity"] is not None
                else None
            )
        )

    # 7. Write to approved_actuals (Rule A & Rule F)
    if existing:
        saved_row = conn.execute(
            """
            UPDATE approved_actuals
            SET
                decision_id = %s,
                event_id = %s,
                actual_start = %s,
                actual_finish = %s,
                actual_pct_complete = %s,
                actual_quantity = %s
            WHERE schedule_id = %s
              AND activity_id = %s
            RETURNING
                actual_id,
                decision_id,
                event_id,
                schedule_id,
                activity_id,
                actual_start,
                actual_finish,
                actual_pct_complete,
                actual_quantity,
                exported_at,
                created_at
            """,
            (
                decision_id,
                event_id,
                final_start,
                final_finish,
                final_pct,
                final_quantity,
                schedule_id,
                activity_id,
            ),
        ).fetchone()
    else:
        new_actual_id = str(uuid.uuid4())
        saved_row = conn.execute(
            """
            INSERT INTO approved_actuals (
                actual_id,
                decision_id,
                event_id,
                schedule_id,
                activity_id,
                actual_start,
                actual_finish,
                actual_pct_complete,
                actual_quantity
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (schedule_id, activity_id) DO UPDATE SET
                decision_id = EXCLUDED.decision_id,
                event_id = EXCLUDED.event_id,
                actual_start = COALESCE(EXCLUDED.actual_start, approved_actuals.actual_start),
                actual_finish = COALESCE(EXCLUDED.actual_finish, approved_actuals.actual_finish),
                actual_pct_complete = COALESCE(EXCLUDED.actual_pct_complete, approved_actuals.actual_pct_complete),
                actual_quantity = COALESCE(EXCLUDED.actual_quantity, approved_actuals.actual_quantity)
            RETURNING
                actual_id,
                decision_id,
                event_id,
                schedule_id,
                activity_id,
                actual_start,
                actual_finish,
                actual_pct_complete,
                actual_quantity,
                exported_at,
                created_at
            """,
            (
                new_actual_id,
                decision_id,
                event_id,
                schedule_id,
                activity_id,
                final_start,
                final_finish,
                final_pct,
                final_quantity,
            ),
        ).fetchone()

    # V7 ownership stamp: the approved actual belongs to the activity's project/stage. Only executed
    # when the caller (the project-scoped decisions router) supplies it; V6 callers are unchanged.
    if saved_row is not None and fields.get("project_id") is not None:
        stamped = conn.execute(
            """
            UPDATE approved_actuals
            SET project_id = COALESCE(project_id, %s), stage_id = COALESCE(stage_id, %s)
            WHERE schedule_id = %s AND activity_id = %s
            RETURNING project_id, stage_id
            """,
            (fields["project_id"], fields.get("stage_id"), schedule_id, activity_id),
        ).fetchone()
        result = dict(saved_row)
        if stamped:
            result["project_id"] = stamped["project_id"]
            result["stage_id"] = stamped["stage_id"]
        return result

    result = dict(saved_row) if saved_row else None
    return result


def upsert_approved_actual(
    schedule_id: str,
    activity_id: str,
    event_id: str,
    decision_id: str,
    **fields: Any,
) -> Optional[dict]:
    """
    Canonical approved-actual upsert according to PRD v5 Section 15.

    - Merges start/finish dates without erasing existing values.
    - Replaces cumulative percentages (does not add).
    - Recalculates incremental quantities from latest APPROVE/EDIT planner decisions.
    - Honors selected_activity_id from planner_decisions as authoritative.
    - Safely skips write for HOLD and REJECT actions.
    - Commits approved_actuals database transaction before triggering adapters.
    - Dispatches isolated, non-blocking auto-export hook post-commit.
    """
    fields_copy = dict(fields)
    external_conn = fields_copy.pop("conn", None)

    if external_conn is not None:
        result = _execute_upsert(
            external_conn,
            schedule_id,
            activity_id,
            event_id,
            decision_id,
            **fields_copy,
        )
        if result is None:
            return None

        if hasattr(external_conn, "commit"):
            external_conn.commit()

        try:
            from backend.routers.export import trigger_auto_export
            trigger_auto_export(conn=external_conn, schedule_id=schedule_id)
        except Exception as e:
            logger.warning("Auto-triggered CSV export error (non-blocking): %s", e)

        try:
            from backend.shared.p6 import trigger_p6_actual_push
            trigger_p6_actual_push(actual=result)
        except Exception as e:
            logger.warning("Auto-triggered P6 push error (non-blocking): %s", e)

        return result

    with get_connection() as conn:
        result = _execute_upsert(
            conn,
            schedule_id,
            activity_id,
            event_id,
            decision_id,
            **fields_copy,
        )
        if result is None:
            return None

        conn.commit()

        try:
            from backend.routers.export import trigger_auto_export
            trigger_auto_export(conn=conn, schedule_id=schedule_id)
        except Exception as e:
            logger.warning("Auto-triggered CSV export error (non-blocking): %s", e)

        try:
            from backend.shared.p6 import trigger_p6_actual_push
            trigger_p6_actual_push(actual=result)
        except Exception as e:
            logger.warning("Auto-triggered P6 push error (non-blocking): %s", e)

        return result
