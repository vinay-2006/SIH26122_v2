"""Legacy `/api/v1/claims/*` contract on v2 data: the original Claim Intake pipeline (submit -> match -> check -> clarify) for the Site Engineer, and the claim reads the
Supervisor pages use. Writes go through the v2 domain services; extraction is the ORIGINAL extraction code (LLM when configured, rule fallback otherwise)."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel

from .. import audit, permissions as P
from ..domain import claims as dc
from ..domain.common import DECIDABLE_STATUSES, PM, SE, SUP, ProjectActor, active_version, actor_tx
from ..errors import ApiError, forbidden
from ..matching import service as matching
from ..services import documents as docsvc
from . import shapes
from .context import Ctx, legacy_ctx, require_version

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["legacy-contract: claims"])


# ----------------------------------------------------------------------------------------------------------------------- loading
_CLAIM_SQL = """
select e.*, ba.external_activity_id as matched_ext,
       (select d.action from planner_decisions d where d.event_id = e.event_id order by d.decided_at desc limit 1) as last_action,
       exists(select 1 from claim_validations v where v.event_id = e.event_id and v.rule_code = 'NO_AUTOMATIC_MATCH') as nomatch,
       exists(select 1 from candidate_matches cm where cm.event_id = e.event_id) as has_cand,
       exists(select 1 from claim_validations v where v.event_id = e.event_id and v.rule_code = 'REVIEW_REQUIRED') as flagged,
       (exists(select 1 from claim_evidence ce join source_documents d on d.document_id = ce.document_id where ce.event_id = e.event_id and d.mime_type like 'image/%%')
        or exists(select 1 from source_documents d where d.document_id = e.document_id and d.mime_type like 'image/%%')) as has_photo
  from execution_events e
  left join baseline_activities ba on ba.version_id = e.filed_in_version_id and ba.activity_uid = e.matched_activity_uid
 where e.project_id = %(project)s and {where}
 order by {order}
 limit %(limit)s
"""


def _visible(ctx: Ctx) -> str:
    """a Site Engineer sees only their own claims; a Supervisor sees the project's; a Project Manager never reaches claim content"""
    if ctx.access.role == SUP:
        return "true"
    if ctx.access.role == SE:
        return "e.filed_by = %(user)s"
    raise forbidden("Project managers see aggregate claim counts only, not claim content", "CLAIM_CONTENT_FORBIDDEN")


def load_claims(c, ctx: Ctx, where: str = "true", params: Optional[dict] = None, order: str = "e.created_at desc", limit: int = 500) -> List[Dict[str, Any]]:
    p = {"project": ctx.project_id, "user": ctx.user.id, "limit": limit, **(params or {})}
    rows = c.execute(_CLAIM_SQL.format(where=f"({_visible(ctx)}) and ({where})", order=order), p).fetchall()
    if not rows:
        return []
    ids = [r["event_id"] for r in rows]
    qmap: Dict[Any, list] = {}
    for q in c.execute("select event_id, reported_qty, reported_uom, qty_basis from claim_quantities where event_id = any(%s) order by claim_quantity_id", (ids,)).fetchall():
        qmap.setdefault(q["event_id"], []).append(q)
    completed = completed_activity_uids(c, ctx, [r["matched_activity_uid"] for r in rows if r["matched_activity_uid"]])
    out = []
    for r in rows:
        st = shapes.legacy_status(r, last_action=r["last_action"], has_no_match_note=r["nomatch"], has_candidates=r["has_cand"], flagged=r["flagged"])
        out.append(shapes.claim_to_legacy(r, qmap.get(r["event_id"], []), status=st, matched_ext=r["matched_ext"], completed_target=r["matched_activity_uid"] in completed,
                                          has_photo=r["has_photo"]))
    return out


def completed_activity_uids(c, ctx: Ctx, uids: List[Any]) -> set:
    """activities whose approved progress is complete (100%) in the project's ACTIVE version"""
    if not uids or ctx.active_version_id is None:
        return set()
    rows = c.execute("select activity_uid from activity_progress_as_of(%s, current_date) where activity_uid = any(%s) and physical_pct >= 100", (ctx.active_version_id, uids)).fetchall()
    return {r["activity_uid"] for r in rows}


def one_claim(c, ctx: Ctx, claim_id) -> Dict[str, Any]:
    rows = load_claims(c, ctx, "e.event_id = %(id)s", {"id": claim_id}, limit=1)
    if not rows:
        raise ApiError(404, "RESOURCE_NOT_FOUND", f"Execution claim '{claim_id}' not found.")
    return rows[0]


def _uuid(raw: str, what: str = "claim") -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        raise ApiError(404, "RESOURCE_NOT_FOUND", f"Execution {what} '{raw}' not found.")


def _need_member(ctx: Ctx) -> None:
    if ctx.access.role not in (SE, SUP):
        raise forbidden("Project managers see aggregate claim counts only, not claim content", "CLAIM_CONTENT_FORBIDDEN")


# ----------------------------------------------------------------------------------------------------------------------- candidates
def candidates_for(c, ctx: Ctx, claim_id) -> List[Dict[str, Any]]:
    rows = c.execute(
        "select cm.*, ba.external_activity_id as ext, ba.activity_name, sw.wbs_code, e.filed_in_version_id as vid, "
        "(select st.wbs_name from schedule_wbs st where st.version_id = ba.version_id and st.node_type = 'STAGE' and sw.wbs_path like st.wbs_path || '%%' order by length(st.wbs_path) desc limit 1) as stage_name, "
        "(select p.physical_pct from activity_progress_as_of(ba.version_id, current_date) p where p.activity_uid = ba.activity_uid) as pct "
        "from candidate_matches cm join execution_events e on e.event_id = cm.event_id "
        "join baseline_activities ba on ba.activity_uid = cm.activity_uid and ba.version_id = e.filed_in_version_id "
        "join schedule_wbs sw on sw.wbs_id = ba.wbs_id where cm.project_id = %s and cm.event_id = %s order by cm.rank_order", (ctx.project_id, claim_id)).fetchall()
    out = []
    for r in rows:
        pct = float(r["pct"]) if r["pct"] is not None else 0.0
        state = "COMPLETED" if pct >= 100 else ("IN_PROGRESS" if pct > 0 else "NOT_STARTED")
        out.append({"candidate_id": str(r["candidate_id"]), "event_id": str(r["event_id"]), "schedule_id": str(r["vid"]), "project_id": str(ctx.project_id), "activity_id": r["ext"],
                    "activity_name": r["activity_name"], "rank_order": r["rank_order"], "match_tier": r["match_tier"], "composite_confidence": float(r["composite_confidence"]),
                    "semantic_score": shapes.num(r["semantic_score"]), "fuzzy_score": shapes.num(r["fuzzy_score"]), "location_score": shapes.num(r["location_score"]),
                    "discipline_score": shapes.num(r["discipline_score"]), "supporting_signals": r["supporting_signals"], "disqualifying_signals": r["disqualifying_signals"],
                    "execution_state": state, "stage_name": r["stage_name"], "wbs_code": r["wbs_code"], "is_eligible": state != "COMPLETED",
                    "is_completed_protected": state == "COMPLETED", "is_stage_completed": False})
    return out


# ----------------------------------------------------------------------------------------------------------------------- extraction -> v2 claim
def run_extraction(text: str):
    from backend.shared.llm_extraction import LLMExtractionError, extract_claim_fields
    try:
        return extract_claim_fields(text)
    except LLMExtractionError as e:
        raise ApiError(502, "EXTRACTION_FAILED", f"Claim extraction failed: {e}. The claim was not created — please retry.")


def _fields(ex) -> Dict[str, Any]:
    ev = shapes.EVENT_IN.get(ex.event_type.value) if ex.event_type else None
    return dict(discipline_code=ex.discipline.value if ex.discipline else None, event_type=ev, delay_reason=ex.delay_reason.value if ex.delay_reason else None,
                language_detected=ex.language_detected, reported_activity_ref=ex.reported_activity_id, location=ex.location, asset_tag=ex.asset_tag)


def _quantities(ex) -> List[dict]:
    if ex.claimed_quantity is None or not (ex.claimed_uom or "").strip():
        return []
    basis = "INCREMENTAL" if ex.claim_mode.value == "INCREMENTAL_QUANTITY" else "CUMULATIVE"
    return [{"qty": Decimal(str(ex.claimed_quantity)), "uom": ex.claimed_uom.strip(), "basis": basis}]


def _provenance(ex, *, schedule: bool = False) -> Dict[str, str]:
    from backend.routers import intake as legacy_intake
    return legacy_intake._build_schedule_provenance(ex) if schedule else legacy_intake._build_ai_provenance(ex)


def submit_extracted(ctx: Ctx, ex, raw_text: str, *, channel: str, document_id=None, batch_id=None, evidence_ids=None, schedule: bool = False,
                     source_snippet: Optional[str] = None, sheet: Optional[str] = None, cell_ref: Optional[str] = None, ask: bool = True) -> Dict[str, Any]:
    """one extracted claim -> one v2 claim (the Field Copilot may hold it for a clarification first). Returns the v2 result."""
    from backend.shared.llm_extraction import check_missing_required_fields, generate_clarification_question
    missing = check_missing_required_fields(ex) if ask else []
    question = generate_clarification_question(raw_text, missing, ex.language_detected) if missing else None
    f = _fields(ex)
    today = date.today()
    ev_date = min(ex.event_date or today, today)
    et = ex.event_type.value if ex.event_type else None
    pct = Decimal(str(ex.claimed_pct)) if ex.claimed_pct is not None else None
    res = dc.submit_claim(
        ctx.actor, event_date=ev_date, raw_text=raw_text, input_channel=channel, quantities=_quantities(ex), claimed_pct=pct,
        claimed_start=ev_date if et == "ACTUAL_START" else None, claimed_finish=ev_date if et == "ACTUAL_FINISH" else None,
        evidence_document_ids=list(evidence_ids or []), document_id=document_id, batch_id=batch_id, field_provenance=_provenance(ex, schedule=schedule),
        incomplete_ok=bool(question), clarification_question=question, **f)
    if source_snippet is not None and document_id is not None:
        with actor_tx(ctx.actor, write=True) as c:
            c.execute("insert into source_references (project_id, event_id, document_id, sheet_name, row_cell_ref, raw_snippet) values (%s,%s,%s,%s,%s,%s)",
                      (ctx.project_id, res["claim_id"], document_id, sheet, cell_ref, source_snippet[:4000]))
    return res


def _legacy_one(ctx: Ctx, claim_id) -> Dict[str, Any]:
    with actor_tx(ctx.actor, readonly=True) as c:
        return one_claim(c, ctx, claim_id)


# ----------------------------------------------------------------------------------------------------------------------- intake endpoints
class TextClaimBody(BaseModel):
    raw_claim_text: str
    input_channel: str = "TYPED_TEXT"
    schedule_id: Optional[str] = None
    evidence_filename: Optional[str] = None
    evidence_base64: Optional[str] = None


@router.post("/claims/text")
def create_text_claim(body: TextClaimBody, ctx: Ctx = Depends(legacy_ctx(P.SUBMIT_CLAIM, writable=True))):
    """Typed text or voice transcript (the browser's speech recognition already produced the text). One report = one claim."""
    require_version(ctx)
    text = (body.raw_claim_text or "").strip()
    if len(text) < 3:
        raise ApiError(422, "CLAIM_TEXT_REQUIRED", "A claim needs the report text it came from")
    channel = shapes.CHANNEL_IN.get(body.input_channel, "TYPED")
    ex = run_extraction(text)
    evidence = []
    if body.evidence_base64:
        try:
            raw = base64.b64decode(body.evidence_base64)
            evidence.append(docsvc.upload_document(ctx.user, ctx.project_id, ctx.access.role, "EVIDENCE", Path(body.evidence_filename or "evidence.bin").name, raw)["document_id"])
        except ApiError as e:
            if e.code == "DUPLICATE_UPLOAD" and e.details:
                evidence.append(uuid.UUID(e.details["document_id"]))
            else:
                raise
    res = submit_extracted(ctx, ex, text, channel=channel, evidence_ids=evidence)
    return _legacy_one(ctx, res["claim_id"])


@router.post("/claims/file")
def create_file_claim(file: UploadFile = File(...), purpose: str = Form("EVIDENCE_PHOTO"), raw_claim_text: Optional[str] = Form(None), schedule_id: Optional[str] = Form(None),
                      ctx: Ctx = Depends(legacy_ctx(P.SUBMIT_CLAIM, writable=True))):
    """File intake: the ORIGINAL strategies (structured spreadsheet / XER rows without an LLM; PDF, text, scans and photographs through the batch extractor). One file may
    carry several claims. An evidence photo with typed text is evidence for that one claim."""
    require_version(ctx)
    from backend.routers import intake as li
    contents = file.file.read()
    if not contents:
        raise ApiError(422, "EMPTY_FILE", "Uploaded file is empty.")
    name = Path(file.filename or "upload").name
    ext = Path(name).suffix.lower()
    attach_only = purpose == "EVIDENCE_PHOTO" and bool(raw_claim_text)
    drafts, channel_v2, kind = [], "TXT", "DAILY_REPORT"
    if attach_only:
        drafts = [li.ClaimDraft(raw_text=raw_claim_text.strip(), extracted=run_extraction(raw_claim_text.strip()))]
        channel_v2, kind = "TYPED", "EVIDENCE"
    else:
        try:
            drafts, _doc_type, legacy_channel = li._build_claim_drafts(name, contents, raw_claim_text_fallback=raw_claim_text)
        except li.UnsupportedFileError as e:
            raise ApiError(415, "UNSUPPORTED_FILE", str(e))
        except li.FileParseError as e:
            raise ApiError(422, "FILE_PARSE_ERROR", str(e))
        except li.LLMExtractionError as e:
            raise ApiError(502, "EXTRACTION_FAILED", f"Claim extraction failed: {e}. No claims were created — please retry.")
        channel_v2 = {"SCANNED_OCR": "IMAGE", "SCHEDULE_EXPORT": "API"}.get(legacy_channel.value) or {".txt": "TXT", ".pdf": "PDF", ".csv": "CSV", ".xlsx": "XLSX", ".xls": "XLSX"}.get(ext, "TXT")
        kind = "SITE_REPORT" if ext in (".jpg", ".jpeg", ".png") else "DAILY_REPORT"
    try:
        doc = docsvc.upload_document(ctx.user, ctx.project_id, ctx.access.role, kind if not attach_only else ("PHOTO" if ext in (".jpg", ".jpeg", ".png") else "EVIDENCE"), name, contents)
        doc_id = doc["document_id"]
    except ApiError as e:
        if e.code == "DUPLICATE_UPLOAD" and e.details:
            doc_id = uuid.UUID(e.details["document_id"])
        else:
            raise
    out = []
    for i, d in enumerate(drafts, 1):
        try:
            res = submit_extracted(ctx, d.extracted, d.raw_text, channel=channel_v2, document_id=None if attach_only else doc_id, evidence_ids=[doc_id],
                                   schedule=(channel_v2 == "API"), source_snippet=d.raw_text, cell_ref=f"item {i}")
            out.append(res["claim_id"])
        except ApiError as e:
            if e.code != "DUPLICATE_CLAIM":
                raise
            logger.info("duplicate claim skipped while ingesting %s", name)
    if not out:
        raise ApiError(409, "DUPLICATE_CLAIM", "Every claim in this file already exists")
    with actor_tx(ctx.actor, readonly=True) as c:
        return load_claims(c, ctx, "e.event_id = any(%(ids)s)", {"ids": out}, order="e.created_at asc")


@router.post("/claims/schedule-export")
def create_schedule_export_claims(file: UploadFile = File(...), schedule_id: Optional[str] = Form(None), ctx: Ctx = Depends(legacy_ctx(P.SUBMIT_CLAIM, writable=True))):
    """P6 / MS Project progress export used as a SOURCE OF PROGRESS CLAIMS (not a new baseline): every row names its activity, so no LLM is involved."""
    require_version(ctx)
    from backend.shared.tabular_extraction import build_claim_from_row, detect_progress_columns, read_tabular_file
    contents = file.file.read()
    if not contents:
        raise ApiError(422, "EMPTY_FILE", "Uploaded file is empty.")
    name = Path(file.filename or "upload").name
    try:
        sheets = read_tabular_file(name, contents)
    except ValueError as e:
        raise ApiError(422, "FILE_PARSE_ERROR", f"Could not parse schedule-export file: {e}")
    cols = {n: detect_progress_columns(list(df.columns)) for n, df in sheets.items()}
    if not any(v is not None for v in cols.values()):
        raise ApiError(422, "NO_ACTIVITY_COLUMN", "Schedule-export file has no 'Activity ID' column.")
    try:
        doc_id = docsvc.upload_document(ctx.user, ctx.project_id, ctx.access.role, "DAILY_REPORT", name, contents)["document_id"]
    except ApiError as e:
        if e.code == "DUPLICATE_UPLOAD" and e.details:
            doc_id = uuid.UUID(e.details["document_id"])
        else:
            raise
    out = []
    for sheet, df in sheets.items():
        if cols[sheet] is None:
            continue
        for idx, row in df.iterrows():
            built = build_claim_from_row(row.to_dict(), cols[sheet], discipline_hint=sheet or None)
            if built is None:
                continue
            raw_text, ex = built
            try:
                res = submit_extracted(ctx, ex, raw_text, channel="API", document_id=doc_id, evidence_ids=[doc_id], schedule=True, ask=False,
                                       source_snippet=str(row.to_dict()), sheet=sheet or None, cell_ref=f"row {int(idx) + 2}")
                out.append(res["claim_id"])
            except ApiError as e:
                if e.code != "DUPLICATE_CLAIM":
                    raise
    if not out:
        raise ApiError(422, "NO_PROGRESS_ROWS", "The file has no rows with reportable progress (or every claim already exists).")
    with actor_tx(ctx.actor, readonly=True) as c:
        return load_claims(c, ctx, "e.event_id = any(%(ids)s)", {"ids": out}, order="e.created_at asc")


# ----------------------------------------------------------------------------------------------------------------------- clarification / match / check
class ClarifyBody(BaseModel):
    answer: str


@router.post("/claims/{event_id}/clarify")
def clarify_claim(event_id: str, body: ClarifyBody, ctx: Ctx = Depends(legacy_ctx(P.SUBMIT_CLAIM, writable=True))):
    """Field Copilot: the engineer answers the question about their incomplete report. The answer is merged with the original text, the ORIGINAL extraction runs again,
    and the gaps are filled (the filed text and date themselves stay as filed)."""
    cid = _uuid(event_id)
    with actor_tx(ctx.actor, readonly=True) as c:
        claim = dc._claim_row(c, ctx.project_id, cid)
        dc._own_or_404(ctx.actor, claim)
    ans = (body.answer or "").strip()
    if not ans:
        raise ApiError(422, "ANSWER_REQUIRED", "Please provide a clarification answer.")
    ex = run_extraction(f"{claim['raw_claim_text']}\nClarification: {ans}")
    f = _fields(ex)
    et = ex.event_type.value if ex.event_type else None
    ev_date = min(ex.event_date or claim["event_date"], date.today())
    dc.complete_reported_claim(
        ctx.actor, cid, answer=ans, quantities=_quantities(ex), claimed_pct=Decimal(str(ex.claimed_pct)) if ex.claimed_pct is not None else None,
        claimed_start=claim["event_date"] if et == "ACTUAL_START" else None, claimed_finish=claim["event_date"] if et == "ACTUAL_FINISH" else None,
        field_provenance=_provenance(ex), **{k: v for k, v in f.items()})
    return _legacy_one(ctx, cid)


def _status_after_check(c, ctx: Ctx, claim_id) -> str:
    row = c.execute("select status from execution_events where event_id = %s", (claim_id,)).fetchone()
    return row["status"]


@router.post("/claims/{event_id}/match")
def match_claim_endpoint(event_id: str, ctx: Ctx = Depends(legacy_ctx(None, writable=True))):
    """Run the (unchanged) matching engine for the claim against the project's ACTIVE schedule. Never approves anything; an activity chosen by a person is never replaced."""
    _need_member(ctx)
    cid = _uuid(event_id)
    with actor_tx(ctx.actor, write=True) as c:
        claim = dc._claim_row(c, ctx.project_id, cid, lock=True)
        if ctx.access.role == SE:
            dc._own_or_404(ctx.actor, claim)
        if claim["status"] == "REPORTED":
            raise ApiError(400, "CLARIFICATION_PENDING", "Cannot match claim while clarification is pending. Engineer must submit clarification first.")
        ver = active_version(c, ctx.project_id)
        prov = (claim.get("field_provenance") or {}).get("activity")
        res: Dict[str, Any]
        if claim["status"] in ("EXTRACTED", "MATCHED"):
            res = matching.auto_match(c, ctx.actor, cid, ver, overwrite_pick=prov in (None, "SCHEDULE_AUTO_FILLED"))
        else:
            res = {"skipped": "NOT_MATCHABLE", "matched": claim["matched_activity_uid"] is not None}
        cands = candidates_for(c, ctx, cid)
        legacy = one_claim(c, ctx, cid)
    top = cands[0] if cands else None
    out = {"event_id": str(cid), "matched_activity_id": legacy["matched_activity_id"], "match_tier": top["match_tier"] if top else None,
           "composite_confidence": top["composite_confidence"] if top else 0.0, "candidates": cands, "status": legacy["status"], "claim_scope": "SPECIFIC",
           "scope_reason": "A claim is decided against a single activity; a broad report is assigned to its child activity by the Supervisor",
           "wbs_group": None, "splits": [], "allocation_status": "SKIPPED", "allocation_reason": "Claim scope is not BROAD_WBS", "excluded_siblings": []}
    if res.get("reason"):
        out["unmatched_reason"] = res["reason"]
    return out


@router.get("/claims/{event_id}/candidates")
def get_candidates(event_id: str, ctx: Ctx = Depends(legacy_ctx())):
    _need_member(ctx)
    cid = _uuid(event_id)
    with actor_tx(ctx.actor, readonly=True) as c:
        one_claim(c, ctx, cid)                                   # visibility: own claim (engineer) / project claim (supervisor)
        return {"event_id": str(cid), "candidates": candidates_for(c, ctx, cid)}


# ---- the full legacy check engine (validation rules, conflict records, smart priority) lives in checks_api.py
from .checks_api import run_check  # noqa: E402


@router.post("/claims/{event_id}/check")
def check_claim(event_id: str, ctx: Ctx = Depends(legacy_ctx(None, writable=True))):
    _need_member(ctx)
    return run_check(ctx, _uuid(event_id))


@router.get("/claims/{event_id}/conflicts")
def get_conflicts(event_id: str, ctx: Ctx = Depends(legacy_ctx(P.REVIEW_CLAIMS))):
    cid = _uuid(event_id)
    with actor_tx(ctx.actor, readonly=True) as c:
        one_claim(c, ctx, cid)
        rows = c.execute("select cr.*, a.external_activity_id as ext from conflict_records cr left join baseline_activities a on a.activity_uid = cr.activity_uid "
                         "and a.version_id = (select filed_in_version_id from execution_events where event_id = %s) where cr.project_id = %s and (cr.event_id_a = %s or cr.event_id_b = %s) "
                         "order by cr.reporting_period desc", (cid, ctx.project_id, cid, cid)).fetchall()
    return {"event_id": str(cid), "conflicts": [{"conflict_id": str(r["conflict_id"]), "schedule_id": str(ctx.version_id), "activity_id": r["ext"], "reporting_period": shapes.iso(r["reporting_period"]),
                                                 "event_id_a": str(r["event_id_a"]), "event_id_b": str(r["event_id_b"]), "value_a": shapes.num(r["value_a"]), "value_b": shapes.num(r["value_b"]),
                                                 "variance_pct": shapes.num(r["variance_pct"]), "status": r.get("status") or "OPEN"} for r in rows]}


@router.get("/claims/{event_id}/validation")
def get_validation(event_id: str, ctx: Ctx = Depends(legacy_ctx(P.REVIEW_CLAIMS))):
    cid = _uuid(event_id)
    with actor_tx(ctx.actor, readonly=True) as c:
        one_claim(c, ctx, cid)
        rows = c.execute("select validation_id, rule_code, severity, description from claim_validations where event_id = %s order by severity, rule_code", (cid,)).fetchall()
    return {"event_id": str(cid), "validation_issues": [{"issue_id": str(r["validation_id"]), "event_id": str(cid), "rule_code": r["rule_code"],
                                                         "severity": r["severity"] if r["severity"] in ("WARNING", "ERROR") else "WARNING", "description": r["description"]} for r in rows]}


@router.get("/claims/{event_id}/splits")
def get_splits(event_id: str, ctx: Ctx = Depends(legacy_ctx())):
    """v2 decides a claim against ONE activity; there are no automatic WBS splits (owner decision), so this is always empty."""
    _need_member(ctx)
    cid = _uuid(event_id)
    with actor_tx(ctx.actor, readonly=True) as c:
        one_claim(c, ctx, cid)
    return {"event_id": str(cid), "splits": [], "split_count": 0, "total_split_pct": 0}


@router.get("/claims/{event_id}/photo")
def get_photo(event_id: str, ctx: Ctx = Depends(legacy_ctx())):
    _need_member(ctx)
    cid = _uuid(event_id)
    with actor_tx(ctx.actor, readonly=True) as c:
        one_claim(c, ctx, cid)
        d = c.execute("select d.document_id from source_documents d where d.mime_type like 'image/%%' and (d.document_id = (select document_id from execution_events where event_id = %s) "
                      "or d.document_id in (select document_id from claim_evidence where event_id = %s)) order by d.uploaded_at limit 1", (cid, cid)).fetchone()
    if d is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "This claim has no photograph")
    data, mime, fname = docsvc.read_content(ctx.actor, d["document_id"])
    return Response(content=data, media_type=mime)


@router.get("/claims/{event_id}")
def get_claim(event_id: str, ctx: Ctx = Depends(legacy_ctx())):
    _need_member(ctx)
    return _legacy_one(ctx, _uuid(event_id))


@router.get("/claims")
def list_claims(status: Optional[str] = None, ctx: Ctx = Depends(legacy_ctx())):
    _need_member(ctx)
    with actor_tx(ctx.actor, readonly=True) as c:
        rows = load_claims(c, ctx)
    return [r for r in rows if not status or r["status"] == status]
