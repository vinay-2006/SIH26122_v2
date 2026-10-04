"""Multi-file intake (the original batch upload) on v2 data: one upload batch -> many files -> many claims -> many activity matches.

    1. extract   per file, isolated (a failing file never fails the batch); the ORIGINAL strategies: structured sheets / XER without an LLM, text / PDF / images through the batch
                 extractor with the deterministic rules fallback
    2. normalise each extracted item becomes a v2 claim with its own text, a source reference to its file and field provenance (matched automatically at filing: pass 1)
    3. match     claims matched with high confidence vote for their stage per file; the rest are re-matched with that batch context as a small, explained prior (pass 2)
    4. de-dupe   a claim is identified by (activity, date, kind, value); an identical claim reported again by another file or an earlier batch of the SAME engineer is not
                 kept twice: the duplicate is WITHDRAWN (v2 claims are append-only; audited) and its file becomes an extra source of the surviving claim
    5. check     matched claims run through the original deterministic checks and arrive in the supervisor review queue
Nothing is approved here; a Supervisor still decides. Unmatched claims and claims waiting for a clarification stay visible in the batch report."""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, UploadFile

from .. import audit, permissions as P
from ..domain import claims as dc
from ..domain.common import actor_tx, active_version
from ..errors import ApiError
from ..matching import adapter, service as matching
from ..services import documents as docsvc
from . import shapes
from .claims_api import candidates_for, load_claims, submit_extracted, _claim_text
from .checks_api import run_check
from .context import Ctx, legacy_ctx, path_ctx, require_version

from .uploads import IMAGE_EXTENSIONS, refusal  # noqa: E402

logger = logging.getLogger(__name__)
router = APIRouter(tags=["legacy-contract: batches"])
MAX_FILES = 25
CONFIDENT = 0.75              # a pass-1 match at or above this votes for its stage in the batch context
_STRUCTURED_EXT = {".csv", ".xlsx", ".xls", ".xer"}
MAX_BYTES = 25 * 1024 * 1024


def _extract_file(filename: str, contents: bytes):
    """(drafts, legacy_channel, method). method: STRUCTURED | LLM | RULES_FALLBACK. Raises the original FileParseError / UnsupportedFileError."""
    from .uploads import build_drafts as _build_claim_drafts
    from backend.shared.llm_extraction import LLMExtractionError
    from backend.shared.rule_extraction import force_rules, track_rules
    ext = Path(filename or "").suffix.lower()
    try:
        with track_rules() as tracked:
            drafts, _doc_type, channel = _build_claim_drafts(filename, contents)
        method = "STRUCTURED" if ext in _STRUCTURED_EXT and not tracked.used else ("RULES_FALLBACK" if tracked.used else "LLM")
        return drafts, channel, method
    except LLMExtractionError as e:
        logger.warning("LLM extraction unavailable for %s (%s); using the deterministic rules extractor", filename, e)
        with force_rules():
            drafts, _doc_type, channel = _build_claim_drafts(filename, contents)
        return drafts, channel, "RULES_FALLBACK"


def _v2_channel(legacy_channel, ext: str) -> str:
    v = getattr(legacy_channel, "value", str(legacy_channel))
    return {"SCANNED_OCR": "IMAGE", "SCHEDULE_EXPORT": "API"}.get(v) or {".txt": "TXT", ".pdf": "PDF", ".csv": "CSV", ".xlsx": "XLSX", ".xls": "XLSX"}.get(ext, "TXT")


@router.post("/api/v1/claims/batch")
def create_batch(files: List[UploadFile] = File(...), schedule_id: Optional[str] = Form(None), notes: Optional[str] = Form(None),
                 ctx: Ctx = Depends(legacy_ctx(P.SUBMIT_CLAIM, writable=True))):
    require_version(ctx)
    from backend.routers.intake import FileParseError, UnsupportedFileError
    from backend.shared.llm_extraction import LLMExtractionError
    from backend.shared.match_signals import BatchContext, claim_fingerprint
    uploads: List[Tuple[str, bytes]] = []
    for f in files:
        data = f.file.read()
        if len(data) > MAX_BYTES:
            raise ApiError(413, "FILE_TOO_LARGE", f"'{f.filename}' exceeds the {MAX_BYTES // (1024 * 1024)} MB limit.")
        uploads.append((Path(f.filename or "upload").name, data))
    if not uploads:
        raise ApiError(422, "NO_FILES", "Upload at least one file.")
    if len(uploads) > MAX_FILES:
        raise ApiError(422, "TOO_MANY_FILES", f"A batch can contain at most {MAX_FILES} files.")

    with actor_tx(ctx.actor, write=True) as c:
        batch_id = c.execute("insert into upload_batches (project_id, uploaded_by, status, file_count, notes) values (%s,%s,'PROCESSING',%s,%s) returning batch_id",
                             (ctx.project_id, ctx.user.id, len(uploads), notes)).fetchone()["batch_id"]
    errors: Dict[str, str] = {}
    rejected: List[Dict[str, Any]] = []        # files that could not even be stored (wrong content for the name, unreadable ...): listed in the report, never silently dropped
    claim_events: List[Dict[str, Any]] = []
    seen: Dict[bytes, str] = {}
    import hashlib
    for filename, contents in uploads:
        status, method, error, drafts, channel = "EXTRACTED", None, None, [], "TXT"
        ext = Path(filename).suffix.lower()
        h = hashlib.sha256(contents).digest()
        if not contents:
            status, error = "FAILED", "Uploaded file is empty."
        elif h in seen:
            status, error = "EMPTY", f"Identical to '{seen[h]}' already in this batch; its claims are not duplicated."
        else:
            seen[h] = filename
            try:
                drafts, legacy_channel, method = _extract_file(filename, contents)
                channel = _v2_channel(legacy_channel, ext)
                if not drafts:
                    status, error = "EMPTY", "No claim or progress information found in this file."
            except (UnsupportedFileError, FileParseError) as e:
                status, error = "FAILED", str(e)
            except LLMExtractionError as e:
                status, error = "FAILED", f"Claim extraction failed: {e}"
            except Exception as e:                                  # an unreadable file never fails the batch
                logger.exception("batch intake: extraction failed for %s", filename)
                status, error = "FAILED", f"Could not read the file: {e}"
        doc_id = None
        if contents and not refusal(filename):         # a baseline schedule or legacy Office file is reported with its reason and not stored
            kind = "EVIDENCE" if ext in IMAGE_EXTENSIONS else "DAILY_REPORT"
            try:
                doc_id = docsvc.upload_document(ctx.user, ctx.project_id, ctx.access.role, kind, filename, contents, batch_id=batch_id)["document_id"]
            except ApiError as e:
                if e.code == "DUPLICATE_UPLOAD":
                    status, error, drafts = "EMPTY", "This exact file was already uploaded to the project earlier; its claims are not duplicated.", []
                else:
                    status, error, drafts = "FAILED", e.message, []
                doc_id = None
        if doc_id is None and (status == "FAILED" or error):
            rejected.append({"file_name": filename, "error": error or "The file could not be stored", "extraction_status": status})
        created = 0
        multi = len(drafts) > 1
        for i, d in enumerate(drafts, 1):
            try:
                res = submit_extracted(ctx, d.extracted, _claim_text(d, multi), channel=channel, document_id=doc_id, batch_id=batch_id, evidence_ids=[doc_id],
                                       schedule=(method == "STRUCTURED" and ext == ".xer"), source_snippet=d.raw_text[:4000], cell_ref=f"item {i}")
                claim_events.append({"event_id": res["claim_id"], "document_id": doc_id, "file_name": filename, "clarification": "PENDING" if res["status"] == "REPORTED" else "NONE"})
                created += 1
            except ApiError as e:
                if e.code != "DUPLICATE_CLAIM":
                    errors[filename] = e.message
        if doc_id is not None:
            with actor_tx(ctx.actor, write=True) as c:
                c.execute("update source_documents set extraction_status = %s, extraction_method = %s, extraction_error = %s, claims_extracted = %s where project_id = %s and document_id = %s",
                          (status, method, error, created, ctx.project_id, doc_id))

    # ---------- 3. pass 2: re-match the claims that were not confidently matched, with the batch context as a small explained prior
    ver_id = ctx.active_version_id          # claims are always filed against (and matched in) the ACTIVE schedule
    with actor_tx(ctx.actor, readonly=True) as c:
        acts = adapter.load_activities(c, ctx.project_id, ver_id)
        rows = {r["event_id"]: r for r in c.execute("select event_id, status, matched_activity_uid from execution_events where event_id = any(%s)", ([e["event_id"] for e in claim_events],)).fetchall()}
        conf = {r["event_id"]: float(r["composite_confidence"]) for r in c.execute("select event_id, composite_confidence from candidate_matches where event_id = any(%s) and rank_order = 1",
                                                                                   ([e["event_id"] for e in claim_events],)).fetchall()}
    stage_of = {a["activity_uid"]: a["stage_id"] for a in acts}
    ext_of = {a["activity_uid"]: a["activity_id"] for a in acts}
    matchable = [e for e in claim_events if e["clarification"] != "PENDING"]

    def confident(ev) -> bool:
        r = rows.get(ev["event_id"])
        return bool(r and r["matched_activity_uid"] and conf.get(ev["event_id"], 0) >= CONFIDENT)

    per_file: Dict[Any, BatchContext] = {}
    whole = BatchContext()
    for ev in matchable:
        if confident(ev):
            uid = rows[ev["event_id"]]["matched_activity_uid"]
            per_file.setdefault(ev["document_id"], BatchContext()).vote(stage_of.get(uid), ext_of.get(uid))
            whole.vote(stage_of.get(uid), ext_of.get(uid), 0.5)
    for ev in matchable:
        if confident(ev) or rows.get(ev["event_id"]) is None:
            continue
        bc = BatchContext()
        for stage, w in per_file.get(ev["document_id"], BatchContext()).stage_votes.items():
            bc.vote(stage, None, w)
        for stage, w in whole.stage_votes.items():
            bc.vote(stage, None, w)
        if bc:
            try:
                with actor_tx(ctx.actor, write=True) as c:
                    matching.auto_match(c, ctx.actor, uuid.UUID(str(ev["event_id"])), active_version(c, ctx.project_id), overwrite_pick=True, batch_context=bc)
            except Exception as e:                                  # one claim failing to re-match must not stop the others
                logger.exception("batch intake: pass-2 matching failed for %s", ev["event_id"])
                errors[str(ev["event_id"])] = str(e)

    # ---------- 4. de-duplicate identical claims (activity, date, kind, value) of this engineer
    merged = _deduplicate(ctx, claim_events, claim_fingerprint)

    # ---------- 5. validate matched claims into the review queue
    for ev in claim_events:
        if ev["event_id"] in merged:
            continue
        with actor_tx(ctx.actor, readonly=True) as c:
            st = c.execute("select status, matched_activity_uid from execution_events where event_id = %s", (ev["event_id"],)).fetchone()
        if st and st["matched_activity_uid"] is not None and st["status"] in ("MATCHED", "EXTRACTED"):
            try:
                run_check(ctx, ev["event_id"])
            except Exception as e:
                logger.warning("batch intake: validation step failed for %s: %s", ev["event_id"], e)
                errors[str(ev["event_id"])] = f"validation: {e}"

    # ---------- finalise
    with actor_tx(ctx.actor, write=True) as c:
        docs = c.execute("select extraction_status from source_documents where batch_id = %s", (batch_id,)).fetchall()
        n_claims = c.execute("select count(*) n from execution_events where batch_id = %s and status <> 'WITHDRAWN'", (batch_id,)).fetchone()["n"]
        failed = sum(1 for d in docs if d["extraction_status"] == "FAILED") + sum(1 for r in rejected if r["extraction_status"] == "FAILED")
        total = len(docs) + len(rejected)
        status = "FAILED" if total and failed == total else ("PARTIAL" if failed or errors else "COMPLETED")
        c.execute("update upload_batches set status = %s, claim_count = %s, merged_count = %s, completed_at = now() where project_id = %s and batch_id = %s",
                  (status, n_claims, len(merged), ctx.project_id, batch_id))
        audit.log(c, project_id=ctx.project_id, actor_id=ctx.user.id, role=ctx.access.role, action="BATCH_INTAKE", entity_type="UPLOAD_BATCH", entity_id=batch_id,
                  after={"status": status, "files": total, "claims": n_claims, "merged": len(merged), "failed_files": failed, "rejected_files": rejected})
    return _report(ctx, batch_id, errors)


def _deduplicate(ctx: Ctx, events: List[Dict[str, Any]], claim_fingerprint) -> Dict[Any, Any]:
    """{duplicate claim id: surviving claim id}; the duplicate is withdrawn and its file becomes a source of the survivor"""
    merged: Dict[Any, Any] = {}
    keeper: Dict[str, Any] = {}
    ids = [e["event_id"] for e in events]
    with actor_tx(ctx.actor, readonly=True) as c:
        rows = {r["event_id"]: r for r in c.execute(
            "select e.event_id, e.event_date, e.claim_mode, e.event_type, e.claimed_pct, e.raw_claim_text, ba.external_activity_id as ext, "
            "(select reported_qty from claim_quantities q where q.event_id = e.event_id and (select count(*) from claim_quantities q2 where q2.event_id = e.event_id) = 1) as qty, "
            "(select reported_uom from claim_quantities q where q.event_id = e.event_id and (select count(*) from claim_quantities q2 where q2.event_id = e.event_id) = 1) as uom "
            "from execution_events e join baseline_activities ba on ba.version_id = e.filed_in_version_id and ba.activity_uid = e.matched_activity_uid where e.event_id = any(%s)", (ids,)).fetchall()}
        earlier = c.execute(
            "select e.event_id, e.event_date, e.claim_mode, e.event_type, e.claimed_pct, ba.external_activity_id as ext, "
            "(select reported_qty from claim_quantities q where q.event_id = e.event_id and (select count(*) from claim_quantities q2 where q2.event_id = e.event_id) = 1) as qty, "
            "(select reported_uom from claim_quantities q where q.event_id = e.event_id and (select count(*) from claim_quantities q2 where q2.event_id = e.event_id) = 1) as uom "
            "from execution_events e join baseline_activities ba on ba.version_id = e.filed_in_version_id and ba.activity_uid = e.matched_activity_uid "
            "where e.project_id = %s and e.filed_by = %s and e.filed_in_version_id = %s and e.status not in ('REJECTED','WITHDRAWN') and not (e.event_id = any(%s))",
            (ctx.project_id, ctx.user.id, ctx.active_version_id, ids)).fetchall()

    def fp(r):
        mode = "CUMULATIVE_PCT" if r["claim_mode"] == "CUMULATIVE_PCT" else "INCREMENTAL_QUANTITY"
        et = {"PROGRESS": "PROGRESS_UPDATE", "START": "ACTUAL_START", "FINISH": "ACTUAL_FINISH"}.get(r["event_type"], "DELAY")
        return claim_fingerprint(project_id=ctx.project_id, schedule_id=str(ctx.active_version_id), activity_id=r["ext"], event_date=r["event_date"], claim_mode=mode,
                                 claimed_pct=float(r["claimed_pct"]) if r["claimed_pct"] is not None else None,
                                 claimed_quantity=float(r["qty"]) if r["qty"] is not None else None, claimed_uom=r["uom"], event_type=et)

    prior = {fp(r): r["event_id"] for r in earlier}
    for ev in events:
        r = rows.get(ev["event_id"])
        if r is None:
            continue                                                    # unmatched / pending claims are never merged
        key = fp(r)
        survivor = keeper.get(key) or prior.get(key)
        if survivor is None:
            keeper[key] = ev["event_id"]
            continue
        try:
            with actor_tx(ctx.actor, write=True) as c:
                c.execute("insert into source_references (project_id, event_id, document_id, raw_snippet) values (%s,%s,%s,%s)",
                          (ctx.project_id, survivor, ev["document_id"], r["raw_claim_text"][:4000]))
            dc.withdraw_claim(ctx.actor, ev["event_id"], f"Merged: the same claim was already reported ({str(survivor)[:8]})")
            merged[ev["event_id"]] = survivor
        except ApiError as e:
            logger.warning("batch de-duplication skipped for %s: %s", ev["event_id"], e.message)
    return merged


def _report(ctx: Ctx, batch_id, errors: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """the batch as persisted: files, the claims they produced (the caller's own), how each matched, and which files back each claim"""
    errors = errors or {}
    with actor_tx(ctx.actor, readonly=True) as c:
        b = c.execute("select * from upload_batches where project_id = %s and batch_id = %s", (ctx.project_id, batch_id)).fetchone()
        if b is None or (ctx.access.role != "SUPERVISOR" and b["uploaded_by"] != ctx.user.id):
            raise ApiError(404, "RESOURCE_NOT_FOUND", "Upload batch not found.")
        files = c.execute("select * from source_documents where batch_id = %s order by uploaded_at, file_name", (batch_id,)).fetchall()
        doc_ids = [f["document_id"] for f in files]
        claims = load_claims(c, ctx, "(e.document_id = any(%(docs)s) or e.event_id in (select event_id from source_references where document_id = any(%(docs)s))) and e.status <> 'WITHDRAWN'",
                             {"docs": doc_ids}, order="e.created_at, e.event_id", limit=2000)
        ev_ids = [uuid.UUID(x["event_id"]) for x in claims]
        refs = c.execute("select sr.event_id, sr.document_id, d.file_name, sr.raw_snippet from source_references sr left join source_documents d on d.document_id = sr.document_id "
                         "where sr.event_id = any(%s) order by sr.reference_id", (ev_ids,)).fetchall() if ev_ids else []
        cands = {x["event_id"]: candidates_for(c, ctx, uuid.UUID(x["event_id"])) for x in claims}
        stages = {r["external_activity_id"]: r["stage_name"] for r in c.execute(
            "select ba.external_activity_id, (select st.wbs_name from schedule_wbs st where st.version_id = ba.version_id and st.node_type = 'STAGE' and sw.wbs_path like st.wbs_path || '%%' "
            "order by length(st.wbs_path) desc limit 1) as stage_name from baseline_activities ba join schedule_wbs sw on sw.wbs_id = ba.wbs_id where ba.version_id = %s", (ctx.version_id,)).fetchall()} if ctx.version_id else {}
        names = {r["external_activity_id"]: r["activity_name"] for r in c.execute("select external_activity_id, activity_name from baseline_activities where version_id = %s", (ctx.version_id,)).fetchall()}
    sources: Dict[str, list] = {}
    for r in refs:
        sources.setdefault(str(r["event_id"]), []).append({"document_id": str(r["document_id"]) if r["document_id"] else None, "file_name": r["file_name"], "snippet": r["raw_snippet"]})
    in_batch = {str(d) for d in doc_ids}
    claim_rows, per_file_created, per_file_merged = [], {}, {}
    for cl in claims:
        eid = cl["event_id"]
        srcs = sources.get(eid, [])
        cs = cands.get(eid, [])
        top = cs[0] if cs else None
        row = {"event_id": eid, "document_id": cl["document_id"], "event_date": cl["event_date"], "raw_claim_text": cl["raw_claim_text"], "status": cl["status"], "claimed_pct": cl["claimed_pct"],
               "claimed_quantity": cl["claimed_quantity"], "claimed_uom": cl["claimed_uom"], "claim_mode": cl["claim_mode"], "event_type": cl["event_type"], "discipline": cl["discipline"],
               "matched_activity_id": cl["matched_activity_id"], "reported_activity_id": cl["reported_activity_id"], "activity_name": names.get(cl["matched_activity_id"]),
               "stage_name": stages.get(cl["matched_activity_id"]), "clarification_status": cl["clarification_status"], "clarification_question": cl["clarification_question"],
               "candidates": [{"activity_id": x["activity_id"], "activity_name": x["activity_name"], "rank": x["rank_order"], "tier": x["match_tier"], "confidence": x["composite_confidence"],
                               "supporting": x["supporting_signals"], "disqualifying": x["disqualifying_signals"]} for x in cs],
               "match_confidence": top["composite_confidence"] if top else None, "match_tier": top["match_tier"] if top else None, "sources": srcs,
               "file_names": sorted({s["file_name"] for s in srcs if s["file_name"]}), "reported_by_multiple_files": len({s["document_id"] for s in srcs}) > 1,
               "created_in_this_batch": cl["document_id"] in in_batch, "error": errors.get(eid)}
        claim_rows.append(row)
        if row["created_in_this_batch"]:
            per_file_created.setdefault(cl["document_id"], []).append(eid)
        for s in srcs:
            if s["document_id"] in in_batch and s["document_id"] != cl["document_id"]:
                per_file_merged.setdefault(s["document_id"], []).append(eid)
    with actor_tx(ctx.actor, readonly=True) as c:
        a = c.execute("select after_state from audit_logs where entity_type = 'UPLOAD_BATCH' and entity_id = %s and action = 'BATCH_INTAKE' order by log_id desc limit 1", (str(batch_id),)).fetchone()
    import json as _json
    try:
        rej = (_json.loads(a["after_state"]) if a and isinstance(a["after_state"], str) else (a["after_state"] if a else {})).get("rejected_files", [])
    except Exception:
        rej = []
    file_rows = [{"document_id": str(f["document_id"]), "file_name": f["file_name"], "document_type": {"SITE_REPORT": "SCANNED_DIARY", "DAILY_REPORT": "DPR"}.get(f["kind"], f["kind"]),
                  "extraction_status": f["extraction_status"], "extraction_method": f["extraction_method"], "error": f["extraction_error"], "claims_extracted": f["claims_extracted"],
                  "claim_ids": per_file_created.get(str(f["document_id"]), []), "merged_into_claim_ids": per_file_merged.get(str(f["document_id"]), [])} for f in files]
    file_rows += [{"document_id": f"rejected-{i}", "file_name": r["file_name"], "document_type": None, "extraction_status": r["extraction_status"], "extraction_method": None,
                   "error": r["error"], "claims_extracted": 0, "claim_ids": [], "merged_into_claim_ids": []} for i, r in enumerate(rej, 1)]
    by_act: Dict[str, Dict[str, Any]] = {}
    for cr in claim_rows:
        a = cr["matched_activity_id"]
        if a:
            g = by_act.setdefault(a, {"activity_id": a, "activity_name": cr["activity_name"], "stage_name": cr["stage_name"], "claim_ids": [], "file_names": set()})
            g["claim_ids"].append(cr["event_id"])
            g["file_names"].update(cr["file_names"])
    return {"batch_id": str(b["batch_id"]), "project_id": str(b["project_id"]), "schedule_id": str(ctx.version_id), "status": b["status"], "created_at": shapes.iso(b["created_at"]),
            "completed_at": shapes.iso(b["completed_at"]), "uploaded_by": str(b["uploaded_by"]), "notes": b["notes"], "file_count": b["file_count"], "claim_count": len(claim_rows),
            "merged_count": b["merged_count"], "matched_count": sum(1 for x in claim_rows if x["matched_activity_id"]),
            "unmatched_count": sum(1 for x in claim_rows if not x["matched_activity_id"] and x["clarification_status"] != "PENDING"),
            "needs_clarification_count": sum(1 for x in claim_rows if x["clarification_status"] == "PENDING"), "files": file_rows, "claims": claim_rows,
            "activities": [{**g, "file_names": sorted(g["file_names"])} for g in sorted(by_act.values(), key=lambda g: g["activity_id"])]}


@router.get("/api/v1/projects/{project_id}/upload-batches")
def list_batches(mine: bool = False, limit: int = 50, ctx: Ctx = Depends(path_ctx(P.SUBMIT_CLAIM))):
    with actor_tx(ctx.actor, readonly=True) as c:
        rows = c.execute("select b.batch_id, b.status, b.file_count, b.claim_count, b.merged_count, b.created_at, b.completed_at, b.uploaded_by, p.full_name as uploaded_by_name "
                         "from upload_batches b left join profiles p on p.id = b.uploaded_by where b.project_id = %s and b.uploaded_by = %s order by b.created_at desc limit %s",
                         (ctx.project_id, ctx.user.id, max(1, min(limit, 200)))).fetchall()
    return [{"batch_id": str(r["batch_id"]), "status": r["status"], "file_count": r["file_count"], "claim_count": r["claim_count"], "merged_count": r["merged_count"],
             "created_at": shapes.iso(r["created_at"]), "completed_at": shapes.iso(r["completed_at"]), "uploaded_by_name": r["uploaded_by_name"]} for r in rows]


@router.get("/api/v1/projects/{project_id}/upload-batches/{batch_id}")
def get_batch(batch_id: str, ctx: Ctx = Depends(path_ctx(P.SUBMIT_CLAIM))):
    try:
        bid = uuid.UUID(batch_id)
    except ValueError:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "Upload batch not found.")
    return _report(ctx, bid)
