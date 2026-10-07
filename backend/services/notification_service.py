"""
Notification service (SETUAI V7): supervisor decision -> the site engineer who reported the claim.

A notification is written in the SAME transaction as the decision (see routers/decisions.py::_record_decision), so a
decision can never exist without its notice and a notice can never exist without its decision. The notification only
carries a short title/body; the authoritative decision (action, comment, who, when) is read through the
planner_decisions join, so the engineer always sees what is actually recorded.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Tuple

from backend.context.errors import raise_permission_denied, raise_resource_not_found
from backend.context.project import ProjectContext
from backend.rbac.permissions import Permission, has_permission
from backend.repositories.base import BaseRepository

_ACTION_TITLES = {
    "APPROVE": "Claim approved",
    "EDIT": "Claim approved with edits",
    "REJECT": "Claim rejected",
    "HOLD": "Claim on hold — changes requested",
}


def _fmt(pct: Optional[float]) -> str:
    return "" if pct is None else f"{pct:g}%"


def decision_message(
    action: str,
    activity_name: Optional[str],
    claimed_pct: Optional[float],
    approved_pct: Optional[float],
    comment: Optional[str],
) -> Tuple[str, str]:
    """Title and body of the notice for a supervisor decision."""
    head = _ACTION_TITLES.get(action.upper(), f"Claim {action.lower()}")
    title = f"{head}: {activity_name}" if activity_name else head
    parts: List[str] = []
    if action.upper() == "EDIT" and approved_pct is not None:
        parts.append(f"You reported {_fmt(claimed_pct)}; the supervisor approved {_fmt(approved_pct)}.")
    elif action.upper() == "APPROVE" and approved_pct is not None:
        parts.append(f"Approved at {_fmt(approved_pct)}.")
    if comment:
        parts.append(comment.strip())
    return title, " ".join(parts)


def insert_decision_notification(
    cur: Any,
    *,
    project_id: Any,
    decision_id: str,
    event_id: str,
    recipient_id: Any,
    created_by: Any,
    action: str,
    activity_name: Optional[str],
    claimed_pct: Optional[float],
    approved_pct: Optional[float],
    comment: Optional[str],
) -> Optional[str]:
    """Insert the CLAIM_DECISION notice on an open cursor (caller owns the transaction). No-op without a recipient."""
    if recipient_id is None:
        return None
    title, body = decision_message(action, activity_name, claimed_pct, approved_pct, comment)
    notification_id = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO notifications (notification_id, project_id, recipient_id, notification_type, decision_id, event_id,
                                   title, body, created_by)
        VALUES (%s, %s, %s, 'CLAIM_DECISION', %s, %s, %s, %s, %s)
        ON CONFLICT (decision_id, recipient_id) WHERE decision_id IS NOT NULL DO NOTHING
        """,
        (notification_id, project_id, recipient_id, decision_id, event_id, title, body, created_by),
    )
    return notification_id


_NOTIFICATION_SQL = """
    SELECT n.notification_id, n.notification_type, n.title, n.body, n.created_at, n.read_at,
           n.event_id, n.decision_id, n.issue_id,
           pd.action        AS decision_action,
           pd.justification AS decision_comment,
           pd.decided_at    AS decided_at,
           pd.approved_pct  AS approved_pct,
           dp.full_name     AS decided_by_name,
           ee.status        AS claim_status,
           ee.raw_claim_text,
           ee.claimed_pct,
           ee.schedule_id,
           pd.selected_activity_id AS activity_id,
           sa.activity_name,
           i.title          AS issue_title,
           i.status         AS issue_status
      FROM notifications n
      LEFT JOIN planner_decisions pd ON pd.decision_id = n.decision_id
      LEFT JOIN execution_events ee ON ee.event_id = n.event_id
      LEFT JOIN schedule_activities sa ON sa.schedule_id = ee.schedule_id AND sa.activity_id = pd.selected_activity_id
      LEFT JOIN profiles dp ON dp.id = pd.planner_id
      LEFT JOIN issues i ON i.issue_id = n.issue_id
     WHERE n.project_id = %s AND n.recipient_id = %s
"""


class NotificationService(BaseRepository):
    @classmethod
    def _require(cls, context: ProjectContext) -> None:
        if not has_permission(context.role, Permission.VIEW_PROJECT):
            raise_permission_denied(Permission.VIEW_PROJECT.value, context.role)

    @classmethod
    def list_for_user(cls, context: ProjectContext, unread_only: bool = False, limit: int = 50) -> Dict[str, Any]:
        """The caller's own notifications in this project, newest first (row-level security enforces recipient-only)."""
        cls._require(context)
        sql = _NOTIFICATION_SQL + (" AND n.read_at IS NULL" if unread_only else "") + " ORDER BY n.created_at DESC, n.notification_id LIMIT %s"
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (context.project_id, context.user_id, max(1, min(limit, 200))))
                items = [dict(r) for r in cur.fetchall()]
                cur.execute(
                    "SELECT count(*) AS n FROM notifications WHERE project_id = %s AND recipient_id = %s AND read_at IS NULL",
                    (context.project_id, context.user_id),
                )
                unread = cur.fetchone()["n"]
        return {"unread_count": unread, "items": items}

    @classmethod
    def mark_read(cls, context: ProjectContext, notification_id: uuid.UUID) -> Dict[str, Any]:
        cls._require(context)
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE notifications SET read_at = COALESCE(read_at, now()) "
                    "WHERE notification_id = %s AND project_id = %s AND recipient_id = %s RETURNING notification_id, read_at",
                    (notification_id, context.project_id, context.user_id),
                )
                row = cur.fetchone()
                if row is None:
                    raise_resource_not_found("Notification", str(notification_id))
                conn.commit()
        return dict(row)

    @classmethod
    def mark_all_read(cls, context: ProjectContext) -> Dict[str, Any]:
        cls._require(context)
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE notifications SET read_at = now() WHERE project_id = %s AND recipient_id = %s AND read_at IS NULL",
                    (context.project_id, context.user_id),
                )
                n = cur.rowcount
                conn.commit()
        return {"marked_read": n}

    @classmethod
    def my_claims(cls, context: ProjectContext, limit: int = 100) -> List[Dict[str, Any]]:
        """Claims the caller submitted, each with its current status and latest supervisor decision (if any)."""
        cls._require(context)
        sql = """
            SELECT ee.event_id, ee.schedule_id, ee.event_date, ee.created_at, ee.raw_claim_text, ee.status,
                   ee.claimed_pct, COALESCE(ee.matched_activity_id, ee.reported_activity_id) AS activity_id,
                   sa.activity_name, sa.discipline,
                   d.decision_id, d.action AS decision_action, d.justification AS decision_comment,
                   d.decided_at, d.approved_pct, dp.full_name AS decided_by_name
              FROM execution_events ee
              LEFT JOIN schedule_activities sa
                     ON sa.schedule_id = ee.schedule_id
                    AND sa.activity_id = COALESCE(ee.matched_activity_id, ee.reported_activity_id)
              LEFT JOIN LATERAL (
                    SELECT * FROM planner_decisions pd WHERE pd.event_id = ee.event_id
                     ORDER BY pd.decided_at DESC, pd.decision_id DESC LIMIT 1) d ON TRUE
              LEFT JOIN profiles dp ON dp.id = d.planner_id
             WHERE ee.project_id = %s AND ee.supervisor_id = %s
             ORDER BY COALESCE(d.decided_at, ee.created_at) DESC, ee.event_id
             LIMIT %s
        """
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (context.project_id, context.user_id, max(1, min(limit, 300))))
                return [dict(r) for r in cur.fetchall()]
