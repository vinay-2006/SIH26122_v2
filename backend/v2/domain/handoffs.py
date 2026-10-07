"""Time Agent hand-off: a Supervisor's drafted claim goes to a Site Engineer, who files it through the ordinary intake (or dismisses it). A draft is not a claim: it
has no workflow status and no ledger effect; every step is audited."""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from .. import audit
from ..errors import ApiError
from .common import SE, SUP, ProjectActor, actor_tx, require_role, require_writable


def create(actor: ProjectActor, draft: Dict[str, Any], note: Optional[str] = None, to_user_id: Optional[uuid.UUID] = None) -> Dict[str, Any]:
    require_role(actor, SUP, what="handing a claim draft to a Site Engineer")
    require_writable(actor)
    if len(str(draft.get("rawText") or "").strip()) < 3:
        raise ApiError(422, "DRAFT_EMPTY", "A hand-off needs the drafted report text")
    with actor_tx(actor, write=True) as c:
        if to_user_id is not None and not c.execute("select 1 from project_memberships where project_id = %s and user_id = %s and role = 'SITE_ENGINEER' and status = 'ACTIVE'",
                                                    (actor.project_id, to_user_id)).fetchone():
            raise ApiError(404, "RECIPIENT_NOT_FOUND", "That Site Engineer is not an active member of the project")
        if to_user_id is None and not c.execute("select 1 from project_memberships where project_id = %s and role = 'SITE_ENGINEER' and status = 'ACTIVE'", (actor.project_id,)).fetchone():
            raise ApiError(409, "NO_SITE_ENGINEER", "The project has no active Site Engineer to hand the draft to")
        r = c.execute("insert into claim_handoffs (project_id, created_by, to_user_id, draft, note) values (%s,%s,%s,%s::jsonb,%s) returning *",
                      (actor.project_id, actor.user_id, to_user_id, json.dumps(draft), note)).fetchone()
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=actor.role, action="CLAIM_DRAFT_HANDED_OFF", entity_type="CLAIM_HANDOFF", entity_id=r["handoff_id"],
                  after={"to_user_id": str(to_user_id) if to_user_id else None, "activity": draft.get("reportedActivityId")})
    return r


def list_open(actor: ProjectActor, include_closed: bool = False) -> List[Dict[str, Any]]:
    require_role(actor, SE, SUP, what="reading claim-draft hand-offs")
    with actor_tx(actor, readonly=True) as c:
        rows = c.execute("select * from claim_handoffs where project_id = %s " + ("" if include_closed else "and status = 'OPEN' ") +
                         ("and (created_by = %s) " if actor.role == SUP else "and (to_user_id is null or to_user_id = %s) ") + "order by created_at desc limit 100",
                         (actor.project_id, actor.user_id)).fetchall()
    return rows


def _close(actor: ProjectActor, handoff_id, status: str, event_id=None) -> Dict[str, Any]:
    require_role(actor, SE, SUP, what="closing a claim-draft hand-off")
    require_writable(actor)
    with actor_tx(actor, write=True) as c:
        h = c.execute("select * from claim_handoffs where project_id = %s and handoff_id = %s for update", (actor.project_id, handoff_id)).fetchone()
        mine = h is not None and (h["created_by"] == actor.user_id if actor.role == SUP else (h["to_user_id"] is None or h["to_user_id"] == actor.user_id))
        if not mine:
            raise ApiError(404, "HANDOFF_NOT_FOUND", "Hand-off not found")
        if h["status"] != "OPEN":
            raise ApiError(409, "HANDOFF_CLOSED", f"This hand-off is already {h['status']}")
        if status == "FILED":
            if actor.role != SE:
                raise ApiError(403, "FORBIDDEN", "Only a Site Engineer files a hand-off")
            if not c.execute("select 1 from execution_events where project_id = %s and event_id = %s and filed_by = %s", (actor.project_id, event_id, actor.user_id)).fetchone():
                raise ApiError(404, "CLAIM_NOT_FOUND", "The claim you filed was not found")
        r = c.execute("update claim_handoffs set status = %s, filed_event_id = %s, closed_by = %s, closed_at = now() where handoff_id = %s returning *",
                      (status, event_id, actor.user_id, handoff_id)).fetchone()
        audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=actor.role, action=f"CLAIM_DRAFT_{status}", entity_type="CLAIM_HANDOFF", entity_id=handoff_id,
                  after={"event_id": str(event_id) if event_id else None})
    return r


def file_it(actor: ProjectActor, handoff_id, event_id) -> Dict[str, Any]:
    return _close(actor, handoff_id, "FILED", event_id)


def dismiss(actor: ProjectActor, handoff_id) -> Dict[str, Any]:
    return _close(actor, handoff_id, "DISMISSED")
