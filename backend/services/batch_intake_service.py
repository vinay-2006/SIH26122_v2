"""
Multi-file intake (SETUAI V7 prototype): one upload batch -> many files -> many claims -> many activity matches.

    Upload Batch
       |- File 1 -> claim -> Activity A      (a file reports several activities: one claim each)
       |         -> claim -> Activity B
       |- File 2 -> claim -> Activity C
       '- File 3 -> claim -> Activity A      (same activity, same value as File 1: ONE claim, two sources)

Pipeline (each stage uses what already exists; nothing here approves anything, a supervisor still decides):
  1. extract   per file, isolated: a failing file never fails the batch. Structured sheets / XER need no LLM; text, PDF and
               images use the LLM extractor and, when it is unavailable, the deterministic rules extractor (one claim per
               reported item).
  2. normalise each extracted item becomes an execution event with its own claim text (never the whole document), a source
               reference to the file it came from, and field provenance.
  3. match     the existing matching pipeline (run_claim_match) pass 1; claims matched with high confidence vote for their
               stage per file; pass 2 re-matches the rest with that batch context as a small, explained prior.
  4. de-dupe   a claim is identified by (activity, date, kind, value). An identical claim reported by a second file (or an
               earlier batch) is not created again: the second file becomes an additional source of the existing claim.
  5. validate  matched claims run through the existing deterministic checks and arrive in the supervisor review queue.
Unmatched claims and claims waiting for a clarification stay visible in the batch report; nothing is silently dropped.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from backend.context.event import EventContext
from backend.context.project import ProjectContext
from backend.shared.audit import append_audit_record
from backend.shared.db import get_connection
from backend.shared.llm_extraction import (
    LLMExtractionError,
    check_missing_required_fields,
    generate_clarification_question,
)
from backend.shared.match_signals import BatchContext, claim_fingerprint
from backend.shared.rule_extraction import force_rules, track_rules

logger = logging.getLogger(__name__)

MAX_FILES = 25
CONFIDENT = 0.75   # a pass-1 match at or above this votes for its stage in the batch context
_STRUCTURED_EXT = {".csv", ".xlsx", ".xls", ".xer"}


# ------------------------------------------------------------------------------------------------- extraction
def _extract_file(filename: str, contents: bytes):
    """(drafts, document_type, channel, method). method: STRUCTURED | LLM | RULES_FALLBACK. Raises FileParseError etc."""
    from backend.routers.intake import _build_claim_drafts  # local import: intake imports heavy optional deps

    ext = Path(filename or "").suffix.lower()
    try:
        with track_rules() as tracked:
            drafts, doc_type, channel = _build_claim_drafts(filename, contents)
        # the rules extractor may already be the configured fallback (EXTRACTION_FALLBACK=rules): report what really ran
        method = "STRUCTURED" if ext in _STRUCTURED_EXT and not tracked.used else ("RULES_FALLBACK" if tracked.used else "LLM")
        return drafts, doc_type, channel, method
    except LLMExtractionError as e:
        logger.warning("LLM extraction unavailable for %s (%s); using the deterministic rules extractor", filename, e)
        with force_rules():
            drafts, doc_type, channel = _build_claim_drafts(filename, contents)
        return drafts, doc_type, channel, "RULES_FALLBACK"


def _claim_text(draft, multi: bool) -> str:
    """The text matching should see for ONE claim: its own line/summary, not the whole document it came from."""
    action = (draft.extracted.action or "").strip()
    if action and (multi or len(draft.raw_text) > 400):
        return action
    return draft.raw_text


# ---------------------------------------------------------------------------------------------------- service
class BatchIntakeService:
    @classmethod
    def process(
        cls,
        project_context: ProjectContext,
        schedule_id: str,
        uploads: List[Tuple[str, bytes]],
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        from backend.routers.intake import (
            FileParseError,
            UnsupportedFileError,
            _build_ai_provenance,
            _derive_attribution,
            _insert_execution_event,
        )
        from backend.routers.matching import run_claim_match

        if not uploads:
            raise HTTPException(status_code=422, detail="Upload at least one file.")
        if len(uploads) > MAX_FILES:
            raise HTTPException(status_code=422, detail=f"A batch can contain at most {MAX_FILES} files.")

        user = project_context.user
        project_id = str(project_context.project_id)
        batch_id = str(uuid.uuid4())
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO upload_batches (batch_id, project_id, schedule_id, uploaded_by, status, file_count, notes) "
                "VALUES (%s, %s, %s, %s, 'PROCESSING', %s, %s)",
                (batch_id, project_id, schedule_id, user.id, len(uploads), notes),
            )

        # ---------- 1+2. extract each file in isolation, create one claim per extracted item
        seen_hashes: Dict[str, str] = {}
        claim_events: List[Dict[str, Any]] = []   # in upload order
        for filename, contents in uploads:
            document_id = str(uuid.uuid4())
            file_hash = hashlib.sha256(contents).hexdigest()
            status, method, error, doc_type, drafts = "EXTRACTED", None, None, "DPR", []
            channel = "FILE_UPLOAD"
            if not contents:
                status, error = "FAILED", "Uploaded file is empty."
            elif file_hash in seen_hashes:
                status, error = "EMPTY", f"Identical to '{seen_hashes[file_hash]}' already in this batch; its claims are not duplicated."
            else:
                seen_hashes[file_hash] = filename
                try:
                    drafts, doc_type, channel_enum, method = _extract_file(filename, contents)
                    channel = channel_enum.value if hasattr(channel_enum, "value") else str(channel_enum)
                    if not drafts:
                        status, error = "EMPTY", "No claim or progress information found in this file."
                except UnsupportedFileError as e:
                    status, error = "FAILED", str(e)
                except FileParseError as e:
                    status, error = "FAILED", str(e)
                except LLMExtractionError as e:
                    status, error = "FAILED", f"Extraction failed: {e}"
                except Exception as e:  # a bad file must never take the batch down
                    logger.exception("batch intake: unexpected error extracting %s", filename)
                    status, error = "FAILED", f"Unexpected error while reading the file: {e}"

            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO source_documents (document_id, file_name, document_type, uploader_id, file_hash, project_id, "
                        "batch_id, extraction_status, extraction_method, extraction_error, claims_extracted) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (document_id, filename, doc_type, user.id, file_hash, project_id, batch_id, status, method, error, len(drafts)),
                    )
                    multi = len(drafts) > 1
                    for draft in drafts:
                        ex = draft.extracted
                        event_id = str(uuid.uuid4())
                        text = _claim_text(draft, multi)
                        missing = check_missing_required_fields(ex)
                        # a missing discipline is not a reason to bounce the claim back: it is derived from the matched activity
                        blocking = [m for m in missing if m != "discipline"]
                        if blocking:
                            question = generate_clarification_question(text, blocking, ex.language_detected)
                            clar_status, clar_q = "PENDING", question
                        else:
                            clar_status, clar_q = "NONE", None
                        # A discipline guessed from keywords ("pipe rack STEEL" -> PIPING) is the least reliable thing the rules
                        # extractor produces and a discipline conflict is a hard cap in matching, so it is withheld from matching
                        # and filled from the matched activity afterwards. The LLM / structured sources keep theirs.
                        discipline = None if method == "RULES_FALLBACK" else (ex.discipline.value if ex.discipline else None)
                        provenance = _build_ai_provenance(ex)
                        if discipline is None:
                            provenance.pop("discipline", None)
                        _insert_execution_event(
                            cur, event_id=event_id, document_id=document_id, schedule_id=schedule_id,
                            event_date_val=ex.event_date or date.today(), raw_claim_text=text, input_channel=channel,
                            language_detected=ex.language_detected, reported_activity_id=ex.reported_activity_id,
                            discipline=discipline, action=ex.action,
                            event_type=ex.event_type.value if ex.event_type else None, claim_mode=ex.claim_mode.value,
                            asset_tag=ex.asset_tag, location=ex.location, claimed_quantity=ex.claimed_quantity,
                            claimed_uom=ex.claimed_uom, claimed_pct=ex.claimed_pct,
                            delay_reason=ex.delay_reason.value if ex.delay_reason else None, supervisor_id=user.id,
                            clarification_status=clar_status, clarification_question=clar_q, clarification_answer=None,
                            field_provenance=provenance, project_id=project_context.project_id,
                            **_derive_attribution(cur, project_context.project_id, schedule_id, ex.reported_activity_id),
                        )
                        cur.execute(
                            "INSERT INTO source_references (reference_id, event_id, file_name, raw_snippet, document_id) "
                            "VALUES (%s, %s, %s, %s, %s)",
                            (str(uuid.uuid4()), event_id, filename, draft.raw_text[:2000], document_id),
                        )
                        claim_events.append({"event_id": event_id, "document_id": document_id, "file_name": filename,
                                             "clarification": clar_status})

        # ---------- 3. match: pass 1, batch context from confident matches, pass 2 for the rest
        results: Dict[str, Dict[str, Any]] = {}
        errors: Dict[str, str] = {}
        stage_of = cls._activity_stages(project_id, schedule_id)

        def run(ev, ctx=None):
            try:
                ec = EventContext(project_context=project_context, event_id=ev["event_id"], schedule_id=schedule_id)
                results[ev["event_id"]] = run_claim_match(ev["event_id"], "BATCH_MATCH", ec, batch_context=ctx)
            except Exception as e:  # one claim failing to match must not stop the others
                logger.exception("batch intake: matching failed for %s", ev["event_id"])
                errors[ev["event_id"]] = str(e)

        matchable = [e for e in claim_events if e["clarification"] != "PENDING"]
        for ev in matchable:
            run(ev)
        per_file: Dict[str, BatchContext] = {}
        whole = BatchContext()
        for ev in matchable:
            r = results.get(ev["event_id"])
            if r and r.get("status") == "MATCHED" and r.get("matched_activity_id") and (r.get("composite_confidence") or 0) >= CONFIDENT:
                stage = stage_of.get(r["matched_activity_id"])
                per_file.setdefault(ev["document_id"], BatchContext()).vote(stage, r["matched_activity_id"])
                whole.vote(stage, r["matched_activity_id"], 0.5)
        for ev in matchable:
            r = results.get(ev["event_id"])
            if r is None or (r.get("status") == "MATCHED" and (r.get("composite_confidence") or 0) >= CONFIDENT):
                continue
            ctx = BatchContext()
            for stage, w in per_file.get(ev["document_id"], BatchContext()).stage_votes.items():
                ctx.vote(stage, None, w)
            for stage, w in whole.stage_votes.items():
                ctx.vote(stage, None, w)
            if ctx:
                run(ev, ctx)

        # ---------- 4. de-duplicate identical claims (activity, date, kind, value) across files and earlier batches
        merged = cls._deduplicate(project_id, schedule_id, claim_events, results)

        # ---------- 5. derive discipline, validate matched claims into the review queue
        for ev in claim_events:
            if ev["event_id"] in merged:
                continue
            r = results.get(ev["event_id"])
            if r and r.get("status") == "MATCHED":
                cls._fill_discipline(ev["event_id"], r.get("matched_activity_id"), schedule_id, project_id)
                try:
                    from backend.routers.checks import check_claim

                    check_claim(ev["event_id"], EventContext(project_context=project_context, event_id=ev["event_id"], schedule_id=schedule_id))
                except Exception as e:
                    logger.warning("batch intake: validation step failed for %s: %s", ev["event_id"], e)
                    errors[ev["event_id"]] = f"validation: {e}"

        report = cls._finalise(batch_id, project_context, schedule_id, len(merged), errors)
        return report

    # ---------------------------------------------------------------------------------------------- helpers
    @staticmethod
    def _activity_stages(project_id: str, schedule_id: str) -> Dict[str, Optional[str]]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT activity_id, stage_id FROM schedule_activities WHERE project_id = %s AND schedule_id = %s",
                (project_id, schedule_id),
            ).fetchall()
        return {r["activity_id"]: (str(r["stage_id"]) if r["stage_id"] else None) for r in rows}

    @staticmethod
    def _fill_discipline(event_id: str, activity_id: Optional[str], schedule_id: str, project_id: str) -> None:
        if not activity_id:
            return
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE execution_events ee
                   SET discipline = sa.discipline,
                       field_provenance = COALESCE(ee.field_provenance, '{}'::jsonb) || '{"discipline": "SCHEDULE_AUTO_FILLED"}'::jsonb
                  FROM schedule_activities sa
                 WHERE ee.event_id = %s AND ee.discipline IS NULL
                   AND sa.schedule_id = %s AND sa.project_id = %s AND sa.activity_id = %s
                """,
                (event_id, schedule_id, project_id, activity_id),
            )

    @classmethod
    def _deduplicate(cls, project_id: str, schedule_id: str, events: List[Dict[str, Any]], results: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
        """Returns {duplicate_event_id: surviving_event_id}. Duplicates are removed; their file becomes a source of the survivor."""
        merged: Dict[str, str] = {}
        keeper_by_fp: Dict[str, str] = {}
        with get_connection() as conn:
            for ev in events:
                r = results.get(ev["event_id"])
                if not r or r.get("status") != "MATCHED" or not r.get("matched_activity_id"):
                    continue   # unmatched / split / pending claims are never merged
                row = conn.execute("SELECT * FROM execution_events WHERE event_id = %s", (ev["event_id"],)).fetchone()
                fp = claim_fingerprint(
                    project_id=project_id, schedule_id=schedule_id, activity_id=r["matched_activity_id"],
                    event_date=row["event_date"], claim_mode=row["claim_mode"], claimed_pct=row["claimed_pct"],
                    claimed_quantity=row["claimed_quantity"], claimed_uom=row["claimed_uom"], event_type=row["event_type"],
                )
                survivor = keeper_by_fp.get(fp)
                if survivor is None:
                    existing = conn.execute(
                        "SELECT event_id FROM execution_events WHERE project_id = %s AND schedule_id = %s AND claim_fingerprint = %s "
                        "AND status <> 'REJECTED' AND event_id <> %s",
                        (project_id, schedule_id, fp, ev["event_id"]),
                    ).fetchone()
                    survivor = existing["event_id"] if existing else None
                if survivor is None:
                    conn.execute("UPDATE execution_events SET claim_fingerprint = %s WHERE event_id = %s", (fp, ev["event_id"]))
                    keeper_by_fp[fp] = ev["event_id"]
                    continue
                snippet = conn.execute("SELECT raw_snippet FROM source_references WHERE event_id = %s LIMIT 1", (ev["event_id"],)).fetchone()
                conn.execute(
                    "INSERT INTO source_references (reference_id, event_id, file_name, raw_snippet, document_id) VALUES (%s, %s, %s, %s, %s)",
                    (str(uuid.uuid4()), survivor, ev["file_name"], (snippet or {}).get("raw_snippet") or row["raw_claim_text"], ev["document_id"]),
                )
                # the duplicate never became a claim: drop it and everything that hung off it (its file now backs the survivor)
                for table in ("candidate_matches", "claim_activity_splits", "claim_wbs_splits", "source_references"):
                    conn.execute(f"DELETE FROM {table} WHERE event_id = %s", (ev["event_id"],))
                conn.execute("DELETE FROM execution_events WHERE event_id = %s", (ev["event_id"],))
                merged[ev["event_id"]] = survivor
        return merged

    @classmethod
    def _finalise(cls, batch_id: str, context: ProjectContext, schedule_id: str, merged_count: int, errors: Dict[str, str]) -> Dict[str, Any]:
        with get_connection() as conn:
            docs = conn.execute("SELECT extraction_status FROM source_documents WHERE batch_id = %s", (batch_id,)).fetchall()
            n_claims = conn.execute(
                "SELECT count(*) AS n FROM execution_events ee JOIN source_documents sd ON sd.document_id = ee.document_id WHERE sd.batch_id = %s",
                (batch_id,),
            ).fetchone()["n"]
            failed = sum(1 for d in docs if d["extraction_status"] == "FAILED")
            status = "FAILED" if docs and failed == len(docs) else ("PARTIAL" if failed or errors else "COMPLETED")
            conn.execute(
                "UPDATE upload_batches SET status = %s, claim_count = %s, merged_count = %s, completed_at = now() WHERE batch_id = %s",
                (status, n_claims, merged_count, batch_id),
            )
            append_audit_record(
                conn, entity_type="UPLOAD_BATCH", entity_id=batch_id, action="BATCH_INTAKE", actor_id=str(context.user.id),
                before_state=None, after_state={"status": status, "files": len(docs), "claims": n_claims, "merged": merged_count, "failed_files": failed},
                project_id=context.project_id, schedule_id=schedule_id, role=context.role,
            )
        return cls.report(context, uuid.UUID(batch_id), errors)

    # ----------------------------------------------------------------------------------------------- reading
    @classmethod
    def report(cls, context: ProjectContext, batch_id: uuid.UUID, errors: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """The batch as persisted: files, the claims they produced, how each matched, and which files back each claim."""
        errors = errors or {}
        with get_connection() as conn:
            batch = conn.execute("SELECT * FROM upload_batches WHERE batch_id = %s AND project_id = %s", (batch_id, context.project_id)).fetchone()
            if batch is None:
                raise HTTPException(status_code=404, detail="Upload batch not found.")
            files = conn.execute("SELECT * FROM source_documents WHERE batch_id = %s ORDER BY uploaded_at, file_name", (batch_id,)).fetchall()
            doc_ids = [f["document_id"] for f in files]
            claims = conn.execute(
                """
                SELECT ee.event_id, ee.document_id, ee.event_date, ee.raw_claim_text, ee.status, ee.claimed_pct, ee.claimed_quantity,
                       ee.claimed_uom, ee.claim_mode, ee.event_type, ee.discipline, ee.matched_activity_id, ee.reported_activity_id,
                       ee.clarification_status, ee.clarification_question, ee.supervisor_id, sa.activity_name, st.stage_name,
                       (SELECT COALESCE(json_agg(json_build_object(
                                'activity_id', cm.activity_id, 'activity_name', a2.activity_name, 'rank', cm.rank_order,
                                'tier', cm.match_tier, 'confidence', cm.composite_confidence,
                                'supporting', cm.supporting_signals, 'disqualifying', cm.disqualifying_signals) ORDER BY cm.rank_order), '[]'::json)
                          FROM candidate_matches cm
                          LEFT JOIN schedule_activities a2 ON a2.schedule_id = cm.schedule_id AND a2.activity_id = cm.activity_id
                         WHERE cm.event_id = ee.event_id) AS candidates
                  FROM execution_events ee
                  LEFT JOIN schedule_activities sa ON sa.schedule_id = ee.schedule_id AND sa.activity_id = ee.matched_activity_id
                  LEFT JOIN stages st ON st.stage_id = sa.stage_id
                 WHERE ee.event_id IN (
                        SELECT ee2.event_id FROM execution_events ee2 WHERE ee2.document_id = ANY(%s)
                        UNION SELECT sr.event_id FROM source_references sr WHERE sr.document_id = ANY(%s))
                 ORDER BY ee.created_at, ee.event_id
                """,
                (doc_ids, doc_ids),
            ).fetchall()
            ev_ids = [c["event_id"] for c in claims]
            refs = conn.execute(
                "SELECT sr.event_id, sr.document_id, sr.file_name, sr.raw_snippet FROM source_references sr WHERE sr.event_id = ANY(%s) ORDER BY sr.reference_id",
                (ev_ids,),
            ).fetchall() if ev_ids else []

        sources: Dict[str, List[Dict[str, Any]]] = {}
        for r in refs:
            sources.setdefault(r["event_id"], []).append({"document_id": r["document_id"], "file_name": r["file_name"], "snippet": r["raw_snippet"]})
        in_batch = set(doc_ids)
        claim_rows: List[Dict[str, Any]] = []
        per_file_created: Dict[str, List[str]] = {}
        per_file_merged: Dict[str, List[str]] = {}
        for c in claims:
            d = dict(c)
            srcs = sources.get(d["event_id"], [])
            d["sources"] = srcs
            d["file_names"] = sorted({s["file_name"] for s in srcs if s["file_name"]})
            d["reported_by_multiple_files"] = len({s["document_id"] for s in srcs}) > 1
            d["created_in_this_batch"] = d["document_id"] in in_batch
            d["error"] = errors.get(d["event_id"])
            top = (d["candidates"] or [None])[0]
            d["match_confidence"] = top["confidence"] if top else None
            d["match_tier"] = top["tier"] if top else None
            claim_rows.append(d)
            if d["created_in_this_batch"]:
                per_file_created.setdefault(d["document_id"], []).append(d["event_id"])
            for s in srcs:
                if s["document_id"] in in_batch and s["document_id"] != d["document_id"]:
                    per_file_merged.setdefault(s["document_id"], []).append(d["event_id"])

        file_rows = []
        for f in files:
            file_rows.append({
                "document_id": f["document_id"], "file_name": f["file_name"], "document_type": f["document_type"],
                "extraction_status": f["extraction_status"], "extraction_method": f["extraction_method"],
                "error": f["extraction_error"], "claims_extracted": f["claims_extracted"],
                "claim_ids": per_file_created.get(f["document_id"], []),
                "merged_into_claim_ids": per_file_merged.get(f["document_id"], []),
            })

        by_activity: Dict[str, Dict[str, Any]] = {}
        for c in claim_rows:
            aid = c["matched_activity_id"]
            if not aid:
                continue
            g = by_activity.setdefault(aid, {"activity_id": aid, "activity_name": c["activity_name"], "stage_name": c["stage_name"], "claim_ids": [], "file_names": set()})
            g["claim_ids"].append(c["event_id"])
            g["file_names"].update(c["file_names"])
        activities = [{**g, "file_names": sorted(g["file_names"])} for g in sorted(by_activity.values(), key=lambda g: g["activity_id"])]

        return {
            "batch_id": batch["batch_id"], "project_id": batch["project_id"], "schedule_id": batch["schedule_id"],
            "status": batch["status"], "created_at": batch["created_at"], "completed_at": batch["completed_at"],
            "uploaded_by": batch["uploaded_by"], "notes": batch["notes"],
            "file_count": batch["file_count"], "claim_count": len(claim_rows), "merged_count": batch["merged_count"],
            "matched_count": sum(1 for c in claim_rows if c["matched_activity_id"]),
            "unmatched_count": sum(1 for c in claim_rows if not c["matched_activity_id"] and c["clarification_status"] != "PENDING"),
            "needs_clarification_count": sum(1 for c in claim_rows if c["clarification_status"] == "PENDING"),
            "files": file_rows, "claims": claim_rows, "activities": activities,
        }

    @classmethod
    def list_batches(cls, context: ProjectContext, mine_only: bool, limit: int = 50) -> List[Dict[str, Any]]:
        sql = ("SELECT b.batch_id, b.status, b.file_count, b.claim_count, b.merged_count, b.created_at, b.completed_at, b.uploaded_by, "
               "p.full_name AS uploaded_by_name FROM upload_batches b LEFT JOIN profiles p ON p.id = b.uploaded_by WHERE b.project_id = %s")
        params: list = [context.project_id]
        if mine_only:
            sql += " AND b.uploaded_by = %s"
            params.append(context.user.id)
        sql += " ORDER BY b.created_at DESC LIMIT %s"
        params.append(max(1, min(limit, 200)))
        with get_connection() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
