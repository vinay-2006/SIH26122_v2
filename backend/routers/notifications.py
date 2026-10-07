"""Notifications router (V7): what the site engineer sees after a supervisor decides on their claims."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path, Query

from backend.context.project import ProjectContext, require_project_context
from backend.services.notification_service import NotificationService

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["notifications"])


@router.get("/notifications")
def list_notifications(
    project_id: uuid.UUID = Path(...),
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    context: ProjectContext = Depends(require_project_context),
):
    """The caller's own notifications in this project (recipient-only, enforced by row-level security)."""
    return NotificationService.list_for_user(context, unread_only, limit)


@router.post("/notifications/read-all")
def mark_all_notifications_read(
    project_id: uuid.UUID = Path(...),
    context: ProjectContext = Depends(require_project_context),
):
    return NotificationService.mark_all_read(context)


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(
    project_id: uuid.UUID = Path(...),
    notification_id: uuid.UUID = Path(...),
    context: ProjectContext = Depends(require_project_context),
):
    return NotificationService.mark_read(context, notification_id)


@router.get("/my-claims")
def my_claims(
    project_id: uuid.UUID = Path(...),
    limit: int = Query(100, ge=1, le=300),
    context: ProjectContext = Depends(require_project_context),
):
    """Claims the caller submitted with their current status and latest supervisor decision, comment and timestamp."""
    return NotificationService.my_claims(context, limit)
