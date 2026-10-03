"""Issues and delays. Site Engineers and Supervisors report; only Supervisors resolve, group by root cause and promote to memory;
the Project Manager reads. 'Blocked' is derived from active blocking issues and is never stored."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import idempotency, permissions as P
from ..auth import ProjectAccess, require
from ..domain import issues as di
from ._common import ERRORS, LIMIT, OFFSET, actor_of, page

router = APIRouter(prefix="/api/v2/projects/{project_id}", tags=["issues"], responses=ERRORS)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IssueCreate(Strict):
    title: str = Field(min_length=3, max_length=200)
    category_code: str = Field(min_length=2, max_length=60, description="a code from the issue_categories reference list")
    activity_uid: Optional[uuid.UUID] = None
    stage_wbs_uid: Optional[uuid.UUID] = Field(default=None, description="a WBS stage, when the issue is not about one activity")
    description: Optional[str] = Field(default=None, max_length=4000)
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    blocks_work: bool = Field(default=True, description="an ACTIVE blocking issue makes its activity / stage 'blocked' (derived)")
    delay_started_on: Optional[date] = None
    impact_days_estimated: Optional[Decimal] = Field(default=None, ge=0, le=100000)
    expected_duration_days: Optional[Decimal] = Field(default=None, ge=0, le=100000)
    source_event_id: Optional[uuid.UUID] = Field(default=None, description="the claim this issue came out of")
    evidence_document_ids: List[uuid.UUID] = Field(default_factory=list, max_length=20)


class ResolveBody(Strict):
    notes: str = Field(min_length=2, max_length=2000)
    delay_ended_on: Optional[date] = None
    impact_days_actual: Optional[Decimal] = Field(default=None, ge=0, le=100000)


class EvidenceBody(Strict):
    document_id: uuid.UUID


class RootCauseCreate(Strict):
    title: str = Field(min_length=3, max_length=200)
    category_code: str = Field(min_length=2, max_length=60)
    summary: Optional[str] = Field(default=None, max_length=2000)


class AssignRootCause(Strict):
    root_cause_id: uuid.UUID


class MemoryBody(Strict):
    lessons_learned: str = Field(min_length=5, max_length=4000)
    corrective_action: Optional[str] = Field(default=None, max_length=2000)
    outcome: Optional[str] = Field(default=None, max_length=2000)
    visibility: Literal["PROJECT", "ORGANISATION"] = "PROJECT"


@router.post("/issues", status_code=201, summary="Report an issue or delay (Site Engineer, Supervisor)")
def report(body: IssueCreate, response: Response, idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
           access: ProjectAccess = Depends(require(P.REPORT_ISSUE, writable=True))):
    a = actor_of(access)
    kw = body.model_dump()
    kw["evidence_document_ids"] = body.evidence_document_ids
    out, replayed = idempotency.run(a.user_id, a.project_id, "issues.create", idempotency_key, body.model_dump(mode="json"), lambda: di.report_issue(a, **kw))
    if replayed:
        response.headers["Idempotent-Replay"] = "true"
    return out


@router.get("/issues", summary="Issues (own for a Site Engineer; all for Supervisor and Project Manager)")
def issues(status: Optional[Literal["ACTIVE", "RESOLVED"]] = None, limit: int = LIMIT, offset: int = OFFSET, access: ProjectAccess = Depends(require(P.VIEW_ISSUES))):
    return page(lambda l, o: di.list_issues(actor_of(access), status, l, o), limit, offset)


@router.get("/blockers", summary="Activities and stages blocked by an active blocking issue (derived)")
def blockers(access: ProjectAccess = Depends(require(P.VIEW_ISSUES))):
    return di.active_blockers(actor_of(access))


@router.get("/issues/{issue_id}", summary="One issue with its evidence")
def issue(issue_id: uuid.UUID, access: ProjectAccess = Depends(require(P.VIEW_ISSUES))):
    return di.get_issue(actor_of(access), issue_id)


@router.post("/issues/{issue_id}/resolve", summary="Resolve an issue (Supervisor); a resolved issue is never reopened")
def resolve(issue_id: uuid.UUID, body: ResolveBody, access: ProjectAccess = Depends(require(P.RESOLVE_ISSUE, writable=True))):
    return di.resolve_issue(actor_of(access), issue_id, body.notes, body.delay_ended_on, body.impact_days_actual)


@router.post("/issues/{issue_id}/evidence", status_code=201, summary="Attach an uploaded document to an active issue")
def evidence(issue_id: uuid.UUID, body: EvidenceBody, access: ProjectAccess = Depends(require(P.REPORT_ISSUE, writable=True))):
    return di.attach_issue_evidence(actor_of(access), issue_id, body.document_id)


@router.post("/issues/{issue_id}/root-cause", summary="Group an issue under a root cause (Supervisor)")
def assign(issue_id: uuid.UUID, body: AssignRootCause, access: ProjectAccess = Depends(require(P.RESOLVE_ISSUE, writable=True))):
    return di.assign_root_cause(actor_of(access), issue_id, body.root_cause_id)


@router.post("/issues/{issue_id}/memory", status_code=201, summary="Promote a RESOLVED issue to institutional memory (Supervisor)")
def memory(issue_id: uuid.UUID, body: MemoryBody, access: ProjectAccess = Depends(require(P.RESOLVE_ISSUE, writable=True))):
    return di.promote_to_memory(actor_of(access), issue_id, **body.model_dump())


@router.get("/root-causes", summary="Root causes (Supervisor, Project Manager)")
def root_causes(access: ProjectAccess = Depends(require(P.VIEW_ROOT_CAUSES))):
    return {"items": di.list_root_causes(actor_of(access))}


@router.post("/root-causes", status_code=201, summary="Create a root cause (Supervisor)")
def create_root_cause(body: RootCauseCreate, access: ProjectAccess = Depends(require(P.RESOLVE_ISSUE, writable=True))):
    return di.create_root_cause(actor_of(access), **body.model_dump())


@router.get("/memory", summary="Institutional memory entries (Supervisor, Project Manager)")
def memory_list(access: ProjectAccess = Depends(require(P.VIEW_ISSUES))):
    return {"items": di.list_memory(actor_of(access))}
