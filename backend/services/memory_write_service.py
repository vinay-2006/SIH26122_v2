"""
Institutional-memory write path (SETUAI V7).

A memory record is knowledge a project (or the organisation) can retrieve later:
    category, project/stage/activity context, cause, resolution (corrective action), outcome, lessons learned.
Records come from two places only: a RESOLVED issue promoted to memory (IssueService.resolve_issue / promote_issue) and a
manually recorded lesson. Both are audited, project-scoped, and idempotent per issue. Sharing with the organisation is
an explicit choice per record (visibility = ORGANISATION); everything else stays visible to this project's members only.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from fastapi import HTTPException, status

from backend.context.errors import raise_permission_denied, raise_resource_not_found
from backend.context.project import ProjectContext
from backend.context.schedule import ScheduleContext
from backend.rbac.permissions import Permission, has_permission
from backend.repositories.base import BaseRepository
from backend.schemas.issue import IssueCategory
from backend.services.issue_service import IssueService, promote_issue_to_memory, _jsonable
from backend.shared.audit import append_audit_record
from pydantic import BaseModel, Field


class MemoryPromote(BaseModel):
    cause: Optional[str] = Field(None, max_length=2000)
    outcome: Optional[str] = Field(None, max_length=2000)
    lessons_learned: Optional[str] = Field(None, max_length=2000)
    share_with_organisation: bool = False


class MemoryCreate(BaseModel):
    category_code: IssueCategory
    title: str = Field(..., min_length=3, max_length=200)
    narrative: str = Field(..., min_length=3, max_length=4000, description="What happened")
    root_cause: Optional[str] = Field(None, max_length=2000)
    resolution: Optional[str] = Field(None, max_length=2000, description="What resolved it (corrective action)")
    outcome: Optional[str] = Field(None, max_length=2000)
    lessons_learned: Optional[str] = Field(None, max_length=2000)
    stage_id: Optional[uuid.UUID] = None
    activity_id: Optional[str] = None
    schedule_id: Optional[str] = None
    delay_days: Optional[float] = Field(None, ge=0)
    share_with_organisation: bool = False


class IssueMemoryQuery(BaseModel):
    category_code: IssueCategory
    query: str = Field("", max_length=2000, description="Free text of the current issue (title + description)")
    activity_id: Optional[str] = None
    stage_id: Optional[uuid.UUID] = None
    top_k: int = Field(5, ge=1, le=50)


class MemoryWriteService(BaseRepository):
    @classmethod
    def promote_issue(cls, context: ScheduleContext, issue_id: uuid.UUID, payload: MemoryPromote) -> Dict[str, Any]:
        """Store an already RESOLVED issue as institutional knowledge (no-op if it already is)."""
        if not has_permission(context.role, Permission.MANAGE_BLOCKERS):
            raise_permission_denied(Permission.MANAGE_BLOCKERS.value, context.role)
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                issue = IssueService._get(cur, context, issue_id)
                if issue is None:
                    raise_resource_not_found("Issue", str(issue_id))
                if issue["status"] != "RESOLVED":
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only a resolved issue can become institutional knowledge")
                cause = payload.cause or issue.get("root_cause_title")
                memory_id = promote_issue_to_memory(
                    cur, issue, recorded_by=context.user_id, cause=cause, outcome=payload.outcome,
                    lessons_learned=payload.lessons_learned, share_with_organisation=payload.share_with_organisation,
                )
                if memory_id is None:
                    cur.execute("SELECT incident_id FROM institutional_incidents WHERE issue_id = %s", (issue_id,))
                    memory_id = str(cur.fetchone()["incident_id"])
                else:
                    append_audit_record(
                        cur, entity_type="INSTITUTIONAL_INCIDENT", entity_id=memory_id, action="MEMORY_RECORDED",
                        actor_id=str(context.user_id), before_state=None,
                        after_state={"issue_id": str(issue_id), "visibility": "ORGANISATION" if payload.share_with_organisation else "PROJECT"},
                        project_id=context.project_id, schedule_id=context.schedule_id, role=context.role,
                    )
                conn.commit()
        return {"memory_id": memory_id, "issue_id": str(issue_id)}

    @classmethod
    def create_record(cls, context: ProjectContext, payload: MemoryCreate) -> Dict[str, Any]:
        """Record a lesson that did not come from a tracked issue."""
        if not has_permission(context.role, Permission.MANAGE_BLOCKERS):
            raise_permission_denied(Permission.MANAGE_BLOCKERS.value, context.role)
        memory_id = uuid.uuid4()
        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                if payload.stage_id:
                    cur.execute("SELECT 1 FROM stages WHERE stage_id = %s AND project_id = %s", (payload.stage_id, context.project_id))
                    if cur.fetchone() is None:
                        raise_resource_not_found("Stage", str(payload.stage_id))
                if payload.activity_id:
                    if not payload.schedule_id:
                        raise HTTPException(status_code=422, detail="schedule_id is required with activity_id")
                    cur.execute(
                        "SELECT discipline FROM schedule_activities WHERE schedule_id = %s AND project_id = %s AND activity_id = %s",
                        (payload.schedule_id, context.project_id, payload.activity_id),
                    )
                    act = cur.fetchone()
                    if act is None:
                        raise_resource_not_found("Activity", payload.activity_id)
                    discipline = act["discipline"]
                else:
                    discipline = None
                cur.execute(
                    """
                    INSERT INTO institutional_incidents (incident_id, project_id, stage_id, activity_id, schedule_id, discipline,
                        incident_type, category_code, title, narrative, root_cause, delay_days, corrective_action,
                        lessons_learned, outcome, recorded_by, status, visibility, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'CLOSED', %s, 'MANUAL')
                    """,
                    (memory_id, context.project_id, payload.stage_id, payload.activity_id, payload.schedule_id, discipline,
                     payload.category_code.value, payload.category_code.value, payload.title.strip(), payload.narrative.strip(),
                     payload.root_cause, payload.delay_days, payload.resolution, payload.lessons_learned, payload.outcome,
                     context.user_id, "ORGANISATION" if payload.share_with_organisation else "PROJECT"),
                )
                append_audit_record(
                    cur, entity_type="INSTITUTIONAL_INCIDENT", entity_id=str(memory_id), action="MEMORY_RECORDED",
                    actor_id=str(context.user_id), before_state=None,
                    after_state={"title": payload.title, "category_code": payload.category_code.value, "source": "MANUAL"},
                    project_id=context.project_id, schedule_id=payload.schedule_id, role=context.role,
                )
                conn.commit()
        return {"memory_id": str(memory_id)}
