"""v2 rows -> the legacy response shapes (see frontend/src/api.ts). Pure translation of vocabulary; no business rule lives here."""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

CHANNEL_OUT = {"TYPED": "TYPED_TEXT", "VOICE_TRANSCRIPT": "VOICE", "TXT": "FILE_UPLOAD", "CSV": "FILE_UPLOAD", "XLSX": "FILE_UPLOAD", "PDF": "FILE_UPLOAD",
               "SCANNED": "SCANNED_OCR", "IMAGE": "SCANNED_OCR", "API": "SCHEDULE_EXPORT"}
CHANNEL_IN = {"TYPED_TEXT": "TYPED", "VOICE": "VOICE_TRANSCRIPT"}
EVENT_OUT = {"PROGRESS": "PROGRESS_UPDATE", "START": "ACTUAL_START", "FINISH": "ACTUAL_FINISH", "DELAY_NOTE": "DELAY"}
EVENT_IN = {"PROGRESS_UPDATE": "PROGRESS", "ACTUAL_START": "START", "ACTUAL_FINISH": "FINISH", "DELAY": "DELAY_NOTE", "BLOCKER": "DELAY_NOTE"}
PROV_OUT = {"ENGINEER": "ENGINEER_ENTERED", "SCHEDULE_AUTO_FILLED": "SCHEDULE_AUTO_FILLED", "AI_EXTRACTED": "AI_EXTRACTED", "SUPERVISOR_EDITED": "SUPERVISOR_EDITED"}


def num(v) -> Optional[float]:
    return None if v is None else float(v)


def iso(v) -> Optional[str]:
    return v.isoformat() if isinstance(v, (date, datetime)) else (None if v is None else str(v))


def legacy_status(row: Dict[str, Any], *, last_action: Optional[str], has_no_match_note: bool, has_candidates: bool, flagged: bool) -> str:
    """v2 claim status -> legacy ClaimStatus. v2 has no UNMATCHED / EDITED / HOLD / REVIEW_REQUIRED states; they are derived from facts the database does hold
    (REVIEW_REQUIRED = the claim checks left a 'REVIEW_REQUIRED' marker validation on a claim that is still open)."""
    s = row["status"]
    if s in ("EXTRACTED", "MATCHED", "VALIDATED") and flagged:
        return "REVIEW_REQUIRED"
    if s == "REPORTED":
        return "EXTRACTED"
    if s == "EXTRACTED":
        return "UNMATCHED" if (row["matched_activity_uid"] is None and (has_no_match_note or has_candidates)) else "EXTRACTED"
    if s == "APPROVED":
        return "EDITED" if last_action == "EDIT" else "APPROVED"
    if s == "DISPUTED":
        return "HOLD"
    return s                                        # MATCHED, VALIDATED, REJECTED, WITHDRAWN


def legacy_clarification(row: Dict[str, Any]) -> str:
    if row["clarification_status"] == "ASKED":
        return "PENDING" if row["status"] == "REPORTED" else "NONE"       # a Supervisor's question on a DISPUTED claim is a "hold" in the legacy vocabulary
    return row["clarification_status"]


def reasons(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    if "\n" in text or text.startswith("["):               # the original smart-priority text: already one reason per line
        return text
    return "\n".join(p.strip().replace("_", " ").capitalize() for p in text.split(";") if p.strip())


def provenance(raw: Any) -> Dict[str, str]:
    d = raw if isinstance(raw, dict) else (json.loads(raw) if isinstance(raw, str) and raw else {})
    out = {}
    for k, v in (d or {}).items():
        if v is None:
            continue
        key = {"activity": "activity_id"}.get(k, k)
        out[key] = PROV_OUT.get(v, v)
    return out


def claim_to_legacy(row: Dict[str, Any], quantities: List[Dict[str, Any]], *, status: str, matched_ext: Optional[str], completed_target: bool, has_photo: bool) -> Dict[str, Any]:
    one = quantities[0] if len(quantities) == 1 else None
    mode = row["claim_mode"]
    return {
        "event_id": str(row["event_id"]), "document_id": str(row["document_id"]) if row["document_id"] else None, "schedule_id": str(row["filed_in_version_id"]),
        "event_date": iso(row["event_date"]), "raw_claim_text": row["raw_claim_text"], "input_channel": CHANNEL_OUT.get(row["input_channel"], "TYPED_TEXT"),
        "language_detected": row["language_detected"], "reported_activity_id": row["reported_activity_ref"], "matched_activity_id": matched_ext,
        "discipline": row["discipline_code"] or row.get("act_discipline"), "action": None, "event_type": EVENT_OUT.get(row["event_type"], "PROGRESS_UPDATE"),
        "claim_mode": "CUMULATIVE_PCT" if mode == "CUMULATIVE_PCT" else "INCREMENTAL_QUANTITY",
        "asset_tag": row["asset_tag"], "location": row["location"],
        "claimed_quantity": num(one["reported_qty"]) if one else None, "claimed_uom": one["reported_uom"] if one else None, "claimed_pct": num(row["claimed_pct"]),
        "delay_reason": row["delay_reason"], "supervisor_id": None, "photo_path": "document" if has_photo else None, "status": status,
        "created_at": iso(row["created_at"]), "clarification_status": legacy_clarification(row), "clarification_question": row["clarification_question"],
        "clarification_answer": row["clarification_answer"], "priority_score": num(row["priority_score"]), "priority_reasons": reasons(row["priority_reasons"]),
        "field_provenance": provenance(row["field_provenance"]), "is_completed_activity_target": completed_target,
    }
