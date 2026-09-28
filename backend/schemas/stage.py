"""
Stage Domain and Execution State Schemas for SETUAI V7.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class CanonicalExecutionState(str, Enum):
    """Canonical 3-state deterministic execution state."""
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class WorkflowCondition(str, Enum):
    """Operational workflow condition (distinct from execution state)."""
    NONE = "NONE"
    REOPEN_REQUESTED = "REOPEN_REQUESTED"
    REWORK_IN_PROGRESS = "REWORK_IN_PROGRESS"
    QUALITY_HOLD = "QUALITY_HOLD"
    BLOCKED = "BLOCKED"


class StageStatus(str, Enum):
    """Database stored stage status."""
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    ON_HOLD = "ON_HOLD"


class StageCreate(BaseModel):
    stage_code: Optional[str] = Field(None, description="Unique human-readable stage code")
    stage_name: str = Field(..., min_length=1, max_length=255, description="Stage name")
    parent_stage_id: Optional[uuid.UUID] = Field(None, description="Parent stage for hierarchy")
    sequence_order: int = Field(1, ge=1, description="Sequence order within schedule")
    weight_pct: Optional[float] = Field(None, ge=0.0, le=100.0, description="Stage weighting percentage")
    planned_start: Optional[date] = Field(None, description="Planned stage start date")
    planned_finish: Optional[date] = Field(None, description="Planned stage completion date")
    contract_milestone_date: Optional[date] = Field(None, description="Contractual milestone deadline")
    gating_predecessor_stage_id: Optional[uuid.UUID] = Field(None, description="Predecessor stage that must be completed")
    completion_rule: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Custom gating completion criteria")


class StageUpdate(BaseModel):
    stage_code: Optional[str] = None
    stage_name: Optional[str] = Field(None, min_length=1, max_length=255)
    parent_stage_id: Optional[uuid.UUID] = None
    sequence_order: Optional[int] = Field(None, ge=1)
    weight_pct: Optional[float] = Field(None, ge=0.0, le=100.0)
    status: Optional[StageStatus] = None
    planned_start: Optional[date] = None
    planned_finish: Optional[date] = None
    contract_milestone_date: Optional[date] = None
    gating_predecessor_stage_id: Optional[uuid.UUID] = None
    completion_rule: Optional[Dict[str, Any]] = None


class StageResponse(BaseModel):
    stage_id: uuid.UUID
    project_id: uuid.UUID
    schedule_id: str
    parent_stage_id: Optional[uuid.UUID] = None
    stage_code: Optional[str] = None
    stage_name: str
    sequence_order: int
    weight_pct: Optional[float] = None
    status: str
    planned_start: Optional[date] = None
    planned_finish: Optional[date] = None
    contract_milestone_date: Optional[date] = None
    gating_predecessor_stage_id: Optional[uuid.UUID] = None
    completion_rule: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime


class StageTreeNode(StageResponse):
    children: List[StageTreeNode] = Field(default_factory=list)


class StageActivityExecutionSummary(BaseModel):
    activity_id: str
    activity_name: str
    canonical_state: str
    workflow_condition: str
    actual_pct_complete: Optional[float] = None
    actual_start: Optional[date] = None
    actual_finish: Optional[date] = None
    weight_factor: float = 1.0
    quality_gate_required: bool = False
    is_reopened: Optional[bool] = False


class StageStateResponse(BaseModel):
    stage_id: uuid.UUID
    stage_name: str
    computed_state: str
    stored_status: str
    workflow_condition: str
    total_activities: int
    completed_activities: int
    in_progress_activities: int
    not_started_activities: int
    activities: List[StageActivityExecutionSummary] = Field(default_factory=list)


class StageProgressResponse(BaseModel):
    stage_id: uuid.UUID
    stage_name: str
    progress_pct: float
    calculation_basis: str
    activity_count: int
    activities: List[StageActivityExecutionSummary] = Field(default_factory=list)


class StageDependencyCheckResponse(BaseModel):
    stage_id: uuid.UUID
    gating_predecessor_stage_id: Optional[uuid.UUID] = None
    predecessor_name: Optional[str] = None
    predecessor_status: Optional[str] = None
    is_satisfied: bool
    blocking_reason: Optional[str] = None


class QualityGateSummary(BaseModel):
    quality_gate_id: uuid.UUID
    gate_name: str
    gate_type: str
    required: bool
    status: str
    activity_id: Optional[str] = None


class StageGateCheckResponse(BaseModel):
    stage_id: uuid.UUID
    total_gates: int
    passed_gates: int
    pending_gates: int
    failed_gates: int
    all_cleared: bool
    gates: List[QualityGateSummary] = Field(default_factory=list)


class StageCompletionCheckResponse(BaseModel):
    stage_id: uuid.UUID
    stage_name: str
    is_complete: bool
    activities_completed: bool
    dependencies_satisfied: bool
    gates_cleared: bool
    has_blockers: bool
    blocking_conditions: List[str] = Field(default_factory=list)


class ActivityExecutionContextResponse(BaseModel):
    activity_id: str
    schedule_id: str
    project_id: uuid.UUID
    stage_id: Optional[uuid.UUID] = None
    canonical_execution_state: str
    workflow_condition: str
    actual_start: Optional[date] = None
    actual_finish: Optional[date] = None
    actual_pct_complete: Optional[float] = None
    quality_gate_required: bool = False
    is_reopened: Optional[bool] = False
    reopen_status: Optional[str] = None

