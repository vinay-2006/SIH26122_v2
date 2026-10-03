"""Report and evidence files. Uploads are validated by content, stored under a generated name, and referenced by id only: no response ever
carries a filesystem path or storage key. Schedule files are NOT accepted here (they go through the Project Manager's import pipeline)."""
from __future__ import annotations

import urllib.parse
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel, ConfigDict

from .. import filetypes, permissions as P
from ..auth import ProjectAccess, require
from ..errors import ApiError
from ..services import documents as svc
from ._common import ERRORS, LIMIT, OFFSET, actor_of, page

router = APIRouter(prefix="/api/v2/projects/{project_id}", tags=["documents"], responses=ERRORS)
HARD_CAP = max(filetypes.LIMITS.values()) + 1


class ExtractBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_date: Optional[date] = None
    create_claims: bool = True


@router.post("/documents", status_code=201, summary="Upload a report, photograph or evidence file")
async def upload(kind: str = Form(..., description="DAILY_REPORT, SITE_REPORT, PHOTO, EVIDENCE or ISSUE_REPORT (Supervisors: EVIDENCE, ISSUE_REPORT)"),
                 file: UploadFile = File(...), access: ProjectAccess = Depends(require(P.UPLOAD_EVIDENCE, writable=True))):
    content = await file.read(HARD_CAP)
    if len(content) >= HARD_CAP:
        raise ApiError(413, "TOO_LARGE", "The file is too large")
    return svc.upload_document(access.user, access.project_id, access.role, kind, file.filename or "upload", content)


@router.get("/documents", summary="Documents visible to the caller")
def list_docs(kind: Optional[str] = None, limit: int = LIMIT, offset: int = OFFSET, access: ProjectAccess = Depends(require(P.VIEW_DOCUMENTS))):
    return page(lambda l, o: svc.list_documents(actor_of(access), kind, l, o), limit, offset)


@router.get("/documents/{document_id}", summary="Document metadata")
def get_doc(document_id: uuid.UUID, access: ProjectAccess = Depends(require(P.VIEW_DOCUMENTS))):
    return svc.get_document(actor_of(access), document_id)


@router.get("/documents/{document_id}/content", summary="Download the stored file (attachment)", response_class=Response)
def content(document_id: uuid.UUID, access: ProjectAccess = Depends(require(P.VIEW_DOCUMENTS))):
    data, mime, name = svc.read_content(actor_of(access), document_id)
    ascii_name = "".join(ch if 32 < ord(ch) < 127 and ch not in '"\\;' else "_" for ch in name) or "download"
    return Response(content=data, media_type=mime, headers={
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(name)}",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store", "Content-Security-Policy": "default-src 'none'; sandbox"})


@router.post("/documents/{document_id}/extract", summary="Read an uploaded report and file the claims it contains (Site Engineer, own uploads)")
def extract(document_id: uuid.UUID, body: ExtractBody = ExtractBody(), access: ProjectAccess = Depends(require(P.SUBMIT_CLAIM, writable=True))):
    """Synchronous, deterministic and strict. Supported: CSV, XLSX, plain text and text-layer PDF. A failed extraction files no claim
    (422 EXTRACTION_FAILED); ambiguous rows are listed under `skipped` with reasons and are never guessed. `create_claims=false` previews only."""
    return svc.extract_document(actor_of(access), document_id, event_date=body.event_date, create_claims=body.create_claims)
