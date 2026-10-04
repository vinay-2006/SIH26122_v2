"""What a Site Engineer may upload as a PROGRESS REPORT, and how those files reach the ORIGINAL extraction strategies (backend/routers/intake.py, unchanged).

* Progress reports / evidence: CSV, XLSX (structured rows, no LLM), TXT, PDF with a text layer, DOCX (text), and photographs / scans (JPG, PNG, WebP: vision model when the
  live-LLM opt-in is on, otherwise Tesseract OCR when installed; if neither can read it the file is reported as not readable, never guessed).
* Baseline SCHEDULE files (.xer, .xml, .mpp ...) are never reports: they are imported by a Project Manager under Schedule, so they are refused here.
* Legacy binary Office files (.xls, .doc) are refused with a pointer to the modern format: there is no safe reader for them in this application.
The original code only knows .jpg/.jpeg/.png/.txt/.pdf/.csv/.xlsx/.xls/.xer; DOCX and WebP are converted to what it already reads (text / PNG) before the call."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

from ..errors import ApiError

REPORT_EXTENSIONS = (".csv", ".xlsx", ".pdf", ".txt", ".docx", ".jpg", ".jpeg", ".png", ".webp")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
SCHEDULE_EXTENSIONS = (".xer", ".xml", ".mpp", ".mpx", ".p6", ".pmxml")
LEGACY_OFFICE = {".xls": "XLSX", ".doc": "DOCX"}


def refusal(filename: str) -> Optional[str]:
    """the message for a file that is not a progress report, or None when it may be read"""
    ext = Path(filename or "").suffix.lower()
    if ext in SCHEDULE_EXTENSIONS:
        return ("Schedule files (Primavera P6 .xer, MS Project .xml/.mpp) are baseline schedules, not progress reports. A Project Manager imports them under Schedule; "
                "upload a progress report (CSV, XLSX, PDF, DOCX, TXT or a photo/scan) here instead.")
    if ext in LEGACY_OFFICE:
        return f"Legacy {ext} files cannot be read. Open the file and save it as {LEGACY_OFFICE[ext]} (or PDF), then upload that."
    if ext not in REPORT_EXTENSIONS:
        return f"Unsupported file type '{ext or 'none'}'. Accepted progress-report files: " + ", ".join(REPORT_EXTENSIONS) + "."
    return None


def refuse_or_pass(filename: str) -> None:
    msg = refusal(filename)
    if msg:
        raise ApiError(415, "UNSUPPORTED_FILE", msg)


def _png_from_webp(contents: bytes) -> bytes:
    from PIL import Image
    try:
        with Image.open(io.BytesIO(contents)) as im:
            out = io.BytesIO()
            im.convert("RGB").save(out, "PNG")
            return out.getvalue()
    except Exception as e:
        from backend.routers.intake import FileParseError
        raise FileParseError(f"The WebP image could not be read: {e}") from e


def build_drafts(filename: str, contents: bytes, raw_claim_text_fallback: Optional[str] = None):
    """the original `_build_claim_drafts(filename, contents, ...)`, with DOCX and WebP adapted. Raises the original UnsupportedFileError / FileParseError / LLMExtractionError."""
    from backend.routers import intake as li
    ext = Path(filename or "").suffix.lower()
    msg = refusal(filename)
    if msg:
        raise li.UnsupportedFileError(msg)
    if ext == ".docx":
        from .. import extraction
        try:
            text = extraction.docx_text(contents)
        except extraction.ExtractionError as e:
            raise li.FileParseError(e.message)
        if not text.strip():
            raise li.FileParseError("The Word document has no readable text.")
        return li._batch_text_drafts(text), "DPR", li.InputChannel.FILE_UPLOAD
    if ext == ".webp":
        return li._build_claim_drafts(Path(filename).stem + ".png", _png_from_webp(contents), raw_claim_text_fallback)
    return li._build_claim_drafts(filename, contents, raw_claim_text_fallback)
