"""
Progress Domain Schemas for SETUAI V7 Phase 8.
Defines canonical data contracts for Activity, Stage, Schedule, and Project Weighted Progress.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ActivityProgressItem(BaseModel):
    """Explainable progress record for a single activity within rollups."""
    activity_id: str
    activity_name: str
    stage_id: Optional[uuid.UUID] = None
    canonical_state: str
    workflow_condition: str
    is_reopened: bool = False
    progress_pct: float = Field(..., ge=0.0, le=100.0)
    actual_pct_complete: Optional[float] = None
    actual_quantity: Optional[float] = None
    planned_quantity: Optional[float] = None
    weight_factor: float = Field(1.0, ge=0.0)
    weighted_contribution: float = Field(0.0, ge=0.0, le=100.0)


class ActivityProgressResponse(BaseModel):
    """Authoritative progress response for a single activity."""
    activity_id: str
    schedule_id: str
    project_id: uuid.UUID
    progress_pct: float = Field(..., ge=0.0, le=100.0)
    canonical_state: str
    workflow_condition: str
    is_reopened: bool = False
    actual_pct_complete: Optional[float] = None
    actual_quantity: Optional[float] = None
    planned_quantity: Optional[float] = None
    calculation_basis: str


class StageProgressBreakdown(BaseModel):
    """Explainable progress rollup for a stage."""
    stage_id: uuid.UUID
    stage_name: str
    sequence_order: int = 1
    weight_pct: Optional[float] = None
    progress_pct: float = Field(..., ge=0.0, le=100.0)
    weighted_contribution: float = Field(0.0, ge=0.0, le=100.0)
    activity_count: int = 0
    completed_count: int = 0
    activities: List[ActivityProgressItem] = Field(default_factory=list)


class ScheduleProgressResponse(BaseModel):
    """Authoritative progress response for a schedule version."""
    schedule_id: str
    project_id: uuid.UUID
    progress_pct: float = Field(..., ge=0.0, le=100.0)
    calculation_basis: str
    total_activities: int = 0
    completed_activities: int = 0
    in_progress_activities: int = 0
    not_started_activities: int = 0
    stages: List[StageProgressBreakdown] = Field(default_factory=list)
    activities: List[ActivityProgressItem] = Field(default_factory=list)


class ScheduleVersionProgressItem(BaseModel):
    """Summary progress for a specific schedule version within a project."""
    schedule_id: str
    version_code: Optional[str] = None
    active: bool = False
    progress_pct: float = Field(..., ge=0.0, le=100.0)
    calculation_basis: str
    total_activities: int = 0
    completed_activities: int = 0


class ProjectProgressResponse(BaseModel):
    """Authoritative progress response for a project across schedules and stages."""
    project_id: uuid.UUID
    project_name: str
    progress_pct: float = Field(..., ge=0.0, le=100.0)
    calculation_basis: str
    target_schedule_id: Optional[str] = None
    schedules: List[ScheduleVersionProgressItem] = Field(default_factory=list)
    stages: List[StageProgressBreakdown] = Field(default_factory=list)


class ProgressBreakdownResponse(BaseModel):
    """Comprehensive hierarchical breakdown from project down to activities."""
    project_id: uuid.UUID
    project_name: str
    schedule_id: str
    overall_progress_pct: float = Field(..., ge=0.0, le=100.0)
    calculation_basis: str
    stages: List[StageProgressBreakdown] = Field(default_factory=list)
    unassigned_activities: List[ActivityProgressItem] = Field(default_factory=list)
