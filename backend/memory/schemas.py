"""
Domain-neutral schemas for Institutional Memory Retrieval in SETUAI V7 Phase 11.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class MemoryRecord(BaseModel):
    """
    Normalized retrieval representation of an institutional memory record.
    Domain-neutral: Member 2's eventual Institutional Memory domain maps into this interface.
    """
    memory_id: str = Field(description="Unique identifier of the memory item")
    project_id: uuid.UUID = Field(description="Scoped Project UUID for strict multi-tenant isolation")
    schedule_id: Optional[str] = Field(default=None, description="Associated schedule version code")
    stage_id: Optional[uuid.UUID] = Field(default=None, description="Associated stage UUID")
    activity_id: Optional[str] = Field(default=None, description="External schedule activity ID (e.g. A1000)")
    contractor_id: Optional[uuid.UUID] = Field(default=None, description="Associated contractor UUID")
    work_package_id: Optional[uuid.UUID] = Field(default=None, description="Associated work package UUID")

    title: str = Field(description="Short human-readable title or summary headline")
    summary: Optional[str] = Field(default=None, description="Synthesized executive summary or lesson")
    content: str = Field(description="Full text narrative, root cause, or resolution description")

    incident_type: Optional[str] = Field(default=None, description="Categorization (e.g. DELAY, ACCESS, QUALITY, DISPUTE)")
    severity: Optional[str] = Field(default=None, description="Impact severity (e.g. LOW, MEDIUM, HIGH, CRITICAL)")
    status: Optional[str] = Field(default=None, description="Lifecycle status (e.g. OPEN, RESOLVED, CLOSED)")

    metadata: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary domain-specific metadata")
    source_type: str = Field(default="INSTITUTIONAL_INCIDENT", description="Origin source system or entity type")
    source_reference: Optional[str] = Field(default=None, description="Pointer to source document or reference ID")

    created_at: Optional[datetime] = Field(default=None, description="Timestamp of record creation")
    updated_at: Optional[datetime] = Field(default=None, description="Timestamp of latest modification")

    embedding_reference: Optional[List[float]] = Field(
        default=None,
        description="Precomputed vector representation if available"
    )


class MemoryFilter(BaseModel):
    """
    Metadata filters applied in Stage A of two-stage retrieval.
    All fields are optional; when present, they enforce strict filtering.
    """
    project_id: Optional[uuid.UUID] = Field(
        default=None,
        description="Explicit project filter; must match caller ProjectContext"
    )
    schedule_id: Optional[str] = Field(default=None, description="Scope to schedule version")
    stage_id: Optional[uuid.UUID] = Field(default=None, description="Scope to specific stage")
    activity_id: Optional[str] = Field(default=None, description="Scope to specific activity")
    contractor_id: Optional[uuid.UUID] = Field(default=None, description="Scope to specific contractor")
    work_package_id: Optional[uuid.UUID] = Field(default=None, description="Scope to specific work package")
    incident_type: Optional[str] = Field(default=None, description="Filter by incident type")
    severity: Optional[str] = Field(default=None, description="Filter by severity level")
    status: Optional[str] = Field(default=None, description="Filter by status")
    date_from: Optional[date] = Field(default=None, description="Earliest recorded date")
    date_to: Optional[date] = Field(default=None, description="Latest recorded date")


class MemoryQuery(BaseModel):
    """
    Client query request payload.
    """
    query: str = Field(default="", description="Natural language search query")
    filters: Optional[MemoryFilter] = Field(default=None, description="Metadata filters")
    top_k: int = Field(default=10, ge=1, le=100, description="Maximum number of results to return")


class ScoreBreakdown(BaseModel):
    """
    Explainable breakdown of coefficients contributing to the final ranking score.
    """
    semantic_score: float = Field(default=0.0, description="Vector cosine similarity score")
    lexical_score: float = Field(default=0.0, description="Token-overlap lexical score when semantic is unavailable")
    activity_bonus: float = Field(default=0.0, description="Bonus for exact activity match")
    stage_bonus: float = Field(default=0.0, description="Bonus for exact stage match")
    contractor_bonus: float = Field(default=0.0, description="Bonus for exact contractor match")
    recency_bonus: float = Field(default=0.0, description="Time-decay recency bonus")
    total_score: float = Field(default=0.0, description="Summed explainable final score")


class MemoryMatchReason(str):
    EXACT_ACTIVITY = "EXACT_ACTIVITY"
    EXACT_STAGE = "EXACT_STAGE"
    EXACT_CONTRACTOR = "EXACT_CONTRACTOR"
    EXACT_WORK_PACKAGE = "EXACT_WORK_PACKAGE"
    EXACT_INCIDENT_TYPE = "EXACT_INCIDENT_TYPE"
    SEMANTIC_SIMILARITY = "SEMANTIC_SIMILARITY"
    LEXICAL_TOKEN_MATCH = "LEXICAL_TOKEN_MATCH"
    RECENCY_BONUS = "RECENCY_BONUS"


class MemoryResult(BaseModel):
    """
    Normalized result item returned from the retrieval pipeline.
    """
    record: MemoryRecord
    score: float = Field(description="Explainable final relevance score in range [0.0, 1.0+]")
    match_reasons: List[str] = Field(default_factory=list, description="Explicit reasons why this item matched")
    score_breakdown: ScoreBreakdown = Field(description="Detailed numerical breakdown of score")


class MemoryRetrievalResponse(BaseModel):
    """
    API and service response payload for institutional memory searches.
    """
    query: str
    results: List[MemoryResult]
    total_candidates: int = Field(description="Count of candidate records before top_k truncation")
    retrieval_mode: str = Field(description="Retrieval mode used: 'metadata_only', 'metadata+semantic', 'metadata+lexical_fallback'")
    filters_applied: Dict[str, Any] = Field(default_factory=dict, description="Normalized filters applied during retrieval")
    generated_at: datetime = Field(default_factory=datetime.utcnow)
