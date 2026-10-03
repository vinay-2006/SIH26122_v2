"""Knowledge graph, Ask Why, investigation context and the mock P6 target on v2 data. The ORIGINAL code runs unchanged:
  * backend/routers/claim_graph.py  build_claim_graph / explain_activity   (claim knowledge graph, Ask Why)
  * backend/routers/graph.py        build_activity_graph                   (activity execution graph)
  * backend/routers/investigation.py build_investigation_context
  * backend/routers/mock_p6.py      update_activity / received             (the local stand-in for a P6 EPPM server)
They read the legacy read model (schema lrm) through the connection's search_path, scoped to the project and schedule version of the request.
Everything here exposes claim content, so it is Supervisor-only (REVIEW_CLAIMS); read-only. The mock P6 is a development stand-in (404 unless local sign-in is enabled)."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query

from .. import permissions as P
from ..domain.common import actor_tx
from ..errors import ApiError
from .checks_api import legacy_view_mode
from .context import Ctx, legacy_ctx, require_version

router = APIRouter(tags=["legacy-contract: knowledge"])


def _in_view(ctx: Ctx, fn, *args, **kw):
    version = require_version(ctx)
    with actor_tx(ctx.actor, readonly=True) as c:
        with legacy_view_mode(c, ctx.project_id, version):
            return fn(c, version, *args, **kw)


@router.get("/api/v1/claims/{event_id}/knowledge-graph")
def claim_knowledge_graph(event_id: str, ctx: Ctx = Depends(legacy_ctx(P.REVIEW_CLAIMS))):
    from backend.routers.claim_graph import build_claim_graph
    return _in_view(ctx, lambda c, v: build_claim_graph(c, event_id))


@router.get("/api/v1/graph/explain/{activity_id}")
def ask_why(activity_id: str, event_id: Optional[str] = Query(default=None), depth: int = Query(default=1, ge=1, le=6),
            schedule_id: Optional[str] = Query(default=None), ctx: Ctx = Depends(legacy_ctx(P.REVIEW_CLAIMS))):
    from backend.routers.claim_graph import explain_activity
    return _in_view(ctx, lambda c, v: explain_activity(c, activity_id, str(v), event_id, depth))


@router.get("/api/v1/graph/activity/{activity_id}")
def activity_graph(activity_id: str, depth: int = Query(default=1, ge=0, le=10), schedule_id: Optional[str] = Query(default=None),
                   ctx: Ctx = Depends(legacy_ctx(P.REVIEW_CLAIMS))) -> Dict[str, Any]:
    from backend.routers.graph import build_activity_graph
    return _in_view(ctx, lambda c, v: build_activity_graph(activity_id=activity_id, depth=depth, schedule_id=str(v), conn=c))


@router.get("/api/v1/investigation/activity/{activity_id}")
def investigation(activity_id: str, depth: int = Query(default=1, ge=0, le=10), schedule_id: Optional[str] = Query(default=None),
                  ctx: Ctx = Depends(legacy_ctx(P.REVIEW_CLAIMS))) -> Dict[str, Any]:
    from backend.routers.investigation import build_investigation_context
    return _in_view(ctx, lambda c, v: build_investigation_context(activity_id=activity_id, depth=depth, schedule_id=str(v), conn=c))


# ---- the mock P6 push target (development only) -----------------------------------------------------------------------------------------

def _dev_only() -> None:
    if not os.environ.get("V2_LOCAL_LOGIN_PASSWORD"):
        raise ApiError(404, "NOT_FOUND", "Not Found")


p6 = APIRouter(prefix="/api/v1/mock-p6", tags=["legacy-contract: mock-p6"], dependencies=[Depends(_dev_only)])


@p6.get("/health")
def p6_health():
    from backend.routers.mock_p6 import health
    return health()


@p6.get("/received")
def p6_received(ctx: Ctx = Depends(legacy_ctx(P.VIEW_AUDIT))):
    from backend.routers.mock_p6 import received
    return received()


@p6.post("/activities/{activity_id}")
def p6_update(activity_id: str, payload: dict, ctx: Ctx = Depends(legacy_ctx(P.VIEW_AUDIT))):
    from backend.routers.mock_p6 import P6ActivityPayload, update_activity
    from fastapi import HTTPException
    try:
        return update_activity(activity_id, P6ActivityPayload(**payload))
    except HTTPException as e:
        raise ApiError(e.status_code, "MOCK_P6_REJECTED", str(e.detail))
