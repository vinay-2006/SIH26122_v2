"""
Issue / delay schemas for SETUAI V7 (the generalisation of the former "blocker").

An issue is an explainable fact reported from the field:
    project -> stage -> activity -> category, severity, dates -> evidence -> (root cause) -> resolution -> memory
`blocks_work` marks the issues that stop the activity: the BLOCKED workflow condition is DERIVED from ACTIVE
work-blocking issues, never stored on the activity.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


class IssueCategory(str, Enum):
    LABOUR_SHORTAGE = "LABOUR_SHORTAGE"
    EQUIPMENT_SHORTAGE = "EQUIPMENT_SHORTAGE"
    MATERIAL_SHORTAGE = "MATERIAL_SHORTAGE"
    MATERIAL_DELIVERY_DELAY = "MATERIAL_DELIVERY_DELAY"
    CONTRACTOR_ISSUE = "CONTRACTOR_ISSUE"
    WEATHER = "WEATHER"
    SITE_ACCESS = "SITE_ACCESS"
    SAFETY = "SAFETY"
    TECHNICAL = "TECHNICAL"
    DESIGN_DOCUMENTATION = "DESIGN_DOCUMENTATION"
    PERMIT_APPROVAL = "PERMIT_APPROVAL"
    OTHER = "OTHER"


class IssueSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class IssueCreate(BaseModel):
    activity_id: Optional[str] = Field(None, description="Affected activity (its stage is derived)")
    stage_id: Optional[uuid.UUID] = Field(None, description="Affected stage (when no single activity is affected)")
    category_code: IssueCategory
    title: str = Field(..., min_length=3, max_length=200)
    description: str = Field(..., min_length=3, max_length=4000)
    severity: IssueSeverity = IssueSeverity.MEDIUM
    reported_date: Optional[date] = Field(None, description="Date the issue occurred (defaults to today)")
    expected_duration_days: Optional[float] = Field(None, ge=0, le=3650)
    blocks_work: bool = Field(False, description="True when work on the activity/stage is stopped by this issue")
    source_event_id: Optional[str] = Field(None, description="Field claim this issue was raised from")

    @model_validator(mode="after")
    def _needs_a_target(self):
        if not self.activity_id and not self.stage_id:
            raise ValueError("an issue must target an activity_id or a stage_id")
        return self


class IssueResolve(BaseModel):
    resolution_notes: str = Field(..., min_length=3, max_length=4000, description="What was done to resolve it")
    cause: Optional[str] = Field(None, max_length=2000, description="Root cause, if known (defaults to the linked root cause)")
    outcome: Optional[str] = Field(None, max_length=2000, description="Result of the resolution (e.g. delay avoided)")
    lessons_learned: Optional[str] = Field(None, max_length=2000)
    add_to_memory: bool = Field(True, description="Store the resolved issue as institutional knowledge")
    share_with_organisation: bool = Field(False, description="Let other projects retrieve this lesson")


class IssueEvidenceItem(BaseModel):
    document_id: str
    file_name: Optional[str] = None


class IssueResponse(BaseModel):
    issue_id: uuid.UUID
    project_id: uuid.UUID
    schedule_id: str
    activity_id: Optional[str] = None
    activity_name: Optional[str] = None
    discipline: Optional[str] = None
    stage_id: Optional[uuid.UUID] = None
    stage_name: Optional[str] = None
    category_code: str
    category_name: str
    title: str
    description: str
    severity: str
    reported_date: date
    expected_duration_days: Optional[float] = None
    blocks_work: bool
    status: str
    source_event_id: Optional[str] = None
    reported_by: uuid.UUID
    reported_by_name: Optional[str] = None
    created_at: datetime
    resolved_by: Optional[uuid.UUID] = None
    resolved_by_name: Optional[str] = None
    resolved_at: Optional[datetime] = None
    resolution_notes: Optional[str] = None
    root_cause_id: Optional[uuid.UUID] = None
    root_cause_title: Optional[str] = None
    evidence: List[IssueEvidenceItem] = []
    memory_incident_id: Optional[uuid.UUID] = None


class RootCauseCreate(BaseModel):
    category_code: IssueCategory
    title: str = Field(..., min_length=3, max_length=200)
    summary: Optional[str] = Field(None, max_length=4000)
    issue_ids: List[uuid.UUID] = Field(default_factory=list, description="Issues of this project to link to the root cause")


class RootCauseLink(BaseModel):
    issue_ids: List[uuid.UUID] = Field(..., min_length=1)


class RootCauseResponse(BaseModel):
    root_cause_id: uuid.UUID
    project_id: uuid.UUID
    category_code: str
    category_name: str
    title: str
    summary: Optional[str] = None
    status: str
    identified_at: datetime
    issue_count: int = 0
    open_issue_count: int = 0
    activity_ids: List[str] = []
    stage_names: List[str] = []
    expected_delay_days: float = 0.0


class CategoryPattern(BaseModel):
    category_code: str
    category_name: str
    issue_count: int
    open_count: int
    activity_count: int
    stage_count: int
    expected_delay_days: float
    linked_to_root_cause: int
    activity_ids: List[str] = []
    stage_names: List[str] = []
    is_repeated_pattern: bool = Field(description="3+ issues of one category across 2+ activities")


class RootCauseAnalysis(BaseModel):
    project_id: uuid.UUID
    schedule_id: str
    total_issues: int
    open_issues: int
    categories: List[CategoryPattern]
    root_causes: List[RootCauseResponse]
    delayed_stages: List[Dict[str, Any]] = []
