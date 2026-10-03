"""Claims: the Site Engineer files and manages their own; the Supervisor reviews and decides; the Project Manager sees aggregate counts only.
Handlers are thin: all rules live in backend/v2/domain (and the database guards behind them)."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import idempotency, permissions as P
from ..auth import ProjectAccess, require
from ..domain import catalog, claims as dc, decisions as dd
from ._common import ERRORS, LIMIT, OFFSET, actor_of, page

router = APIRouter(prefix="/api/v2/projects/{project_id}", tags=["claims"], responses=ERRORS)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuantityIn(Strict):
    qty: Decimal = Field(ge=0, max_digits=16, decimal_places=4, description="the figure exactly as reported")
    uom: str = Field(min_length=1, max_length=32, description="unit as written; must be a recognised unit")
    basis: Literal["CUMULATIVE", "INCREMENTAL"] = Field(description="CUMULATIVE = total to date; INCREMENTAL = since the previous report")
    resource_hint: Optional[str] = Field(default=None, max_length=80, description="which measured resource this is, when the unit alone is ambiguous")


class ClaimCreate(Strict):
    event_date: date = Field(description="the day the work was done / reported (not in the future)")
    raw_text: str = Field(min_length=3, max_length=4000, description="the report text the claim comes from")
    input_channel: Literal["TYPED", "VOICE_TRANSCRIPT", "API"] = "TYPED"
    activity_uid: Optional[uuid.UUID] = Field(default=None, description="an activity of the ACTIVE schedule; omit to let a Supervisor match it")
    reported_activity_ref: Optional[str] = Field(default=None, max_length=120)
    quantities: List[QuantityIn] = Field(default_factory=list, max_length=20)
    claimed_pct: Optional[Decimal] = Field(default=None, ge=0, le=100, description="percent-only claims are stored as reported and never converted automatically")
    claimed_start: Optional[date] = None
    claimed_finish: Optional[date] = None
    location: Optional[str] = Field(default=None, max_length=200)
    asset_tag: Optional[str] = Field(default=None, max_length=80, description="equipment / asset tag the report mentions; used by automatic matching")
    evidence_document_ids: List[uuid.UUID] = Field(default_factory=list, max_length=20)


class WithdrawBody(Strict):
    reason: str = Field(min_length=3, max_length=500)


class AnswerBody(Strict):
    answer: str = Field(min_length=2, max_length=2000)
    evidence_document_ids: List[uuid.UUID] = Field(default_factory=list, max_length=20)


class EvidenceBody(Strict):
    document_id: uuid.UUID


class RematchBody(Strict):
    activity_uid: Optional[uuid.UUID] = Field(default=None, description="the activity to assign (manual override); omit to run the automatic matching engine again")


class BindBody(Strict):
    bindings: Dict[uuid.UUID, uuid.UUID] = Field(description="claim_quantity_id -> assignment_uid")


class DecisionBody(Strict):
    action: Literal["APPROVE", "EDIT", "REJECT", "HOLD"] = Field(description="HOLD asks the engineer a question (clarification_question) and keeps the claim open")
    justification: Optional[str] = Field(default=None, max_length=2000, description="required for REJECT and EDIT")
    method: Optional[Literal["QUANTITIES_AS_CLAIMED", "MANUAL_QUANTITIES", "APPLY_PCT_TO_ASSIGNMENTS", "PCT_ONLY_ACTIVITY"]] = Field(
        default=None, description="how the approved figures are obtained; a percent-only claim on a measured activity needs an explicit method")
    activity_uid: Optional[uuid.UUID] = None
    approved_quantities: Optional[Dict[str, Dict[str, Any]]] = Field(default=None, description="assignment_uid -> {cumulative|incremental: number}")
    apply_pct: Optional[Decimal] = Field(default=None, ge=0, le=100)
    actual_start: Optional[date] = None
    actual_finish: Optional[date] = None
    overrun_ack_note: Optional[str] = Field(default=None, max_length=2000, description="required beyond the project's over-baseline tolerance; kept with the decision")
    short_close_note: Optional[str] = Field(default=None, max_length=2000)
    clarification_question: Optional[str] = Field(default=None, max_length=2000)

    def params(self) -> Dict[str, Any]:
        return {k: v for k, v in self.model_dump().items() if v is not None and k != "action"}


class QuestionBody(Strict):
    question: str = Field(min_length=3, max_length=2000)


def _replay(response: Response, replayed: bool):
    if replayed:
        response.headers["Idempotent-Replay"] = "true"


# ---------------------------------------------------------------------------------------------- everyone: the active schedule
@router.get("/activities", summary="Active-schedule activities with their measured assignments and approved progress")
def activities(q: Optional[str] = None, wbs_prefix: Optional[str] = None, discipline: Optional[str] = None, as_of: Optional[date] = None,
               limit: int = LIMIT, offset: int = OFFSET, access: ProjectAccess = Depends(require(P.VIEW_SCHEDULE))):
    r = catalog.active_activities(actor_of(access), q, wbs_prefix, discipline, limit + 1, offset, as_of)
    more = len(r["items"]) > limit
    return {"version": r["version"], "items": r["items"][:limit], "limit": limit, "offset": offset, "next_offset": offset + limit if more else None}


# ---------------------------------------------------------------------------------------------- Site Engineer
@router.post("/claims", status_code=201, summary="File a progress claim (Site Engineer)")
def submit(body: ClaimCreate, response: Response, idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
           access: ProjectAccess = Depends(require(P.SUBMIT_CLAIM, writable=True))):
    """Stores exactly what was reported. The claim is a proposal: it never changes progress until a Supervisor approves it.
    Send `Idempotency-Key` to make a retry safe; an identical claim is also refused as DUPLICATE_CLAIM."""
    a = actor_of(access)
    payload = body.model_dump(mode="json")
    out, replayed = idempotency.run(a.user_id, a.project_id, "claims.create", idempotency_key, payload, lambda: dc.submit_claim(
        a, event_date=body.event_date, raw_text=body.raw_text, input_channel=body.input_channel, activity_uid=body.activity_uid, reported_activity_ref=body.reported_activity_ref,
        quantities=[q.model_dump() for q in body.quantities], claimed_pct=body.claimed_pct, claimed_start=body.claimed_start, claimed_finish=body.claimed_finish,
        location=body.location, asset_tag=body.asset_tag, evidence_document_ids=body.evidence_document_ids))
    _replay(response, replayed)
    return out


@router.get("/my-claims", summary="Own claims (Site Engineer)")
def my_claims(status: Optional[str] = None, limit: int = LIMIT, offset: int = OFFSET, access: ProjectAccess = Depends(require(P.VIEW_OWN_CLAIMS))):
    return page(lambda l, o: dc.list_my_claims(actor_of(access), status, l, o), limit, offset)


@router.post("/claims/{claim_id}/withdraw", summary="Withdraw an own pending claim (Site Engineer)")
def withdraw(claim_id: uuid.UUID, body: WithdrawBody, access: ProjectAccess = Depends(require(P.SUBMIT_CLAIM, writable=True))):
    return dc.withdraw_claim(actor_of(access), claim_id, body.reason)


@router.post("/claims/{claim_id}/clarification-answer", summary="Answer a Supervisor's question (Site Engineer)")
def answer(claim_id: uuid.UUID, body: AnswerBody, access: ProjectAccess = Depends(require(P.SUBMIT_CLAIM, writable=True))):
    return dc.answer_clarification(actor_of(access), claim_id, body.answer, body.evidence_document_ids)


@router.post("/claims/{claim_id}/evidence", status_code=201, summary="Attach an uploaded document to an own pending claim (Site Engineer)")
def attach(claim_id: uuid.UUID, body: EvidenceBody, access: ProjectAccess = Depends(require(P.SUBMIT_CLAIM, writable=True))):
    return dc.attach_evidence(actor_of(access), claim_id, body.document_id)


@router.post("/claims/{claim_id}/correction", status_code=201, summary="File a corrected claim linked to an own REJECTED claim (Site Engineer)")
def correction(claim_id: uuid.UUID, body: ClaimCreate, response: Response, idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
               access: ProjectAccess = Depends(require(P.SUBMIT_CLAIM, writable=True))):
    """The rejected claim is never edited: a correction is a NEW claim that points back at it."""
    a = actor_of(access)
    out, replayed = idempotency.run(a.user_id, a.project_id, f"claims.correction.{claim_id}", idempotency_key, body.model_dump(mode="json"), lambda: dc.submit_claim(
        a, event_date=body.event_date, raw_text=body.raw_text, input_channel=body.input_channel, activity_uid=body.activity_uid, reported_activity_ref=body.reported_activity_ref,
        quantities=[q.model_dump() for q in body.quantities], claimed_pct=body.claimed_pct, claimed_start=body.claimed_start, claimed_finish=body.claimed_finish,
        location=body.location, asset_tag=body.asset_tag, evidence_document_ids=body.evidence_document_ids, resubmits_event_id=claim_id))
    _replay(response, replayed)
    return out


# ---------------------------------------------------------------------------------------------- Supervisor
@router.get("/review-queue", summary="Claims awaiting a decision, highest priority first (Supervisor)")
def queue(status: Optional[List[str]] = None, limit: int = LIMIT, offset: int = OFFSET, access: ProjectAccess = Depends(require(P.REVIEW_CLAIMS))):
    return page(lambda l, o: dc.review_queue(actor_of(access), status, l, o), limit, offset)


@router.get("/claims/{claim_id}", summary="One claim with quantities, evidence, validations and decisions (own claim: Site Engineer; any: Supervisor; never: PM)")
def get_claim(claim_id: uuid.UUID, access: ProjectAccess = Depends(require(P.VIEW_PROJECT))):
    return dc.get_claim(actor_of(access), claim_id)


@router.post("/claims/{claim_id}/rematch", summary="Assign a claim to an activity of the active schedule (manual override), or re-run automatic matching when no activity is given (Supervisor)")
def rematch(claim_id: uuid.UUID, body: RematchBody, access: ProjectAccess = Depends(require(P.REVIEW_CLAIMS, writable=True))):
    return dc.rematch_claim(actor_of(access), claim_id, body.activity_uid)


@router.post("/claims/{claim_id}/bind-quantities", summary="Bind reported quantities to measured assignments (Supervisor)")
def bind(claim_id: uuid.UUID, body: BindBody, access: ProjectAccess = Depends(require(P.REVIEW_CLAIMS, writable=True))):
    return dc.bind_quantities(actor_of(access), claim_id, body.bindings)


@router.post("/claims/{claim_id}/decision-preview", summary="What a decision would do, without recording it (Supervisor)")
def preview(claim_id: uuid.UUID, body: DecisionBody, access: ProjectAccess = Depends(require(P.REVIEW_CLAIMS))):
    return dd.preview_decision(actor_of(access), claim_id, action=body.action, **body.params())


@router.post("/claims/{claim_id}/decision", status_code=201, summary="Approve, edit, reject or hold a claim (Supervisor) — the only way progress is created")
def decide(claim_id: uuid.UUID, body: DecisionBody, access: ProjectAccess = Depends(require(P.REVIEW_CLAIMS, writable=True))):
    """Atomic: decision, ledger entries, notification and audit records commit together or not at all.
    Reported figures are never overwritten; the approved figures, the method and the result are stored beside them."""
    return dd.decide(actor_of(access), claim_id, action=body.action, **body.params())


@router.post("/claims/{claim_id}/clarification-request", status_code=201, summary="Ask the engineer a question and keep the claim open (Supervisor)")
def ask(claim_id: uuid.UUID, body: QuestionBody, access: ProjectAccess = Depends(require(P.REVIEW_CLAIMS, writable=True))):
    return dd.decide(actor_of(access), claim_id, action="HOLD", clarification_question=body.question, justification=body.question)


# ---------------------------------------------------------------------------------------------- Supervisor and Project Manager
@router.get("/claim-counts", summary="Aggregate claim counts by status (Supervisor, Project Manager) — no claim content")
def counts(access: ProjectAccess = Depends(require(P.VIEW_CLAIM_COUNTS))):
    return dc.claim_counts(actor_of(access))
