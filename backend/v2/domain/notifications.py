"""A user's own notifications inside one project (decisions on their claims, new claims for Supervisors, clarification traffic, issue updates)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..errors import ApiError
from .common import actor_tx, ProjectActor


def list_notifications(actor: ProjectActor, unread_only: bool = False, limit: int = 50, offset: int = 0) -> List[dict]:
    with actor_tx(actor, readonly=True) as c:
        return c.execute(
            "select notification_id, notification_type, title, body, event_id as claim_id, decision_id, issue_id, created_at, read_at from notifications "
            "where project_id = %s and recipient_id = %s and (not %s or read_at is null) order by created_at desc, notification_id limit %s offset %s",
            (actor.project_id, actor.user_id, unread_only, limit, offset)).fetchall()


def mark_read(actor: ProjectActor, notification_id) -> Dict[str, Any]:
    with actor_tx(actor, write=True) as c:
        r = c.execute("update notifications set read_at = coalesce(read_at, now()) where project_id = %s and recipient_id = %s and notification_id = %s returning notification_id, read_at",
                      (actor.project_id, actor.user_id, notification_id)).fetchone()
        if r is None:
            raise ApiError(404, "NOTIFICATION_NOT_FOUND", "No such notification")
    return r
