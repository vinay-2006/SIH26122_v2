"""
Pydantic Schemas for SETUAI V7 Phase 12 — Supervising Agent.
Defines structured contracts for findings, briefings, queries, and tool payloads.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AgentStatus(str, Enum):
    """Operational status of the supervising agent."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"


class FindingCategory(str, Enum):
    """Classification of supervisory intelligence findings."""
    STAGE_RISK = "STAGE_RISK"
    ACTIVITY_DELAY = "ACTIVITY_DELAY"
    QUALITY_HOLD = "QUALITY_HOLD"
    CONTRACTOR_ISSUE = "CONTRACTOR_ISSUE"
    INCIDENT_ALERT = "INCIDENT_ALERT"
    SCHEDULE_RISK = "SCHEDULE_RISK"
    REVIEW_QUEUE = "REVIEW_QUEUE"
    DEPENDENCY_BLOCK = "DEPENDENCY_BLOCK"


class FindingSeverity(str, Enum):
    """Severity of a supervisory finding."""
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFORMATIONAL = "INFORMATIONAL"


class InvocationMode(str, Enum):
    """Supported invocation modes for the supervising agent."""
    ON_DEMAND_BRIEFING = "ON_DEMAND_BRIEFING"
    ENTITY_ANALYSIS = "ENTITY_ANALYSIS"
    STAGE_ANALYSIS = "STAGE_ANALYSIS"
    INCIDENT_INTELLIGENCE = "INCIDENT_INTELLIGENCE"
    REVIEW_QUEUE = "REVIEW_QUEUE"


class EvidenceReference(BaseModel):
    """Traceable evidence backing a supervisory finding."""
    entity_type: str = Field(description="Type of entity: e.g. EXECUTION_EVENT, QUALITY_GATE, AUDIT_RECORD, INCIDENT")
    entity_id: str = Field(description="Unique identifier of the entity")
    source_type: Optional[str] = Field(default=None, description="Source domain or document type")
    reference_code: Optional[str] = Field(default=None, description="Human-readable code or label")
    details: Optional[str] = Field(default=None, description="Summary details of the evidence")


class AgentFinding(BaseModel):
    """Structured, evidence-backed finding surfaced by the supervising agent."""
    finding_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    category: FindingCategory
    severity: FindingSeverity
    title: str
    description: str
    why_it_matters: str
    evidence: List[EvidenceReference] = Field(default_factory=list)
    affected_entity_type: Optional[str] = None
    affected_entity_id: Optional[str] = None
    recommended_action: str


class SupervisoryBriefing(BaseModel):
    """Comprehensive, structured supervisory briefing for a project."""
    briefing_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    generated_at: str
    agent_status: AgentStatus
    summary: str
    findings: List[AgentFinding] = Field(default_factory=list)
    overall_progress: Dict[str, Any] = Field(default_factory=dict)
    stage_summary: List[Dict[str, Any]] = Field(default_factory=list)
    critical_activities: List[Dict[str, Any]] = Field(default_factory=list)
    blocked_activities: List[Dict[str, Any]] = Field(default_factory=list)
    quality_holds: List[Dict[str, Any]] = Field(default_factory=list)
    contractor_issues: List[Dict[str, Any]] = Field(default_factory=list)
    schedule_risks: List[Dict[str, Any]] = Field(default_factory=list)
    relevant_historical_incidents: List[Dict[str, Any]] = Field(default_factory=list)
    review_queue_summary: Dict[str, Any] = Field(default_factory=dict)
    recommended_reviews: List[str] = Field(default_factory=list)
    audit_verification: Optional[Dict[str, Any]] = None


class AgentQueryRequest(BaseModel):
    """User request payload for the supervising agent."""
    query: str
    mode: Optional[InvocationMode] = InvocationMode.ON_DEMAND_BRIEFING
    stage_id: Optional[str] = None
    activity_id: Optional[str] = None
    contractor_id: Optional[str] = None


class AgentQueryResponse(BaseModel):
    """Structured answer to a specific user query."""
    project_id: str
    query: str
    generated_at: str
    agent_status: AgentStatus
    answer: str
    findings: List[AgentFinding] = Field(default_factory=list)
    evidence: List[EvidenceReference] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    context_used: Dict[str, Any] = Field(default_factory=dict)
