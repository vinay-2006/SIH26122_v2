import math
import os
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Set, Sequence

from fastapi import APIRouter, Depends, HTTPException

from backend.context import gates
from backend.context.event import EventContext
from backend.context.project import ProjectContext
from backend.context.schedule import ScheduleContext

from backend.shared.actuals import approved_split_quantity
from backend.shared.audit import get_audit_trail, list_recent_audit_logs, write_audit_log
from backend.shared.db import get_connection

from backend.shared.auth import UserProfile

SYSTEM_ACTOR_M4 = "SYSTEM:M4"
CONFLICT_TOLERANCE_PCT = float(os.getenv("CONFLICT_TOLERANCE_PCT", "10.0"))
EVIDENCE_COMPARISON_WINDOW_DAYS = int(os.getenv("EVIDENCE_COMPARISON_WINDOW_DAYS", "14"))

router = APIRouter(prefix="/api/v1", tags=["checks"])


@router.get("/claims/checks/health")
def health():
    return {"router": "checks", "status": "ok"}


def _convert_to_degrees(value) -> float:
    d = float(value.values[0].num) / float(value.values[0].den)
    m = float(value.values[1].num) / float(value.values[1].den)
    s = float(value.values[2].num) / float(value.values[2].den)
    return d + (m / 60.0) + (s / 3600.0)


def haversine_distance_km(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def _parse_exif(photo_path: str) -> Dict[str, Any]:
    exif_data = {}
    if not os.path.exists(photo_path):
        return exif_data

    try:
        import exifread

        with open(photo_path, "rb") as f:
            tags = exifread.process_file(f, details=False)
            if "EXIF DateTimeOriginal" in tags:
                exif_data["datetime_original"] = str(
                    tags["EXIF DateTimeOriginal"]
                )
            elif "Image DateTime" in tags:
                exif_data["datetime_original"] = str(tags["Image DateTime"])

            lat_tag = tags.get("GPS GPSLatitude")
            lon_tag = tags.get("GPS GPSLongitude")
            lat_ref = tags.get("GPS GPSLatitudeRef")
            lon_ref = tags.get("GPS GPSLongitudeRef")

            if lat_tag and lon_tag:
                exif_data["has_gps"] = True
                lat = _convert_to_degrees(lat_tag)
                if lat_ref and str(lat_ref.values).upper() == "S":
                    lat = -lat
                lon = _convert_to_degrees(lon_tag)
                if lon_ref and str(lon_ref.values).upper() == "W":
                    lon = -lon
                exif_data["lat"] = lat
                exif_data["lon"] = lon
    except Exception:
        pass

    return exif_data


def normalize_date(val: Any) -> Optional[date]:
    """Normalize a date object, datetime, or ISO string to a datetime.date."""
    if val is None:
        return None
    if isinstance(val, date):
        if isinstance(val, datetime):
            return val.date()
        return val
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        try:
            return date.fromisoformat(val[:10])
        except Exception:
            try:
                return datetime.strptime(val[:10], "%Y-%m-%d").date()
            except Exception:
                return None
    return None


def has_rework_or_reset_context(claim: Dict[str, Any]) -> bool:
    """
    Check if a claim explicitly contains an accepted rework or reset explanation.
    Per PRD v6 Feature 9, progress regression is permitted without conflict flag
    if rework/reset context exists.
    """
    delay_reason = str(claim.get("delay_reason") or "").strip().upper()
    if delay_reason == "REWORK":
        return True

    action = str(claim.get("action") or "").strip().upper()
    if "REWORK" in action or "RESET" in action:
        return True

    raw_text = str(claim.get("raw_claim_text") or "").lower()
    rework_keywords = [
        "rework",
        "reset",
        "rectification",
        "re-excavation",
        "demolition",
        "redo",
        "re-do",
        "dismantle",
        "cut back",
    ]
    if any(kw in raw_text for kw in rework_keywords):
        return True

    return False


def compare_chronological_claims(
    date_a: Any,
    val_a: float,
    date_b: Any,
    val_b: float,
    tolerance_pct: float = CONFLICT_TOLERANCE_PCT,
    has_rework_a: bool = False,
    has_rework_b: bool = False,
) -> Dict[str, Any]:
    """
    Directional and chronological comparison of two cumulative claims.
    Reusable helper for conflict detection (Feature 9) and evidence fusion (Feature 31).

    Rules:
    1. Same date:
       - difference > tolerance_pct -> SAME_DATE_DISAGREEMENT (conflict)
       - difference <= tolerance_pct -> SAME_DATE_AGREEMENT (no conflict)
    2. Chronologically ordered (t_newer > t_older):
       - pct(t_newer) >= pct(t_older): NORMAL_PROGRESSION (no conflict)
       - pct(t_newer) < pct(t_older):
         - if newer claim has rework context: ACCEPTED_REWORK (no conflict)
         - else: PROGRESS_REGRESSION (conflict)
    """
    d_a = normalize_date(date_a)
    d_b = normalize_date(date_b)

    if d_a is None or d_b is None:
        return {
            "relationship": "UNDETERMINED",
            "is_conflict": False,
            "conflict_type": None,
            "variance_pct": 0.0,
            "description": "Missing date information for comparison.",
        }

    diff = round(abs(val_a - val_b), 2)

    # 1. Same reporting date
    if d_a == d_b:
        if diff > tolerance_pct:
            return {
                "relationship": "SAME_DATE_DISAGREEMENT",
                "is_conflict": True,
                "conflict_type": "SAME_DATE_DISAGREEMENT",
                "variance_pct": diff,
                "description": (
                    f"Same-date progress disagreement on {d_a}: "
                    f"{val_a}% vs {val_b}% (variance {diff}% > {tolerance_pct}%)."
                ),
            }
        else:
            return {
                "relationship": "SAME_DATE_AGREEMENT",
                "is_conflict": False,
                "conflict_type": None,
                "variance_pct": diff,
                "description": (
                    f"Same-date progress agreement on {d_a}: "
                    f"{val_a}% vs {val_b}% (within tolerance {tolerance_pct}%)."
                ),
            }

    # 2. Chronologically different dates
    if d_a > d_b:
        newer_date, newer_val, newer_rework = d_a, val_a, has_rework_a
        older_date, older_val = d_b, val_b
        a_is_newer = True
    else:
        newer_date, newer_val, newer_rework = d_b, val_b, has_rework_b
        older_date, older_val = d_a, val_a
        a_is_newer = False

    if newer_val >= older_val:
        return {
            "relationship": "NORMAL_PROGRESSION",
            "is_conflict": False,
            "conflict_type": None,
            "variance_pct": round(newer_val - older_val, 2),
            "description": (
                f"Normal chronological progression from {older_date} ({older_val}%) "
                f"to {newer_date} ({newer_val}%)."
            ),
        }
    else:
        regress_diff = round(older_val - newer_val, 2)
        if newer_rework:
            return {
                "relationship": "ACCEPTED_REWORK",
                "is_conflict": False,
                "conflict_type": None,
                "variance_pct": regress_diff,
                "description": (
                    f"Progress reduction from {older_val}% on {older_date} "
                    f"to {newer_val}% on {newer_date} accepted due to rework/reset context."
                ),
            }
        else:
            actor = "Claim" if a_is_newer else f"Prior event on {newer_date}"
            return {
                "relationship": "PROGRESS_REGRESSION",
                "is_conflict": True,
                "conflict_type": "PROGRESS_REGRESSION",
                "variance_pct": regress_diff,
                "description": (
                    f"Progress regression: {actor} reports {newer_val}% on {newer_date}, "
                    f"which is lower than progress {older_val}% on {older_date} without accepted rework/reset."
                ),
            }


def evaluate_cumulative_conflicts(
    conn: Any,
    schedule_id: str,
    matched_activity_id: str,
    event_id: str,
    event_date: Any,
    claimed_pct: float,
    current_claim: Dict[str, Any],
    tolerance_pct: float = CONFLICT_TOLERANCE_PCT,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Evaluate directional and chronological conflicts for a CUMULATIVE_PCT claim:
    1. Same-date comparison (SAME_DATE_DISAGREEMENT): compares against other non-rejected
       claims for the same activity on the exact same reporting date.
    2. Chronological comparison (PROGRESS_REGRESSION): compares newer cumulative progress
       strictly against authoritative/approved historical state:
       - approved_actuals (actual_pct_complete)
       - prior claims with an authoritative APPROVE or EDIT decision
       Unapproved claims on different dates are NOT treated as authoritative.
    """
    validation_issues: List[Dict[str, Any]] = []
    conflict_records: List[Dict[str, Any]] = []

    d_current = normalize_date(event_date)
    if not matched_activity_id or d_current is None or claimed_pct is None:
        return validation_issues, conflict_records

    has_rework = has_rework_or_reset_context(current_claim)

    # 1. Same-date claim-vs-claim comparison for SAME_DATE_DISAGREEMENT
    same_date_events = conn.execute(
        """
        SELECT event_id, claimed_pct, event_date, status, delay_reason, action, raw_claim_text
        FROM execution_events
        WHERE schedule_id = %s
          AND matched_activity_id = %s
          AND event_id != %s
          AND event_date = %s
          AND claim_mode = 'CUMULATIVE_PCT'
          AND status IN ('APPROVED', 'EDITED', 'HOLD', 'VALIDATED', 'REVIEW_REQUIRED')
          AND claimed_pct IS NOT NULL
        """,
        (schedule_id, matched_activity_id, event_id, d_current),
    ).fetchall()

    for other in same_date_events:
        other_id = other["event_id"]
        other_val = float(other["claimed_pct"] or 0.0)
        diff = round(abs(claimed_pct - other_val), 2)

        if diff > tolerance_pct:
            conflict_id = str(uuid.uuid4())
            conflict_records.append(
                {
                    "conflict_id": conflict_id,
                    "schedule_id": schedule_id,
                    "activity_id": matched_activity_id,
                    "reporting_period": d_current,
                    "event_id_a": event_id,
                    "event_id_b": other_id,
                    "value_a": claimed_pct,
                    "value_b": other_val,
                    "variance_pct": diff,
                    "status": "OPEN",
                }
            )
            validation_issues.append(
                {
                    "issue_id": str(uuid.uuid4()),
                    "event_id": event_id,
                    "rule_code": "SAME_DATE_DISAGREEMENT",
                    "severity": "WARNING",
                    "description": (
                        f"Same-date progress disagreement on {d_current}: "
                        f"{claimed_pct}% vs {other_val}% (variance {diff}% > {tolerance_pct}%)."
                    ),
                }
            )

    # 2. Chronological PROGRESS_REGRESSION against authoritative/approved historical state
    if not has_rework:
        recorded_regress_event_ids = set()

        # A. Check against approved_actuals
        actual_row = conn.execute(
            """
            SELECT actual_pct_complete, event_id, actual_finish
            FROM approved_actuals
            WHERE schedule_id = %s AND activity_id = %s
            """,
            (schedule_id, matched_activity_id),
        ).fetchone()

        if actual_row and actual_row.get("actual_pct_complete") is not None:
            approved_pct = float(actual_row["actual_pct_complete"])
            if claimed_pct < approved_pct:
                regress_diff = round(approved_pct - claimed_pct, 2)
                approved_event_id = actual_row.get("event_id")
                if approved_event_id:
                    recorded_regress_event_ids.add(approved_event_id)

                conflict_id = str(uuid.uuid4())
                conflict_records.append(
                    {
                        "conflict_id": conflict_id,
                        "schedule_id": schedule_id,
                        "activity_id": matched_activity_id,
                        "reporting_period": d_current,
                        "event_id_a": event_id,
                        "event_id_b": approved_event_id or f"APPROVED_ACTUAL:{matched_activity_id}",
                        "value_a": claimed_pct,
                        "value_b": approved_pct,
                        "variance_pct": regress_diff,
                        "status": "OPEN",
                    }
                )
                validation_issues.append(
                    {
                        "issue_id": str(uuid.uuid4()),
                        "event_id": event_id,
                        "rule_code": "PROGRESS_REGRESSION",
                        "severity": "ERROR",
                        "description": (
                            f"Progress regression against approved actuals: "
                            f"claimed {claimed_pct}% is lower than approved {approved_pct}% "
                            f"for activity '{matched_activity_id}' without accepted rework/reset."
                        ),
                    }
                )

        # B. Check against earlier authoritative decisions (APPROVE or EDIT in planner_decisions)
        prior_approved_claims = conn.execute(
            """
            SELECT ee.event_id, ee.event_date,
                   COALESCE(pd.approved_pct, ee.claimed_pct) AS authoritative_pct
            FROM execution_events ee
            JOIN planner_decisions pd ON pd.event_id = ee.event_id
            WHERE ee.schedule_id = %s
              AND ee.matched_activity_id = %s
              AND ee.event_id != %s
              AND ee.event_date < %s
              AND pd.action IN ('APPROVE', 'EDIT')
              AND pd.decision_id = (
                  SELECT decision_id FROM planner_decisions pd2
                  WHERE pd2.event_id = ee.event_id
                  ORDER BY pd2.decided_at DESC LIMIT 1
              )
              AND (pd.approved_pct IS NOT NULL OR ee.claimed_pct IS NOT NULL)
            ORDER BY ee.event_date DESC
            """,
            (schedule_id, matched_activity_id, event_id, d_current),
        ).fetchall()

        for prior in prior_approved_claims:
            prior_id = prior["event_id"]
            if prior_id in recorded_regress_event_ids:
                continue

            prior_pct = float(prior["authoritative_pct"] or 0.0)
            prior_date = normalize_date(prior["event_date"])

            if claimed_pct < prior_pct:
                recorded_regress_event_ids.add(prior_id)
                regress_diff = round(prior_pct - claimed_pct, 2)
                conflict_id = str(uuid.uuid4())
                conflict_records.append(
                    {
                        "conflict_id": conflict_id,
                        "schedule_id": schedule_id,
                        "activity_id": matched_activity_id,
                        "reporting_period": d_current,
                        "event_id_a": event_id,
                        "event_id_b": prior_id,
                        "value_a": claimed_pct,
                        "value_b": prior_pct,
                        "variance_pct": regress_diff,
                        "status": "OPEN",
                    }
                )
                validation_issues.append(
                    {
                        "issue_id": str(uuid.uuid4()),
                        "event_id": event_id,
                        "rule_code": "PROGRESS_REGRESSION",
                        "severity": "ERROR",
                        "description": (
                            f"Progress regression against authoritative prior event '{prior_id}' on {prior_date}: "
                            f"claimed {claimed_pct}% is lower than approved {prior_pct}% without accepted rework/reset."
                        ),
                    }
                )

    return validation_issues, conflict_records


def normalize_claim_text(text: Optional[str]) -> str:
    """Normalize raw claim text for deterministic comparison: strip, lowercase, collapse whitespace."""
    if not text:
        return ""
    return " ".join(str(text).strip().lower().split())


def _fetch_source_references(conn: Any, event_id: str) -> List[Dict[str, Any]]:
    """Retrieve source reference rows for an execution event from DB or mock connection."""
    if not event_id:
        return []
    try:
        rows = conn.execute(
            """
            SELECT file_name, sheet_name, row_cell_ref, message_id, raw_snippet
            FROM source_references
            WHERE event_id = %s
            """,
            (event_id,),
        ).fetchall()
        return [dict(r) for r in rows] if rows else []
    except Exception:
        return []


def has_deterministic_duplicate_evidence(
    claim_a: Dict[str, Any],
    claim_b: Dict[str, Any],
    source_refs_a: Optional[List[Dict[str, Any]]] = None,
    source_refs_b: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[bool, str]:
    """
    Determine whether deterministic duplicate physical contribution evidence exists
    between two claims per PRD v6 Feature 9.

    Identical quantity alone is NOT treated as deterministic proof of duplicate contribution.
    Requires stronger evidence such as:
    1. Identical normalized raw claim text
    2. Same source/document row/reference (e.g. same file_name + row_cell_ref, matching message_id,
       or identical source reference snippet)
    3. Another existing deterministic identifier proving the same physical contribution
       (e.g. matching physical asset_tag or photo_path)

    Returns:
        (is_duplicate, duplicate_reason)
    """
    # 1. Identical normalized raw claim text
    text_a = normalize_claim_text(claim_a.get("raw_claim_text"))
    text_b = normalize_claim_text(claim_b.get("raw_claim_text"))
    if text_a and text_b and text_a == text_b:
        return True, f"identical normalized raw claim text: '{text_a[:50]}'"

    # 2. Source references check (file row / message / snippet)
    refs_a = source_refs_a or []
    refs_b = source_refs_b or []
    for ref_a in refs_a:
        for ref_b in refs_b:
            file_a = (ref_a.get("file_name") or "").strip().lower()
            file_b = (ref_b.get("file_name") or "").strip().lower()
            row_a = (ref_a.get("row_cell_ref") or "").strip().lower()
            row_b = (ref_b.get("row_cell_ref") or "").strip().lower()
            if file_a and file_b and file_a == file_b and row_a and row_b and row_a == row_b:
                return True, f"same source document row/reference: '{file_a}' {row_a}"

            msg_a = (ref_a.get("message_id") or "").strip()
            msg_b = (ref_b.get("message_id") or "").strip()
            if msg_a and msg_b and msg_a == msg_b:
                return True, f"same source message reference: '{msg_a}'"

            snip_a = normalize_claim_text(ref_a.get("raw_snippet"))
            snip_b = normalize_claim_text(ref_b.get("raw_snippet"))
            if snip_a and snip_b and len(snip_a) > 5 and snip_a == snip_b:
                return True, f"identical source reference snippet: '{snip_a[:50]}'"

    # 3. Deterministic physical identifier: matching non-empty asset_tag
    tag_a = (claim_a.get("asset_tag") or "").strip().lower()
    tag_b = (claim_b.get("asset_tag") or "").strip().lower()
    if tag_a and tag_b and tag_a == tag_b:
        return True, f"identical physical asset tag: '{tag_a}'"

    # 4. Deterministic photo evidence match
    photo_a = (claim_a.get("photo_path") or "").strip()
    photo_b = (claim_b.get("photo_path") or "").strip()
    if photo_a and photo_b and photo_a == photo_b:
        return True, f"identical evidence photo: '{photo_a}'"

    return False, ""


def evaluate_incremental_quantity_anomalies(
    conn: Any,
    schedule_id: str,
    matched_activity_id: str,
    event_id: str,
    event_date: Any,
    claimed_qty: Optional[float],
    claimed_uom: Optional[str],
    activity_row: Optional[Dict[str, Any]],
    raw_claim_text: Optional[str] = None,
    current_claim: Optional[Dict[str, Any]] = None,
    source_refs: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Optional[float]]:
    """
    Evaluate INCREMENTAL_QUANTITY claims per PRD v6 Feature 9:
    1. Negative quantity check (VAL_NEGATIVE)
    2. Planned-quantity exceedance check (VAL_OVER_100)
    3. Deterministic duplicate contribution check (DUPLICATE_CONTRIBUTION)
       - Identical quantity alone is NOT treated as proof of duplicate contribution.
       - Requires stronger deterministic evidence (same source doc row/ref, identical normalized
         raw claim text, matching asset tag, etc.).
       - Two separate claims with the same quantity are allowed unless duplicate evidence exists.
    4. Implausible/unsupported accumulation check (VAL_UNSUPPORTED_ACCUMULATION)
    5. Planned UOM mismatch check (VAL_UOM_MISMATCH)

    Returns: (validation_issues, conflict_records, derived_pct)
    """
    validation_issues: List[Dict[str, Any]] = []
    conflict_records: List[Dict[str, Any]] = []
    derived_pct: Optional[float] = None

    d_current = normalize_date(event_date)
    planned_qty = float(activity_row["planned_quantity"]) if (activity_row and activity_row.get("planned_quantity") is not None) else None
    planned_uom = activity_row.get("uom") if activity_row else None

    # 1. Negative quantity check
    if claimed_qty is not None and claimed_qty < 0.0:
        validation_issues.append(
            {
                "issue_id": str(uuid.uuid4()),
                "event_id": event_id,
                "rule_code": "VAL_NEGATIVE",
                "severity": "ERROR",
                "description": f"Claimed incremental quantity ({claimed_qty}) cannot be negative.",
            }
        )

    # 2. UOM mismatch check
    if claimed_uom and planned_uom:
        if claimed_uom.strip().lower() != planned_uom.strip().lower():
            validation_issues.append(
                {
                    "issue_id": str(uuid.uuid4()),
                    "event_id": event_id,
                    "rule_code": "VAL_UOM_MISMATCH",
                    "severity": "WARNING",
                    "description": f"Claimed UOM '{claimed_uom}' does not match planned UOM '{planned_uom}'.",
                }
            )

    # 3. Unsupported accumulation: check if activity has no planned quantity or already completed
    if (planned_qty is None or planned_qty <= 0) and claimed_qty is not None and claimed_qty > 0:
        validation_issues.append(
            {
                "issue_id": str(uuid.uuid4()),
                "event_id": event_id,
                "rule_code": "VAL_UNSUPPORTED_ACCUMULATION",
                "severity": "WARNING",
                "description": (
                    f"Activity '{matched_activity_id}' has no planned quantity; "
                    "incremental accumulation cannot be baseline verified."
                ),
            }
        )

    actual_row = conn.execute(
        """
        SELECT actual_finish, actual_pct_complete
        FROM approved_actuals
        WHERE schedule_id = %s AND activity_id = %s
        """,
        (schedule_id, matched_activity_id),
    ).fetchone() if matched_activity_id else None

    if actual_row and actual_row.get("actual_finish") is not None and claimed_qty is not None and claimed_qty > 0:
        validation_issues.append(
            {
                "issue_id": str(uuid.uuid4()),
                "event_id": event_id,
                "rule_code": "VAL_REOPENED_COMPLETED_ACTIVITY",
                "severity": "ERROR",
                "description": f"Incremental quantity reported for completed activity '{matched_activity_id}'.",
            }
        )

    # 4. Planned-quantity exceedance & derived pct calculation
    if planned_qty and planned_qty > 0 and claimed_qty is not None:
        prior_qty_row = conn.execute(
            """
            SELECT COALESCE(SUM(COALESCE(pd.approved_qty, ee.claimed_quantity)), 0.0) AS total_qty
            FROM execution_events ee
            JOIN planner_decisions pd ON pd.event_id = ee.event_id
            WHERE ee.schedule_id = %s
              AND ee.matched_activity_id = %s
              AND pd.action IN ('APPROVE', 'EDIT')
              AND pd.decision_id = (
                  SELECT decision_id FROM planner_decisions pd2
                  WHERE pd2.event_id = ee.event_id
                  ORDER BY pd2.decided_at DESC LIMIT 1
              )
            """,
            (schedule_id, matched_activity_id),
        ).fetchone()

        total_prior = float(prior_qty_row["total_qty"]) if (prior_qty_row and prior_qty_row.get("total_qty") is not None) else 0.0
        total_prior += approved_split_quantity(conn, schedule_id, matched_activity_id)
        total_sum = total_prior + claimed_qty
        derived_pct = round((total_sum / planned_qty) * 100.0, 2)

        if total_sum > planned_qty or derived_pct > 100.0:
            validation_issues.append(
                {
                    "issue_id": str(uuid.uuid4()),
                    "event_id": event_id,
                    "rule_code": "VAL_OVER_100",
                    "severity": "ERROR",
                    "description": (
                        f"Cumulative quantity ({total_sum} {planned_uom or ''}) "
                        f"exceeds planned quantity ({planned_qty} {planned_uom or ''}). "
                        f"Derived progress is {derived_pct}%."
                    ),
                }
            )

        # Unsupported accumulation: implausible single-claim quantity > 150% of plan
        if claimed_qty > (planned_qty * 1.5):
            validation_issues.append(
                {
                    "issue_id": str(uuid.uuid4()),
                    "event_id": event_id,
                    "rule_code": "VAL_UNSUPPORTED_ACCUMULATION",
                    "severity": "ERROR",
                    "description": (
                        f"Single-claim quantity ({claimed_qty}) exceeds 150% of "
                        f"total planned quantity ({planned_qty})."
                    ),
                }
            )

    # 5. Deterministic duplicate contribution check on the same date
    if matched_activity_id and d_current and claimed_qty is not None:
        other_inc_events = conn.execute(
            """
            SELECT event_id, document_id, claimed_quantity, claimed_uom, raw_claim_text, asset_tag, photo_path, status
            FROM execution_events
            WHERE schedule_id = %s
              AND matched_activity_id = %s
              AND event_id != %s
              AND event_date = %s
              AND claim_mode = 'INCREMENTAL_QUANTITY'
              AND status IN ('APPROVED', 'EDITED', 'HOLD', 'VALIDATED', 'REVIEW_REQUIRED')
              AND claimed_quantity IS NOT NULL
            """,
            (schedule_id, matched_activity_id, event_id, d_current),
        ).fetchall()

        claim_a_data = {
            "event_id": event_id,
            "raw_claim_text": raw_claim_text or (current_claim.get("raw_claim_text") if current_claim else None),
            "asset_tag": current_claim.get("asset_tag") if current_claim else None,
            "photo_path": current_claim.get("photo_path") if current_claim else None,
            "document_id": current_claim.get("document_id") if current_claim else None,
        }
        refs_a = source_refs if source_refs is not None else _fetch_source_references(conn, event_id)

        for other in other_inc_events:
            other_dict = dict(other)
            other_id = other_dict["event_id"]
            other_qty = float(other_dict.get("claimed_quantity") or 0.0)

            refs_b = _fetch_source_references(conn, other_id)
            is_dup, dup_reason = has_deterministic_duplicate_evidence(
                claim_a=claim_a_data,
                claim_b=other_dict,
                source_refs_a=refs_a,
                source_refs_b=refs_b,
            )

            # Two separate claims with the same quantity must be allowed
            # unless deterministic duplicate evidence exists.
            if is_dup:
                conflict_id = str(uuid.uuid4())
                conflict_records.append(
                    {
                        "conflict_id": conflict_id,
                        "schedule_id": schedule_id,
                        "activity_id": matched_activity_id,
                        "reporting_period": d_current,
                        "event_id_a": event_id,
                        "event_id_b": other_id,
                        "value_a": claimed_qty,
                        "value_b": other_qty,
                        "variance_pct": round(abs(claimed_qty - other_qty), 2),
                        "status": "OPEN",
                    }
                )
                validation_issues.append(
                    {
                        "issue_id": str(uuid.uuid4()),
                        "event_id": event_id,
                        "rule_code": "DUPLICATE_CONTRIBUTION",
                        "severity": "WARNING",
                        "description": (
                            f"Duplicate incremental physical contribution detected on {d_current}: "
                            f"event '{other_id}' already claimed {other_qty} {claimed_uom or ''} ({dup_reason})."
                        ),
                    }
                )

    return validation_issues, conflict_records, derived_pct


def get_activity_approved_state(
    conn: Any,
    schedule_id: str,
    activity_id: str,
) -> Dict[str, Any]:
    """
    Retrieve authoritative approved execution state for an activity.
    Queries approved_actuals first, then falls back to approved planner_decisions.

    Returns:
        dict with:
            - actual_start: Optional[date]
            - actual_finish: Optional[date]
            - actual_pct_complete: float
            - actual_quantity: Optional[float]
            - is_started: bool
            - is_finished: bool
    """
    act_row = None
    try:
        act_row = conn.execute(
            """
            SELECT actual_start, actual_finish, actual_pct_complete, actual_quantity
            FROM approved_actuals
            WHERE schedule_id = %s AND activity_id = %s
            """,
            (schedule_id, activity_id),
        ).fetchone()
    except Exception:
        act_row = None

    if act_row:
        act_row_dict = dict(act_row)
        act_start = normalize_date(act_row_dict.get("actual_start"))
        act_finish = normalize_date(act_row_dict.get("actual_finish"))
        pct = float(act_row_dict.get("actual_pct_complete") or 0.0)
        qty = (
            float(act_row_dict["actual_quantity"])
            if act_row_dict.get("actual_quantity") is not None
            else None
        )
    else:
        dec_row = None
        try:
            dec_row = conn.execute(
                """
                SELECT ee.event_type, ee.event_date,
                       COALESCE(pd.approved_pct, ee.claimed_pct) AS authoritative_pct,
                       COALESCE(pd.approved_qty, ee.claimed_quantity) AS authoritative_qty
                FROM execution_events ee
                JOIN planner_decisions pd ON pd.event_id = ee.event_id
                WHERE ee.schedule_id = %s
                  AND ee.matched_activity_id = %s
                  AND pd.action IN ('APPROVE', 'EDIT')
                ORDER BY pd.decided_at DESC
                LIMIT 1
                """,
                (schedule_id, activity_id),
            ).fetchone()
        except Exception:
            dec_row = None

        if dec_row:
            dec_dict = dict(dec_row)
            pct = float(dec_dict.get("authoritative_pct") or 0.0)
            qty = (
                float(dec_dict["authoritative_qty"])
                if dec_dict.get("authoritative_qty") is not None
                else None
            )
            ev_type = str(dec_dict.get("event_type") or "").upper()
            act_start = (
                normalize_date(dec_dict.get("event_date"))
                if ev_type == "ACTUAL_START" or pct > 0.0
                else None
            )
            act_finish = (
                normalize_date(dec_dict.get("event_date"))
                if ev_type == "ACTUAL_FINISH" or pct >= 100.0
                else None
            )
        else:
            pct = 0.0
            qty = None
            act_start = None
            act_finish = None

    is_started = (
        (act_start is not None)
        or (pct > 0.0)
        or (qty is not None and qty > 0.0)
        or (act_finish is not None)
    )
    is_finished = (act_finish is not None) or (pct >= 100.0)

    return {
        "actual_start": act_start,
        "actual_finish": act_finish,
        "actual_pct_complete": pct,
        "actual_quantity": qty,
        "is_started": is_started,
        "is_finished": is_finished,
    }


def is_successor_legitimately_started(
    conn: Any,
    schedule_id: str,
    activity_id: str,
    current_event_id: Optional[str] = None,
) -> bool:
    """
    Check if the successor activity has already legitimately started prior to the current claim.
    An already in-progress activity must not repeatedly receive false out-of-sequence flags.
    """
    succ_state = get_activity_approved_state(conn, schedule_id, activity_id)
    if succ_state["is_started"]:
        return True

    prior_app = None
    try:
        prior_app = conn.execute(
            """
            SELECT ee.event_id
            FROM execution_events ee
            JOIN planner_decisions pd ON pd.event_id = ee.event_id
            WHERE ee.schedule_id = %s
              AND ee.matched_activity_id = %s
              AND ee.event_id != %s
              AND pd.action IN ('APPROVE', 'EDIT')
              AND (
                  ee.event_type = 'ACTUAL_START'
                  OR COALESCE(pd.approved_pct, ee.claimed_pct, 0.0) > 0.0
                  OR COALESCE(pd.approved_qty, ee.claimed_quantity, 0.0) > 0.0
              )
            LIMIT 1
            """,
            (schedule_id, activity_id, current_event_id or ""),
        ).fetchone()
    except Exception:
        prior_app = None

    return prior_app is not None


def evaluate_dependency_rule(
    relationship_type: str,
    pred_id: str,
    pred_state: Dict[str, Any],
    succ_id: str,
    succ_is_already_started: bool,
    event_type: Optional[str],
    is_finish_claim: bool,
    event_date: Optional[date] = None,
) -> Optional[str]:
    """
    Evaluate a single dependency relationship (FS, SS, FF, SF) per PRD v6.

    Returns:
        A human-readable violation description if violated, or None if satisfied.
    """
    rel = (relationship_type or "FS").strip().upper()

    if rel == "FS":
        # Predecessor must be complete before successor start/progress
        # when successor is otherwise unstarted.
        if not succ_is_already_started:
            if not pred_state["is_finished"]:
                return (
                    f"Predecessor activity '{pred_id}' is not complete "
                    f"({pred_state['actual_pct_complete']}% < 100%) before unstarted successor "
                    f"'{succ_id}' starts/progresses (relationship FS)."
                )
            if event_date and pred_state["actual_finish"] and event_date < pred_state["actual_finish"]:
                return (
                    f"Successor activity '{succ_id}' start/progress date ({event_date}) precedes "
                    f"predecessor '{pred_id}' finish date ({pred_state['actual_finish']}) (relationship FS)."
                )
        return None

    elif rel == "SS":
        # Predecessor must have started / have progress before successor starts.
        if not succ_is_already_started or event_type == "ACTUAL_START":
            if not pred_state["is_started"]:
                return (
                    f"Predecessor activity '{pred_id}' has not started or recorded progress "
                    f"before successor '{succ_id}' starts (relationship SS)."
                )
            if event_date and pred_state["actual_start"] and event_date < pred_state["actual_start"]:
                return (
                    f"Successor activity '{succ_id}' start date ({event_date}) precedes "
                    f"predecessor '{pred_id}' start date ({pred_state['actual_start']}) (relationship SS)."
                )
        return None

    elif rel == "FF":
        # Successor finish must not precede predecessor finish.
        if is_finish_claim:
            if not pred_state["is_finished"]:
                return (
                    f"Successor activity '{succ_id}' claimed finish/100% completion before "
                    f"predecessor activity '{pred_id}' has finished "
                    f"(predecessor progress is {pred_state['actual_pct_complete']}% < 100%, relationship FF)."
                )
            if event_date and pred_state["actual_finish"] and event_date < pred_state["actual_finish"]:
                return (
                    f"Successor activity '{succ_id}' finish date ({event_date}) precedes "
                    f"predecessor '{pred_id}' finish date ({pred_state['actual_finish']}) (relationship FF)."
                )
        return None

    elif rel == "SF":
        # Apply start-to-finish constraint where available schedule data supports it.
        # Predecessor must start before successor finishes.
        if is_finish_claim:
            if not pred_state["is_started"]:
                return (
                    f"Successor activity '{succ_id}' claimed finish before predecessor '{pred_id}' "
                    f"has started (relationship SF)."
                )
            if event_date and pred_state["actual_start"] and event_date < pred_state["actual_start"]:
                return (
                    f"Successor activity '{succ_id}' finish date ({event_date}) precedes "
                    f"predecessor '{pred_id}' start date ({pred_state['actual_start']}) (relationship SF)."
                )
        return None

    return None


def evaluate_sequence_validation(
    conn: Any,
    schedule_id: str,
    matched_activity_id: str,
    event_id: str,
    event_type: Optional[str],
    event_date: Any,
    claimed_pct: Optional[float] = None,
    claimed_qty: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Evaluate schedule dependency sequence constraints per PRD v6 Feature 9 / Sequence Validation:
    - ACTUAL_START
    - PROGRESS_UPDATE when successor has not legitimately started
    - ACTUAL_FINISH / 100% where finish relationships apply (FF, SF)

    Dependency relationships evaluated:
    - FS: Predecessor must be complete before an otherwise-unstarted successor starts/progresses.
    - SS: Predecessor must already have started or have approved progress.
    - FF: Successor finish must not occur before predecessor finish.
    - SF: Successor finish must not occur before predecessor starts.

    An already legitimately in-progress successor does NOT repeatedly receive
    VAL_OUT_OF_SEQUENCE for ordinary progress updates.

    Severity is strictly ERROR.
    """
    validation_issues: List[Dict[str, Any]] = []
    if not matched_activity_id or not schedule_id:
        return validation_issues

    norm_event_date = normalize_date(event_date)
    norm_event_type = (event_type or "").strip().upper()

    # Determine finish intent
    is_finish_claim = (norm_event_type == "ACTUAL_FINISH") or (
        claimed_pct is not None and claimed_pct >= 100.0
    )

    # Determine if successor has already legitimately started
    succ_is_already_started = is_successor_legitimately_started(
        conn, schedule_id, matched_activity_id, event_id
    )

    # Check if this event warrants sequence evaluation:
    # 1. ACTUAL_START
    # 2. PROGRESS_UPDATE (or positive progress/qty) when successor is not yet legitimately started
    # 3. ACTUAL_FINISH / 100% completion claim (finish constraints FF / SF)
    is_start_claim = norm_event_type == "ACTUAL_START"
    is_unstarted_progress = (not succ_is_already_started) and (
        norm_event_type == "PROGRESS_UPDATE"
        or (claimed_pct is not None and claimed_pct > 0.0)
        or (claimed_qty is not None and claimed_qty > 0.0)
    )

    if not (is_start_claim or is_unstarted_progress or is_finish_claim):
        # Already legitimately in-progress successor receiving ordinary progress updates
        # does NOT trigger sequence errors.
        return validation_issues

    # Fetch all dependencies for this successor
    dep_rows = conn.execute(
        """
        SELECT predecessor_activity_id, relationship_type
        FROM schedule_dependencies
        WHERE schedule_id = %s AND successor_activity_id = %s
        """,
        (schedule_id, matched_activity_id),
    ).fetchall()

    if not dep_rows:
        return validation_issues

    for dep in dep_rows:
        pred_id = dep["predecessor_activity_id"]
        rel_type = dep.get("relationship_type") or "FS"

        pred_state = get_activity_approved_state(conn, schedule_id, pred_id)

        violation_desc = evaluate_dependency_rule(
            relationship_type=rel_type,
            pred_id=pred_id,
            pred_state=pred_state,
            succ_id=matched_activity_id,
            succ_is_already_started=succ_is_already_started,
            event_type=norm_event_type,
            is_finish_claim=is_finish_claim,
            event_date=norm_event_date,
        )

        if violation_desc:
            validation_issues.append(
                {
                    "issue_id": str(uuid.uuid4()),
                    "event_id": event_id,
                    "rule_code": "VAL_OUT_OF_SEQUENCE",
                    "severity": "ERROR",
                    "description": violation_desc,
                }
            )

    return validation_issues


def load_claim_activity_splits(
    conn: Any,
    event_id: str,
) -> List[Dict[str, Any]]:
    """
    Load WBS split rows for a claim event from claim_activity_splits table.
    Returns empty list if no splits exist or if table is not yet migrated in database.
    """
    if not event_id:
        return []
    try:
        rows = conn.execute(
            """
            SELECT split_id, event_id, activity_id, split_basis, split_pct
            FROM claim_activity_splits
            WHERE event_id = %s
            ORDER BY split_id ASC
            """,
            (event_id,),
        ).fetchall()
        return [dict(r) for r in rows] if rows else []
    except Exception:
        # Table might not exist yet if schema migration is pending
        return []


def validate_split_claim(
    conn: Any,
    event_id: str,
    schedule_id: str,
    event_row: Dict[str, Any],
    splits: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Validate a WBS split claim per PRD v6 Feature 30:
    1. Defensively verify split_pct values (> 0, numeric) and sum(split_pct) == 1.0000 ± 0.0001.
       Do not repair percentages automatically.
    2. For each child:
       contribution = claim_value * split_pct
       (claimed_pct for CUMULATIVE_PCT, claimed_quantity for INCREMENTAL_QUANTITY)
    3. Run existing M4 validation per child using contribution:
       - negative / >100 checks
       - UOM / planned quantity checks
       - reopened-completed check (VAL_REOPENED_COMPLETED_ACTIVITY)
       - sequence validation (FS/SS/FF/SF via evaluate_sequence_validation)
       - invalid/unstarted child surfaces sequence validation (VAL_OUT_OF_SEQUENCE)
    4. Does NOT mutate approved_actuals.

    Returns:
        (validation_issues, conflict_records)
    """
    validation_issues: List[Dict[str, Any]] = []
    conflict_records: List[Dict[str, Any]] = []

    if not splits:
        return validation_issues, conflict_records

    # 1. Defensive verification of split_pct values
    total_split = 0.0
    split_pct_error = False

    for s in splits:
        child_id = s.get("activity_id")
        raw_pct = s.get("split_pct")

        if not child_id or str(child_id).strip() == "":
            validation_issues.append(
                {
                    "issue_id": str(uuid.uuid4()),
                    "event_id": event_id,
                    "rule_code": "VAL_INVALID_SPLIT",
                    "severity": "ERROR",
                    "description": f"WBS split record '{s.get('split_id')}' has missing or empty child activity_id.",
                }
            )
            split_pct_error = True

        if raw_pct is None:
            validation_issues.append(
                {
                    "issue_id": str(uuid.uuid4()),
                    "event_id": event_id,
                    "rule_code": "VAL_INVALID_SPLIT",
                    "severity": "ERROR",
                    "description": f"WBS split for child activity '{child_id}' is missing split_pct.",
                }
            )
            split_pct_error = True
        else:
            try:
                val = float(raw_pct)
                if val < 0.0 or val > 1.0:
                    validation_issues.append(
                        {
                            "issue_id": str(uuid.uuid4()),
                            "event_id": event_id,
                            "rule_code": "VAL_INVALID_SPLIT",
                            "severity": "ERROR",
                            "description": f"WBS split for child activity '{child_id}' has negative or out-of-bounds split_pct ({val}).",
                        }
                    )
                    split_pct_error = True
                total_split += val
            except (ValueError, TypeError):
                validation_issues.append(
                    {
                        "issue_id": str(uuid.uuid4()),
                        "event_id": event_id,
                        "rule_code": "VAL_INVALID_SPLIT",
                        "severity": "ERROR",
                        "description": f"WBS split for child activity '{child_id}' has non-numeric split_pct: {raw_pct}.",
                    }
                )
                split_pct_error = True

    # Verify total split sum is 1.0000 ± 0.0001 (do not repair automatically)
    if not split_pct_error:
        if abs(total_split - 1.0) > 0.0001:
            validation_issues.append(
                {
                    "issue_id": str(uuid.uuid4()),
                    "event_id": event_id,
                    "rule_code": "VAL_INVALID_SPLIT",
                    "severity": "ERROR",
                    "description": (
                        f"WBS split percentages must sum to 1.0000 ± 0.0001, but sum to {total_split:.6f}. "
                        "Allocation cannot be validated."
                    ),
                }
            )

    # 2. Extract claim attributes
    claim_mode = event_row.get("claim_mode") or "CUMULATIVE_PCT"
    event_type = event_row.get("event_type")
    event_date = event_row.get("event_date")
    claimed_pct = event_row.get("claimed_pct")
    claimed_qty = event_row.get("claimed_quantity")
    claimed_uom = event_row.get("claimed_uom")
    raw_claim_text = event_row.get("raw_claim_text")

    # 3. Validate each child using contribution = claim_value * split_pct
    for s in splits:
        child_id = s.get("activity_id")
        if not child_id:
            continue

        raw_pct = s.get("split_pct")
        try:
            split_share = float(raw_pct) if raw_pct is not None else 0.0
        except (ValueError, TypeError):
            split_share = 0.0

        if split_share <= 0.0:
            # PRD v6 Feature 30: split_pct == 0 for completed/ineligible siblings
            # - contribution = 0
            # - do not flag reopened/completion or sequence errors solely because the row exists
            # - do not create progress contribution for that child
            continue

        # Load child activity row from schedule
        child_act_row = None
        try:
            child_act_row = conn.execute(
                """
                SELECT * FROM schedule_activities
                WHERE schedule_id = %s AND activity_id = %s
                """,
                (schedule_id, child_id),
            ).fetchone()
        except Exception:
            child_act_row = None

        if not child_act_row:
            validation_issues.append(
                {
                    "issue_id": str(uuid.uuid4()),
                    "event_id": event_id,
                    "rule_code": "VAL_LOW_MATCH_CONFIDENCE",
                    "severity": "WARNING",
                    "description": f"Child activity '{child_id}' in WBS split was not found in schedule '{schedule_id}'.",
                }
            )

        if claim_mode == "INCREMENTAL_QUANTITY":
            # contribution = claimed_quantity * split_pct
            child_claimed_qty = (
                round(float(claimed_qty) * split_share, 4)
                if claimed_qty is not None
                else None
            )

            # Evaluate incremental quantity anomalies for child
            child_inc_issues, child_inc_conflicts, child_derived_pct = (
                evaluate_incremental_quantity_anomalies(
                    conn=conn,
                    schedule_id=schedule_id,
                    matched_activity_id=child_id,
                    event_id=event_id,
                    event_date=event_date,
                    claimed_qty=child_claimed_qty,
                    claimed_uom=claimed_uom,
                    activity_row=dict(child_act_row) if child_act_row else None,
                    raw_claim_text=raw_claim_text,
                    current_claim=event_row,
                )
            )
            validation_issues.extend(child_inc_issues)
            conflict_records.extend(child_inc_conflicts)

            # Evaluate sequence validation for child
            child_seq_issues = evaluate_sequence_validation(
                conn=conn,
                schedule_id=schedule_id,
                matched_activity_id=child_id,
                event_id=event_id,
                event_type=event_type,
                event_date=event_date,
                claimed_pct=child_derived_pct,
                claimed_qty=child_claimed_qty,
            )
            validation_issues.extend(child_seq_issues)

        else:
            # CUMULATIVE_PCT: contribution = claimed_pct * split_pct
            child_claimed_pct = (
                round(float(claimed_pct) * split_share, 4)
                if claimed_pct is not None
                else None
            )

            if child_claimed_pct is not None:
                if child_claimed_pct > 100.0:
                    validation_issues.append(
                        {
                            "issue_id": str(uuid.uuid4()),
                            "event_id": event_id,
                            "rule_code": "VAL_OVER_100",
                            "severity": "ERROR",
                            "description": (
                                f"Allocated progress percentage ({child_claimed_pct}%) for "
                                f"child activity '{child_id}' exceeds 100%."
                            ),
                        }
                    )
                if child_claimed_pct < 0.0:
                    validation_issues.append(
                        {
                            "issue_id": str(uuid.uuid4()),
                            "event_id": event_id,
                            "rule_code": "VAL_NEGATIVE",
                            "severity": "ERROR",
                            "description": (
                                f"Allocated progress percentage ({child_claimed_pct}%) for "
                                f"child activity '{child_id}' cannot be negative."
                            ),
                        }
                    )

            # Reopened completed child check
            if child_claimed_pct is not None:
                actual_row = None
                try:
                    actual_row = conn.execute(
                        """
                        SELECT actual_finish FROM approved_actuals
                        WHERE schedule_id = %s AND activity_id = %s
                        """,
                        (schedule_id, child_id),
                    ).fetchone()
                except Exception:
                    actual_row = None

                if actual_row and actual_row.get("actual_finish") is not None:
                    validation_issues.append(
                        {
                            "issue_id": str(uuid.uuid4()),
                            "event_id": event_id,
                            "rule_code": "VAL_REOPENED_COMPLETED_ACTIVITY",
                            "severity": "ERROR",
                            "description": (
                                f"Claim allocated {child_claimed_pct}% to child activity '{child_id}' "
                                "that was already marked as completed."
                            ),
                        }
                    )

            # Evaluate sequence validation for child
            child_seq_issues = evaluate_sequence_validation(
                conn=conn,
                schedule_id=schedule_id,
                matched_activity_id=child_id,
                event_id=event_id,
                event_type=event_type,
                event_date=event_date,
                claimed_pct=child_claimed_pct,
                claimed_qty=None,
            )
            validation_issues.extend(child_seq_issues)

            # Directional chronological conflict check for child
            if event_date and child_claimed_pct is not None:
                cum_issues, cum_conflicts = evaluate_cumulative_conflicts(
                    conn=conn,
                    schedule_id=schedule_id,
                    matched_activity_id=child_id,
                    event_id=event_id,
                    event_date=event_date,
                    claimed_pct=child_claimed_pct,
                    current_claim=event_row,
                )
                validation_issues.extend(cum_issues)
                conflict_records.extend(cum_conflicts)

    return validation_issues, conflict_records


def get_event_document_type(conn: Any, event_row: Dict[str, Any]) -> Optional[str]:
    """
    Retrieve document_type for an execution event.
    Checks event_row directly, then queries source_documents via document_id,
    and falls back to input_channel.
    """
    if event_row.get("document_type"):
        return str(event_row["document_type"]).strip()
    doc_id = event_row.get("document_id")
    if doc_id:
        try:
            row = conn.execute(
                "SELECT document_type FROM source_documents WHERE document_id = %s",
                (doc_id,),
            ).fetchone()
            if row and row.get("document_type"):
                return str(row.get("document_type")).strip()
        except Exception:
            pass
    channel = event_row.get("input_channel")
    return str(channel).strip() if channel else None


def get_event_activities(
    conn: Any,
    event_row: Dict[str, Any],
    splits: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, float]:
    """
    Return mapping of {activity_id: split_weight} for an execution event.
    Only includes positive split allocations (split_pct > 0).
    """
    if splits is None:
        event_id = event_row.get("event_id")
        if event_id:
            splits = load_claim_activity_splits(conn, event_id)
        else:
            splits = []

    if splits:
        res: Dict[str, float] = {}
        for s in splits:
            act_id = s.get("activity_id")
            if not act_id:
                continue
            raw_pct = s.get("split_pct")
            try:
                val = float(raw_pct) if raw_pct is not None else 1.0
            except (ValueError, TypeError):
                val = 0.0
            if val > 0.0:
                res[act_id] = val
        if res:
            return res

    matched_act = event_row.get("matched_activity_id")
    if matched_act and str(matched_act).strip():
        return {str(matched_act).strip(): 1.0}

    return {}


def is_missing_evidence_links_table_error(exc: Exception) -> bool:
    """
    Check if an exception is specifically caused by the evidence_links table not existing.
    Detects Postgres 42P01 / UndefinedTable and SQLite 'no such table: evidence_links'.
    Does NOT match syntax errors, constraint violations, connection errors, or general DB failures.
    """
    msg = str(exc).lower()
    if "evidence_links" not in msg:
        return False
    missing_patterns = [
        "does not exist",
        "no such table",
        "undefined_table",
        "42p01",
        "doesn't exist",
    ]
    return any(pat in msg for pat in missing_patterns)


def evidence_link_exists(
    conn: Any,
    event_id_a: str,
    event_id_b: str,
) -> bool:
    """
    Check if an evidence link between two events already exists (unordered pair).
    """
    try:
        row = conn.execute(
            """
            SELECT 1 FROM evidence_links
            WHERE (event_id_a = %s AND event_id_b = %s)
               OR (event_id_a = %s AND event_id_b = %s)
            LIMIT 1
            """,
            (event_id_a, event_id_b, event_id_b, event_id_a),
        ).fetchone()
        return row is not None
    except Exception as e:
        if is_missing_evidence_links_table_error(e):
            return False
        raise


def classify_evidence_fusion_relationship(
    event_a: Dict[str, Any],
    channel_a: str,
    val_a: float,
    event_b: Dict[str, Any],
    channel_b: str,
    val_b: float,
    activity_id: str,
    claim_mode: str,
    tolerance_pct: float = 10.0,
) -> Optional[Dict[str, Any]]:
    """
    Classify evidence relationship between two cross-channel claims (PRD v6 Feature 31).
    Reuses Feature 9 chronological comparator logic.

    CUMULATIVE_PCT:
    - same-date within tolerance -> CORROBORATES
    - same-date beyond tolerance -> CONTRADICTS
    - later higher cumulative pct -> normal progression, NOT contradiction (returns None)
    - deterministic regression/context inconsistency -> CONTRADICTS

    INCREMENTAL_QUANTITY:
    - classify only when context/UOM/metric are comparable.
    - differing quantities without proof of duplicate physical contribution are legitimate contributions -> None.
    - matching physical contribution with agreeing quantity -> CORROBORATES.
    - matching physical contribution with disagreeing quantity -> CONTRADICTS.

    Returns:
        {"relation_type": "CORROBORATES" | "CONTRADICTS", "confidence": float, "rationale": str} or None
    """
    date_a = normalize_date(event_a.get("event_date"))
    date_b = normalize_date(event_b.get("event_date"))
    if not date_a or not date_b:
        return None

    id_a = event_a.get("event_id", "A")
    id_b = event_b.get("event_id", "B")

    if claim_mode == "INCREMENTAL_QUANTITY":
        uom_a = event_a.get("claimed_uom")
        uom_b = event_b.get("claimed_uom")
        if uom_a and uom_b and uom_a.strip().lower() != uom_b.strip().lower():
            # Incomparable metrics
            return None

        if date_a == date_b:
            is_dup, dup_reason = has_deterministic_duplicate_evidence(event_a, event_b)
            if is_dup:
                diff = round(abs(val_a - val_b), 2)
                allowed_variance = (tolerance_pct / 100.0) * max(val_a, val_b, 1.0)
                if diff <= allowed_variance:
                    return {
                        "relation_type": "CORROBORATES",
                        "confidence": 0.95,
                        "rationale": (
                            f"Cross-channel agreement: {channel_a} (claim {id_a}) corroborates {channel_b} (claim {id_b}) "
                            f"on physical contribution for activity '{activity_id}' on {date_a}: {val_a} vs {val_b} ({dup_reason})."
                        ),
                    }
                else:
                    return {
                        "relation_type": "CONTRADICTS",
                        "confidence": 0.90,
                        "rationale": (
                            f"Cross-channel contradiction: {channel_a} (claim {id_a}) contradicts {channel_b} (claim {id_b}) "
                            f"on physical contribution for activity '{activity_id}' on {date_a}: {val_a} vs {val_b} ({dup_reason})."
                        ),
                    }
        # Legitimate different incremental contributions across time or shifts are not automatically contradictions
        return None

    # CUMULATIVE_PCT
    has_rework_a = has_rework_or_reset_context(event_a)
    has_rework_b = has_rework_or_reset_context(event_b)

    cmp = compare_chronological_claims(
        date_a=date_a,
        val_a=val_a,
        date_b=date_b,
        val_b=val_b,
        tolerance_pct=tolerance_pct,
        has_rework_a=has_rework_a,
        has_rework_b=has_rework_b,
    )

    rel = cmp.get("relationship")
    diff = cmp.get("variance_pct", 0.0)

    if rel == "SAME_DATE_AGREEMENT":
        conf = round(max(0.70, 1.0 - (diff / 100.0)), 2)
        return {
            "relation_type": "CORROBORATES",
            "confidence": conf,
            "rationale": (
                f"Cross-channel agreement: {channel_a} (claim {id_a}) corroborates {channel_b} (claim {id_b}) "
                f"for activity '{activity_id}' on {date_a}: {val_a}% vs {val_b}% (variance {diff}% <= tolerance {tolerance_pct}%)."
            ),
        }
    elif rel == "SAME_DATE_DISAGREEMENT":
        conf = round(min(1.0, 0.60 + (diff / 100.0)), 2)
        return {
            "relation_type": "CONTRADICTS",
            "confidence": conf,
            "rationale": (
                f"Cross-channel disagreement: {channel_a} (claim {id_a}) contradicts {channel_b} (claim {id_b}) "
                f"for activity '{activity_id}' on {date_a}: {val_a}% vs {val_b}% (variance {diff}% > tolerance {tolerance_pct}%)."
            ),
        }
    elif rel == "PROGRESS_REGRESSION":
        if date_a > date_b:
            newer_ch, newer_id, newer_val, newer_d = channel_a, id_a, val_a, date_a
            older_ch, older_id, older_val, older_d = channel_b, id_b, val_b, date_b
        else:
            newer_ch, newer_id, newer_val, newer_d = channel_b, id_b, val_b, date_b
            older_ch, older_id, older_val, older_d = channel_a, id_a, val_a, date_a

        conf = round(min(1.0, 0.60 + (diff / 100.0)), 2)
        return {
            "relation_type": "CONTRADICTS",
            "confidence": conf,
            "rationale": (
                f"Cross-channel progress regression: {newer_ch} (claim {newer_id}) reports {newer_val}% on {newer_d}, "
                f"contradicting {older_ch} (claim {older_id}) reporting {older_val}% on {older_d} "
                f"for activity '{activity_id}' without accepted rework."
            ),
        }
    elif rel == "NORMAL_PROGRESSION":
        # Later higher cumulative percentage -> normal progression, NOT contradiction
        return None
    elif rel == "ACCEPTED_REWORK":
        # Progress regression accepted with rework context -> NOT contradiction
        return None

    return None


def evaluate_evidence_fusion(
    conn: Any,
    event_id: str,
    schedule_id: str,
    event_row: Dict[str, Any],
    splits: Optional[List[Dict[str, Any]]] = None,
    tolerance_pct: float = 10.0,
    window_days: int = 14,
    persist: bool = True,
) -> List[Dict[str, Any]]:
    """
    Evaluate Feature 31 Evidence Fusion for an execution event:
    - Compares current event with candidate events sharing same or overlapping activity.
    - Within configured comparison window (window_days).
    - Different source_documents.document_type (or input_channel).
    - Reuses Feature 9 chronological comparator.
    - Prevents duplicate A-B / B-A link pairs.
    - Defensively persists links into evidence_links table.
    """
    channel_curr = get_event_document_type(conn, event_row)
    if not channel_curr:
        return []

    curr_activities = get_event_activities(conn, event_row, splits)
    if not curr_activities:
        return []

    event_date = normalize_date(event_row.get("event_date"))
    if not event_date:
        return []

    claim_mode = event_row.get("claim_mode") or "CUMULATIVE_PCT"

    try:
        candidate_rows = conn.execute(
            """
            SELECT * FROM execution_events
            WHERE schedule_id = %s AND event_id != %s
            """,
            (schedule_id, event_id),
        ).fetchall()
        candidates = [dict(r) for r in candidate_rows] if candidate_rows else []
    except Exception:
        candidates = []

    links_to_insert: List[Dict[str, Any]] = []
    seen_pairs: Set[Tuple[str, str]] = set()

    for other in candidates:
        other_id = other.get("event_id")
        if not other_id:
            continue

        other_date = normalize_date(other.get("event_date"))
        if not other_date:
            continue

        if abs((event_date - other_date).days) > window_days:
            continue

        channel_other = get_event_document_type(conn, other)
        if not channel_other:
            continue

        if channel_curr.strip().upper() == channel_other.strip().upper():
            # Same channel -> no evidence link
            continue

        other_splits = load_claim_activity_splits(conn, other_id)
        other_activities = get_event_activities(conn, other, other_splits)
        common_activities = set(curr_activities.keys()) & set(other_activities.keys())
        if not common_activities:
            # Unrelated activity -> no link
            continue

        pair_key = (min(event_id, other_id), max(event_id, other_id))
        if pair_key in seen_pairs:
            continue
        if evidence_link_exists(conn, pair_key[0], pair_key[1]):
            continue

        for act_id in sorted(list(common_activities)):
            weight_curr = curr_activities[act_id]
            weight_other = other_activities[act_id]

            if claim_mode == "INCREMENTAL_QUANTITY":
                raw_qty_curr = event_row.get("claimed_quantity")
                raw_qty_other = other.get("claimed_quantity")
                if raw_qty_curr is None or raw_qty_other is None:
                    continue
                val_curr = round(float(raw_qty_curr) * weight_curr, 4)
                val_other = round(float(raw_qty_other) * weight_other, 4)
            else:
                raw_pct_curr = event_row.get("claimed_pct")
                raw_pct_other = other.get("claimed_pct")
                if raw_pct_curr is None or raw_pct_other is None:
                    continue
                val_curr = round(float(raw_pct_curr) * weight_curr, 4)
                val_other = round(float(raw_pct_other) * weight_other, 4)

            classified = classify_evidence_fusion_relationship(
                event_a=event_row,
                channel_a=channel_curr,
                val_a=val_curr,
                event_b=other,
                channel_b=channel_other,
                val_b=val_other,
                activity_id=act_id,
                claim_mode=claim_mode,
                tolerance_pct=tolerance_pct,
            )

            if classified:
                link_record = {
                    "link_id": str(uuid.uuid4()),
                    "event_id_a": pair_key[0],
                    "event_id_b": pair_key[1],
                    "relation_type": classified["relation_type"],
                    "confidence": classified["confidence"],
                    "rationale": classified["rationale"],
                    "created_at": datetime.now(),
                }
                links_to_insert.append(link_record)
                seen_pairs.add(pair_key)
                break

    if persist:
        for link in links_to_insert:
            try:
                conn.execute(
                    """
                    INSERT INTO evidence_links (
                        link_id, event_id_a, event_id_b,
                        relation_type, confidence, rationale, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        link["link_id"],
                        link["event_id_a"],
                        link["event_id_b"],
                        link["relation_type"],
                        link["confidence"],
                        link["rationale"],
                        link["created_at"],
                    ),
                )
            except Exception as e:
                if is_missing_evidence_links_table_error(e):
                    # Suppress ONLY missing evidence_links table error
                    break
                raise

    return links_to_insert


# ============================================================================
# Feature 32: Smart Review Priority
# ============================================================================

def is_missing_column_error(exc: Exception, column_names: Sequence[str]) -> bool:
    """
    Check if an exception is specifically caused by missing columns in a table.
    Detects Postgres 42703 / UndefinedColumn, SQLite 'no such column', etc.
    Does NOT swallow syntax errors, constraint errors, connection failures, etc.
    """
    msg = str(exc).lower()
    missing_patterns = [
        "no such column",
        "does not exist",
        "undefined_column",
        "42703",
        "unknown column",
    ]
    if not any(pat in msg for pat in missing_patterns):
        return False
    return any(col.lower() in msg for col in column_names)


# PRD v6 Section 12 explicit critical physical/sequence rules
CRITICAL_ERROR_RULES: Set[str] = {
    "VAL_OUT_OF_SEQUENCE",
    "VAL_OVER_100",
    "VAL_REOPENED_COMPLETED_ACTIVITY",
}


def get_queue_entry_timestamp(event_row: Dict[str, Any]) -> Tuple[Any, str]:
    """
    Determine the timestamp representing entry into review/review-required state.
    Prefers an explicit review-entry timestamp if available in the event record
    (e.g., review_entered_at, review_required_at, queued_at).
    If no such timestamp exists, uses execution_events.created_at as the documented fallback.
    Does NOT fabricate a queue-entry timestamp.
    """
    candidates = [
        ("review_entered_at", event_row.get("review_entered_at")),
        ("review_required_at", event_row.get("review_required_at")),
        ("queued_at", event_row.get("queued_at")),
    ]
    for name, ts in candidates:
        if ts is not None:
            return ts, name

    # Documented fallback: execution_events schema does not define a dedicated review-entry timestamp
    return event_row.get("created_at"), "execution_events.created_at (documented fallback)"


def compute_queue_hours(timestamp: Any, current_time: Optional[datetime] = None) -> float:
    """
    Compute elapsed hours in review queue from the queue entry timestamp (or fallback created_at).
    Recomputed fresh each time; does not accumulate previous stored aging.
    """
    if not timestamp:
        return 0.0
    if current_time is None:
        current_time = datetime.now(timezone.utc)

    dt: Optional[datetime] = None
    if isinstance(timestamp, str):
        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except Exception:
            try:
                dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
            except Exception:
                return 0.0
    elif isinstance(timestamp, datetime):
        dt = timestamp
    elif isinstance(timestamp, date):
        dt = datetime.combine(timestamp, datetime.min.time())

    if dt is None:
        return 0.0

    if dt.tzinfo is not None and current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    elif dt.tzinfo is None and current_time.tzinfo is not None:
        dt = dt.replace(tzinfo=timezone.utc)

    diff_seconds = (current_time - dt).total_seconds()
    return max(0.0, diff_seconds / 3600.0)


def compute_priority_aging(hours_in_queue: float) -> float:
    """
    PRD v6 Feature 32 Aging calculation:
    aging = min(30, 6 * ln(1 + hours_in_queue))
    """
    if hours_in_queue <= 0.0:
        return 0.0
    val = 6.0 * math.log(1.0 + hours_in_queue)
    return round(min(30.0, val), 2)


def compute_criticality_multiplier(
    total_float: Optional[float],
    is_critical: Optional[bool] = None,
) -> Tuple[float, str]:
    """
    Compute criticality multiplier based on schedule_activities.total_float or authoritative is_critical.

    Rules:
    - total_float <= 0 -> 2.0 (critical path)
    - > 0 and <= 5 -> 1.5 (near-critical)
    - > 5 -> 1.0 (non-critical)
    - If total_float is None / unavailable:
        - authoritative is_critical=True -> 2.0
        - otherwise -> neutral 1.0 (documented fallback; float not fabricated)
    """
    if total_float is not None:
        try:
            tf = float(total_float)
            if tf <= 0.0:
                return 2.0, f"Criticality multiplier 2.0x: Critical path activity (total_float = {tf:g}d <= 0d)"
            elif tf <= 5.0:
                return 1.5, f"Criticality multiplier 1.5x: Near-critical activity (total_float = {tf:g}d <= 5d)"
            else:
                return 1.0, f"Criticality multiplier 1.0x: Non-critical activity (total_float = {tf:g}d > 5d)"
        except (ValueError, TypeError):
            pass

    if is_critical is True or str(is_critical).lower() in ("true", "1", "t"):
        return 2.0, "Criticality multiplier 2.0x: Authoritative is_critical = True (total_float not provided)"

    return 1.0, "Criticality multiplier 1.0x: Neutral fallback (total_float and is_critical unavailable; float not fabricated)"


def fetch_activity_schedule_criticality(
    conn: Any,
    schedule_id: str,
    activity_id: str,
) -> Tuple[Optional[float], Optional[bool]]:
    """
    Fetch total_float and is_critical for an activity from schedule_activities.
    Handles unmigrated columns defensively without raising schema errors.
    """
    if not conn or not schedule_id or not activity_id:
        return None, None

    try:
        row = conn.execute(
            """
            SELECT * FROM schedule_activities
            WHERE schedule_id = %s AND activity_id = %s
            """,
            (schedule_id, activity_id),
        ).fetchone()
        if not row:
            return None, None

        row_dict = dict(row) if hasattr(row, "keys") else (row if isinstance(row, dict) else {})
        tf_val = row_dict.get("total_float")
        ic_val = row_dict.get("is_critical")

        tf = float(tf_val) if tf_val is not None else None
        ic = bool(ic_val) if ic_val is not None else None
        return tf, ic
    except Exception as e:
        msg = str(e).lower()
        if "schedule_activities" in msg and ("does not exist" in msg or "no such table" in msg):
            return None, None
        raise


def evaluate_smart_review_priority(
    event_row: Dict[str, Any],
    validation_issues: Optional[List[Dict[str, Any]]] = None,
    conflicts: Optional[List[Dict[str, Any]]] = None,
    evidence_links: Optional[List[Dict[str, Any]]] = None,
    splits: Optional[List[Dict[str, Any]]] = None,
    candidate_matches: Optional[List[Dict[str, Any]]] = None,
    conn: Optional[Any] = None,
    activity_info: Optional[Dict[str, Any]] = None,
    current_time: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    PRD v6 Feature 32 Smart Review Priority evaluation.

    BASE SEVERITY (strongest applicable category, NOT additive):
    100: Critical physical/sequence error (VAL_OUT_OF_SEQUENCE, VAL_OVER_100, VAL_REOPENED_COMPLETED_ACTIVITY, etc.)
     70: Open conflict OR evidence CONTRADICTS
     40: Incoming UNMATCHED OR real WBS ambiguity/inconsistency (valid WBS split alone != 40)
    Warnings only: min(25, 10 * warning_count)
    No flags: 5

    CRITICALITY MULTIPLIER:
    total_float <= 0 -> 2.0
    total_float > 0 and <= 5 -> 1.5
    total_float > 5 -> 1.0
    unavailable -> authoritative is_critical=True (2.0) or neutral 1.0 fallback

    AGING:
    aging = min(30, 6 * ln(1 + hours_in_queue))
    Recomputed from source timestamp each time.

    FINAL:
    priority_score = base_severity * criticality_multiplier + aging
    """
    issues = validation_issues or []
    event_id = event_row.get("event_id", "")
    schedule_id = event_row.get("schedule_id", "")

    # 1. Base Severity Analysis
    # A. Critical physical/sequence errors (Base 100)
    # Strictly explicit critical rule set per PRD v6 Section 12;
    # generic ERROR issues or VAL_EVIDENCE_MISMATCH are NOT promoted to Base 100.
    critical_issues = [
        iss for iss in issues
        if iss.get("rule_code") in CRITICAL_ERROR_RULES
    ]
    has_critical_error = len(critical_issues) > 0

    # B. Conflict / Contradiction (Base 70)
    conflict_list = conflicts if conflicts is not None else []
    if conflicts is None and conn and event_id:
        try:
            c_rows = conn.execute(
                """
                SELECT * FROM conflict_records
                WHERE (event_id_a = %s OR event_id_b = %s) AND status = 'OPEN'
                """,
                (event_id, event_id),
            ).fetchall()
            if c_rows:
                conflict_list = [dict(r) for r in c_rows]
        except Exception:
            pass

    open_conflicts = [
        c for c in conflict_list
        if str(c.get("status", "OPEN")).upper() == "OPEN"
    ]
    has_open_conflict = len(open_conflicts) > 0

    links_list = evidence_links if evidence_links is not None else []
    if evidence_links is None and conn and event_id:
        try:
            l_rows = conn.execute(
                """
                SELECT * FROM evidence_links
                WHERE (event_id_a = %s OR event_id_b = %s) AND relation_type = 'CONTRADICTS'
                """,
                (event_id, event_id),
            ).fetchall()
            if l_rows:
                links_list = [dict(r) for r in l_rows]
        except Exception as e:
            if is_missing_evidence_links_table_error(e):
                pass
            else:
                raise

    contradict_links = [
        l for l in links_list
        if str(l.get("relation_type", "")).upper() == "CONTRADICTS"
    ]
    has_contradiction = len(contradict_links) > 0

    # C. Unmatched or real WBS decomposition ambiguity/inconsistency (Base 40)
    wbs_issues = [
        iss for iss in issues
        if iss.get("rule_code") in {"VAL_SPLIT_INCONSISTENCY", "VAL_INVALID_SPLIT"}
    ]
    has_wbs_inconsistency = len(wbs_issues) > 0

    is_unmatched = False
    unmatched_reason = ""
    matched_act = event_row.get("matched_activity_id")
    has_valid_splits = bool(splits and len(splits) > 0 and not has_wbs_inconsistency)

    top_tier = None
    if candidate_matches and len(candidate_matches) > 0:
        top_tier = candidate_matches[0].get("match_tier")
    elif conn and event_id:
        try:
            cand_r = conn.execute(
                """
                SELECT match_tier FROM candidate_matches
                WHERE event_id = %s
                ORDER BY rank_order ASC
                LIMIT 1
                """,
                (event_id,),
            ).fetchone()
            if cand_r:
                top_tier = cand_r.get("match_tier") if isinstance(cand_r, dict) else cand_r["match_tier"]
        except Exception:
            pass

    if str(event_row.get("status", "")).upper() == "UNMATCHED" or str(top_tier).upper() == "UNMATCHED":
        is_unmatched = True
        unmatched_reason = "Claim match tier is explicitly UNMATCHED."
    elif not matched_act and not has_valid_splits and not has_wbs_inconsistency:
        is_unmatched = True
        unmatched_reason = "No matched schedule activity found and no valid split decomposition provided."

    # D. Administrative warnings & non-critical issues (capped at 25)
    # Includes all validation issues not classified into Base 100 or Base 40
    # (e.g. VAL_EVIDENCE_MISMATCH, administrative warnings, and non-critical flags)
    warning_issues = [
        iss for iss in issues
        if iss.get("rule_code") not in CRITICAL_ERROR_RULES
        and iss.get("rule_code") not in {"VAL_SPLIT_INCONSISTENCY", "VAL_INVALID_SPLIT"}
    ]
    warning_count = len(warning_issues)

    # Determine Base Severity using strongest applicable category
    if has_critical_error:
        base_severity = 100.0
        c_code = critical_issues[0].get("rule_code", "CRITICAL_ERROR")
        severity_reason = f"Base severity 100: Critical physical/sequence error ({c_code})."
        cause_descriptions = [f"{iss.get('rule_code')}: {iss.get('description')}" for iss in critical_issues[:2]]
        cause_reason = "; ".join(cause_descriptions)
    elif has_open_conflict or has_contradiction:
        base_severity = 70.0
        reasons_70 = []
        if has_open_conflict:
            c_desc = f"{len(open_conflicts)} open conflict(s)"
            if open_conflicts and "variance_pct" in open_conflicts[0]:
                c_desc += f" (variance {open_conflicts[0]['variance_pct']}%)"
            reasons_70.append(c_desc)
        if has_contradiction:
            l_desc = f"{len(contradict_links)} cross-channel evidence contradiction(s)"
            if contradict_links and "rationale" in contradict_links[0]:
                l_desc += f" ({contradict_links[0]['rationale']})"
            reasons_70.append(l_desc)
        severity_reason = "Base severity 70: Open progress conflict or cross-channel evidence contradiction."
        cause_reason = "; ".join(reasons_70)
    elif is_unmatched or has_wbs_inconsistency:
        base_severity = 40.0
        if is_unmatched and has_wbs_inconsistency:
            w_desc = "; ".join(f"{iss.get('rule_code')}: {iss.get('description')}" for iss in wbs_issues)
            severity_reason = "Base severity 40: Unmatched claim with WBS decomposition inconsistency."
            cause_reason = f"{unmatched_reason}; {w_desc}"
        elif is_unmatched:
            severity_reason = "Base severity 40: Incoming UNMATCHED claim requiring supervisor review."
            cause_reason = unmatched_reason
        else:
            w_desc = "; ".join(f"{iss.get('rule_code')}: {iss.get('description')}" for iss in wbs_issues)
            severity_reason = "Base severity 40: WBS decomposition ambiguity or split inconsistency."
            cause_reason = w_desc
    elif warning_count > 0:
        base_severity = float(min(25, 10 * warning_count))
        severity_reason = f"Base severity {int(base_severity)}: Administrative warnings ({warning_count} warning(s), capped at 25)."
        w_desc = "; ".join(f"{iss.get('rule_code')}: {iss.get('description')}" for iss in warning_issues[:3])
        cause_reason = f"Administrative warnings flagged: {w_desc}"
    else:
        base_severity = 5.0
        severity_reason = "Base severity 5: Routine claim with no flags or issues."
        cause_reason = "Routine progress claim; all sequence, physical, and cross-channel checks passed normally."

    # 2. Criticality Multiplier
    activities_to_evaluate: List[str] = []
    if splits:
        for s in splits:
            act_id = s.get("activity_id")
            raw_pct = s.get("split_pct")
            try:
                pct_val = float(raw_pct) if raw_pct is not None else 1.0
            except (ValueError, TypeError):
                pct_val = 0.0
            if act_id and pct_val > 0.0 and act_id not in activities_to_evaluate:
                activities_to_evaluate.append(str(act_id).strip())

    if not activities_to_evaluate:
        if matched_act:
            activities_to_evaluate.append(str(matched_act).strip())

    best_multiplier = 1.0
    best_crit_reason = "Criticality multiplier 1.0x: Neutral fallback (total_float and is_critical unavailable; float not fabricated)"

    if activities_to_evaluate:
        multipliers: List[Tuple[float, str]] = []
        for act in activities_to_evaluate:
            tf: Optional[float] = None
            ic: Optional[bool] = None

            if activity_info:
                if act in activity_info:
                    act_entry = activity_info[act]
                    if isinstance(act_entry, dict):
                        tf = act_entry.get("total_float")
                        ic = act_entry.get("is_critical")
                elif "total_float" in activity_info or "is_critical" in activity_info:
                    tf = activity_info.get("total_float")
                    ic = activity_info.get("is_critical")

            if tf is None and ic is None and conn:
                tf, ic = fetch_activity_schedule_criticality(conn, schedule_id, act)

            m, reason = compute_criticality_multiplier(tf, ic)
            if len(activities_to_evaluate) > 1:
                reason = f"Activity '{act}': {reason}"
            multipliers.append((m, reason))

        if multipliers:
            # Pick the strongest multiplier (e.g. 2.0 > 1.5 > 1.0)
            multipliers.sort(key=lambda x: x[0], reverse=True)
            best_multiplier = multipliers[0][0]
            best_crit_reason = multipliers[0][1]

    # 3. Aging Calculation
    # PRD calls it hours_in_queue. Prefer existing review-entry timestamp if available,
    # otherwise fallback to execution_events.created_at (documented limitation; no fabricated timestamp).
    queue_ts, ts_source_name = get_queue_entry_timestamp(event_row)
    hours_in_queue = compute_queue_hours(queue_ts, current_time=current_time)
    aging = compute_priority_aging(hours_in_queue)
    if hours_in_queue > 0.0:
        aging_reason = (
            f"Queue aging +{aging:g} points ({hours_in_queue:.1f}h elapsed in review queue "
            f"via {ts_source_name}; formula: min(30, 6*ln(1+hours)))."
        )
    else:
        aging_reason = f"Queue aging +0.0 points (recently queued via {ts_source_name}; 0.0h elapsed)."

    # 4. Final Score & Structured Reasons
    priority_score = round(base_severity * best_multiplier + aging, 2)
    priority_reasons = (
        f"[Severity] {severity_reason}\n"
        f"[Cause] {cause_reason}\n"
        f"[Criticality] {best_crit_reason}\n"
        f"[Aging] {aging_reason}"
    )

    return {
        "priority_score": priority_score,
        "priority_reasons": priority_reasons,
        "base_severity": base_severity,
        "criticality_multiplier": best_multiplier,
        "aging": aging,
        "hours_in_queue": hours_in_queue,
        "reasons_breakdown": {
            "severity_reason": severity_reason,
            "cause_reason": cause_reason,
            "criticality_reason": best_crit_reason,
            "aging_reason": aging_reason,
            "aging_timestamp_source": ts_source_name,
        },
    }


@router.post("/claims/{event_id}/check")
def check_claim(
    event_id: str,
    event_context: EventContext = Depends(gates.event_process),
):
    with get_connection() as conn:
        with conn.transaction():
            event_row = conn.execute(
                """
                SELECT * FROM execution_events
                WHERE event_id = %s
                FOR UPDATE
                """,
                (event_id,),
            ).fetchone()

            if not event_row:
                raise HTTPException(
                    status_code=404,
                    detail=f"Claim event {event_id} not found.",
                )

            before_state = dict(event_row)
            incoming_status = event_row.get("status")

            schedule_id = event_row["schedule_id"]
            matched_activity_id = event_row.get("matched_activity_id")
            event_date = event_row["event_date"]
            claimed_pct = event_row.get("claimed_pct")
            claimed_qty = event_row.get("claimed_quantity")
            claimed_uom = event_row.get("claimed_uom")
            photo_path = event_row.get("photo_path")
            claim_mode = event_row.get("claim_mode") or "CUMULATIVE_PCT"
            event_type = event_row.get("event_type")

            activity_row = None
            if matched_activity_id:
                activity_row = conn.execute(
                    """
                    SELECT * FROM schedule_activities
                    WHERE schedule_id = %s AND activity_id = %s
                    """,
                    (schedule_id, matched_activity_id),
                ).fetchone()

            validation_issues: List[Dict[str, Any]] = []
            conflict_records_to_insert: List[Dict[str, Any]] = []

            # Check for WBS splits (PRD v6 Feature 30)
            splits = load_claim_activity_splits(conn, event_id)
            has_splits = len(splits) > 0
            has_matched = matched_activity_id is not None and str(matched_activity_id).strip() != ""

            # Check mutual exclusivity and consistency after match
            if has_matched and has_splits:
                validation_issues.append(
                    {
                        "issue_id": str(uuid.uuid4()),
                        "event_id": event_id,
                        "rule_code": "VAL_SPLIT_INCONSISTENCY",
                        "severity": "ERROR",
                        "description": (
                            f"Inconsistent matching state: Event '{event_id}' has both matched_activity_id "
                            f"('{matched_activity_id}') and {len(splits)} WBS split rows. Normal match and split are mutually exclusive."
                        ),
                    }
                )
            elif (
                not has_matched
                and not has_splits
                and incoming_status in ("MATCHED", "VALIDATED", "REVIEW_REQUIRED", "APPROVED", "EDITED")
            ):
                validation_issues.append(
                    {
                        "issue_id": str(uuid.uuid4()),
                        "event_id": event_id,
                        "rule_code": "VAL_SPLIT_INCONSISTENCY",
                        "severity": "ERROR",
                        "description": (
                            f"Inconsistent matching state: Event '{event_id}' has status '{incoming_status}' "
                            "but has neither a matched_activity_id nor WBS split rows."
                        ),
                    }
                )

            if has_splits and not has_matched:
                # SPLIT PATH: Multi-activity WBS split decomposition
                split_issues, split_conflicts = validate_split_claim(
                    conn=conn,
                    event_id=event_id,
                    schedule_id=schedule_id,
                    event_row=dict(event_row),
                    splits=splits,
                )
                validation_issues.extend(split_issues)
                conflict_records_to_insert.extend(split_conflicts)
            else:
                # NORMAL PATH: Single activity validation
                # 1. Incoming UNMATCHED status check
                if incoming_status == "UNMATCHED":
                    validation_issues.append(
                        {
                            "issue_id": str(uuid.uuid4()),
                            "event_id": event_id,
                            "rule_code": "VAL_LOW_MATCH_CONFIDENCE",
                            "severity": "WARNING",
                            "description": "Match confidence fell below matching threshold.",
                        }
                    )

                # 2. Granularity Rollup vs Directional Conflict Evaluation (PRD v6 Feature 9)
                if claim_mode == "INCREMENTAL_QUANTITY":
                    inc_issues, inc_conflicts, derived_pct = (
                        evaluate_incremental_quantity_anomalies(
                            conn=conn,
                            schedule_id=schedule_id,
                            matched_activity_id=matched_activity_id,
                            event_id=event_id,
                            event_date=event_date,
                            claimed_qty=claimed_qty,
                            claimed_uom=claimed_uom,
                            activity_row=dict(activity_row) if activity_row else None,
                            raw_claim_text=event_row.get("raw_claim_text"),
                            current_claim=dict(event_row),
                        )
                    )
                    validation_issues.extend(inc_issues)
                    conflict_records_to_insert.extend(inc_conflicts)
                    if derived_pct is not None:
                        claimed_pct = derived_pct
                else:
                    # CUMULATIVE_PCT
                    if claimed_pct is not None and claimed_pct > 100.0:
                        validation_issues.append(
                            {
                                "issue_id": str(uuid.uuid4()),
                                "event_id": event_id,
                                "rule_code": "VAL_OVER_100",
                                "severity": "ERROR",
                                "description": f"Claimed progress percentage ({claimed_pct}%) exceeds 100%.",
                            }
                        )

                    # Directional Chronological Conflict Detection
                    if matched_activity_id and event_date and claimed_pct is not None:
                        cum_issues, cum_conflicts = evaluate_cumulative_conflicts(
                            conn=conn,
                            schedule_id=schedule_id,
                            matched_activity_id=matched_activity_id,
                            event_id=event_id,
                            event_date=event_date,
                            claimed_pct=claimed_pct,
                            current_claim=dict(event_row),
                        )
                        validation_issues.extend(cum_issues)
                        conflict_records_to_insert.extend(cum_conflicts)

                # 3. VAL_NEGATIVE (for cumulative percentage if not already flagged)
                if claimed_pct is not None and claimed_pct < 0.0:
                    if not any(iss["rule_code"] == "VAL_NEGATIVE" for iss in validation_issues):
                        validation_issues.append(
                            {
                                "issue_id": str(uuid.uuid4()),
                                "event_id": event_id,
                                "rule_code": "VAL_NEGATIVE",
                                "severity": "ERROR",
                                "description": f"Claimed progress percentage ({claimed_pct}%) cannot be negative.",
                            }
                        )

                # 4. VAL_OUT_OF_SEQUENCE (PRD v6 Sequence Validation)
                seq_issues = evaluate_sequence_validation(
                    conn=conn,
                    schedule_id=schedule_id,
                    matched_activity_id=matched_activity_id,
                    event_id=event_id,
                    event_type=event_type,
                    event_date=event_date,
                    claimed_pct=claimed_pct,
                    claimed_qty=claimed_qty,
                )
                validation_issues.extend(seq_issues)

                # 5. VAL_REOPENED_COMPLETED_ACTIVITY
                if (
                    claim_mode == "CUMULATIVE_PCT"
                    and claimed_pct is not None
                    and claimed_pct < 100.0
                    and matched_activity_id
                ):
                    actual_row = conn.execute(
                        """
                        SELECT actual_finish FROM approved_actuals
                        WHERE schedule_id = %s AND activity_id = %s
                        """,
                        (schedule_id, matched_activity_id),
                    ).fetchone()

                    if actual_row and actual_row.get("actual_finish") is not None:
                        validation_issues.append(
                            {
                                "issue_id": str(uuid.uuid4()),
                                "event_id": event_id,
                                "rule_code": "VAL_REOPENED_COMPLETED_ACTIVITY",
                                "severity": "ERROR",
                                "description": "Claim reported for an activity that was already marked as completed.",
                            }
                        )

            # 6. VAL_EVIDENCE_MISMATCH
            if photo_path:
                if not os.path.exists(photo_path):
                    validation_issues.append(
                        {
                            "issue_id": str(uuid.uuid4()),
                            "event_id": event_id,
                            "rule_code": "VAL_EVIDENCE_MISMATCH",
                            "severity": "ERROR",
                            "description": f"Evidence photo file not found at path '{photo_path}'.",
                        }
                    )
                else:
                    exif_data = _parse_exif(photo_path)
                    if "datetime_original" in exif_data:
                        try:
                            raw_dt = (
                                exif_data["datetime_original"]
                                .split()[0]
                                .replace(":", "-")
                            )
                            exif_date = datetime.strptime(
                                raw_dt, "%Y-%m-%d"
                            ).date()
                            if (
                                event_date
                                and abs((exif_date - event_date).days) > 2
                            ):
                                validation_issues.append(
                                    {
                                        "issue_id": str(uuid.uuid4()),
                                        "event_id": event_id,
                                        "rule_code": "VAL_EVIDENCE_MISMATCH",
                                        "severity": "WARNING",
                                        "description": f"Photo EXIF date ({exif_date}) differs from event date ({event_date}) by > 2 days.",
                                    }
                                )
                        except Exception:
                            pass

                    site_lat = float(os.getenv("PROJECT_SITE_LAT", "27.4728"))
                    site_lon = float(os.getenv("PROJECT_SITE_LON", "95.3547"))
                    site_radius = float(
                        os.getenv("PROJECT_SITE_RADIUS_KM", "5.0")
                    )

                    if exif_data.get("has_gps"):
                        photo_lat = exif_data["lat"]
                        photo_lon = exif_data["lon"]
                        dist = haversine_distance_km(
                            photo_lat, photo_lon, site_lat, site_lon
                        )
                        if dist > site_radius:
                            validation_issues.append(
                                {
                                    "issue_id": str(uuid.uuid4()),
                                    "event_id": event_id,
                                    "rule_code": "VAL_EVIDENCE_MISMATCH",
                                    "severity": "WARNING",
                                    "description": f"Photo GPS location is {round(dist, 2)}km from project site (exceeds {site_radius}km radius).",
                                }
                            )

            # Candidate Match check
            match_row = conn.execute(
                """
                SELECT match_tier FROM candidate_matches
                WHERE event_id = %s AND rank_order = 1
                """,
                (event_id,),
            ).fetchone()
            is_unmatched_tier = False
            if match_row and match_row.get("match_tier") in (
                "UNMATCHED",
                "HARD_MISMATCH",
            ):
                is_unmatched_tier = True

            # Determine Final Status
            has_any_flags = (
                len(validation_issues) > 0 or len(conflict_records_to_insert) > 0
            )

            new_status = "VALIDATED"
            if (
                incoming_status == "UNMATCHED"
                or is_unmatched_tier
                or has_any_flags
            ):
                new_status = "REVIEW_REQUIRED"

            # Update Database Records
            conn.execute(
                """
                UPDATE execution_events
                SET status = %s, claimed_pct = %s
                WHERE event_id = %s
                """,
                (new_status, claimed_pct, event_id),
            )

            conn.execute(
                "DELETE FROM validation_issues WHERE event_id = %s",
                (event_id,),
            )

            conn.execute(
                "DELETE FROM conflict_records WHERE event_id_a = %s OR event_id_b = %s",
                (event_id, event_id),
            )

            try:
                conn.execute(
                    "DELETE FROM evidence_links WHERE event_id_a = %s OR event_id_b = %s",
                    (event_id, event_id),
                )
            except Exception as e:
                if is_missing_evidence_links_table_error(e):
                    pass
                else:
                    raise

            for issue in validation_issues:
                conn.execute(
                    """
                    INSERT INTO validation_issues (issue_id, event_id, rule_code, severity, description)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        issue["issue_id"],
                        issue["event_id"],
                        issue["rule_code"],
                        issue["severity"],
                        issue["description"],
                    ),
                )

            for conf in conflict_records_to_insert:
                conn.execute(
                    """
                    INSERT INTO conflict_records (
                        conflict_id, schedule_id, activity_id, reporting_period,
                        event_id_a, event_id_b, value_a, value_b, variance_pct, status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        conf["conflict_id"],
                        conf["schedule_id"],
                        conf["activity_id"],
                        conf["reporting_period"],
                        conf["event_id_a"],
                        conf["event_id_b"],
                        conf["value_a"],
                        conf["value_b"],
                        conf["variance_pct"],
                        conf["status"],
                    ),
                )

            # Evidence Fusion (PRD v6 Feature 31)
            event_row_for_fusion = dict(event_row)
            event_row_for_fusion["claimed_pct"] = claimed_pct
            evidence_links = evaluate_evidence_fusion(
                conn=conn,
                event_id=event_id,
                schedule_id=schedule_id,
                event_row=event_row_for_fusion,
                splits=splits,
                tolerance_pct=CONFLICT_TOLERANCE_PCT,
                window_days=EVIDENCE_COMPARISON_WINDOW_DAYS,
                persist=True,
            )

            # Feature 32: Smart Review Priority
            priority_result = evaluate_smart_review_priority(
                event_row=event_row_for_fusion,
                validation_issues=validation_issues,
                conflicts=conflict_records_to_insert,
                evidence_links=evidence_links,
                splits=splits,
                conn=conn,
            )
            priority_score = priority_result["priority_score"]
            priority_reasons = priority_result["priority_reasons"]

            try:
                conn.execute(
                    """
                    UPDATE execution_events
                    SET priority_score = %s, priority_reasons = %s
                    WHERE event_id = %s
                    """,
                    (priority_score, priority_reasons, event_id),
                )
            except Exception as e:
                if is_missing_column_error(e, ["priority_score", "priority_reasons"]):
                    pass
                else:
                    raise

            after_state = dict(before_state)
            after_state["status"] = new_status
            after_state["claimed_pct"] = claimed_pct
            after_state["priority_score"] = priority_score
            after_state["priority_reasons"] = priority_reasons

            # Transactional Audit Log with actor SYSTEM:M4
            write_audit_log(
                entity_type="execution_events",
                entity_id=event_id,
                action="CHECK_CLAIM",
                actor_id=str(event_context.project_context.user.id),
                before_state=before_state,
                after_state=after_state,
                payload={"performed_by": SYSTEM_ACTOR_M4},
                project_id=event_context.project_id,
                schedule_id=schedule_id,
                role=event_context.role,
            )

            return {
                "event_id": event_id,
                "status": new_status,
                "claimed_pct": claimed_pct,
                "validation_issues": validation_issues,
                "conflicts": conflict_records_to_insert,
                "evidence_links": evidence_links,
                "priority_score": priority_score,
                "priority_reasons": priority_reasons,
            }


@router.get("/claims/{event_id}/conflicts")
def get_claim_conflicts(event_id: str, _event_context: EventContext = Depends(gates.event_review)):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM conflict_records
            WHERE event_id_a = %s OR event_id_b = %s
            ORDER BY reporting_period DESC
            """,
            (event_id, event_id),
        ).fetchall()
        return {"event_id": event_id, "conflicts": [dict(r) for r in rows]}


@router.get("/claims/{event_id}/validation")
def get_claim_validation(event_id: str, _event_context: EventContext = Depends(gates.event_review)):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM validation_issues
            WHERE event_id = %s
            """,
            (event_id,),
        ).fetchall()
        return {
            "event_id": event_id,
            "validation_issues": [dict(r) for r in rows],
        }


@router.get("/claims/{event_id}/evidence")
def get_claim_evidence(event_id: str, _event_context: EventContext = Depends(gates.event_review)):
    """
    GET /api/v1/claims/{event_id}/evidence
    PRD v6 Feature 31 Evidence Fusion read endpoint (SUPERVISOR protected).

    - Returns evidence_links where event_id is event_id_a OR event_id_b
    - Preserves relation_type (CORROBORATES vs CONTRADICTS), confidence, rationale, created_at
    - Includes useful opposite-event / source-channel context
    - Keeps evidence_links separate from conflict_records
    - Suppresses only known missing evidence_links table migration case; propagates other DB errors
    """
    with get_connection() as conn:
        # Verify claim event existence
        ev_row = conn.execute(
            "SELECT event_id FROM execution_events WHERE event_id = %s",
            (event_id,),
        ).fetchone()
        if not ev_row:
            raise HTTPException(
                status_code=404,
                detail=f"Claim event {event_id} not found.",
            )

        try:
            rows = conn.execute(
                """
                SELECT * FROM evidence_links
                WHERE event_id_a = %s OR event_id_b = %s
                ORDER BY created_at DESC
                """,
                (event_id, event_id),
            ).fetchall()
        except Exception as e:
            if is_missing_evidence_links_table_error(e):
                return {
                    "event_id": event_id,
                    "evidence_links": [],
                    "evidence": [],
                    "total": 0,
                    "migration_blockers": [
                        "evidence_links table does not exist in database (PRD v6 Section 16.3 pending migration)."
                    ],
                }
            raise

        evidence_list = []
        for r in rows:
            r_dict = dict(r)
            id_a = r_dict.get("event_id_a")
            id_b = r_dict.get("event_id_b")
            opposite_id = id_b if id_a == event_id else id_a

            opposite_channel = None
            opposite_doc_type = None
            opposite_info = None

            if opposite_id:
                try:
                    opp_row = conn.execute(
                        """
                        SELECT ee.event_id, ee.event_date, ee.input_channel, ee.claimed_pct,
                               ee.claimed_quantity, ee.claimed_uom, ee.raw_claim_text,
                               sd.document_type, sd.file_name
                        FROM execution_events ee
                        LEFT JOIN source_documents sd ON ee.document_id = sd.document_id
                        WHERE ee.event_id = %s
                        """,
                        (opposite_id,),
                    ).fetchone()
                    if opp_row:
                        opp_dict = dict(opp_row)
                        opposite_channel = opp_dict.get("input_channel")
                        opposite_doc_type = opp_dict.get("document_type")
                        opposite_info = opp_dict
                except Exception:
                    pass

            item = {
                "link_id": r_dict.get("link_id"),
                "event_id_a": id_a,
                "event_id_b": id_b,
                "relation_type": r_dict.get("relation_type"),  # CORROBORATES / CONTRADICTS
                "confidence": r_dict.get("confidence"),
                "rationale": r_dict.get("rationale"),
                "created_at": r_dict.get("created_at"),
                "opposite_event_id": opposite_id,
                "opposite_channel": opposite_channel,
                "opposite_document_type": opposite_doc_type,
                "opposite_context": opposite_info,
            }
            evidence_list.append(item)

        return {
            "event_id": event_id,
            "evidence_links": evidence_list,
            "evidence": evidence_list,
            "total": len(evidence_list),
        }


@router.get("/review-queue")
def get_review_queue(
    sort: Optional[str] = "priority",
    schedule_id: Optional[str] = None,  # validated via schedule_context (explicit, belongs to the project)
    status: Optional[str] = None,
    current_time: Optional[datetime] = None,
    schedule_context: ScheduleContext = Depends(gates.claim_review_schedule),
):
    """
    GET /api/v1/review-queue?sort=priority
    PRD v6 Feature 32 Review Queue read endpoint (SUPERVISOR protected).

    - Includes statuses: VALIDATED, REVIEW_REQUIRED, HOLD
    - Recomputes priority fresh on every read using Feature 32 helpers
    - Recomputes aging from queue-entry timestamp/fallback without accumulating stored aging
    - Sorts priority_score DESC when sort=priority
    - Uses deterministic secondary sort for ties: created_at ASC, then event_id ASC
    - Persists refreshed priority only if columns exist; reports migration blocker if missing
    - Propagates unexpected DB errors
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)

    allowed_statuses = ("VALIDATED", "REVIEW_REQUIRED", "HOLD")
    if status and status.upper() in allowed_statuses:
        filter_statuses = (status.upper(),)
    else:
        filter_statuses = allowed_statuses

    with get_connection() as conn:
        query = f"""
            SELECT * FROM execution_events
            WHERE status IN ({','.join(['%s'] * len(filter_statuses))})
              AND schedule_id = %s AND project_id = %s
        """
        params: List[Any] = list(filter_statuses) + [schedule_context.schedule_id, str(schedule_context.project_id)]

        rows = conn.execute(query, tuple(params)).fetchall()

        queue_items = []
        columns_missing = False

        for row in rows:
            ev_dict = dict(row)
            ev_id = ev_dict.get("event_id")

            # 1. Fetch validation issues for this claim
            ev_issues = []
            try:
                iss_rows = conn.execute(
                    "SELECT * FROM validation_issues WHERE event_id = %s",
                    (ev_id,),
                ).fetchall()
                if iss_rows:
                    ev_issues = [dict(r) for r in iss_rows]
            except Exception:
                pass

            # 2. Fetch open conflicts for this claim
            ev_conflicts = []
            try:
                conf_rows = conn.execute(
                    """
                    SELECT * FROM conflict_records
                    WHERE (event_id_a = %s OR event_id_b = %s) AND status = 'OPEN'
                    """,
                    (ev_id, ev_id),
                ).fetchall()
                if conf_rows:
                    ev_conflicts = [dict(r) for r in conf_rows]
            except Exception:
                pass

            # 3. Fetch evidence links for this claim (handling missing table gracefully)
            ev_evidence = []
            try:
                ev_links_rows = conn.execute(
                    """
                    SELECT * FROM evidence_links
                    WHERE event_id_a = %s OR event_id_b = %s
                    """,
                    (ev_id, ev_id),
                ).fetchall()
                if ev_links_rows:
                    ev_evidence = [dict(r) for r in ev_links_rows]
            except Exception as e:
                if is_missing_evidence_links_table_error(e):
                    pass
                else:
                    raise

            # 4. Fetch WBS splits for this claim
            ev_splits = load_claim_activity_splits(conn, ev_id)

            # 5. Fetch candidate matches for this claim
            ev_candidates = []
            try:
                cand_rows = conn.execute(
                    "SELECT * FROM candidate_matches WHERE event_id = %s ORDER BY rank_order ASC",
                    (ev_id,),
                ).fetchall()
                if cand_rows:
                    ev_candidates = [dict(r) for r in cand_rows]
            except Exception:
                pass

            # 6. Recompute priority fresh using Feature 32 evaluator
            prio_res = evaluate_smart_review_priority(
                event_row=ev_dict,
                validation_issues=ev_issues,
                conflicts=ev_conflicts,
                evidence_links=ev_evidence,
                splits=ev_splits,
                candidate_matches=ev_candidates,
                conn=conn,
                current_time=current_time,
            )
            prio_score = prio_res["priority_score"]
            prio_reasons = prio_res["priority_reasons"]

            # 7. Persist refreshed priority only if columns exist
            try:
                conn.execute(
                    """
                    UPDATE execution_events
                    SET priority_score = %s, priority_reasons = %s
                    WHERE event_id = %s
                    """,
                    (prio_score, prio_reasons, ev_id),
                )
            except Exception as e:
                if is_missing_column_error(e, ["priority_score", "priority_reasons"]):
                    columns_missing = True
                else:
                    raise

            item = {
                "event_id": ev_id,
                "schedule_id": ev_dict.get("schedule_id"),
                "status": ev_dict.get("status"),
                "event_date": ev_dict.get("event_date"),
                "raw_claim_text": ev_dict.get("raw_claim_text"),
                "input_channel": ev_dict.get("input_channel"),
                "discipline": ev_dict.get("discipline"),
                "action": ev_dict.get("action"),
                "event_type": ev_dict.get("event_type"),
                "claim_mode": ev_dict.get("claim_mode"),
                "asset_tag": ev_dict.get("asset_tag"),
                "location": ev_dict.get("location"),
                "matched_activity_id": ev_dict.get("matched_activity_id"),
                "claimed_pct": ev_dict.get("claimed_pct"),
                "claimed_quantity": ev_dict.get("claimed_quantity"),
                "claimed_uom": ev_dict.get("claimed_uom"),
                "created_at": ev_dict.get("created_at"),
                "priority_score": prio_score,
                "priority_reasons": prio_reasons,
                "base_severity": prio_res["base_severity"],
                "criticality_multiplier": prio_res["criticality_multiplier"],
                "aging": prio_res["aging"],
                "hours_in_queue": prio_res["hours_in_queue"],
                "validation_issues": ev_issues,
                "conflicts": ev_conflicts,
                "evidence_links": ev_evidence,
                "reasons_breakdown": prio_res.get("reasons_breakdown", {}),
            }
            queue_items.append(item)

        # 8. Sort: priority_score DESC when sort=priority, with tie-breakers: created_at ASC, event_id ASC
        def _queue_sort_key(it: Dict[str, Any]):
            score = float(it.get("priority_score") or 0.0)
            raw_c = it.get("created_at")
            if isinstance(raw_c, str):
                try:
                    c_dt = datetime.fromisoformat(raw_c.replace("Z", "+00:00"))
                except Exception:
                    c_dt = datetime.min.replace(tzinfo=timezone.utc)
            elif isinstance(raw_c, datetime):
                c_dt = raw_c
            elif isinstance(raw_c, date):
                c_dt = datetime.combine(raw_c, datetime.min.time()).replace(tzinfo=timezone.utc)
            else:
                c_dt = datetime.min.replace(tzinfo=timezone.utc)

            if c_dt.tzinfo is None:
                c_dt = c_dt.replace(tzinfo=timezone.utc)

            it_id = str(it.get("event_id") or "")
            return (-score, c_dt, it_id)

        if not sort or sort.lower() == "priority":
            queue_items.sort(key=_queue_sort_key)
        elif sort.lower() == "created_at":
            def _created_sort_key(it: Dict[str, Any]):
                raw_c = it.get("created_at")
                if isinstance(raw_c, str):
                    try:
                        c_dt = datetime.fromisoformat(raw_c.replace("Z", "+00:00"))
                    except Exception:
                        c_dt = datetime.min.replace(tzinfo=timezone.utc)
                elif isinstance(raw_c, datetime):
                    c_dt = raw_c
                elif isinstance(raw_c, date):
                    c_dt = datetime.combine(raw_c, datetime.min.time()).replace(tzinfo=timezone.utc)
                else:
                    c_dt = datetime.min.replace(tzinfo=timezone.utc)
                if c_dt.tzinfo is None:
                    c_dt = c_dt.replace(tzinfo=timezone.utc)
                return (c_dt, str(it.get("event_id") or ""))
            queue_items.sort(key=_created_sort_key)

        migration_blockers = []
        if columns_missing:
            migration_blockers.append(
                "execution_events table lacks priority_score and priority_reasons columns (PRD v6 Section 16.1 unmigrated). "
                "Returned priorities were computed dynamically on read."
            )

        return {
            "total": len(queue_items),
            "items": queue_items,
            "review_queue": queue_items,
            "sort": sort,
            "migration_blockers": migration_blockers,
        }


@router.get("/activities/{activity_id}/rollup")
def get_activity_rollup(
    activity_id: str,
    schedule_id: Optional[str] = None,  # validated via schedule_context
    schedule_context: ScheduleContext = Depends(gates.events_view),
):
    schedule_id = schedule_context.schedule_id
    with get_connection() as conn:
        planned_quantity = 0.0
        if schedule_id:
            act_row = conn.execute(
                """
                SELECT planned_quantity, uom FROM schedule_activities
                WHERE schedule_id = %s AND activity_id = %s
                """,
                (schedule_id, activity_id),
            ).fetchone()
            if act_row:
                planned_quantity = act_row.get("planned_quantity") or 0.0

        # Sum APPROVED/EDITED incremental quantities using latest decision
        qty_row = conn.execute(
            """
            SELECT COALESCE(SUM(COALESCE(pd.approved_qty, ee.claimed_quantity)), 0.0) AS total_approved_qty
            FROM execution_events ee
            JOIN planner_decisions pd ON pd.event_id = ee.event_id
            WHERE (CAST(%s AS TEXT) IS NULL OR ee.schedule_id = %s)
              AND ee.matched_activity_id = %s
              AND pd.action IN ('APPROVE', 'EDIT')
              AND pd.decision_id = (
                  SELECT decision_id FROM planner_decisions pd2
                  WHERE pd2.event_id = ee.event_id
                  ORDER BY pd2.decided_at DESC LIMIT 1
              )
            """,
            (schedule_id, schedule_id, activity_id),
        ).fetchone()

        sum_qty = qty_row["total_approved_qty"] if qty_row else 0.0
        sum_qty = float(sum_qty or 0.0) + approved_split_quantity(conn, schedule_id, activity_id)
        derived_pct = 0.0
        if planned_quantity > 0:
            derived_pct = min(
                round((sum_qty / planned_quantity) * 100.0, 2), 100.0
            )

        # Full claim history for context
        claims_rows = conn.execute(
            """
            SELECT ee.*, pd.action AS latest_decision_action, pd.approved_qty
            FROM execution_events ee
            LEFT JOIN planner_decisions pd ON pd.event_id = ee.event_id
              AND pd.decision_id = (
                  SELECT decision_id FROM planner_decisions pd2
                  WHERE pd2.event_id = ee.event_id
                  ORDER BY pd2.decided_at DESC LIMIT 1
              )
            WHERE (CAST(%s AS TEXT) IS NULL OR ee.schedule_id = %s)
              AND (ee.matched_activity_id = %s OR ee.reported_activity_id = %s)
            ORDER BY ee.event_date DESC, ee.created_at DESC
            """,
            (schedule_id, schedule_id, activity_id, activity_id),
        ).fetchall()

        return {
            "activity_id": activity_id,
            "schedule_id": schedule_id,
            "planned_quantity": planned_quantity,
            "summed_approved_quantity": sum_qty,
            "derived_pct_complete": derived_pct,
            "claim_history": [dict(r) for r in claims_rows],
        }


@router.get("/audit")
def get_recent_audit(limit: int = 20, project_context: ProjectContext = Depends(gates.audit_view)):
    """Recent-activity feed of THIS PROJECT's audit chain (newest first) --
    distinct from GET /audit/{entity_id}'s per-entity chain view below."""
    return list_recent_audit_logs(limit=limit, project_id=project_context.project_id)


@router.get("/audit/{entity_id}")
def get_audit(entity_id: str, project_context: ProjectContext = Depends(gates.audit_view)):
    return get_audit_trail(entity_id, project_id=project_context.project_id)


@router.get("/alerts/silent-activities")
@router.get("/claims/silent-activities")
@router.get("/claims/checks/silent-activities")
@router.get("/dashboard/silent-activities")
def get_silent_activities(
    schedule_id: Optional[str] = None,  # validated via schedule_context
    schedule_context: ScheduleContext = Depends(gates.events_view),
):
    schedule_id = schedule_context.schedule_id
    with get_connection() as conn:
        query = """
            SELECT sa.activity_id, sa.schedule_id, sa.activity_name, sa.discipline,
                   sa.location, sa.planned_start, sa.planned_finish, sa.baseline_pct_complete
            FROM schedule_activities sa
            LEFT JOIN approved_actuals aa ON sa.schedule_id = aa.schedule_id AND sa.activity_id = aa.activity_id
            WHERE sa.planned_start <= CURRENT_DATE
              AND (aa.actual_pct_complete IS NULL OR aa.actual_pct_complete < 100.0)
              AND NOT EXISTS (
                  SELECT 1 FROM execution_events ee
                  WHERE ee.schedule_id = sa.schedule_id
                    AND ee.matched_activity_id = sa.activity_id
                    AND ee.created_at >= CURRENT_DATE - INTERVAL '3 days'
              )
        """
        params = [schedule_id, str(schedule_context.project_id)]
        query += " AND sa.schedule_id = %s AND sa.project_id = %s"

        query += " ORDER BY sa.planned_start ASC"

        rows = conn.execute(query, params).fetchall()
        return {"silent_activities": [dict(r) for r in rows]}
