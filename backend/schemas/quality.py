"""
Pydantic schemas for Phase 10 Quality, ITP, and Hold Points domain.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ITPBase(BaseModel):
    title: str = Field(..., min_length=1, description="Title/name of the Inspection and Test Plan")
    description: Optional[str] = None
    schedule_id: Optional[str] = None
    stage_id: Optional[uuid.UUID] = None
    discipline: Optional[str] = None
    responsible_party: Optional[str] = None
    contractor_id: Optional[uuid.UUID] = None
    work_package_id: Optional[uuid.UUID] = None
    status: str = Field(default="DRAFT", pattern="^(DRAFT|ACTIVE|ARCHIVED)$")


class ITPCreate(ITPBase):
    pass


class ITPUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    schedule_id: Optional[str] = None
    stage_id: Optional[uuid.UUID] = None
    discipline: Optional[str] = None
    responsible_party: Optional[str] = None
    contractor_id: Optional[uuid.UUID] = None
    work_package_id: Optional[uuid.UUID] = None
    status: Optional[str] = Field(default=None, pattern="^(DRAFT|ACTIVE|ARCHIVED)$")


class ITPResponse(ITPBase):
    itp_id: uuid.UUID
    project_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class QualityGateBase(BaseModel):
    gate_name: str = Field(..., min_length=1, description="Name/title of the quality checkpoint")
    gate_type: str = Field(
        ...,
        pattern="^(INSPECTION|TEST|POUR_CARD|WELD_INSPECTION|NDT|MATERIAL_CERTIFICATE|NCR_CLEARANCE|CLIENT_APPROVAL|PRE_COMMENCEMENT|INTERMEDIATE_HOLD|CLEARANCE|FINAL_TAKEOVER|SAFETY_AUDIT)$",
    )
    checkpoint_category: str = Field(
        default="QUALITY_CHECK",
        pattern="^(HOLD|WITNESS|REVIEW|QUALITY_CHECK)$",
    )
    stage_id: Optional[uuid.UUID] = None
    schedule_id: Optional[str] = None
    activity_id: Optional[str] = None
    itp_id: Optional[uuid.UUID] = None
    contractor_id: Optional[uuid.UUID] = None
    work_package_id: Optional[uuid.UUID] = None
    required: bool = True
    due_date: Optional[date] = None
    remarks: Optional[str] = None


class QualityGateCreate(QualityGateBase):
    pass


class QualityGateResponse(QualityGateBase):
    quality_gate_id: uuid.UUID
    project_id: uuid.UUID
    status: str
    passed_at: Optional[datetime] = None
    passed_by: Optional[uuid.UUID] = None
    waived_at: Optional[datetime] = None
    waived_by: Optional[uuid.UUID] = None
    waiver_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class QualityEvidenceCreate(BaseModel):
    source_document_id: Optional[str] = None
    evidence_type: str = Field(
        ...,
        pattern="^(TEST_REPORT|PHOTO|THIRD_PARTY_CERT|NCR_CLEARANCE|INSPECTION_NOTE|POUR_CARD|WELD_INSPECTION|NDT_RESULT|MATERIAL_CERTIFICATE|CLIENT_APPROVAL|OTHER)$",
    )
    result: str = Field(default="PASS", pattern="^(PASS|FAIL|PENDING_REVIEW)$")
    inspector_name: Optional[str] = None
    inspection_date: Optional[date] = None
    evidence_hash: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class QualityEvidenceResponse(QualityEvidenceCreate):
    quality_evidence_id: uuid.UUID
    quality_gate_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class QualityGatePassPayload(BaseModel):
    remarks: Optional[str] = None


class QualityGateFailPayload(BaseModel):
    remarks: Optional[str] = None


class QualityGateWaivePayload(BaseModel):
    waiver_reason: str = Field(..., min_length=3, description="Reason for waiving the quality gate")
    remarks: Optional[str] = None


class QualityStatusResponse(BaseModel):
    activity_id: str
    quality_gate_required: bool
    is_eligible: bool
    status: str
    total_gates: int
    passed_gates: int
    pending_gates: int
    failed_gates: int
    waived_gates: int
    has_active_hold_point: bool
    blocking_reason: Optional[str] = None
    gates: List[QualityGateResponse] = Field(default_factory=list)
