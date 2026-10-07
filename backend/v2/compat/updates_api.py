"""Legacy `my-claims` and `notifications` (the Site Engineer's My Updates page) on v2 data."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from .. import permissions as P
from ..domain.common import SE, SUP, actor_tx
from ..errors import ApiError
from . import shapes
from .context import Ctx, path_ctx

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["legacy-contract: updates"])


def _decision_fields(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"decision_id": str(r["decision_id"]) if r.get("decision_id") else None, "decision_action": r.get("d_action"), "decision_comment": r.get("d_comment"),
            "decided_at": shapes.iso(r.get("d_at")), "approved_pct": shapes.num(r.get("d_pct")), "decided_by_name": r.get("d_by")}


@router.get("/my-claims")
def my_claims(limit: int = 150, ctx: Ctx = Depends(path_ctx(P.SUBMIT_CLAIM))):
    """the engineer's own claims, each with the latest supervisor decision (persisted by the backend, not inferred)"""
    with actor_tx(ctx.actor, readonly=True) as c:
        rows = c.execute(
            "select e.event_id, e.filed_in_version_id, e.event_date, e.created_at, e.raw_claim_text, e.status, e.claimed_pct, e.discipline_code, ba.external_activity_id as ext, ba.activity_name, "
            "d.decision_id, d.action as d_action, d.justification as d_comment, d.decided_at as d_at, d.approved_pct as d_pct, pr.full_name as d_by "
            "from execution_events e left join baseline_activities ba on ba.version_id = e.filed_in_version_id and ba.activity_uid = e.matched_activity_uid "
            "left join lateral (select * from planner_decisions x where x.event_id = e.event_id order by x.decided_at desc limit 1) d on true "
            "left join profiles pr on pr.id = d.decided_by where e.project_id = %s and e.filed_by = %s order by e.created_at desc limit %s",
            (ctx.project_id, ctx.user.id, min(max(limit, 1), 500))).fetchall()
    out = []
    for r in rows:
        st = {"EXTRACTED": "EXTRACTED", "REPORTED": "EXTRACTED", "DISPUTED": "HOLD"}.get(r["status"], r["status"])
        if r["status"] == "APPROVED" and r["d_action"] == "EDIT":
            st = "EDITED"
        out.append({"event_id": str(r["event_id"]), "schedule_id": str(r["filed_in_version_id"]), "event_date": shapes.iso(r["event_date"]), "created_at": shapes.iso(r["created_at"]),
                    "raw_claim_text": r["raw_claim_text"], "status": st, "claimed_pct": shapes.num(r["claimed_pct"]), "activity_id": r["ext"], "activity_name": r["activity_name"],
                    "discipline": r["discipline_code"], **_decision_fields(r)})
    return out


@router.get("/notifications")
def notifications(unread_only: bool = False, limit: int = 100, ctx: Ctx = Depends(path_ctx(P.VIEW_PROJECT))):
    with actor_tx(ctx.actor, readonly=True) as c:
        unread = c.execute("select count(*) n from notifications where project_id = %s and recipient_id = %s and read_at is null", (ctx.project_id, ctx.user.id)).fetchone()["n"]
        rows = c.execute(
            "select n.*, e.raw_claim_text, e.claimed_pct, e.status as claim_status, e.filed_in_version_id, ba.external_activity_id as ext, ba.activity_name, "
            "d.action as d_action, d.justification as d_comment, d.decided_at as d_at, d.approved_pct as d_pct, pr.full_name as d_by, i.title as issue_title, i.status as issue_status "
            "from notifications n left join execution_events e on e.event_id = n.event_id left join baseline_activities ba on ba.version_id = e.filed_in_version_id and ba.activity_uid = e.matched_activity_uid "
            "left join planner_decisions d on d.decision_id = n.decision_id left join profiles pr on pr.id = d.decided_by left join issues i on i.issue_id = n.issue_id "
            "where n.project_id = %s and n.recipient_id = %s and (not %s or n.read_at is null) order by n.created_at desc limit %s",
            (ctx.project_id, ctx.user.id, unread_only, min(max(limit, 1), 500))).fetchall()
    items = []
    for r in rows:
        ntype = "CLAIM_DECISION" if r["notification_type"] == "CLAIM_DECISION" else "ISSUE_UPDATE"
        items.append({"notification_id": str(r["notification_id"]), "notification_type": ntype, "title": r["title"], "body": r["body"], "created_at": shapes.iso(r["created_at"]),
                      "read_at": shapes.iso(r["read_at"]), "event_id": str(r["event_id"]) if r["event_id"] else None, "decision_id": str(r["decision_id"]) if r["decision_id"] else None,
                      "issue_id": str(r["issue_id"]) if r["issue_id"] else None, "decision_action": r["d_action"], "decision_comment": r["d_comment"], "decided_at": shapes.iso(r["d_at"]),
                      "approved_pct": shapes.num(r["d_pct"]), "decided_by_name": r["d_by"], "claim_status": r["claim_status"], "raw_claim_text": r["raw_claim_text"],
                      "claimed_pct": shapes.num(r["claimed_pct"]), "schedule_id": str(r["filed_in_version_id"]) if r["filed_in_version_id"] else None, "activity_id": r["ext"],
                      "activity_name": r["activity_name"], "issue_title": r["issue_title"], "issue_status": r["issue_status"]})
    return {"unread_count": unread, "items": items}


@router.post("/notifications/read-all")
def read_all(ctx: Ctx = Depends(path_ctx(P.VIEW_PROJECT, writable=True))):
    with actor_tx(ctx.actor, write=True) as c:
        n = c.execute("update notifications set read_at = now() where project_id = %s and recipient_id = %s and read_at is null", (ctx.project_id, ctx.user.id)).rowcount
    return {"marked_read": n}


@router.post("/notifications/{notification_id}/read")
def read_one(notification_id: str, ctx: Ctx = Depends(path_ctx(P.VIEW_PROJECT, writable=True))):
    import uuid as _u
    try:
        nid = _u.UUID(notification_id)
    except ValueError:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "No such notification")
    with actor_tx(ctx.actor, write=True) as c:
        r = c.execute("update notifications set read_at = coalesce(read_at, now()) where project_id = %s and recipient_id = %s and notification_id = %s returning notification_id, read_at",
                      (ctx.project_id, ctx.user.id, nid)).fetchone()
    if r is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "No such notification")
    return {"notification_id": str(r["notification_id"]), "read_at": shapes.iso(r["read_at"])}
