"""
Schedule Version schemas for SETUAI V7.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class ScheduleVersionCreate(BaseModel):
    version_code: str = Field(..., min_length=1, max_length=50, description="Version label (e.g. V1, V2, BASELINE)")
    csv_content: str = Field(..., min_length=1, description="Raw CSV content containing activities and dependencies")
    data_date: Optional[date] = Field(None, description="Data date / cut-off date of this schedule update")
    source_format: Optional[str] = Field("csv", description="Source format (e.g. csv, xer)")
    version_metadata: Dict[str, Any] = Field(default_factory=dict, description="Custom metadata attributes")
    supersedes_schedule_id: Optional[str] = Field(None, description="Optional ID of schedule version being superseded")
    activate_immediately: bool = Field(False, description="Whether to make this the active version immediately upon creation")


class ScheduleVersionResponse(BaseModel):
    schedule_id: str
    project_id: uuid.UUID
    project_name: str
    version_code: Optional[str] = None
    version_metadata: Dict[str, Any] = Field(default_factory=dict)
    source_hash: Optional[str] = None
    active: bool
    supersedes_schedule_id: Optional[str] = None
    data_date: Optional[date] = None
    source_format: Optional[str] = None
    activity_count: int = 0
    dependency_count: int = 0
    created_at: Optional[datetime] = None


class ScheduleVersionActivateResponse(BaseModel):
    schedule_id: str
    project_id: uuid.UUID
    version_code: Optional[str] = None
    active: bool
    previous_active_schedule_id: Optional[str] = None
    message: str


class ScheduleVersionSupersedeRequest(BaseModel):
    supersedes_schedule_id: str = Field(..., description="Schedule ID that is superseded by this schedule")


class ScheduleMetadataComparison(BaseModel):
    project_id: uuid.UUID
    schedule_a: Dict[str, Any]
    schedule_b: Dict[str, Any]
    differences: Dict[str, Any]
