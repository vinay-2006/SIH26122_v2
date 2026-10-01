"""
Shared request/response models (doc Section 3: "One shared request/response
schema file"). Additive only — add a new model for your own endpoint, never
edit a model another member's code already depends on without flagging the
team first.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class Schedule(BaseModel):
    schedule_id: str
    project_name: str
    data_date: Optional[date] = None
    source_format: Optional[str] = None


class ExecutionState(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class ScheduleActivity(BaseModel):
    schedule_id: str
    activity_id: str
    activity_name: str
    wbs_code: Optional[str] = None
    discipline: str
    location: str
    asset_tag: Optional[str] = None
    planned_start: date
    planned_finish: date
    planned_quantity: Optional[float] = None
    uom: Optional[str] = None
    baseline_pct_complete: float = Field(default=0.0, ge=0.0, le=100.0)
    total_float: Optional[float] = None
    is_critical: Optional[bool] = None


class ScheduleDependency(BaseModel):
    dependency_id: str
    schedule_id: str
    predecessor_activity_id: str
    successor_activity_id: str
    relationship_type: str = "FS"
    lag_days: float = 0.0


# ---------- M1 Feature #30 (WBS Granularity Bridge, schedule-side): WBS tree ----------

class WBSGroupActivity(BaseModel):
    activity_id: str
    planned_quantity: Optional[float] = None


class WBSGroup(BaseModel):
    wbs_code: str
    activities: list[WBSGroupActivity]


class WBSTreeResponse(BaseModel):
    schedule_id: str
    wbs_groups: list[WBSGroup]


# ---------- shared enums (used across M2/M3/M4) ----------

class Discipline(str, Enum):
    CIVIL = "CIVIL"
    PIPING = "PIPING"
    STATIC_ROTATING_EQUIPMENT = "STATIC_ROTATING_EQUIPMENT"
    ELECTRICAL = "ELECTRICAL"
    INSTRUMENTATION = "INSTRUMENTATION"
    HSE = "HSE"
    STRUCTURAL = "STRUCTURAL"
    PROCESS = "PROCESS"
    DRILLING = "DRILLING"
    LOGISTICS = "LOGISTICS"


class EventType(str, Enum):
    ACTUAL_START = "ACTUAL_START"
    ACTUAL_FINISH = "ACTUAL_FINISH"
    PROGRESS_UPDATE = "PROGRESS_UPDATE"
    DELAY = "DELAY"
    BLOCKER = "BLOCKER"


class ClaimMode(str, Enum):
    CUMULATIVE_PCT = "CUMULATIVE_PCT"
    INCREMENTAL_QUANTITY = "INCREMENTAL_QUANTITY"


class DelayReason(str, Enum):
    MATERIAL = "MATERIAL"
    EQUIPMENT = "EQUIPMENT"
    LABOUR = "LABOUR"
    ACCESS = "ACCESS"
    WEATHER = "WEATHER"
    REWORK = "REWORK"
    OTHER = "OTHER"


class InputChannel(str, Enum):
    FILE_UPLOAD = "FILE_UPLOAD"
    SCANNED_OCR = "SCANNED_OCR"
    TYPED_TEXT = "TYPED_TEXT"
    VOICE = "VOICE"
    SCHEDULE_EXPORT = "SCHEDULE_EXPORT"


class UploadPurpose(str, Enum):
    EVIDENCE_PHOTO = "EVIDENCE_PHOTO"
    SCANNED_DIARY = "SCANNED_DIARY"


class ClarificationStatus(str, Enum):
    NONE = "NONE"
    PENDING = "PENDING"
    ANSWERED = "ANSWERED"


class ProvenanceTag(str, Enum):
    AI_EXTRACTED = "AI_EXTRACTED"
    SCHEDULE_AUTO_FILLED = "SCHEDULE_AUTO_FILLED"
    ENGINEER_ENTERED = "ENGINEER_ENTERED"
    SUPERVISOR_EDITED = "SUPERVISOR_EDITED"


class ExecutionClaim(BaseModel):
    event_id: str
    schedule_id: str
    event_date: date
    raw_claim_text: str
    input_channel: str
    language_detected: Optional[str] = None
    reported_activity_id: Optional[str] = None
    discipline: Optional[str] = None
    action: Optional[str] = None
    event_type: Optional[str] = None
    claim_mode: str = "CUMULATIVE_PCT"
    asset_tag: Optional[str] = None
    location: Optional[str] = None
    claimed_quantity: Optional[float] = None
    claimed_uom: Optional[str] = None
    claimed_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    delay_reason: Optional[str] = None
    supervisor_id: Optional[str] = None
    photo_path: Optional[str] = None

    @field_validator("event_id", "schedule_id", "supervisor_id", mode="before")
    @classmethod
    def cast_uuid_to_str(cls, v):
        if v is not None and not isinstance(v, str):
            return str(v)
        return v


# ---------- M2: extraction output shape (this is the LLM's strict JSON contract) ----------

class ExtractedClaimFields(BaseModel):
    """Exact shape the LLM must return. Missing/malformed fields -> null, never crash."""

    event_date: Optional[date] = None
    reported_activity_id: Optional[str] = None
    discipline: Optional[Discipline] = None
    action: Optional[str] = None
    event_type: Optional[EventType] = None
    claim_mode: ClaimMode = ClaimMode.CUMULATIVE_PCT
    asset_tag: Optional[str] = None
    location: Optional[str] = None
    claimed_quantity: Optional[float] = None
    claimed_uom: Optional[str] = None
    claimed_pct: Optional[float] = Field(default=None, ge=0, le=100)
    delay_reason: Optional[DelayReason] = None
    language_detected: Optional[str] = None


# ---------- M2: request/response models for the intake endpoints ----------

class TextClaimRequest(BaseModel):
    """
    Per PRD v5 Section 6.2: "User identity must never be accepted as a
    free-form request-body field." uploader_id/supervisor_id no longer come
    from the client — the router derives them from the authenticated user
    (require_role("SITE_ENGINEER") -> CurrentUser.id) instead.
    """

    raw_claim_text: str
    input_channel: InputChannel = InputChannel.TYPED_TEXT
    schedule_id: Optional[str] = None  # if omitted, defaults to most recently uploaded schedule
    evidence_filename: Optional[str] = None
    evidence_base64: Optional[str] = None


class ClarifyClaimRequest(BaseModel):
    answer: str

    @model_validator(mode="before")
    @classmethod
    def normalize_clarification_input(cls, data: Any) -> Any:
        if isinstance(data, dict):
            ans = data.get("answer")
            if ans is None:
                # Tolerant fallback if a legacy caller sent clarification_answer
                ans = data.get("clarification_answer")
            if ans is not None:
                return {**data, "answer": str(ans).strip()}
        return data


class ClaimResponse(BaseModel):
    event_id: str
    document_id: Optional[str] = None
    schedule_id: str
    event_date: date
    raw_claim_text: str
    input_channel: InputChannel
    language_detected: Optional[str] = None
    reported_activity_id: Optional[str] = None
    matched_activity_id: Optional[str] = None
    discipline: Optional[str] = None
    action: Optional[str] = None
    event_type: Optional[str] = None
    claim_mode: str
    asset_tag: Optional[str] = None
    location: Optional[str] = None
    claimed_quantity: Optional[float] = None
    claimed_uom: Optional[str] = None
    claimed_pct: Optional[float] = None
    delay_reason: Optional[str] = None
    supervisor_id: Optional[str] = None
    photo_path: Optional[str] = None
    status: str
    clarification_status: str = ClarificationStatus.NONE.value
    clarification_question: Optional[str] = None
    clarification_answer: Optional[str] = None
    field_provenance: dict[str, str] = Field(default_factory=dict)
    priority_score: Optional[float] = None    # Feature 32, set by /check and /review-queue
    priority_reasons: Optional[str] = None    # newline-separated "[Tag] explanation" lines
    created_at: datetime


class CandidateMatch(BaseModel):
    candidate_id: str
    event_id: str
    schedule_id: str
    activity_id: str
    rank_order: int = Field(ge=1, le=3)
    match_tier: Optional[str] = None
    composite_confidence: float = Field(ge=0.0, le=1.0)
    semantic_score: Optional[float] = None
    fuzzy_score: Optional[float] = None
    location_score: Optional[float] = None
    discipline_score: Optional[float] = None
    supporting_signals: Optional[str] = None
    disqualifying_signals: Optional[str] = None


class ValidationIssue(BaseModel):
    issue_id: str
    event_id: str
    rule_code: Optional[str] = None
    severity: Optional[str] = None
    description: str


class PlannerDecision(BaseModel):
    decision_id: str
    event_id: str
    selected_activity_id: str
    action: Optional[str] = None
    approved_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    approved_qty: Optional[float] = None
    planner_id: str
    justification: str
    decided_at: Optional[datetime] = None


class DecisionRequest(BaseModel):
    """Body for POST /api/v1/decisions. planner_id is never taken from this
    body — it's derived server-side from the authenticated SUPERVISOR."""

    event_id: str
    action: str  # APPROVE, EDIT, REJECT, HOLD
    selected_activity_id: Optional[str] = None
    approved_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    approved_qty: Optional[float] = None
    justification: str = Field(min_length=1)


class DecisionResponse(BaseModel):
    decision_id: str
    event_id: str
    action: str
    status: str
    selected_activity_id: str
    approved_actual: Optional[dict] = None


class ApprovedActual(BaseModel):
    actual_id: str
    decision_id: str
    event_id: str
    schedule_id: str
    activity_id: str
    actual_start: Optional[date] = None
    actual_finish: Optional[date] = None
    actual_pct_complete: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
    )
    actual_quantity: Optional[float] = None
    exported_at: Optional[datetime] = None
