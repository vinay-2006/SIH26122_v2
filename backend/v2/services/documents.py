"""Reports and evidence files: validated upload into the evidence store, role-filtered reads, and synchronous extraction into claims.

* The file's type is decided from its content (filetypes.detect); its stored name is generated; the database keeps an opaque key, a SHA-256
  and metadata. API responses never contain a storage key or filesystem path.
* A document is readable by: its uploader (Site Engineer); any Supervisor (except schedule files); a Project Manager only for schedule files and
  issue evidence that is not also claim evidence (claim content is not theirs to read).
* Extraction never invents anything: a failed or ambiguous extraction files NO claim. Eligible rows are filed through claims.submit_claim exactly as if
  the engineer had typed them (so matching, unit binding, validation and de-duplication are the same), each in its own transaction."""
from __future__ import annotations

import hashlib
import io
import json
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg.errors as pge

from .. import audit, extraction as ex, filetypes, storage
from ..db import tx
from ..domain import claims as claims_domain
from ..domain.common import PM, SE, SUP, ProjectActor
from ..errors import ApiError, forbidden
from ..schedule_import.mapping import RefData, resolve_uom

DOC_KINDS = ("DAILY_REPORT", "SITE_REPORT", "PHOTO", "EVIDENCE", "ISSUE_REPORT")
SUP_KINDS = ("EVIDENCE", "ISSUE_REPORT")
CHANNEL = {"CSV_TABLE": "CSV", "XLSX_TABLE": "XLSX", "TEXT_RULES": "TXT", "PDF_TEXT_RULES": "PDF"}
_PUBLIC = ("document_id, kind, file_name, mime_type, size_bytes, sha256, uploaded_by, uploaded_at, extraction_status, extraction_method, extraction_error, "
           "claims_extracted, page_count, captured_at, gps_lat, gps_lon")


def _exif(content: bytes) -> Dict[str, Any]:
    """best effort capture time / GPS from a photograph; never fails the upload"""
    out: Dict[str, Any] = {}
    try:
        from PIL import Image
        with Image.open(io.BytesIO(content)) as im:
            e = im.getexif()
            sub = e.get_ifd(0x8769) if hasattr(e, "get_ifd") else {}
            raw = sub.get(36867) or e.get(306)
            if raw:
                out["captured_at"] = datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S").replace(tzinfo=timezone.utc)
            gps = e.get_ifd(0x8825) if hasattr(e, "get_ifd") else {}
            if gps and gps.get(2) and gps.get(4) and gps.get(1) and gps.get(3):
                def deg(v, ref):
                    d = float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
                    return -d if ref in ("S", "W") else d
                lat, lon = deg(gps[2], gps[1]), deg(gps[4], gps[3])
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    out["gps_lat"], out["gps_lon"] = lat, lon
            meta = {k: str(e.get(t))[:60] for k, t in (("make", 271), ("model", 272)) if e.get(t)}
            if meta:
                out["exif"] = meta
    except Exception:
        return {}
    return out


# ------------------------------------------------------------------------------------------------ upload
def upload_document(user, project_id, role: str, kind: str, filename: str, content: bytes, batch_id=None) -> Dict[str, Any]:
    allowed = DOC_KINDS if role == SE else SUP_KINDS if role == SUP else ()
    if kind not in DOC_KINDS:
        raise ApiError(422, "BAD_KIND", f"kind must be one of {', '.join(DOC_KINDS)}")
    if kind not in allowed:
        raise forbidden(f"Role {role} may not upload {kind} documents", "PERMISSION_DENIED")
    if not content:
        raise ApiError(422, "EMPTY_FILE", "The file is empty")
    from ..upload_guard import schedule_like_reason
    why = schedule_like_reason(filename, content)
    if why:
        raise ApiError(422, "SCHEDULE_FILE_NOT_ALLOWED", why)
    det = filetypes.detect(filename, content, kind)
    name = filetypes.safe_display_name(filename)
    sha = hashlib.sha256(content).hexdigest()
    meta = _exif(content) if det.family == "image" else {}
    store = storage.get_store()
    try:
        key = store.put(project_id, content, det.ext)
    except storage.StorageError as e:
        raise ApiError(500, "STORAGE_UNAVAILABLE", "The file could not be stored") from e
    try:
        with tx(user.id) as c:
            try:
                c.execute("savepoint ins")
                row = c.execute(
                    "insert into source_documents (project_id, kind, file_name, mime_type, storage_path, storage_backend, sha256, size_bytes, uploaded_by, extraction_status, "
                    "captured_at, gps_lat, gps_lon, exif, batch_id) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) returning document_id, uploaded_at",
                    (project_id, kind, name, det.mime, key, store.backend, sha, len(content), user.id, "PENDING" if det.family != "image" else None,
                     meta.get("captured_at"), meta.get("gps_lat"), meta.get("gps_lon"), json.dumps(meta["exif"]) if meta.get("exif") else None, batch_id)).fetchone()
            except pge.UniqueViolation as e:
                c.execute("rollback to savepoint ins")
                dup = c.execute("select document_id, uploaded_by from source_documents where project_id = %s and kind = %s and sha256 = %s", (project_id, kind, sha)).fetchone()
                raise ApiError(409, "DUPLICATE_UPLOAD", "This exact file was already uploaded to the project",
                               {"document_id": str(dup["document_id"])} if dup and dup["uploaded_by"] == user.id else None) from e
            audit.log(c, project_id=project_id, actor_id=user.id, role=role, action="DOCUMENT_UPLOADED", entity_type="SOURCE_DOCUMENT", entity_id=row["document_id"],
                      after={"kind": kind, "file": name, "sha256": sha, "size": len(content), "type": det.mime})
    except BaseException:
        store.delete(key)                                                  # no orphan file behind a refused / failed upload
        raise
    return {"document_id": row["document_id"], "kind": kind, "file_name": name, "mime_type": det.mime, "size_bytes": len(content), "sha256": sha,
            "uploaded_at": row["uploaded_at"], "extractable": det.family in ("text", "xlsx", "pdf"), **{k: v for k, v in meta.items() if k != "exif"}}


# ------------------------------------------------------------------------------------------------ reads
def _links(c, project_id, doc_id) -> Tuple[bool, bool]:
    claim = c.execute("select exists(select 1 from claim_evidence where project_id = %s and document_id = %s) or exists(select 1 from execution_events where project_id = %s and document_id = %s) as x",
                      (project_id, doc_id, project_id, doc_id)).fetchone()["x"]
    issue = c.execute("select exists(select 1 from issue_evidence where project_id = %s and document_id = %s) as x", (project_id, doc_id)).fetchone()["x"]
    return claim, issue


def _readable(c, actor: ProjectActor, doc: dict) -> bool:
    if actor.role == SE:
        return doc["uploaded_by"] == actor.user_id
    if actor.role == SUP:
        return doc["kind"] != "SCHEDULE_FILE"
    if actor.role == PM:
        if doc["kind"] == "SCHEDULE_FILE":
            return True
        claim, issue = _links(c, actor.project_id, doc["document_id"])
        return issue and not claim and doc["kind"] in ("EVIDENCE", "ISSUE_REPORT", "PHOTO")
    return False


def _doc(c, project_id, document_id, cols=_PUBLIC + ", storage_path, storage_backend, project_id") -> dict:
    d = c.execute(f"select {cols} from source_documents where project_id = %s and document_id = %s", (project_id, document_id)).fetchone()
    if d is None:
        raise ApiError(404, "DOCUMENT_NOT_FOUND", "No such document")
    return d


def _public(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in ("storage_path", "storage_backend", "project_id")}


def get_document(actor: ProjectActor, document_id) -> Dict[str, Any]:
    with tx(actor.user_id, readonly=True) as c:
        d = _doc(c, actor.project_id, document_id)
        if not _readable(c, actor, d):
            raise ApiError(404, "DOCUMENT_NOT_FOUND", "No such document")
        claim, issue = _links(c, actor.project_id, document_id)
        return {**_public(d), "attached_to_claim": claim if actor.role != PM else None, "attached_to_issue": issue}


def list_documents(actor: ProjectActor, kind: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[dict]:
    base = f"select {_PUBLIC} from source_documents where project_id = %s and (%s::text is null or kind = %s)"
    order = " order by uploaded_at desc, document_id"
    with tx(actor.user_id, readonly=True) as c:
        if actor.role == SE:
            return c.execute(base + " and uploaded_by = %s" + order + " limit %s offset %s", (actor.project_id, kind, kind, actor.user_id, limit, offset)).fetchall()
        if actor.role == SUP:
            return c.execute(base + " and kind <> 'SCHEDULE_FILE'" + order + " limit %s offset %s", (actor.project_id, kind, kind, limit, offset)).fetchall()
        visible = [r for r in c.execute(base + order, (actor.project_id, kind, kind)).fetchall() if _readable(c, actor, r)]
        return visible[offset:offset + limit]


def read_content(actor: ProjectActor, document_id) -> Tuple[bytes, str, str]:
    with tx(actor.user_id, readonly=True) as c:
        d = _doc(c, actor.project_id, document_id)
        if not _readable(c, actor, d):
            raise ApiError(404, "DOCUMENT_NOT_FOUND", "No such document")
    if d["storage_backend"] != storage.get_store().backend or not d["storage_path"]:
        raise ApiError(404, "CONTENT_UNAVAILABLE", "The stored file is not available")
    try:
        data = storage.get_store().read(d["storage_path"])
    except storage.StorageError as e:
        raise ApiError(404, "CONTENT_UNAVAILABLE", "The stored file is not available") from e
    if hashlib.sha256(data).hexdigest() != d["sha256"]:
        raise ApiError(500, "INTEGRITY_FAILURE", "The stored file does not match its recorded hash")
    return data, d["mime_type"] or "application/octet-stream", d["file_name"]


# ------------------------------------------------------------------------------------------------ extraction -> claims
def _unit_checker(c) -> Any:
    ref = RefData(set(), {}, {r["code"]: r["dimension"] for r in c.execute("select code, dimension from units_of_measure").fetchall()})
    return lambda token: resolve_uom(token, None, ref) is not None


def extract_document(actor: ProjectActor, document_id, *, event_date: Optional[date] = None, create_claims: bool = True) -> Dict[str, Any]:
    if actor.role != SE:
        raise forbidden("Only the Site Engineer who uploaded a report can file claims from it", "PERMISSION_DENIED")
    with tx(actor.user_id, readonly=True) as c:
        d = _doc(c, actor.project_id, document_id)
        if d["uploaded_by"] != actor.user_id:
            raise ApiError(404, "DOCUMENT_NOT_FOUND", "No such document")
        unit_ok = _unit_checker(c)
        acts = {r["external_activity_id"].strip().upper(): r["activity_uid"] for r in c.execute(
            "select ba.external_activity_id, ba.activity_uid from baseline_activities ba join schedule_versions v on v.version_id = ba.version_id "
            "where v.project_id = %s and v.status = 'ACTIVE'", (actor.project_id,)).fetchall()}
    if d["kind"] not in ("DAILY_REPORT", "SITE_REPORT", "EVIDENCE", "ISSUE_REPORT"):
        raise ApiError(422, "NOT_EXTRACTABLE", "Photographs are evidence only; claims cannot be extracted from them")
    if not acts:
        raise ApiError(409, "NO_ACTIVE_SCHEDULE", "The project has no active schedule: a Project Manager must activate one first")
    data, mime, _ = read_content(actor, document_id)
    family = {"application/pdf": "pdf", "image/png": "image", "image/jpeg": "image"}.get(mime) or ("xlsx" if "spreadsheetml" in mime else "text")
    ext = "csv" if mime == "text/csv" else "txt"
    try:
        result = ex.extract(data, family, ext, unit_ok=unit_ok, default_date=event_date)
    except ex.ExtractionError as e:
        with tx(actor.user_id) as c:
            c.execute("update source_documents set extraction_status = 'FAILED', extraction_error = %s, claims_extracted = 0 where project_id = %s and document_id = %s",
                      (f"{e.code}: {e.message}"[:300], actor.project_id, document_id))
            audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=SE, action="EXTRACTION_FAILED", entity_type="SOURCE_DOCUMENT", entity_id=document_id, after={"code": e.code})
        raise ApiError(422, "EXTRACTION_FAILED", e.message, {"reason": e.code, "claims_created": 0}) from e

    created: List[dict] = []
    skipped: List[dict] = []
    for cand in result.candidates:
        uid = acts.get(cand.activity_ref.strip().upper())
        reasons = list(cand.problems)
        if uid is None:
            reasons.append("ACTIVITY_NOT_FOUND")
        if reasons:
            skipped.append({"source_ref": cand.source_ref, "activity_ref": cand.activity_ref, "reasons": reasons})
            continue
        if not create_claims:
            created.append({"source_ref": cand.source_ref, "activity_uid": uid, "claim_id": None, "outcome": "ELIGIBLE", "event_date": cand.event_date,
                            "quantities": [{**q, "qty": str(q["qty"])} for q in cand.quantities], "claimed_pct": str(cand.claimed_pct) if cand.claimed_pct is not None else None})
            continue
        try:
            r = claims_domain.submit_claim(actor, event_date=cand.event_date, raw_text=cand.text, input_channel=CHANNEL[result.method], activity_uid=uid,
                                           reported_activity_ref=cand.activity_ref, quantities=cand.quantities, claimed_pct=cand.claimed_pct, document_id=document_id,
                                           evidence_document_ids=[document_id], field_provenance={"extraction": result.method, "source": cand.source_ref, "activity": "EXTRACTED"})
            created.append({"source_ref": cand.source_ref, "activity_uid": uid, "claim_id": r["claim_id"], "outcome": "CREATED"})
        except ApiError as e:
            if e.code == "DUPLICATE_CLAIM" and e.details:
                created.append({"source_ref": cand.source_ref, "activity_uid": uid, "claim_id": e.details["claim_id"], "outcome": "EXISTING"})
            else:
                skipped.append({"source_ref": cand.source_ref, "activity_ref": cand.activity_ref, "reasons": [e.code]})
    n_new = sum(1 for x in created if x["outcome"] == "CREATED")
    status = "EXTRACTED" if (created or skipped) else "NO_CLAIMS"
    if create_claims:
        with tx(actor.user_id) as c:
            c.execute("update source_documents set extraction_status = %s, extraction_method = %s, extraction_error = null, claims_extracted = %s, page_count = %s "
                      "where project_id = %s and document_id = %s", (status, result.method, sum(1 for x in created if x["claim_id"]), result.page_count, actor.project_id, document_id))
            audit.log(c, project_id=actor.project_id, actor_id=actor.user_id, role=SE, action="EXTRACTION_COMPLETED", entity_type="SOURCE_DOCUMENT", entity_id=document_id,
                      after={"method": result.method, "claims_created": n_new, "skipped": len(skipped)})
    return {"document_id": document_id, "status": status, "method": result.method, "page_count": result.page_count, "preview_only": not create_claims,
            "claims": created, "skipped": skipped, "counts": {"created": n_new, "existing": sum(1 for x in created if x["outcome"] == "EXISTING"), "skipped": len(skipped)},
            "note": "Extracted claims are proposals; they affect progress only after a Supervisor approves them."}
