from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel

from .. import permissions as P
from ..auth import ProjectAccess, require
from ..services import schedules as svc

router = APIRouter(prefix="/api/v2/projects/{project_id}", tags=["schedules"])


class ActivateBody(BaseModel):
    reason: Optional[str] = None


# ---- import pipeline (Project Manager only)
@router.post("/schedule-imports", status_code=201)
async def upload_schedule(file: UploadFile = File(...), resources_file: Optional[UploadFile] = File(default=None),
                          baseline_name: Optional[str] = Form(default=None), data_date: Optional[str] = Form(default=None),
                          planned_start: Optional[str] = Form(default=None), planned_finish: Optional[str] = Form(default=None),
                          project_name: Optional[str] = Form(default=None),
                          access: ProjectAccess = Depends(require(P.MANAGE_SCHEDULE, writable=True))):
    content = await file.read()
    res = await resources_file.read() if resources_file is not None else None
    params = {k: v for k, v in dict(baseline_name=baseline_name, data_date=data_date, planned_start=planned_start,
                                    planned_finish=planned_finish, project_name=project_name).items() if v}
    return svc.stage_import(access.user, access.project_id, file.filename or "schedule", content, res, params)


@router.get("/schedule-imports/{import_id}")
def get_import(import_id: uuid.UUID, access: ProjectAccess = Depends(require(P.MANAGE_SCHEDULE))):
    return svc.get_import(access.project_id, import_id)


@router.put("/schedule-imports/{import_id}/decisions")
def put_decisions(import_id: uuid.UUID, body: Dict[str, Any], access: ProjectAccess = Depends(require(P.MANAGE_SCHEDULE, writable=True))):
    return svc.update_decisions(access.user, access.project_id, import_id, body)


@router.post("/schedule-imports/{import_id}/build", status_code=201)
def build(import_id: uuid.UUID, access: ProjectAccess = Depends(require(P.MANAGE_SCHEDULE, writable=True))):
    return svc.build_import(access.user, access.project_id, import_id)


@router.delete("/schedule-imports/{import_id}", status_code=204)
def discard_import(import_id: uuid.UUID, access: ProjectAccess = Depends(require(P.MANAGE_SCHEDULE, writable=True))):
    svc.discard_import(access.user, access.project_id, import_id)


# ---- versions
@router.get("/schedule-versions")
def versions(access: ProjectAccess = Depends(require(P.VIEW_SCHEDULE))):
    return svc.list_versions(access.project_id)


@router.get("/schedule-versions/compare")
def compare(old: uuid.UUID, new: uuid.UUID, access: ProjectAccess = Depends(require(P.MANAGE_SCHEDULE))):
    return svc.compare_versions(access.project_id, old, new)


@router.get("/schedule-versions/{version_id}")
def version(version_id: uuid.UUID, access: ProjectAccess = Depends(require(P.VIEW_SCHEDULE))):
    return svc.get_version(access.project_id, version_id)


@router.get("/schedule-versions/{version_id}/wbs")
def wbs(version_id: uuid.UUID, access: ProjectAccess = Depends(require(P.VIEW_SCHEDULE))):
    return svc.version_wbs(access.project_id, version_id)


@router.get("/schedule-versions/{version_id}/activities")
def activities(version_id: uuid.UUID, access: ProjectAccess = Depends(require(P.VIEW_SCHEDULE))):
    return svc.version_activities(access.project_id, version_id)


@router.post("/schedule-versions/{version_id}/activate")
def activate(version_id: uuid.UUID, body: ActivateBody = ActivateBody(), access: ProjectAccess = Depends(require(P.ACTIVATE_SCHEDULE, writable=True))):
    return svc.activate_version(access.user, access.project_id, version_id, body.reason)


@router.delete("/schedule-versions/{version_id}", status_code=204)
def discard(version_id: uuid.UUID, access: ProjectAccess = Depends(require(P.MANAGE_SCHEDULE, writable=True))):
    svc.discard_version(access.user, access.project_id, version_id)
