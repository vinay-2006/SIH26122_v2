"""
Impact Domain Schemas for SETUAI V7 Phase 10.
Defines canonical data contracts for Compound Impact Intelligence, Scenario Evaluation,
Dependency Graph Propagation, Float Analysis, and Stage/Project Impact.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ImpactRelationshipType(str, Enum):
    FS = "FS"
    SS = "SS"
    FF = "FF"
    SF = "SF"


class ImpactSeverity(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SeedActivityInput(BaseModel):
    """Seed activity and simulated delay parameters for a compound impact scenario."""
    activity_id: str
    delay_days: int = Field(..., ge=0, description="Hypothetical delay in days (>= 0)")
    reason: Optional[str] = Field(None, description="Operational rationale for delay simulation")


class PreviewScenarioRequest(BaseModel):
    """Request payload for an ephemeral compound impact preview."""
    seed_activities: List[SeedActivityInput] = Field(..., min_length=1)
    scenario_name: Optional[str] = Field("Compound Impact Simulation", max_length=255)


class CreateScenarioRequest(BaseModel):
    """Request payload for evaluating and persisting a named impact scenario."""
    name: str = Field(..., min_length=1, max_length=255)
    seed_activities: List[SeedActivityInput] = Field(..., min_length=1)
    description: Optional[str] = None


class ActivityImpactItem(BaseModel):
    """Explainable impact record for a single downstream activity."""
    activity_id: str
    activity_name: str
    stage_id: Optional[uuid.UUID] = None
    stage_name: Optional[str] = None
    is_critical: bool = False
    canonical_state: str
    workflow_condition: str
    baseline_start: Optional[date] = None
    baseline_finish: Optional[date] = None
    shifted_start: Optional[date] = None
    shifted_finish: Optional[date] = None
    gross_delay_days: int = 0
    total_float: Optional[float] = None
    float_status: str = "KNOWN"
    absorbed_delay_days: Optional[int] = 0
    residual_delay_days: Optional[int] = 0
    controlling_predecessor: Optional[str] = None
    controlling_relationship: Optional[str] = None
    lag_days: float = 0.0
    propagation_depth: int = 1
    causal_path: List[str] = Field(default_factory=list)
    classification: str
    explanation: str


class StageImpactItem(BaseModel):
    """Aggregate schedule impact on an affected stage."""
    stage_id: uuid.UUID
    stage_name: str
    affected_activities_count: int = 0
    max_stage_delay_days: int = 0
    stage_progression_impact: str


class FloatAnalysisSummary(BaseModel):
    """Summary of float absorption and critical path stress across evaluated activities."""
    total_activities_evaluated: int = 0
    activities_with_known_float: int = 0
    activities_with_unknown_float: int = 0
    total_float_absorbed_days: int = 0
    critical_path_delays_count: int = 0


class ImpactScenarioResult(BaseModel):
    """Canonical authoritative result contract for a compound impact simulation."""
    scenario_id: Optional[uuid.UUID] = None
    project_id: uuid.UUID
    schedule_id: str
    name: str
    seed_activities: List[SeedActivityInput]
    affected_activities: List[ActivityImpactItem] = Field(default_factory=list)
    affected_stages: List[StageImpactItem] = Field(default_factory=list)
    float_analysis: FloatAnalysisSummary
    schedule_impact_days: int = 0
    project_completion_impact_days: int = 0
    severity: ImpactSeverity
    has_cycle: bool = False
    cycle_path: Optional[List[str]] = None
    calculated_at: datetime
    algorithm_version: str = "V7_COMPOUND_A1"


class ImpactScenarioSummary(BaseModel):
    """Metadata summary of a persisted impact scenario."""
    scenario_id: uuid.UUID
    project_id: uuid.UUID
    schedule_id: str
    name: str
    created_by: Optional[uuid.UUID] = None
    created_at: datetime
    seed_count: int = 0
    affected_count: int = 0
    schedule_impact_days: int = 0
    severity: str
