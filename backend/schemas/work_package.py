"""
Work Package schemas for SETUAI V7.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field


class WorkPackageCreate(BaseModel):
    package_code: Optional[str] = Field(None, description="Project-unique work package code")
    package_name: str = Field(..., min_length=1, max_length=255, description="Work package name")
    contractor_id: Optional[uuid.UUID] = Field(None, description="Assigned contractor ID")
    stage_id: Optional[uuid.UUID] = Field(None, description="Associated stage ID")
    discipline: Optional[str] = Field(None, description="Discipline (e.g. CIVIL, PIPING)")
    description: Optional[str] = Field(None, description="Detailed package description")
    planned_start: Optional[date] = Field(None, description="Planned start date")
    planned_finish: Optional[date] = Field(None, description="Planned finish date")
    status: Optional[str] = Field("NOT_STARTED", description="Status: NOT_STARTED, IN_PROGRESS, COMPLETED, ON_HOLD, CANCELLED")


class WorkPackageUpdate(BaseModel):
    package_name: Optional[str] = Field(None, min_length=1, max_length=255)
    contractor_id: Optional[uuid.UUID] = None
    stage_id: Optional[uuid.UUID] = None
    discipline: Optional[str] = None
    description: Optional[str] = None
    planned_start: Optional[date] = None
    planned_finish: Optional[date] = None
    status: Optional[str] = None


class WorkPackageResponse(BaseModel):
    work_package_id: uuid.UUID
    project_id: uuid.UUID
    contractor_id: Optional[uuid.UUID] = None
    stage_id: Optional[uuid.UUID] = None
    package_code: Optional[str] = None
    package_name: str
    discipline: Optional[str] = None
    description: Optional[str] = None
    planned_start: Optional[date] = None
    planned_finish: Optional[date] = None
    status: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
