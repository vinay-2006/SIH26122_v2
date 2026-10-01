"""Issues & delays router (V7): field-reported issues, root causes and the root-cause analysis view."""
from __future__ import annotations

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, UploadFile, status

from backend.context.project import ProjectContext, require_project_context
from backend.context.schedule import ScheduleContext, require_schedule_context
from backend.schemas.issue import (
    IssueCreate,
    IssueResolve,
    IssueResponse,
    RootCauseAnalysis,
    RootCauseCreate,
    RootCauseLink,
    RootCauseResponse,
)
from backend.services.issue_service import IssueService
from backend.shared.db import get_connection
from backend.shared.uploads import save_upload

router = APIRouter(tags=["issues"])

_BASE = "/api/v1/projects/{project_id}/schedules/{schedule_id}"


@router.get("/api/v1/projects/{project_id}/issue-categories")
def list_issue_categories(
    project_id: uuid.UUID = Path(...),
    context: ProjectContext = Depends(require_project_context),
):
    """Issue categories for the report form (reference data, served under the caller's project like every other route)."""
    with get_connection() as conn:
        rows = conn.execute("SELECT code, name FROM issue_categories ORDER BY sort_order, code").fetchall()
    return [dict(r) for r in rows]


@router.post(_BASE + "/issues", response_model=IssueResponse, status_code=status.HTTP_201_CREATED)
def report_issue(
    payload: IssueCreate,
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    context: ScheduleContext = Depends(require_schedule_context),
) -> IssueResponse:
    """Report an issue / delay on an activity or stage (REPORT_ISSUE). Audited in the same transaction."""
    return IssueResponse(**IssueService.report_issue(context, payload))


@router.get(_BASE + "/issues", response_model=List[IssueResponse])
def list_issues(
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    status_filter: Optional[str] = Query("ALL", alias="status", pattern="^(?i)(ACTIVE|RESOLVED|ALL)$"),
    category: Optional[str] = Query(None),
    activity_id: Optional[str] = Query(None),
    stage_id: Optional[uuid.UUID] = Query(None),
    mine: bool = Query(False, description="Only issues reported by the caller"),
    context: ScheduleContext = Depends(require_schedule_context),
) -> List[IssueResponse]:
    return [IssueResponse(**i) for i in IssueService.list_issues(context, status_filter, category, activity_id, stage_id, mine)]


@router.get(_BASE + "/issues/{issue_id}", response_model=IssueResponse)
def get_issue(
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    issue_id: uuid.UUID = Path(...),
    context: ScheduleContext = Depends(require_schedule_context),
) -> IssueResponse:
    return IssueResponse(**IssueService.get_issue(context, issue_id))


@router.post(_BASE + "/issues/{issue_id}/resolve", response_model=IssueResponse)
def resolve_issue(
    payload: IssueResolve,
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    issue_id: uuid.UUID = Path(...),
    context: ScheduleContext = Depends(require_schedule_context),
) -> IssueResponse:
    """Resolve an issue (MANAGE_BLOCKERS) and, by default, store it as institutional knowledge. The row is kept as history."""
    return IssueResponse(**IssueService.resolve_issue(context, issue_id, payload))


@router.post(_BASE + "/issues/{issue_id}/evidence", response_model=IssueResponse)
async def add_issue_evidence(
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    issue_id: uuid.UUID = Path(...),
    file: UploadFile = File(...),
    notes: Optional[str] = Form(None),
    context: ScheduleContext = Depends(require_schedule_context),
) -> IssueResponse:
    """Attach a supporting file (photo, report, permit letter) to an issue."""
    contents = await file.read()
    try:
        stored_path, digest = save_upload(contents, file.filename or "evidence")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return IssueResponse(**IssueService.attach_evidence(
        context, issue_id, filename=file.filename or "evidence", stored_path=stored_path, file_hash=digest, notes=notes))


@router.get(_BASE + "/root-causes", response_model=List[RootCauseResponse])
def list_root_causes(
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    context: ScheduleContext = Depends(require_schedule_context),
) -> List[RootCauseResponse]:
    return [RootCauseResponse(**r) for r in IssueService.list_root_causes(context)]


@router.post(_BASE + "/root-causes", response_model=RootCauseResponse, status_code=status.HTTP_201_CREATED)
def create_root_cause(
    payload: RootCauseCreate,
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    context: ScheduleContext = Depends(require_schedule_context),
) -> RootCauseResponse:
    """Identify a root cause and link the issues it explains (MANAGE_BLOCKERS)."""
    return RootCauseResponse(**IssueService.create_root_cause(context, payload))


@router.post(_BASE + "/root-causes/{root_cause_id}/issues", response_model=RootCauseResponse)
def link_issues_to_root_cause(
    payload: RootCauseLink,
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    root_cause_id: uuid.UUID = Path(...),
    context: ScheduleContext = Depends(require_schedule_context),
) -> RootCauseResponse:
    return RootCauseResponse(**IssueService.link_issues(context, root_cause_id, payload.issue_ids))


@router.get(_BASE + "/root-cause-analysis", response_model=RootCauseAnalysis)
def root_cause_analysis(
    project_id: uuid.UUID = Path(...),
    schedule_id: str = Path(...),
    context: ScheduleContext = Depends(require_schedule_context),
) -> RootCauseAnalysis:
    """Issues grouped by category with repeated-pattern flags, identified root causes and the most delayed stages."""
    return RootCauseAnalysis(**IssueService.analysis(context))
