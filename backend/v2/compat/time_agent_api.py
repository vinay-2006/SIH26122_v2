"""Time Agent hand-off on v2: the Supervisor's Time Agent drafts the claim and hands it to a Site Engineer (domain/handoffs.py); the engineer sees it on Intake."""
from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import permissions as P
from ..domain import handoffs
from . import shapes
from .context import Ctx, legacy_ctx

router = APIRouter(prefix="/api/v1/time-agent", tags=["legacy-contract: time-agent"])


class HandOff(BaseModel):
    draft: Dict[str, Any]
    note: Optional[str] = None
    to_user_id: Optional[uuid.UUID] = None


class Filed(BaseModel):
    event_id: uuid.UUID


def _out(r) -> Dict[str, Any]:
    return {"handoff_id": str(r["handoff_id"]), "status": r["status"], "draft": r["draft"], "note": r["note"], "created_by": str(r["created_by"]),
            "to_user_id": str(r["to_user_id"]) if r["to_user_id"] else None, "created_at": shapes.iso(r["created_at"]),
            "filed_event_id": str(r["filed_event_id"]) if r["filed_event_id"] else None}


@router.post("/handoffs")
def create(body: HandOff, ctx: Ctx = Depends(legacy_ctx(P.REVIEW_CLAIMS, writable=True))):
    return _out(handoffs.create(ctx.actor, body.draft, body.note, body.to_user_id))


@router.get("/handoffs")
def list_handoffs(all: bool = False, ctx: Ctx = Depends(legacy_ctx(P.VIEW_PROJECT))):
    return [_out(r) for r in handoffs.list_open(ctx.actor, all)]


@router.post("/handoffs/{handoff_id}/filed")
def filed(handoff_id: uuid.UUID, body: Filed, ctx: Ctx = Depends(legacy_ctx(P.SUBMIT_CLAIM, writable=True))):
    return _out(handoffs.file_it(ctx.actor, handoff_id, body.event_id))


@router.post("/handoffs/{handoff_id}/dismiss")
def dismiss(handoff_id: uuid.UUID, ctx: Ctx = Depends(legacy_ctx(P.VIEW_PROJECT, writable=True))):
    return _out(handoffs.dismiss(ctx.actor, handoff_id))
