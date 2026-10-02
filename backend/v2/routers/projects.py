from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field

from .. import permissions as P
from ..auth import CurrentUser, ProjectAccess, get_current_user, require
from ..errors import ApiError
from ..services import projects as svc

router = APIRouter(prefix="/api/v2", tags=["projects"])


class ProjectCreate(BaseModel):
    project_code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{2,31}$")
    project_name: str = Field(min_length=3, max_length=200)
    description: Optional[str] = None
    client_name: Optional[str] = None
    project_type: Optional[str] = None
    location: Optional[str] = None
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    geofence_radius_m: Optional[float] = Field(default=None, gt=0)
    planned_start: Optional[date] = None
    planned_finish: Optional[date] = None
    contract_finish: Optional[date] = None
    lifecycle_status: Optional[str] = Field(default=None, pattern="^(UPCOMING|ONGOING|COMPLETED)$")


class ProjectPatch(BaseModel):
    project_name: Optional[str] = Field(default=None, min_length=3, max_length=200)
    description: Optional[str] = None
    client_name: Optional[str] = None
    project_type: Optional[str] = None
    location: Optional[str] = None
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    geofence_radius_m: Optional[float] = Field(default=None, gt=0)
    planned_start: Optional[date] = None
    planned_finish: Optional[date] = None
    contract_finish: Optional[date] = None
    lifecycle_status: Optional[str] = Field(default=None, pattern="^(UPCOMING|ONGOING|COMPLETED)$")


class SettingsPatch(BaseModel):
    over_baseline_tolerance_pct: Optional[float] = Field(default=None, ge=0, le=100)
    completion_threshold_pct: Optional[float] = Field(default=None, ge=0, le=100)
    working_days_per_week: Optional[int] = Field(default=None, ge=1, le=7)
    require_photo_evidence: Optional[bool] = None
    extra: Optional[Dict[str, Any]] = None


class MemberAdd(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: str


class MemberPatch(BaseModel):
    role: Optional[str] = None
    status: Optional[str] = None


class InviteCreate(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: str
    expires_in_days: int = Field(default=7, ge=1, le=30)


class AcceptInvite(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class GrantBody(BaseModel):
    email: str
    capability: str


# ---- platform
@router.post("/platform/grants", status_code=201)
def grant(body: GrantBody, user: CurrentUser = Depends(get_current_user)):
    return svc.grant_capability(user, body.email, body.capability)


@router.delete("/platform/grants/{user_id}/{capability}", status_code=204)
def revoke(user_id: uuid.UUID, capability: str, user: CurrentUser = Depends(get_current_user)):
    svc.revoke_capability(user, user_id, capability)


@router.post("/platform/users/{user_id}/revoke-sessions")
def revoke_sessions(user_id: uuid.UUID, user: CurrentUser = Depends(get_current_user)):
    return svc.revoke_sessions(user, user_id)


@router.get("/me")
def me(user: CurrentUser = Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "capabilities": sorted(user.capabilities),
            "projects": svc.list_projects(user)}


# ---- projects
@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, user: CurrentUser = Depends(get_current_user)):
    return svc.create_project(user, body.model_dump())


@router.get("/projects")
def list_projects(user: CurrentUser = Depends(get_current_user)):
    return svc.list_projects(user)


@router.get("/projects/{project_id}")
def get_project(access: ProjectAccess = Depends(require(P.VIEW_PROJECT))):
    return {**svc.get_project(access.project_id), "my_role": access.role}


@router.patch("/projects/{project_id}")
def patch_project(body: ProjectPatch, access: ProjectAccess = Depends(require(P.MANAGE_PROJECT, writable=True))):
    return svc.update_project(access.user, access.project_id, body.model_dump(exclude_unset=True))


@router.post("/projects/{project_id}/archive")
def archive(access: ProjectAccess = Depends(require(P.MANAGE_PROJECT))):
    return svc.set_record_status(access.user, access.project_id, "ARCHIVED")


@router.post("/projects/{project_id}/restore")
def restore(access: ProjectAccess = Depends(require(P.MANAGE_PROJECT))):
    return svc.set_record_status(access.user, access.project_id, "ACTIVE")


@router.get("/projects/{project_id}/settings")
def get_settings(access: ProjectAccess = Depends(require(P.MANAGE_SETTINGS))):
    return svc.get_settings(access.project_id)


@router.patch("/projects/{project_id}/settings")
def patch_settings(body: SettingsPatch, access: ProjectAccess = Depends(require(P.MANAGE_SETTINGS, writable=True))):
    return svc.update_settings(access.user, access.project_id, body.model_dump(exclude_unset=True))


# ---- members and invitations
@router.get("/projects/{project_id}/members")
def members(access: ProjectAccess = Depends(require(P.VIEW_MEMBERS))):
    return svc.list_members(access.project_id)


@router.post("/projects/{project_id}/members", status_code=201)
def add_member(body: MemberAdd, access: ProjectAccess = Depends(require(P.MANAGE_MEMBERS, writable=True))):
    return svc.add_existing_member(access.user, access.project_id, body.email, body.role)


@router.patch("/projects/{project_id}/members/{member_id}")
def patch_member(member_id: uuid.UUID, body: MemberPatch, access: ProjectAccess = Depends(require(P.MANAGE_MEMBERS, writable=True))):
    return svc.change_member(access.user, access.project_id, member_id, body.role, body.status)


@router.delete("/projects/{project_id}/members/{member_id}", status_code=204)
def remove_member(member_id: uuid.UUID, access: ProjectAccess = Depends(require(P.MANAGE_MEMBERS, writable=True))):
    svc.change_member(access.user, access.project_id, member_id, None, "REMOVED")


@router.post("/projects/{project_id}/invitations", status_code=201)
def invite(body: InviteCreate, access: ProjectAccess = Depends(require(P.MANAGE_MEMBERS, writable=True))):
    return svc.create_invitation(access.user, access.project_id, body.email, body.role, body.expires_in_days)


@router.get("/projects/{project_id}/invitations")
def invitations(access: ProjectAccess = Depends(require(P.MANAGE_MEMBERS))):
    return svc.list_invitations(access.project_id)


@router.delete("/projects/{project_id}/invitations/{invitation_id}", status_code=204)
def revoke_invite(invitation_id: uuid.UUID, access: ProjectAccess = Depends(require(P.MANAGE_MEMBERS, writable=True))):
    svc.revoke_invitation(access.user, access.project_id, invitation_id)


@router.post("/invitations/accept")
def accept(body: AcceptInvite, user: CurrentUser = Depends(get_current_user)):
    return svc.accept_invitation(user, body.token)


# ---- site engineer: reports and evidence only
@router.post("/projects/{project_id}/documents", status_code=201)
async def upload_document(kind: str = Form(...), file: UploadFile = File(...),
                          access: ProjectAccess = Depends(require(P.UPLOAD_REPORT, writable=True))):
    content = await file.read()
    return svc.upload_document(access.user, access.project_id, kind, file.filename or "upload", content, file.content_type)
