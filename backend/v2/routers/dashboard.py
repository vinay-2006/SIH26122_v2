"""Progress dashboards and timelines. Every figure is derived from the approved ledgers; each payload states its weight basis, its data date
and that planned progress is a linear approximation (SPI is not earned value). The Project Manager sees aggregate claim counts only."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from .. import permissions as P
from ..auth import ProjectAccess, require
from ..domain import notifications as dn, rollups, timeline as tl
from ._common import ERRORS, LIMIT, OFFSET, actor_of, page

router = APIRouter(prefix="/api/v2/projects/{project_id}", tags=["dashboard"], responses=ERRORS)
AS_OF = Query(None, description="report progress as of this date (default: today)")


@router.get("/dashboard/summary", summary="Project progress, planned (approximate), SPI (approximate), counts, issues, claim counts")
def summary(as_of: Optional[date] = AS_OF, version_id: Optional[uuid.UUID] = None, access: ProjectAccess = Depends(require(P.VIEW_DASHBOARD))):
    return rollups.project_summary(actor_of(access), as_of, version_id)


@router.get("/dashboard/wbs", summary="Progress for every WBS node")
def wbs(as_of: Optional[date] = AS_OF, version_id: Optional[uuid.UUID] = None, access: ProjectAccess = Depends(require(P.VIEW_DASHBOARD))):
    return {"items": rollups.wbs_progress(actor_of(access), as_of, version_id)}


@router.get("/dashboard/stages", summary="Progress for each stage")
def stages(as_of: Optional[date] = AS_OF, version_id: Optional[uuid.UUID] = None, access: ProjectAccess = Depends(require(P.VIEW_DASHBOARD))):
    return {"items": rollups.stage_progress(actor_of(access), as_of, version_id)}


@router.get("/dashboard/disciplines", summary="Progress for each discipline")
def disciplines(as_of: Optional[date] = AS_OF, version_id: Optional[uuid.UUID] = None, access: ProjectAccess = Depends(require(P.VIEW_DASHBOARD))):
    return {"items": rollups.discipline_progress(actor_of(access), as_of, version_id)}


@router.get("/dashboard/activities", summary="Per-activity progress (paginated)")
def activities(as_of: Optional[date] = AS_OF, version_id: Optional[uuid.UUID] = None, discipline: Optional[str] = None, state: Optional[str] = Query(None, pattern="^(NOT_STARTED|IN_PROGRESS|COMPLETED)$"),
               wbs_prefix: Optional[str] = None, limit: int = LIMIT, offset: int = OFFSET, access: ProjectAccess = Depends(require(P.VIEW_DASHBOARD))):
    return page(lambda l, o: rollups.activity_progress(actor_of(access), as_of, version_id, discipline, state, wbs_prefix, l, o), limit, offset)


@router.get("/dashboard/timeline", summary="Actual vs approximate planned progress over time")
def timeline(date_from: Optional[date] = None, date_to: Optional[date] = None, step_days: int = Query(7, ge=1, le=366), version_id: Optional[uuid.UUID] = None,
             access: ProjectAccess = Depends(require(P.VIEW_DASHBOARD))):
    return rollups.timeline(actor_of(access), date_from, date_to, step_days, version_id)


@router.get("/dashboard/compare", summary="Progress across two schedule versions by stable activity identity (Project Manager, Supervisor)")
def compare(old: uuid.UUID, new: uuid.UUID, as_of: Optional[date] = AS_OF, access: ProjectAccess = Depends(require(P.VIEW_AUDIT))):
    """Retired scope is listed with `progress_transferred: false`: progress is never moved automatically across a split, merge or retirement."""
    return rollups.compare_versions(actor_of(access), old, new, as_of)


@router.get("/activities/{activity_uid}/timeline", summary="One activity across versions, approved entries, issues and (role-filtered) claims")
def activity_timeline(activity_uid: uuid.UUID, access: ProjectAccess = Depends(require(P.VIEW_DASHBOARD))):
    return tl.activity_timeline(actor_of(access), activity_uid)


@router.get("/audit", summary="Audit trail (Supervisor, Project Manager)")
def audit(entity_type: Optional[str] = None, entity_id: Optional[str] = None, limit: int = LIMIT, access: ProjectAccess = Depends(require(P.VIEW_AUDIT))):
    return {"items": tl.audit_trail(actor_of(access), entity_type, entity_id, limit)}


@router.get("/audit/verify", summary="Verify the audit hash chain (Supervisor, Project Manager)")
def verify(access: ProjectAccess = Depends(require(P.VIEW_AUDIT))):
    return tl.verify_audit_chain(actor_of(access))


@router.get("/notifications", summary="Own notifications in this project (decisions on your claims, new claims to review, clarifications, issue updates)")
def notifications(unread_only: bool = False, limit: int = LIMIT, offset: int = OFFSET, access: ProjectAccess = Depends(require(P.VIEW_PROJECT))):
    return page(lambda l, o: dn.list_notifications(actor_of(access), unread_only, l, o), limit, offset)


@router.post("/notifications/{notification_id}/read", summary="Mark an own notification as read")
def read_notification(notification_id: uuid.UUID, access: ProjectAccess = Depends(require(P.VIEW_PROJECT, writable=True))):
    return dn.mark_read(actor_of(access), notification_id)
