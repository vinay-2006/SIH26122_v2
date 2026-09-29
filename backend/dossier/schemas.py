"""
Authoritative schemas for Audit Dossier Foundation in SETUAI V7 Phase 13.
Encapsulates structured, tamper-evident execution and decision history across project lifecycle.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SectionStatus(str, Enum):
    """Lifecycle and completeness status for each individual dossier section."""
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DossierScope(str, Enum):
    """Scope level of the assembled dossier."""
    PROJECT = "PROJECT"
    SCHEDULE = "SCHEDULE"
    ACTIVITY = "ACTIVITY"


class DossierCompleteness(BaseModel):
    """Deterministic evaluation of dossier completeness."""
    overall_status: SectionStatus = Field(description="Aggregated completeness status")
    complete_sections: List[str] = Field(default_factory=list, description="Sections with AVAILABLE status")
    partial_sections: List[str] = Field(default_factory=list, description="Sections with PARTIAL status")
    missing_sections: List[str] = Field(default_factory=list, description="Sections with NOT_AVAILABLE status")
    total_sections: int = Field(default=0, description="Total evaluated sections count")
    completed_count: int = Field(default=0, description="Count of fully available sections")


# ============================================================================
# SECTION SCHEMAS
# ============================================================================

class ProjectSection(BaseModel):
    status: SectionStatus = SectionStatus.AVAILABLE
    project_id: uuid.UUID
    project_code: str
    project_name: str
    project_status: str
    created_at: Optional[datetime] = None


class ScheduleSection(BaseModel):
    status: SectionStatus = SectionStatus.AVAILABLE
    schedule_id: str
    version_code: Optional[str] = None
    is_active: bool = False
    supersedes_schedule_id: Optional[str] = None
    source_hash: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StageItem(BaseModel):
    stage_id: uuid.UUID
    stage_code: Optional[str] = None
    stage_name: str
    status: Optional[str] = None
    progress_pct: float = 0.0
    sequence_order: int = 1
    parent_stage_id: Optional[uuid.UUID] = None


class StagesSection(BaseModel):
    status: SectionStatus = SectionStatus.AVAILABLE
    stages: List[StageItem] = Field(default_factory=list)


class ActivityItem(BaseModel):
    activity_id: str
    activity_code: Optional[str] = None
    activity_name: str
    canonical_execution_state: str
    workflow_condition: str
    progress_pct: float = 0.0
    weight_factor: float = 1.0
    stage_id: Optional[uuid.UUID] = None
    is_reopened: bool = False


class ActivitiesSection(BaseModel):
    status: SectionStatus = SectionStatus.AVAILABLE
    activities: List[ActivityItem] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    event_id: str
    event_type: Optional[str] = None
    event_date: Optional[date] = None
    document_id: Optional[str] = None
    document_name: Optional[str] = None
    source_reference: Optional[str] = None
    raw_claim_text: str
    matched_activity_id: Optional[str] = None
    claimed_pct: Optional[float] = None
    claimed_quantity: Optional[float] = None


class ExecutionEvidenceSection(BaseModel):
    status: SectionStatus = SectionStatus.AVAILABLE
    evidence: List[EvidenceItem] = Field(default_factory=list)


class MatchingItem(BaseModel):
    candidate_id: str
    event_id: str
    activity_id: str
    rank_order: int
    match_tier: Optional[str] = None
    composite_confidence: Optional[float] = None
    semantic_score: Optional[float] = None
    fuzzy_score: Optional[float] = None
    supporting_signals: Optional[str] = None
    disqualifying_signals: Optional[str] = None


class MatchingSection(BaseModel):
    status: SectionStatus = SectionStatus.AVAILABLE
    candidates: List[MatchingItem] = Field(default_factory=list)


class ValidationItem(BaseModel):
    issue_id: str
    event_id: str
    rule_code: Optional[str] = None
    severity: Optional[str] = None
    description: str


class ValidationSection(BaseModel):
    status: SectionStatus = SectionStatus.AVAILABLE
    issues: List[ValidationItem] = Field(default_factory=list)
    conflict_count: int = 0


class HumanDecisionItem(BaseModel):
    decision_id: str
    event_id: str
    activity_id: str
    action: str
    approved_pct: Optional[float] = None
    approved_qty: Optional[float] = None
    planner_id: Optional[uuid.UUID] = None
    justification: str
    decided_at: Optional[datetime] = None


class HumanDecisionsSection(BaseModel):
    status: SectionStatus = SectionStatus.AVAILABLE
    decisions: List[HumanDecisionItem] = Field(default_factory=list)


class ApprovedActualItem(BaseModel):
    actual_id: str
    activity_id: str
    schedule_id: str
    actual_start: Optional[date] = None
    actual_finish: Optional[date] = None
    actual_pct_complete: Optional[float] = None
    actual_quantity: Optional[float] = None
    decision_id: str
    is_reopened: bool = False
    created_at: Optional[datetime] = None


class ApprovedActualsSection(BaseModel):
    status: SectionStatus = SectionStatus.AVAILABLE
    actuals: List[ApprovedActualItem] = Field(default_factory=list)


class ReopenHistoryItem(BaseModel):
    activity_id: str
    reopen_status: str
    workflow_condition: str
    historical_revisions_count: int = 0
    current_actual: Optional[ApprovedActualItem] = None
    historical_snapshots: List[Dict[str, Any]] = Field(default_factory=list)


class ReopenHistorySection(BaseModel):
    status: SectionStatus = SectionStatus.AVAILABLE
    records: List[ReopenHistoryItem] = Field(default_factory=list)


class ProgressSection(BaseModel):
    status: SectionStatus = SectionStatus.AVAILABLE
    project_progress_pct: float = 0.0
    schedule_progress_pct: float = 0.0
    calculation_basis: str = "STAGE_WEIGHTED"
    breakdowns: Dict[str, Any] = Field(default_factory=dict)


class ImpactSection(BaseModel):
    status: SectionStatus = SectionStatus.AVAILABLE
    scenarios: List[Dict[str, Any]] = Field(default_factory=list)


class AuditChainVerificationResult(BaseModel):
    status: str = Field(description="'VALID', 'BROKEN', 'EMPTY', or 'INCOMPLETE'")
    records_checked: int = Field(default=0, description="Total audit log entries inspected")
    first_log_id: Optional[int] = Field(default=None, description="Starting log_id in chain")
    last_log_id: Optional[int] = Field(default=None, description="Ending log_id in chain")
    broken_at_log_id: Optional[int] = Field(default=None, description="log_id where verification failed")
    reason: Optional[str] = Field(default=None, description="Verification failure diagnostic")
    expected_hash: Optional[str] = Field(default=None, description="Expected previous hash")
    actual_hash: Optional[str] = Field(default=None, description="Actual hash observed")
    verified_at: datetime = Field(default_factory=datetime.utcnow)


class AuditChainSection(BaseModel):
    status: SectionStatus = SectionStatus.AVAILABLE
    verification: AuditChainVerificationResult
    recent_logs: List[Dict[str, Any]] = Field(default_factory=list)


class ExtensionSection(BaseModel):
    """Typed extension slot for future domain additions (Member 2)."""
    status: SectionStatus = SectionStatus.NOT_AVAILABLE
    provider: Optional[str] = None
    records: List[Dict[str, Any]] = Field(default_factory=list)


# ============================================================================
# COMPREHENSIVE AUDIT DOSSIER ROOT
# ============================================================================

class AuditDossier(BaseModel):
    """
    Export-ready, structured audit dossier answering:
    'Show me the complete evidence and decision history behind this project execution state.'
    """
    dossier_id: str = Field(description="Unique generated dossier identifier")
    project_id: uuid.UUID = Field(description="Authorized Project UUID")
    schedule_id: Optional[str] = Field(default=None, description="Scope schedule version identifier")
    activity_id: Optional[str] = Field(default=None, description="Scope activity identifier if activity-level")
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    scope: DossierScope

    # Core Provenance & Execution Sections
    project: ProjectSection
    schedule: Optional[ScheduleSection] = None
    stages: StagesSection
    activities: ActivitiesSection
    execution_evidence: ExecutionEvidenceSection
    matching: MatchingSection
    validation: ValidationSection
    human_decisions: HumanDecisionsSection
    approved_actuals: ApprovedActualsSection
    reopen_history: ReopenHistorySection
    progress: ProgressSection
    impact: ImpactSection

    # Cryptographic Audit Chain Verification
    audit_chain: AuditChainSection

    # Typed Extension Points (for Member 2 Quality / Contractor / Memory / Agent)
    extensions: Dict[str, ExtensionSection] = Field(default_factory=dict)

    # Completeness Evaluation
    completeness: DossierCompleteness
