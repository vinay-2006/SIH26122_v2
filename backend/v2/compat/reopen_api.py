"""Legacy reopen endpoints (request / decide / list) on the append-only reopen model of backend/v2/domain/reopen.py (decision D7)."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import permissions as P
from ..domain import reopen as dr
from ..domain.common import actor_tx
from ..errors import ApiError
from . import shapes
from .context import Ctx, path_ctx

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["legacy-contract: reopen"])
LEGACY_STATUS = {"REQUESTED": "REQUESTED", "APPROVED": "APPROVED", "REJECTED": "REJECTED", "CLOSED": "APPROVED"}


def _uid(c, ctx: Ctx, activity_id: str):
    r = c.execute("select activity_uid from baseline_activities where version_id = %s and external_activity_id = %s", (ctx.version_id or ctx.active_version_id, activity_id)).fetchone()
    if r is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", f"Activity '{activity_id}' not found in current context.")
    return r["activity_uid"]


def _shape(r: Dict[str, Any], ext: str, ctx: Ctx) -> Dict[str, Any]:
    return {"activity_id": ext, "schedule_id": str(ctx.version_id or ctx.active_version_id), "project_id": str(ctx.project_id), "reopen_status": LEGACY_STATUS[r["status"]],
            "reason": r["reason_code"], "justification": r["justification"], "requested_by": str(r["requested_by"]), "requested_at": shapes.iso(r["requested_at"]),
            "decided_by": str(r["decided_by"]) if r["decided_by"] else None, "decided_at": shapes.iso(r["decided_at"]), "decision_notes": r["decision_notes"],
            "rework_instructions": r["rework_instructions"], "original_actual_id": None, "lifecycle": r["status"], "completed_snapshot": r["completed_snapshot"],
            "evidence_event_ids": [str(x) for x in r["evidence_event_ids"]]}


class RequestIn(BaseModel):
    reason: str
    justification: str
    evidence_event_ids: List[str] = []


class DecideIn(BaseModel):
    decision: str
    notes: str
    rework_instructions: Optional[str] = None


def _do_request(ctx: Ctx, activity_id: str, body: RequestIn):
    with actor_tx(ctx.actor, readonly=True) as c:
        uid = _uid(c, ctx, activity_id)
    r = dr.request_reopen(ctx.actor, uid, reason_code=body.reason, justification=body.justification, evidence_event_ids=[uuid.UUID(x) for x in body.evidence_event_ids])
    return _shape(r, activity_id, ctx)


def _do_decide(ctx: Ctx, activity_id: str, body: DecideIn):
    with actor_tx(ctx.actor, readonly=True) as c:
        uid = _uid(c, ctx, activity_id)
    r = dr.decide_reopen(ctx.actor, uid, decision=body.decision, notes=body.notes, rework_instructions=body.rework_instructions)
    return _shape(r, activity_id, ctx)


@router.post("/schedules/{schedule_id}/activities/{activity_id}/reopen", status_code=201)
def request_reopen(activity_id: str, body: RequestIn, ctx: Ctx = Depends(path_ctx(P.REQUEST_REOPEN, writable=True))):
    return _do_request(ctx, activity_id, body)


@router.post("/activities/{activity_id}/reopen", status_code=201)
def request_reopen_p(activity_id: str, body: RequestIn, ctx: Ctx = Depends(path_ctx(P.REQUEST_REOPEN, writable=True))):
    return _do_request(ctx, activity_id, body)


@router.post("/schedules/{schedule_id}/activities/{activity_id}/reopen/decide")
def decide_reopen(activity_id: str, body: DecideIn, ctx: Ctx = Depends(path_ctx(P.APPROVE_REOPEN, writable=True))):
    return _do_decide(ctx, activity_id, body)


@router.post("/activities/{activity_id}/reopen/decide")
def decide_reopen_p(activity_id: str, body: DecideIn, ctx: Ctx = Depends(path_ctx(P.APPROVE_REOPEN, writable=True))):
    return _do_decide(ctx, activity_id, body)


def _list(ctx: Ctx, status: Optional[str]) -> List[Dict[str, Any]]:
    with actor_tx(ctx.actor, readonly=True) as c:
        rows = c.execute("select r.*, ba.external_activity_id as ext from activity_reopens r join baseline_activities ba on ba.activity_uid = r.activity_uid and ba.version_id = %s "
                         "where r.project_id = %s order by r.requested_at desc", (ctx.version_id or ctx.active_version_id, ctx.project_id)).fetchall()
    out = [_shape(r, r["ext"], ctx) for r in rows]
    return [x for x in out if not status or x["reopen_status"] == status]


@router.get("/schedules/{schedule_id}/reopen-requests")
def list_requests(status: Optional[str] = None, ctx: Ctx = Depends(path_ctx(P.VIEW_SCHEDULE))):
    if ctx.access.role == "PROJECT_MANAGER":
        return [{k: v for k, v in x.items() if k not in ("justification", "evidence_event_ids")} for x in _list(ctx, status)]      # a PM sees the lifecycle, not the claim-derived content
    return _list(ctx, status)


@router.get("/schedules/{schedule_id}/activities/{activity_id}/reopen")
def reopen_status(activity_id: str, ctx: Ctx = Depends(path_ctx(P.VIEW_SCHEDULE))):
    mine = [x for x in _list(ctx, None) if x["activity_id"] == activity_id]
    return mine[0] if mine else {"activity_id": activity_id, "schedule_id": str(ctx.version_id), "project_id": str(ctx.project_id), "reopen_status": "NONE"}
