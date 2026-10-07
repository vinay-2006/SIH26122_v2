"""Multi-file intake router (V7): upload several reports/files/photos in one operation."""
from __future__ import annotations

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, Request, UploadFile

from backend.context import gates
from backend.context.project import ProjectContext, require_project_context
from backend.context.schedule import resolve_explicit_schedule
from backend.rbac.permissions import Permission, has_permission
from backend.context.errors import raise_permission_denied
from backend.services.batch_intake_service import BatchIntakeService
from backend.shared.uploads import MAX_UPLOAD_BYTES

router = APIRouter(tags=["upload-batches"])


@router.post("/api/v1/claims/batch")
def create_claim_batch(
    request: Request,
    files: List[UploadFile] = File(..., description="One or more reports: .pdf .xlsx .xls .csv .txt .xer .jpg .png"),
    schedule_id: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    project_context: ProjectContext = Depends(gates.project_events_create),
):
    """
    Process a batch of files in one operation: extract each file (isolated), identify the activities each one reports,
    match every extracted claim to the project schedule, merge identical claims reported by several files, and send the
    matched claims to the supervisor review queue. Returns the batch report (files, claims, matches, activities).
    A file that cannot be read is reported in the batch and never fails the other files.
    """
    scope = resolve_explicit_schedule(
        project_context, schedule_id, request.headers.get("X-Schedule-ID"), request.query_params.get("schedule_id")
    )
    uploads = []
    for f in files:
        data = f.file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"'{f.filename}' exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
        uploads.append((f.filename or "upload", data))
    return BatchIntakeService.process(project_context, scope.schedule_id, uploads, notes)


@router.get("/api/v1/projects/{project_id}/upload-batches")
def list_upload_batches(
    project_id: uuid.UUID = Path(...),
    mine: bool = Query(False, description="Only batches uploaded by the caller"),
    limit: int = Query(50, ge=1, le=200),
    context: ProjectContext = Depends(require_project_context),
):
    if not has_permission(context.role, Permission.VIEW_EXECUTION_EVENTS):
        raise_permission_denied(Permission.VIEW_EXECUTION_EVENTS.value, context.role)
    return BatchIntakeService.list_batches(context, mine, limit)


@router.get("/api/v1/projects/{project_id}/upload-batches/{batch_id}")
def get_upload_batch(
    project_id: uuid.UUID = Path(...),
    batch_id: uuid.UUID = Path(...),
    context: ProjectContext = Depends(require_project_context),
):
    if not has_permission(context.role, Permission.VIEW_EXECUTION_EVENTS):
        raise_permission_denied(Permission.VIEW_EXECUTION_EVENTS.value, context.role)
    return BatchIntakeService.report(context, batch_id)
