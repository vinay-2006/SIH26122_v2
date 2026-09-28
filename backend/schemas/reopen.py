"""
Reopen, Rework, and Actuals Revision Schemas for SETUAI V7 Phase 7.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.schemas.stage import CanonicalExecutionState, WorkflowCondition


class ReopenReason(str, Enum):
    """Categorized justification reason for reopening a completed activity."""
    INCORRECT_COMPLETION = "INCORRECT_COMPLETION"
    CONTRADICTORY_FIELD_REPORT = "CONTRADICTORY_FIELD_REPORT"
    QUALITY_FAILURE = "QUALITY_FAILURE"
    QUANTITY_CORRECTION = "QUANTITY_CORRECTION"
    DATE_CORRECTION = "DATE_CORRECTION"
    SUPERVISOR_CORRECTION = "SUPERVISOR_CORRECTION"
    OTHER = "OTHER"


class ReopenStatus(str, Enum):
    """Reopen status corresponding to database check constraint ('NONE', 'REQUESTED', 'APPROVED', 'REJECTED')."""
    NONE = "NONE"
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ReopenDecision(str, Enum):
    """Planner/Manager decision on a reopen request."""
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ReopenRequestCreate(BaseModel):
    """Request payload to challenge and request reopening of a completed activity."""
    reason: ReopenReason = Field(..., description="Categorized reason for reopen")
    justification: str = Field(..., min_length=10, max_length=2000, description="Detailed human justification")
    evidence_event_ids: Optional[List[str]] = Field(default_factory=list, description="IDs of execution events serving as contradictory evidence")


class ReopenDecisionRequest(BaseModel):
    """Decision payload by an authorized planner/supervisor/PM to approve or reject reopening."""
    decision: ReopenDecision = Field(..., description="Decision: APPROVED or REJECTED")
    notes: Optional[str] = Field(None, max_length=2000, description="Decision explanation or reason for rejection")
    rework_instructions: Optional[str] = Field(None, max_length=2000, description="Operational instructions for field team during rework")


class ReopenStatusResponse(BaseModel):
    """Status and context for an activity's reopen lifecycle."""
    activity_id: str
    schedule_id: str
    project_id: uuid.UUID
    canonical_state: CanonicalExecutionState
    workflow_condition: WorkflowCondition
    reopen_status: ReopenStatus
    reason: Optional[str] = None
    justification: Optional[str] = None
    requested_by: Optional[uuid.UUID] = None
    requested_at: Optional[datetime] = None
    decided_by: Optional[uuid.UUID] = None
    decided_at: Optional[datetime] = None
    decision_notes: Optional[str] = None
    rework_instructions: Optional[str] = None
    original_actual_id: Optional[str] = None


class ActualRevisionApprovalRequest(BaseModel):
    """Payload to approve a revised actual following authorized rework."""
    actual_start: Optional[date] = Field(None, description="Corrected actual start date")
    actual_finish: Optional[date] = Field(None, description="Corrected actual finish date")
    actual_quantity: Optional[float] = Field(None, ge=0.0, description="Corrected actual quantity installed")
    actual_pct_complete: float = Field(..., ge=0.0, le=100.0, description="Revised completion percentage")
    revision_notes: str = Field(..., min_length=5, max_length=2000, description="Audit notes explaining the revision")
    claim_event_id: Optional[str] = Field(None, description="Related execution event triggering or supporting the revision")


class ActualRevisionResponse(BaseModel):
    """Response returned upon successful approval of an actual revision."""
    success: bool
    activity_id: str
    schedule_id: str
    project_id: uuid.UUID
    canonical_state: CanonicalExecutionState
    workflow_condition: WorkflowCondition
    previous_actual: Dict[str, Any]
    current_actual: Dict[str, Any]
    audit_log_id: int


class ActivityExecutionHistoryResponse(BaseModel):
    """Complete, tamper-evident execution and revision history for an activity."""
    activity_id: str
    schedule_id: str
    project_id: uuid.UUID
    canonical_state: CanonicalExecutionState
    workflow_condition: WorkflowCondition
    current_approved_actual: Optional[Dict[str, Any]] = None
    historical_approved_actuals: List[Dict[str, Any]] = Field(default_factory=list)
    reopen_history: List[Dict[str, Any]] = Field(default_factory=list)
    rework_execution_events: List[Dict[str, Any]] = Field(default_factory=list)
    audit_timeline: List[Dict[str, Any]] = Field(default_factory=list)
