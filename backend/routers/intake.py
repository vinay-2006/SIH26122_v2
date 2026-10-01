"""
Owner: M2. Others: read-only. If you need a new field returned from this
router, ask M2 — don't edit this file directly.

V7 context (this revision): every route requires an EXPLICIT project (X-Project-ID) validated against
  active membership, and an EXPLICIT schedule that belongs to that project. There is no active/latest
  schedule fallback. Each execution_event is stamped with project_id (and stage/contractor/work-package
  attribution when the reported activity is known). Roles are the project roles (RBAC permissions), not the
  V6 profile role.

Changes in this version, per PRD v5:
  - uploader_id/supervisor_id are no longer accepted from the request body
    (PRD v5 6.2: "User identity must never be accepted as a free-form
    request-body field"). They're derived from the authenticated user.
  - Postgres via psycopg2 instead of SQLite.

File-intake architecture (this revision):
  A single uploaded file commonly reports progress on SEVERAL distinct
  schedule activities at once -- a daily PDF report with one row per
  discipline, a multi-sheet discipline spreadsheet, a P6 .xer export, a
  photographed site diary listing several disciplines. Collapsing a whole
  file into one LLM call bound for one execution_event silently discards
  every activity but one. POST /claims/file therefore extracts a LIST of
  claim drafts per file (see ClaimDraft/_build_claim_drafts) and creates one
  execution_event per draft -- same pattern /claims/schedule-export already
  used for structured CSV/XLSX exports, now shared via
  shared/tabular_extraction.py and extended to PDF/TXT/image content via
  shared/llm_extraction.py's batch (array) extraction contract, and to .xer
  via shared/xer_parser.py.

  Two claim-construction strategies are used, chosen per file shape:
    - STRUCTURED (no LLM): CSV/XLSX with a recognizable Activity ID column,
      or a .xer file's TASK table. The file's own columns/fields are ground
      truth -- deterministic, no LLM cost/latency, no risk of the model
      mis-reading a value that's already unambiguous.
    - LLM-EXTRACTED (batch): PDF text, plain text, OCR'd/vision-read image
      content, or a CSV/XLSX sheet with no recognizable activity-id column.
      The model returns a JSON array of claims (see
      llm_extraction.BATCH_SYSTEM_PROMPT) instead of a single object.

  Error handling: a genuine extraction failure (corrupt/unreadable file,
  unsupported format, LLM/vision/OCR failure, missing LLM config) raises and
  the endpoint returns a 415/422/502 with a clear reason -- it never falls
  back to silently creating a claim with all-null fields. An all-null/sparse
  claim is only ever created when extraction ran successfully and the
  source genuinely had little to say (a real "insufficient information"
  outcome, not a masked failure).
"""
import hashlib
import io
import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from backend.context import gates
from backend.context.event import EventContext
from backend.context.project import ProjectContext
from backend.context.schedule import ScheduleContext, resolve_explicit_schedule
from backend.shared.db import get_db
from backend.shared.llm_extraction import (
    LLMExtractionError,
    check_missing_required_fields,
    extract_claim_fields,
    extract_claim_fields_batch,
    extract_claim_fields_from_image_batch,
    generate_clarification_question,
)
from backend.shared.schemas import (
    ClaimResponse,
    ClarificationStatus,
    ClarifyClaimRequest,
    ExtractedClaimFields,
    InputChannel,
    ProvenanceTag,
    TextClaimRequest,
    UploadPurpose,
)
from backend.shared.tabular_extraction import (
    build_claim_from_row,
    detect_progress_columns,
    read_tabular_file,
)
from backend.shared.xer_parser import (
    XERParseError,
    activities_with_progress as xer_activities_with_progress,
    build_claim_from_activity as build_claim_from_xer_activity,
    extract_activities as extract_xer_activities,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["intake"])

# Where evidence photos land on disk (a SCANNED_DIARY-style image never gets
# saved here -- it's extracted and discarded, per the purpose contract
# below; only an EVIDENCE_PHOTO attached to an already-typed claim persists).
UPLOAD_DIR = Path(
    os.getenv("UPLOAD_DIR") or (Path(__file__).resolve().parents[1] / "uploads")
)


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
TABULAR_EXTENSIONS = {".csv", ".xlsx", ".xls"}
_IMAGE_MIME_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


class UnsupportedFileError(Exception):
    """Extension not recognized at all -> 415."""


class FileParseError(Exception):
    """Recognized extension, but content unreadable/corrupt/empty/has
    nothing extractable -> 422."""


class OCRUnavailableError(Exception):
    """pytesseract/tesseract isn't installed or failed to run -- distinct
    from "ran and found no text", so a missing OCR dependency is never
    silently indistinguishable from a blank scan."""


@dataclass
class ClaimDraft:
    raw_text: str
    extracted: ExtractedClaimFields


def _extract_pdf_text(contents: bytes) -> str:
    import pymupdf as fitz

    try:
        doc = fitz.open(stream=contents, filetype="pdf")
    except Exception as e:
        raise FileParseError(f"Could not open PDF file: {e}") from e
    try:
        text = "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()
    if not text.strip():
        raise FileParseError(
            "PDF contains no extractable text — likely a scanned/image-only "
            "PDF. Re-upload as a photo/scan (.jpg/.png) for OCR/vision "
            "extraction, or as a text-based PDF."
        )
    return text


def _run_ocr(contents: bytes) -> str:
    """
    Best-effort OCR fallback for images, used only when vision-LLM
    extraction is unavailable/fails (see _build_image_claim_drafts). Raises
    OCRUnavailableError when the tesseract binary itself isn't
    installed/working -- that must never be confused with "OCR ran and the
    scan was genuinely blank", which legitimately returns "".
    """
    from PIL import Image

    try:
        import pytesseract
    except ImportError as e:
        raise OCRUnavailableError(f"pytesseract is not installed: {e}") from e

    try:
        image = Image.open(io.BytesIO(contents))
    except Exception as e:
        raise FileParseError(f"Could not open image file: {e}") from e

    try:
        text = pytesseract.image_to_string(image)
    except Exception as e:
        raise OCRUnavailableError(
            f"OCR failed — tesseract binary not available or errored: {e}"
        ) from e
    return (text or "").strip()


def _batch_text_drafts(text: str) -> list[ClaimDraft]:
    claims = extract_claim_fields_batch(text)
    if not claims:
        raise FileParseError(
            "No extractable claim/progress information found in this file's text."
        )
    return [ClaimDraft(raw_text=text, extracted=c) for c in claims]


def _tabular_claim_drafts(filename: str, contents: bytes) -> list[ClaimDraft]:
    try:
        sheets = read_tabular_file(filename, contents)
    except ValueError as e:
        raise FileParseError(str(e)) from e

    drafts: list[ClaimDraft] = []
    unstructured_chunks: list[str] = []

    for sheet_name, df in sheets.items():
        cols = detect_progress_columns(list(df.columns))
        if cols is None:
            # No recognizable activity-id column on this sheet -- don't
            # drop it, fall back to LLM extraction on its flattened text
            # (still batched together with any other unstructured sheets
            # below, in one pass).
            label = f"Sheet: {sheet_name}\n" if sheet_name else ""
            unstructured_chunks.append(label + df.to_string(index=False))
            continue
        for _, row in df.iterrows():
            result = build_claim_from_row(row.to_dict(), cols, discipline_hint=sheet_name or None)
            if result is not None:
                raw_text, extracted = result
                drafts.append(ClaimDraft(raw_text=raw_text, extracted=extracted))

    if unstructured_chunks:
        combined = "\n\n".join(unstructured_chunks)
        try:
            drafts.extend(_batch_text_drafts(combined))
        except FileParseError:
            # Only raise if this leaves us with nothing at all -- a file
            # that's part-structured, part-narrative-with-nothing-useful
            # shouldn't lose its structured claims over the narrative half
            # coming up empty.
            if not drafts:
                raise

    if not drafts:
        raise FileParseError(
            "No extractable claim/progress information found in this file "
            "(no recognizable activity-id column, and no claim found in the "
            "remaining text)."
        )
    return drafts


def _xer_claim_drafts(contents: bytes) -> list[ClaimDraft]:
    try:
        activities = extract_xer_activities(contents)
    except XERParseError as e:
        raise FileParseError(str(e)) from e

    progressed = xer_activities_with_progress(activities)
    if not progressed:
        raise FileParseError(
            "XER file parsed successfully but contains no activities with "
            "reportable progress (all activities are not-yet-started)."
        )
    return [
        ClaimDraft(raw_text=raw_text, extracted=extracted)
        for raw_text, extracted in (build_claim_from_xer_activity(a) for a in progressed)
    ]


def _image_claim_drafts(contents: bytes, filename: str, raw_claim_text_fallback: Optional[str]) -> list[ClaimDraft]:
    """
    Image-as-claim extraction: try vision-LLM first (per project preference
    for reusing the existing LLM architecture over a second OCR-only
    service), fall back to pytesseract OCR + the same batch text extractor
    used by PDF/TXT if vision is unavailable/fails, and only fall back to a
    caller-supplied raw_claim_text as a last resort. Raises if every path
    fails -- never silently returns a null-filled claim.
    """
    ext = Path(filename or "").suffix.lower()
    mime_type = _IMAGE_MIME_TYPES.get(ext, "image/png")

    vision_error: Optional[str] = None
    try:
        claims = extract_claim_fields_from_image_batch(contents, mime_type=mime_type)
        if claims:
            return [ClaimDraft(raw_text=f"[Vision-extracted from {filename}]", extracted=c) for c in claims]
        vision_error = "vision model found no claims in the image"
    except LLMExtractionError as e:
        vision_error = str(e)
        logger.warning("Vision extraction failed for %s, falling back to OCR: %s", filename, e)

    ocr_error: Optional[str] = None
    try:
        ocr_text = _run_ocr(contents)
        if ocr_text:
            return _batch_text_drafts(ocr_text)
        ocr_error = "OCR produced no text"
    except OCRUnavailableError as e:
        ocr_error = str(e)
    except FileParseError as e:
        ocr_error = str(e)

    if raw_claim_text_fallback:
        return _batch_text_drafts(raw_claim_text_fallback)

    raise FileParseError(
        f"Could not extract claim information from image '{filename}': "
        f"vision extraction failed ({vision_error}); OCR fallback also "
        f"failed ({ocr_error}). Provide raw_claim_text as a fallback, or "
        "configure LLM_PROVIDER=gemini / LLM_VISION_MODEL for image support."
    )


def _build_claim_drafts(
    filename: str, contents: bytes, raw_claim_text_fallback: Optional[str] = None
) -> tuple[list[ClaimDraft], str, InputChannel]:
    """
    Dispatch a non-EVIDENCE_PHOTO file upload to the right extraction
    strategy by extension. Returns (drafts, document_type, input_channel).
    Raises UnsupportedFileError / FileParseError / LLMExtractionError.
    """
    ext = Path(filename or "").suffix.lower()

    if ext in IMAGE_EXTENSIONS:
        return _image_claim_drafts(contents, filename, raw_claim_text_fallback), "SCANNED_DIARY", InputChannel.SCANNED_OCR

    if ext == ".txt":
        text = contents.decode("utf-8", errors="replace")
        if not text.strip():
            raise FileParseError("Text file is empty.")
        return _batch_text_drafts(text), "DPR", InputChannel.FILE_UPLOAD

    if ext == ".pdf":
        text = _extract_pdf_text(contents)
        return _batch_text_drafts(text), "DPR", InputChannel.FILE_UPLOAD

    if ext in TABULAR_EXTENSIONS:
        return _tabular_claim_drafts(filename, contents), "DPR", InputChannel.FILE_UPLOAD

    if ext == ".xer":
        # XER rows already carry a real activity_id, same as a P6/MSP
        # schedule-export CSV -- SCHEDULE_EXPORT is the correct channel
        # label for "structured, pre-identified progress source", not a
        # generic FILE_UPLOAD.
        return _xer_claim_drafts(contents), "SCHEDULE_EXPORT_PROGRESS", InputChannel.SCHEDULE_EXPORT

    raise UnsupportedFileError(
        f"Unsupported file type '{ext}'. Accepted: .pdf, .xlsx, .xls, .csv, "
        ".txt, .xer, .jpg, .jpeg, .png"
    )


@router.get("/intake/health")
def health():
    return {"status": "ok"}


# Registered before GET /claims/{event_id} below -- Starlette matches routes
# in registration order, so this literal path must be added first or every
# request to it would instead match the dynamic route with event_id="health".
@router.get("/claims/health")
def claims_health():
    return {"router": "intake", "status": "ok"}


def _derive_attribution(cur, project_id, schedule_id: str, activity_id: Optional[str]) -> dict:
    """Stage / contractor / work-package of the reported activity, derived deterministically from the
    schedule (never from the claim text). Empty when the activity is unknown or not reported yet."""
    if not activity_id:
        return {}
    cur.execute(
        "SELECT stage_id, contractor_id, work_package_id FROM schedule_activities "
        "WHERE schedule_id = %s AND activity_id = %s AND project_id = %s",
        (schedule_id, activity_id, str(project_id)),
    )
    row = cur.fetchone()
    return dict(row) if row else {}


def _build_ai_provenance(extracted: ExtractedClaimFields) -> dict[str, str]:
    """Feature #33: Tag populated extracted fields with AI_EXTRACTED."""
    prov: dict[str, str] = {}
    if extracted.event_date is not None:
        prov["event_date"] = ProvenanceTag.AI_EXTRACTED.value
    if extracted.reported_activity_id is not None:
        prov["reported_activity_id"] = ProvenanceTag.AI_EXTRACTED.value
    if extracted.discipline is not None:
        prov["discipline"] = ProvenanceTag.AI_EXTRACTED.value
    if extracted.action is not None:
        prov["action"] = ProvenanceTag.AI_EXTRACTED.value
    if extracted.event_type is not None:
        prov["event_type"] = ProvenanceTag.AI_EXTRACTED.value
    if extracted.claim_mode is not None:
        prov["claim_mode"] = ProvenanceTag.AI_EXTRACTED.value
    if extracted.asset_tag is not None:
        prov["asset_tag"] = ProvenanceTag.AI_EXTRACTED.value
    if extracted.location is not None:
        prov["location"] = ProvenanceTag.AI_EXTRACTED.value
    if extracted.claimed_quantity is not None:
        prov["claimed_quantity"] = ProvenanceTag.AI_EXTRACTED.value
    if extracted.claimed_uom is not None:
        prov["claimed_uom"] = ProvenanceTag.AI_EXTRACTED.value
    if extracted.claimed_pct is not None:
        prov["claimed_pct"] = ProvenanceTag.AI_EXTRACTED.value
    if extracted.delay_reason is not None:
        prov["delay_reason"] = ProvenanceTag.AI_EXTRACTED.value
    if extracted.language_detected is not None:
        prov["language_detected"] = ProvenanceTag.AI_EXTRACTED.value
    return prov


def _build_schedule_provenance(extracted: ExtractedClaimFields) -> dict[str, str]:
    """Feature #33: Tag populated direct schedule fields with SCHEDULE_AUTO_FILLED."""
    prov: dict[str, str] = {}
    if extracted.event_date is not None:
        prov["event_date"] = ProvenanceTag.SCHEDULE_AUTO_FILLED.value
    if extracted.reported_activity_id is not None:
        prov["reported_activity_id"] = ProvenanceTag.SCHEDULE_AUTO_FILLED.value
    if extracted.discipline is not None:
        prov["discipline"] = ProvenanceTag.SCHEDULE_AUTO_FILLED.value
    if extracted.action is not None:
        prov["action"] = ProvenanceTag.SCHEDULE_AUTO_FILLED.value
    if extracted.event_type is not None:
        prov["event_type"] = ProvenanceTag.SCHEDULE_AUTO_FILLED.value
    if extracted.claim_mode is not None:
        prov["claim_mode"] = ProvenanceTag.SCHEDULE_AUTO_FILLED.value
    if extracted.asset_tag is not None:
        prov["asset_tag"] = ProvenanceTag.SCHEDULE_AUTO_FILLED.value
    if extracted.location is not None:
        prov["location"] = ProvenanceTag.SCHEDULE_AUTO_FILLED.value
    if extracted.claimed_quantity is not None:
        prov["claimed_quantity"] = ProvenanceTag.SCHEDULE_AUTO_FILLED.value
    if extracted.claimed_uom is not None:
        prov["claimed_uom"] = ProvenanceTag.SCHEDULE_AUTO_FILLED.value
    if extracted.claimed_pct is not None:
        prov["claimed_pct"] = ProvenanceTag.SCHEDULE_AUTO_FILLED.value
    return prov


def _row_to_claim_response(row) -> ClaimResponse:
    raw_prov = row.get("field_provenance") if isinstance(row, dict) else (row["field_provenance"] if "field_provenance" in row else None)
    if isinstance(raw_prov, str):
        try:
            field_provenance = json.loads(raw_prov)
        except Exception:
            field_provenance = {}
    elif isinstance(raw_prov, dict):
        field_provenance = raw_prov
    else:
        field_provenance = {}

    return ClaimResponse(
        event_id=row["event_id"],
        document_id=row["document_id"],
        schedule_id=row["schedule_id"],
        event_date=row["event_date"],
        raw_claim_text=row["raw_claim_text"],
        input_channel=row["input_channel"],
        language_detected=row.get("language_detected"),
        reported_activity_id=row.get("reported_activity_id"),
        matched_activity_id=row.get("matched_activity_id"),
        discipline=row.get("discipline"),
        action=row.get("action"),
        event_type=row.get("event_type"),
        claim_mode=row.get("claim_mode", "CUMULATIVE_PCT"),
        asset_tag=row.get("asset_tag"),
        location=row.get("location"),
        claimed_quantity=row.get("claimed_quantity"),
        claimed_uom=row.get("claimed_uom"),
        claimed_pct=row.get("claimed_pct"),
        delay_reason=row.get("delay_reason"),
        supervisor_id=str(row["supervisor_id"]) if row.get("supervisor_id") else None,
        photo_path=row.get("photo_path"),
        status=row.get("status", "EXTRACTED"),
        clarification_status=row.get("clarification_status") or ClarificationStatus.NONE.value,
        clarification_question=row.get("clarification_question"),
        clarification_answer=row.get("clarification_answer"),
        field_provenance=field_provenance,
        priority_score=row.get("priority_score"),
        priority_reasons=row.get("priority_reasons"),
        created_at=row["created_at"],
    )


def _insert_execution_event(
    cur,
    *,
    event_id: str,
    document_id: Optional[str] = None,
    schedule_id: str,
    event_date_val: date,
    raw_claim_text: str,
    input_channel: str,
    language_detected: Optional[str],
    reported_activity_id: Optional[str],
    discipline: Optional[str],
    action: Optional[str],
    event_type: Optional[str],
    claim_mode: str,
    asset_tag: Optional[str],
    location: Optional[str],
    claimed_quantity: Optional[float],
    claimed_uom: Optional[str],
    claimed_pct: Optional[float],
    delay_reason: Optional[str],
    supervisor_id: str,
    photo_path: Optional[str] = None,
    clarification_status: str = "NONE",
    clarification_question: Optional[str] = None,
    clarification_answer: Optional[str] = None,
    field_provenance: Optional[dict[str, str]] = None,
    project_id=None,
    stage_id=None,
    contractor_id=None,
    work_package_id=None,
) -> None:
    """
    Shared INSERT for every intake path (text, file, schedule-export) --
    factored out so a file that yields N claims doesn't need N copies of
    this statement, and so every path stays in sync on the same column list.
    """
    cur.execute(
        """INSERT INTO execution_events (
            event_id, document_id, schedule_id, event_date, raw_claim_text,
            input_channel, language_detected, reported_activity_id, discipline,
            action, event_type, claim_mode, asset_tag, location,
            claimed_quantity, claimed_uom, claimed_pct, delay_reason,
            supervisor_id, photo_path, status, clarification_status,
            clarification_question, clarification_answer, field_provenance,
            project_id, stage_id, contractor_id, work_package_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'EXTRACTED', %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            event_id, document_id, schedule_id, event_date_val, raw_claim_text,
            input_channel, language_detected, reported_activity_id, discipline,
            action, event_type, claim_mode, asset_tag, location,
            claimed_quantity, claimed_uom, claimed_pct, delay_reason,
            supervisor_id, photo_path, clarification_status, clarification_question,
            clarification_answer, json.dumps(field_provenance or {}),
            project_id, stage_id, contractor_id, work_package_id,
        ),
    )


def _insert_source_reference(cur, *, event_id: str, file_name: Optional[str], raw_snippet: str) -> None:
    cur.execute(
        """INSERT INTO source_references (reference_id, event_id, file_name, sheet_name, row_cell_ref, message_id, raw_snippet)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (str(uuid.uuid4()), event_id, file_name, None, None, None, raw_snippet[:2000]),
    )


@router.post("/claims/text", response_model=ClaimResponse)
def create_text_claim(
    payload: TextClaimRequest,
    request: Request,
    conn=Depends(get_db),
    project_context: ProjectContext = Depends(gates.project_events_create),
):
    """
    Handles both TYPED_TEXT and VOICE (voice already arrives as text from the
    browser's Web Speech API — no audio processing happens here).
    """
    current_user = project_context.user
    schedule_scope = resolve_explicit_schedule(
        project_context, payload.schedule_id, request.headers.get("X-Schedule-ID"), request.query_params.get("schedule_id")
    )
    schedule_id = schedule_scope.schedule_id

    # 1. Hash the raw text itself (no file exists for typed/voice claims) and
    #    log it as a source_document for audit-trail consistency with
    #    file-based claims. file_name is NOT NULL in the schema, so use the
    #    input_channel as a synthetic placeholder for text-based claims
    #    rather than violating the constraint or loosening it unilaterally.
    photo_path: Optional[str] = None
    ref_file_name: Optional[str] = None
    document_id: Optional[str] = None

    if payload.evidence_base64:
        try:
            import base64
            evidence_bytes = base64.b64decode(payload.evidence_base64)
            text_hash = hashlib.sha256(evidence_bytes).hexdigest()
            doc_file_name = Path(payload.evidence_filename or "evidence.bin").name
            doc_type = "QC_INSPECTION"
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            saved_name = f"{uuid.uuid4()}_{doc_file_name}"
            saved_path = UPLOAD_DIR / saved_name
            saved_path.write_bytes(evidence_bytes)
            photo_path = str(saved_path)
            ref_file_name = doc_file_name
            document_id = str(uuid.uuid4())
        except Exception as e:
            logger.warning(f"Could not process evidence_base64: {e}")
            photo_path = None
            ref_file_name = None
            document_id = None

    with conn.cursor() as cur:
        if document_id:
            cur.execute(
                """INSERT INTO source_documents (document_id, file_name, document_type, uploader_id, file_hash)
                   VALUES (%s, %s, %s, %s, %s)""",
                (document_id, doc_file_name, doc_type, current_user.id, text_hash),
            )

        # 2. Run extraction. A genuine extraction failure (bad/missing LLM
        #    config, network error, malformed model output) must not
        #    masquerade as a successful-but-empty claim -- surface it as a
        #    clear error instead of silently writing an all-null row.
        try:
            extracted = extract_claim_fields(payload.raw_claim_text)
        except LLMExtractionError as e:
            conn.rollback()
            raise HTTPException(
                status_code=502,
                detail=f"Claim extraction failed: {e}. The claim was not created — please retry.",
            )

        # 3. Insert the claim at EXTRACTED (the only status M2 is allowed to write).
        event_id = str(uuid.uuid4())
        event_date_val = extracted.event_date or date.today()

        # Feature #33 Provenance Tagging
        provenance = _build_ai_provenance(extracted)

        # Feature #29 Adaptive Field Copilot
        missing_fields = check_missing_required_fields(extracted)
        if missing_fields:
            clarification_question = generate_clarification_question(
                payload.raw_claim_text, missing_fields, extracted.language_detected
            )
            clarification_status = ClarificationStatus.PENDING.value
        else:
            clarification_question = None
            clarification_status = ClarificationStatus.NONE.value

        _insert_execution_event(
            cur,
            event_id=event_id,
            document_id=document_id,
            schedule_id=schedule_id,
            event_date_val=event_date_val,
            raw_claim_text=payload.raw_claim_text,
            input_channel=payload.input_channel.value,
            language_detected=extracted.language_detected,
            reported_activity_id=extracted.reported_activity_id,
            discipline=extracted.discipline.value if extracted.discipline else None,
            action=extracted.action,
            event_type=extracted.event_type.value if extracted.event_type else None,
            claim_mode=extracted.claim_mode.value,
            asset_tag=extracted.asset_tag,
            location=extracted.location,
            claimed_quantity=extracted.claimed_quantity,
            claimed_uom=extracted.claimed_uom,
            claimed_pct=extracted.claimed_pct,
            delay_reason=extracted.delay_reason.value if extracted.delay_reason else None,
            supervisor_id=current_user.id,
            photo_path=photo_path,
            clarification_status=clarification_status,
            clarification_question=clarification_question,
            clarification_answer=None,
            field_provenance=provenance,
            project_id=project_context.project_id,
            **_derive_attribution(cur, project_context.project_id, schedule_id, extracted.reported_activity_id),
        )

        if document_id and photo_path:
            _insert_source_reference(
                cur, event_id=event_id, file_name=ref_file_name, raw_snippet=payload.raw_claim_text
            )

        conn.commit()

        cur.execute("SELECT * FROM execution_events WHERE event_id = %s", (event_id,))
        row = cur.fetchone()

    return _row_to_claim_response(row)


@router.get("/claims/{event_id}", response_model=ClaimResponse)
def get_claim(
    event_id: str,
    conn=Depends(get_db),
    event_context: EventContext = Depends(gates.event_view),
):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM execution_events WHERE event_id = %s", (event_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Claim not found")
    return _row_to_claim_response(row)


@router.get("/claims/{event_id}/photo")
def get_claim_photo(
    event_id: str,
    conn=Depends(get_db),
    event_context: EventContext = Depends(gates.event_view),
):
    """
    Serves the evidence photo attached to a claim (execution_events.photo_path)
    so the Review Workspace / Activity History UI can actually render the image
    instead of only showing its filename (ISS-07 evidence round-trip / ISS-20
    lightbox). Both roles may view it -- evidence review isn't role-restricted
    the way approval decisions are.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM execution_events WHERE event_id = %s", (event_id,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Claim not found")

    photo_path = row["photo_path"]
    if not photo_path:
        raise HTTPException(status_code=404, detail="This claim has no attached photo")

    # Defense in depth: photo_path is server-generated at upload time (see
    # create_file_claim), but never trust a stored path to stay inside
    # UPLOAD_DIR without checking -- refuse to serve anything that resolves
    # outside it rather than risk an arbitrary file read.
    resolved = Path(photo_path).resolve()
    try:
        resolved.relative_to(UPLOAD_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="Evidence photo not found")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="Evidence photo file is missing from storage")

    return FileResponse(resolved)


@router.post("/claims/{event_id}/clarify", response_model=ClaimResponse)
def clarify_claim(
    event_id: str,
    payload: ClarifyClaimRequest,
    conn=Depends(get_db),
    event_context: EventContext = Depends(gates.event_clarify),
):
    """
    Feature #29: Adaptive Field Copilot clarification endpoint.
    Takes user's free-text answer, merges it with the original raw_claim_text,
    re-runs extraction, sets clarification_status = 'ANSWERED' and clarification_answer,
    and updates field_provenance for newly-filled fields.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM execution_events WHERE event_id = %s", (event_id,))
        claim = cur.fetchone()
        if not claim:
            raise HTTPException(status_code=404, detail="Claim not found")

        orig_text = claim["raw_claim_text"]
        merged_text = f"{orig_text}\nClarification: {payload.answer}"

        try:
            extracted = extract_claim_fields(merged_text)
        except LLMExtractionError as e:
            conn.rollback()
            raise HTTPException(
                status_code=502,
                detail=f"Clarification extraction failed: {e}. Please retry.",
            )

        raw_prov = claim.get("field_provenance") if isinstance(claim, dict) else (claim["field_provenance"] if "field_provenance" in claim else None)
        if isinstance(raw_prov, str):
            try:
                existing_prov = json.loads(raw_prov)
            except Exception:
                existing_prov = {}
        elif isinstance(raw_prov, dict):
            existing_prov = dict(raw_prov)
        else:
            existing_prov = {}

        new_prov = _build_ai_provenance(extracted)
        existing_prov.update(new_prov)

        event_date = extracted.event_date or claim["event_date"]
        discipline = extracted.discipline.value if extracted.discipline else claim.get("discipline")
        action = extracted.action or claim.get("action")
        event_type = extracted.event_type.value if extracted.event_type else claim.get("event_type")
        claim_mode = extracted.claim_mode.value if extracted.claim_mode else claim.get("claim_mode", "CUMULATIVE_PCT")
        asset_tag = extracted.asset_tag or claim.get("asset_tag")
        location = extracted.location or claim.get("location")
        claimed_quantity = extracted.claimed_quantity if extracted.claimed_quantity is not None else claim.get("claimed_quantity")
        claimed_uom = extracted.claimed_uom or claim.get("claimed_uom")
        claimed_pct = extracted.claimed_pct if extracted.claimed_pct is not None else claim.get("claimed_pct")
        delay_reason = extracted.delay_reason.value if extracted.delay_reason else claim.get("delay_reason")
        reported_activity_id = extracted.reported_activity_id or claim.get("reported_activity_id")
        language_detected = extracted.language_detected or claim.get("language_detected")

        cur.execute(
            """UPDATE execution_events SET
                event_date = %s,
                raw_claim_text = %s,
                discipline = %s,
                action = %s,
                event_type = %s,
                claim_mode = %s,
                asset_tag = %s,
                location = %s,
                claimed_quantity = %s,
                claimed_uom = %s,
                claimed_pct = %s,
                delay_reason = %s,
                clarification_status = %s,
                clarification_answer = %s,
                field_provenance = %s,
                reported_activity_id = %s,
                language_detected = %s
               WHERE event_id = %s""",
            (
                event_date,
                merged_text,
                discipline,
                action,
                event_type,
                claim_mode,
                asset_tag,
                location,
                claimed_quantity,
                claimed_uom,
                claimed_pct,
                delay_reason,
                ClarificationStatus.ANSWERED.value,
                payload.answer,
                json.dumps(existing_prov),
                reported_activity_id,
                language_detected,
                event_id,
            ),
        )
        conn.commit()

        cur.execute("SELECT * FROM execution_events WHERE event_id = %s", (event_id,))
        row = cur.fetchone()

    return _row_to_claim_response(row)


@router.get("/claims", response_model=list[ClaimResponse])
def list_claims(
    status: Optional[str] = Query(default=None),
    discipline: Optional[str] = Query(default=None),
    schedule_id: Optional[str] = Query(default=None, description="Required (or X-Schedule-ID); validated against the project"),
    conn=Depends(get_db),
    schedule_context: ScheduleContext = Depends(gates.events_view),
):
    """
    ISS-05: scoped to an EXPLICIT schedule of the caller's project -- this backs the Review Workspace
    queue, so an unscoped list here is exactly the kind of cross-schedule/cross-project leak that feature
    must never show. There is no active/latest fallback.
    """
    query = "SELECT * FROM execution_events WHERE schedule_id = %s AND project_id = %s"
    params: list = [schedule_context.schedule_id, str(schedule_context.project_id)]
    if status:
        query += " AND status = %s"
        params.append(status)
    if discipline:
        query += " AND discipline = %s"
        params.append(discipline)
    query += " ORDER BY created_at DESC"
    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return [_row_to_claim_response(r) for r in rows]


@router.post("/claims/file", response_model=List[ClaimResponse])
def create_file_claim(
    file: UploadFile = File(...),
    purpose: UploadPurpose = Form(UploadPurpose.EVIDENCE_PHOTO),
    raw_claim_text: Optional[str] = Form(None),
    schedule_id: Optional[str] = Form(None),
    request: Request = None,
    conn=Depends(get_db),
    project_context: ProjectContext = Depends(gates.project_events_create),
):
    """
    File intake (#2): .pdf/.xlsx/.xls/.csv/.txt/.xer, or an image. A single
    file commonly reports progress on several distinct activities (a daily
    report table, a multi-sheet discipline spreadsheet, a schedule export,
    a multi-section site diary photo) -- this returns one ClaimResponse per
    activity actually found, not one per file. See the module docstring for
    the extraction architecture.

    Images are handled by `purpose`:
      - EVIDENCE_PHOTO (default) + raw_claim_text supplied: the photo is
        evidence attached to that already-typed claim -- saved to disk, its
        path recorded in photo_path for M4's evidence check. Unchanged from
        the original contract.
      - Anything else (explicit SCANNED_DIARY, or an image with no
        raw_claim_text at all): the image itself IS the claim -- extracted
        via vision-LLM/OCR (see _image_claim_drafts), never written to
        photo_path. This is what makes a bare photo upload (no typed text)
        a legitimate claim source instead of a guaranteed 422.
    """
    current_user = project_context.user
    schedule_scope = resolve_explicit_schedule(
        project_context, schedule_id, request.headers.get("X-Schedule-ID"), request.query_params.get("schedule_id")
    )
    schedule_id = schedule_scope.schedule_id

    contents = file.file.read()
    if not contents:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")

    ext = Path(file.filename or "").suffix.lower()
    file_hash = hashlib.sha256(contents).hexdigest()

    photo_path: Optional[str] = None
    document_type = "DPR"
    input_channel = InputChannel.FILE_UPLOAD
    drafts: list[ClaimDraft]

    if purpose == UploadPurpose.EVIDENCE_PHOTO and raw_claim_text:
        # The file (photo or document) is proof/evidence attached to an already-typed claim.
        document_type = "QC_INSPECTION"
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        # Path(...).name strips any directory components from the
        # user-supplied filename so it can never escape UPLOAD_DIR.
        safe_filename = Path(file.filename or "upload").name
        saved_name = f"{uuid.uuid4()}_{safe_filename}"
        saved_path = UPLOAD_DIR / saved_name
        saved_path.write_bytes(contents)
        photo_path = str(saved_path)
        input_channel = InputChannel.TYPED_TEXT
        drafts = [ClaimDraft(raw_text=raw_claim_text, extracted=_extract_single_or_raise(raw_claim_text))]
    else:
        try:
            drafts, document_type, input_channel = _build_claim_drafts(
                file.filename or "upload", contents, raw_claim_text_fallback=raw_claim_text
            )
        except UnsupportedFileError as e:
            raise HTTPException(status_code=415, detail=str(e))
        except FileParseError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except LLMExtractionError as e:
            raise HTTPException(
                status_code=502,
                detail=f"Claim extraction failed: {e}. No claims were created — please retry.",
            )

    document_id = str(uuid.uuid4())
    created_rows = []

    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO source_documents (document_id, file_name, document_type, uploader_id, file_hash)
               VALUES (%s, %s, %s, %s, %s)""",
            (document_id, file.filename, document_type, current_user.id, file_hash),
        )

        for draft in drafts:
            extracted = draft.extracted
            event_id = str(uuid.uuid4())
            event_date_val = extracted.event_date or date.today()

            # Feature #33 Provenance Tagging
            provenance = _build_ai_provenance(extracted)

            # Feature #29 Adaptive Field Copilot
            missing_fields = check_missing_required_fields(extracted)
            if missing_fields:
                clarification_question = generate_clarification_question(
                    draft.raw_text, missing_fields, extracted.language_detected
                )
                clarification_status = ClarificationStatus.PENDING.value
            else:
                clarification_question = None
                clarification_status = ClarificationStatus.NONE.value

            _insert_execution_event(
                cur,
                event_id=event_id,
                document_id=document_id,
                schedule_id=schedule_id,
                event_date_val=event_date_val,
                raw_claim_text=draft.raw_text,
                input_channel=input_channel.value,
                language_detected=extracted.language_detected,
                reported_activity_id=extracted.reported_activity_id,
                discipline=extracted.discipline.value if extracted.discipline else None,
                action=extracted.action,
                event_type=extracted.event_type.value if extracted.event_type else None,
                claim_mode=extracted.claim_mode.value,
                asset_tag=extracted.asset_tag,
                location=extracted.location,
                claimed_quantity=extracted.claimed_quantity,
                claimed_uom=extracted.claimed_uom,
                claimed_pct=extracted.claimed_pct,
                delay_reason=extracted.delay_reason.value if extracted.delay_reason else None,
                supervisor_id=current_user.id,
                photo_path=photo_path,
                clarification_status=clarification_status,
                clarification_question=clarification_question,
                clarification_answer=None,
                field_provenance=provenance,
                project_id=project_context.project_id,
                **_derive_attribution(cur, project_context.project_id, schedule_id, extracted.reported_activity_id),
            )
            _insert_source_reference(
                cur, event_id=event_id, file_name=file.filename, raw_snippet=draft.raw_text
            )

            cur.execute("SELECT * FROM execution_events WHERE event_id = %s", (event_id,))
            created_rows.append(cur.fetchone())

        conn.commit()

    return [_row_to_claim_response(r) for r in created_rows]


def _extract_single_or_raise(text: str) -> ExtractedClaimFields:
    try:
        return extract_claim_fields(text)
    except LLMExtractionError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Claim extraction failed: {e}. The claim was not created — please retry.",
        )


@router.post("/claims/schedule-export", response_model=List[ClaimResponse])
def create_schedule_export_claims(
    file: UploadFile = File(...),
    schedule_id: Optional[str] = Form(None),
    request: Request = None,
    conn=Depends(get_db),
    project_context: ProjectContext = Depends(gates.project_events_create),
):
    """
    Schedule-export claims (#22, parsing half): a second P6/MSP-format file
    used as a *source of progress claims*, not a new baseline. Since each
    row already carries a real activity_id, these skip LLM extraction
    entirely and go straight to M3's matching as EXACT_ID candidates. Only
    rows with actual progress data produce a claim -- an empty/not-started
    row is not a progress report.

    Column detection and per-row claim construction are shared with
    /claims/file's structured CSV/XLSX path (shared/tabular_extraction.py)
    so both endpoints agree on what "the activity id column" means for the
    same file shape.
    """
    current_user = project_context.user
    schedule_scope = resolve_explicit_schedule(
        project_context, schedule_id, request.headers.get("X-Schedule-ID"), request.query_params.get("schedule_id")
    )
    schedule_id = schedule_scope.schedule_id

    contents = file.file.read()
    if not contents:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")

    try:
        sheets = read_tabular_file(file.filename or "upload", contents)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"Could not parse schedule-export file: {e}")

    sheet_cols = {name: detect_progress_columns(list(df.columns)) for name, df in sheets.items()}
    if not any(cols is not None for cols in sheet_cols.values()):
        raise HTTPException(
            status_code=422,
            detail="Schedule-export file has no 'Activity ID' column.",
        )

    file_hash = hashlib.sha256(contents).hexdigest()
    document_id = str(uuid.uuid4())
    claim_rows: List[dict] = []

    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO source_documents (document_id, file_name, document_type, uploader_id, file_hash)
               VALUES (%s, %s, %s, %s, %s)""",
            (document_id, file.filename, "SCHEDULE_EXPORT_PROGRESS", current_user.id, file_hash),
        )

        for sheet_name, df in sheets.items():
            cols = sheet_cols[sheet_name]
            if cols is None:
                continue

            for _, row in df.iterrows():
                result = build_claim_from_row(row.to_dict(), cols, discipline_hint=sheet_name or None)
                if result is None:
                    continue
                raw_text, extracted = result

                event_id = str(uuid.uuid4())
                event_date_val = extracted.event_date or date.today()

                # Feature #33 Provenance tagging for SCHEDULE_AUTO_FILLED
                provenance = _build_schedule_provenance(extracted)

                _insert_execution_event(
                    cur,
                    event_id=event_id,
                    document_id=document_id,
                    schedule_id=schedule_id,
                    event_date_val=event_date_val,
                    raw_claim_text=raw_text,
                    input_channel=InputChannel.SCHEDULE_EXPORT.value,
                    language_detected=extracted.language_detected,
                    reported_activity_id=extracted.reported_activity_id,
                    discipline=extracted.discipline.value if extracted.discipline else None,
                    action=extracted.action,
                    event_type=extracted.event_type.value if extracted.event_type else None,
                    claim_mode=extracted.claim_mode.value,
                    asset_tag=extracted.asset_tag,
                    location=extracted.location,
                    claimed_quantity=extracted.claimed_quantity,
                    claimed_uom=extracted.claimed_uom,
                    claimed_pct=extracted.claimed_pct,
                    delay_reason=extracted.delay_reason.value if extracted.delay_reason else None,
                    supervisor_id=current_user.id,
                    clarification_status=ClarificationStatus.NONE.value,
                    clarification_question=None,
                    clarification_answer=None,
                    field_provenance=provenance,
                    project_id=project_context.project_id,
                    **_derive_attribution(cur, project_context.project_id, schedule_id, extracted.reported_activity_id),
                )
                _insert_source_reference(
                    cur, event_id=event_id, file_name=file.filename, raw_snippet=str(row.to_dict())
                )

                cur.execute("SELECT * FROM execution_events WHERE event_id = %s", (event_id,))
                claim_rows.append(cur.fetchone())

        conn.commit()

    return [_row_to_claim_response(r) for r in claim_rows]
