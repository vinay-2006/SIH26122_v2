"""
SIH26122 — Phase 7: AI Execution Summary + Dynamic Translation Router.

Architecture:
1. Deterministic Aggregator:
   Compiles verified factual project records (claims, approved actuals,
   conflicts, validation issues, delay events, activities, canonical
   execution states, and forecasting indicators) from the database before
   any LLM is invoked.
2. AI Summary Generation:
   Consumes M2's shared LLM client (`backend.shared.llm_extraction`) to
   synthesize a concise, professional canonical English summary.
   If the LLM fails or is unavailable, falls back to a safe deterministic
   template summary without hallucinated claims or invented numbers.
3. Process-Local Dynamic Translation Cache:
   Translates dynamic summary content into Hindi (`hi`) or Telugu (`te`)
   with deterministic in-memory SHA-256 caching.
   Cache HIT returns cached text without LLM invocation.
   Cache MISS calls the shared LLM client and caches successful results.
   Failed translations fall back to canonical English and are never cached.
   Canonical English is never overwritten.
4. Static Content Boundary:
   Predefined UI strings strictly remain within existing i18next bundles.
5. RBAC:
   Strictly restricted to the SUPERVISOR role.
"""
import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.routers.dashboard import (
    _calculate_duration_days,
    _calculate_historical_ratio_for_discipline,
)
from backend.shared.actuals import get_execution_state
from backend.shared.db import get_connection
from backend.shared.discipline_normalize import normalize_discipline
from backend.context import gates
from backend.context.schedule import ScheduleContext
from backend.shared.llm_extraction import (
    LLMExtractionError,
    _create_completion,
    _get_client,
    _get_model,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/execution-summary", tags=["execution-summary"])

SUPPORTED_DISCIPLINES = {
    "ALL",
    "CIVIL",
    "PIPING",
    "STATIC_ROTATING_EQUIPMENT",
    "ELECTRICAL",
    "INSTRUMENTATION",
    "HSE",
}

SUPPORTED_LANGUAGES = {"en": "English", "hi": "Hindi", "te": "Telugu"}

# Process-local in-memory translation cache (non-persistent across restarts)
_translation_cache: Dict[str, str] = {}


def get_translation_cache() -> Dict[str, str]:
    """Return process-local translation cache (primarily for tests)."""
    return _translation_cache


def clear_translation_cache() -> None:
    """Clear process-local translation cache."""
    _translation_cache.clear()


# =========================================================================
# 1. Deterministic Aggregation
# =========================================================================


def _parse_iso_date(val: Optional[str]) -> Optional[date]:
    if not val:
        return None
    val = val.strip()
    try:
        return date.fromisoformat(val[:10])
    except (ValueError, TypeError):
        return None


def resolve_period_dates(
    period: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    reference_date: Optional[date] = None,
) -> tuple[date, date]:
    """
    Resolve and validate start and end dates for the requested period.
    """
    today = reference_date or date.today()
    p_norm = period.strip().lower()

    if p_norm == "last_7_days":
        start = today - timedelta(days=7)
        end = today
    elif p_norm == "this_month":
        start = date(today.year, today.month, 1)
        end = today
    elif p_norm == "custom":
        if not start_date or not end_date:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Both 'start_date' and 'end_date' are required when period='custom'.",
            )
        parsed_start = _parse_iso_date(start_date)
        parsed_end = _parse_iso_date(end_date)
        if not parsed_start or not parsed_end:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date format for 'start_date' or 'end_date' (expected YYYY-MM-DD).",
            )
        if parsed_start > parsed_end:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"start_date ({start_date}) must be less than or equal to end_date ({end_date}).",
            )
        start = parsed_start
        end = parsed_end
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported period '{period}'. Expected 'last_7_days', 'this_month', or 'custom'.",
        )

    return start, end


def build_deterministic_aggregate(
    period: str = "last_7_days",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    discipline: str = "ALL",
    conn: Optional[Any] = None,
    reference_date: Optional[date] = None,
    schedule_id: Optional[str] = None,
) -> dict:
    """
    Compile verified project facts strictly from database records without an LLM.

    schedule_id: when given, every section (claims, approved progress,
    conflicts, validation issues, activities) is restricted to that
    schedule -- a caller with no schedule_id argument at all gets the
    pre-existing unscoped aggregate (used by unit tests seeding a
    single-schedule fixture). The live endpoints (routers/reports.py,
    this module's own /api/v1/summary) always resolve and pass the active
    schedule so AI Execution Summary numbers never mix schedules (ISS-05).
    """
    start_d, end_d = resolve_period_dates(period, start_date, end_date, reference_date)
    start_iso = start_d.isoformat()
    end_iso = end_d.isoformat()

    # Discipline normalization
    disc_norm = discipline.strip().upper() if discipline else "ALL"
    if disc_norm in ("ALL DISCIPLINES", "ALL_DISCIPLINES"):
        disc_norm = "ALL"
    elif disc_norm != "ALL":
        normalized = normalize_discipline(disc_norm)
        if normalized in SUPPORTED_DISCIPLINES:
            disc_norm = normalized

    def _execute(query: str, params: tuple = ()):
        if conn is not None:
            return conn.execute(query, params)
        with get_connection() as c:
            return c.execute(query, params)

    def _row_to_dict(row):
        if hasattr(row, "_mapping"):
            return dict(row._mapping)
        if hasattr(row, "keys"):
            return {k: row[k] for k in row.keys()}
        return row

    # 1. Claims (execution_events within period)
    claims_query = """
        SELECT
            event_id,
            status,
            event_type,
            discipline,
            event_date,
            delay_reason,
            matched_activity_id
        FROM execution_events
        WHERE event_date >= %s AND event_date <= %s
    """
    c_params = [start_iso, end_iso]
    if schedule_id is not None:
        claims_query += " AND schedule_id = %s"
        c_params.append(schedule_id)
    if disc_norm != "ALL":
        claims_query += " AND UPPER(TRIM(discipline)) = %s"
        c_params.append(disc_norm)

    claims_rows = [_row_to_dict(r) for r in _execute(claims_query, tuple(c_params)).fetchall()]

    claims_by_status: Dict[str, int] = {}
    claims_by_event_type: Dict[str, int] = {}
    delays_by_reason: Dict[str, int] = {}
    total_delay_events = 0

    for r in claims_rows:
        st = (r.get("status") or "EXTRACTED").strip().upper()
        claims_by_status[st] = claims_by_status.get(st, 0) + 1

        et = (r.get("event_type") or "UNKNOWN").strip().upper()
        claims_by_event_type[et] = claims_by_event_type.get(et, 0) + 1

        reason = r.get("delay_reason")
        if reason and str(reason).strip():
            r_str = str(reason).strip().upper()
            delays_by_reason[r_str] = delays_by_reason.get(r_str, 0) + 1
            total_delay_events += 1

    # 2. Approved Progress (approved_actuals linked to events within period)
    actuals_query = """
        SELECT
            aa.actual_id,
            aa.activity_id,
            aa.actual_pct_complete,
            aa.actual_start,
            aa.actual_finish
        FROM approved_actuals aa
        JOIN execution_events ee ON ee.event_id = aa.event_id
        WHERE ee.event_date >= %s AND ee.event_date <= %s
    """
    a_params = [start_iso, end_iso]
    if schedule_id is not None:
        actuals_query += " AND ee.schedule_id = %s"
        a_params.append(schedule_id)
    if disc_norm != "ALL":
        actuals_query += " AND UPPER(TRIM(ee.discipline)) = %s"
        a_params.append(disc_norm)

    actuals_rows = [_row_to_dict(r) for r in _execute(actuals_query, tuple(a_params)).fetchall()]
    total_approved = len(actuals_rows)
    unique_activities_approved = len({r["activity_id"] for r in actuals_rows if r.get("activity_id")})
    pct_values = [
        float(r["actual_pct_complete"])
        for r in actuals_rows
        if r.get("actual_pct_complete") is not None
    ]
    avg_approved_pct = round(sum(pct_values) / len(pct_values), 1) if pct_values else 0.0

    # 3. Conflicts (conflict_records within period)
    needs_activity_join = disc_norm != "ALL"
    if needs_activity_join:
        conflicts_query = """
            SELECT
                cr.conflict_id,
                cr.status,
                cr.activity_id
            FROM conflict_records cr
            JOIN schedule_activities sa
              ON sa.activity_id = cr.activity_id AND sa.schedule_id = cr.schedule_id
            WHERE cr.reporting_period >= %s AND cr.reporting_period <= %s
        """
        cf_params = [start_iso, end_iso]
        if schedule_id is not None:
            conflicts_query += " AND cr.schedule_id = %s"
            cf_params.append(schedule_id)
        conflicts_query += " AND UPPER(TRIM(sa.discipline)) = %s"
        cf_params.append(disc_norm)
    else:
        conflicts_query = """
            SELECT
                cr.conflict_id,
                cr.status,
                cr.activity_id
            FROM conflict_records cr
            WHERE cr.reporting_period >= %s AND cr.reporting_period <= %s
        """
        cf_params = [start_iso, end_iso]
        if schedule_id is not None:
            conflicts_query += " AND cr.schedule_id = %s"
            cf_params.append(schedule_id)

    conflict_rows = [_row_to_dict(r) for r in _execute(conflicts_query, tuple(cf_params)).fetchall()]
    conflicts_by_status: Dict[str, int] = {}
    for r in conflict_rows:
        c_st = (r.get("status") or "OPEN").strip().upper()
        conflicts_by_status[c_st] = conflicts_by_status.get(c_st, 0) + 1

    # 4. Validation Issues (validation_issues joined with events within period)
    validation_query = """
        SELECT
            vi.issue_id,
            vi.severity,
            vi.rule_code
        FROM validation_issues vi
        JOIN execution_events ee ON ee.event_id = vi.event_id
        WHERE ee.event_date >= %s AND ee.event_date <= %s
    """
    v_params = [start_iso, end_iso]
    if schedule_id is not None:
        validation_query += " AND ee.schedule_id = %s"
        v_params.append(schedule_id)
    if disc_norm != "ALL":
        validation_query += " AND UPPER(TRIM(ee.discipline)) = %s"
        v_params.append(disc_norm)

    validation_rows = [_row_to_dict(r) for r in _execute(validation_query, tuple(v_params)).fetchall()]
    val_by_severity: Dict[str, int] = {}
    for r in validation_rows:
        sev = (r.get("severity") or "WARNING").strip().upper()
        val_by_severity[sev] = val_by_severity.get(sev, 0) + 1

    # 5. Activities & Canonical Execution States (Phase 1C Rule E)
    # Scope to the given schedule_id, or -- when none was passed -- the active
    # (most recently created) schedule, so re-uploaded baselines are not
    # double counted.
    act_query = """
        SELECT
            sa.activity_id,
            sa.schedule_id,
            sa.activity_name,
            sa.discipline,
            sa.is_critical,
            sa.total_float,
            aa.actual_pct_complete,
            aa.actual_start,
            aa.actual_finish
        FROM schedule_activities sa
        LEFT JOIN approved_actuals aa
          ON aa.schedule_id = sa.schedule_id AND aa.activity_id = sa.activity_id
        WHERE sa.schedule_id = %s
    """
    # No "latest schedule" guess: with an explicit schedule_id the section is scoped to it; without one
    # (internal/unit-test use only, never reachable from an operational route) it is unscoped.
    act_params: list = []
    if schedule_id is not None:
        act_query = act_query.replace("WHERE sa.schedule_id = %s", "WHERE sa.schedule_id = %s")
        act_params.append(schedule_id)
    else:
        act_query = act_query.replace("WHERE sa.schedule_id = %s", "WHERE 1 = 1")
    if disc_norm != "ALL":
        act_query += " AND UPPER(TRIM(sa.discipline)) = %s"
        act_params.append(disc_norm)

    act_rows = [_row_to_dict(r) for r in _execute(act_query, tuple(act_params)).fetchall()]
    activities_total = len(act_rows)
    completed_count = 0
    in_progress_count = 0
    not_started_count = 0

    for act in act_rows:
        state = get_execution_state(
            actual_pct_complete=act.get("actual_pct_complete"),
            actual_start=act.get("actual_start"),
        )
        if state == "COMPLETED":
            completed_count += 1
        elif state == "IN_PROGRESS":
            in_progress_count += 1
        else:
            not_started_count += 1

    # 6. Forecasting Indicators (Reuse existing historical ratio from dashboard)
    forecast_info: Dict[str, Any] = {"status": "unavailable", "historical_ratio": None}
    try:
        if disc_norm != "ALL":
            ratio = _calculate_historical_ratio_for_discipline(disc_norm, conn=conn)
            if ratio is not None:
                forecast_info = {
                    "status": "available",
                    "historical_ratio": round(ratio, 3),
                    "discipline": disc_norm,
                }
        else:
            forecast_info = {
                "status": "available",
                "historical_ratio": None,
                "note": "Select specific discipline to view historical duration ratio",
            }
    except Exception as e:
        logger.warning("Could not compute forecast ratio: %s", e)

    return {
        "period": {
            "type": period,
            "start": start_iso,
            "end": end_iso,
        },
        "discipline": disc_norm,
        "claims": {
            "total_claims": len(claims_rows),
            "activities_with_events": len({r["matched_activity_id"] for r in claims_rows if r.get("matched_activity_id")}),
            "by_status": claims_by_status,
            "by_event_type": claims_by_event_type,
        },
        "approved_progress": {
            "total_approved": total_approved,
            "activities_with_actuals": unique_activities_approved,
            "avg_approved_pct": avg_approved_pct,
        },
        "conflicts": {
            "total_conflicts": len(conflict_rows),
            "by_status": conflicts_by_status,
        },
        "validation_issues": {
            "total_issues": len(validation_rows),
            "by_severity": val_by_severity,
        },
        "delays": {
            "total_delay_events": total_delay_events,
            "reasons": delays_by_reason,
        },
        "activities": {
            "total": activities_total,
            "completed": completed_count,
            "in_progress": in_progress_count,
            "not_started": not_started_count,
        },
        "forecast": forecast_info,
    }


# =========================================================================
# 2. Summary Generation & Deterministic Fallback
# =========================================================================


def generate_deterministic_summary(aggregate: dict) -> str:
    """
    Generate a safe, strictly factual, deterministic template summary
    from the aggregate without calling any LLM.
    """
    period = aggregate.get("period", {})
    p_type = period.get("type", "custom").replace("_", " ").title()
    p_start = period.get("start", "")
    p_end = period.get("end", "")
    discipline = aggregate.get("discipline", "ALL")

    claims = aggregate.get("claims", {})
    tot_claims = claims.get("total_claims", 0)

    approved = aggregate.get("approved_progress", {})
    tot_approved = approved.get("total_approved", 0)
    avg_pct = approved.get("avg_approved_pct", 0.0)

    acts = aggregate.get("activities", {})
    tot_acts = acts.get("total", 0)
    completed = acts.get("completed", 0)
    in_prog = acts.get("in_progress", 0)
    not_started = acts.get("not_started", 0)

    conflicts = aggregate.get("conflicts", {})
    tot_conflicts = conflicts.get("total_conflicts", 0)
    open_conflicts = conflicts.get("by_status", {}).get("OPEN", 0)

    val = aggregate.get("validation_issues", {})
    tot_val = val.get("total_issues", 0)

    delays = aggregate.get("delays", {})
    tot_delays = delays.get("total_delay_events", 0)
    reasons = delays.get("reasons", {})
    reasons_str = (
        ", ".join(f"{k} ({v})" for k, v in reasons.items())
        if reasons
        else "None reported"
    )

    fc = aggregate.get("forecast", {})
    ratio = fc.get("historical_ratio")
    fc_str = (
        f"Historical duration multiplier is {ratio}×"
        if ratio is not None
        else "No historical ratio available for current filter"
    )

    lines = [
        f"Project Execution Summary ({p_type}: {p_start} to {p_end}) — Discipline: {discipline}",
        f"• Schedule Execution: {tot_acts} activities under scope ({completed} Completed, {in_prog} In Progress, {not_started} Not Started).",
        f"• Progress Verification: {tot_claims} field claims submitted; {tot_approved} approved by Supervisor (average approved completion: {avg_pct:.1f}%).",
        f"• Quality & Compliance: {tot_conflicts} conflict records identified ({open_conflicts} open); {tot_val} validation issues flagged.",
        f"• Delays & Constraints: {tot_delays} delay events logged. Delay breakdown: {reasons_str}.",
        f"• Forecasting Indicator: {fc_str}.",
    ]
    return "\n".join(lines)


SUMMARY_SYSTEM_PROMPT = """You are a senior project controls manager generating a concise, professional project execution summary for an infrastructure project supervisor.
Use ONLY the supplied verified aggregate data.
Strict Constraints:
- Do not invent, estimate, calculate, or modify any numbers.
- Do not introduce causes, impacts, or facts that are not explicitly present in the verified data.
- State clearly the activity execution counts, submitted claims, approved progress, recorded conflicts, validation issues, and reported delay factors.
- Return a concise professional summary (2 to 3 paragraphs or structured bullet points).
"""


def generate_llm_summary(aggregate: dict) -> tuple[str, str]:
    """
    Invoke M2's shared LLM client to synthesize the canonical English summary.
    Returns (summary_text, generated_by).
    Falls back to deterministic template if LLM fails or is unconfigured.
    """
    aggregate_json = json.dumps(aggregate, indent=2)
    user_prompt = f"Verified Project Aggregate Data:\n{aggregate_json}"

    try:
        client = _get_client()
        model = _get_model()
        response = _create_completion(
            client=client,
            model=model,
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
        )
        content = response.choices[0].message.content
        if content and content.strip():
            return content.strip(), "llm"
        logger.warning("LLM returned empty summary; using deterministic fallback")
    except Exception as e:
        logger.warning("LLM summary generation failed (%s); using deterministic fallback", e)

    return generate_deterministic_summary(aggregate), "deterministic_fallback"


# =========================================================================
# 3. Dynamic Translation & Process-Local Cache
# =========================================================================


def _compute_cache_key(target_language: str, text: str) -> str:
    raw = f"{target_language.strip().lower()}:{text.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def translate_dynamic_text(text: str, target_language: str) -> tuple[str, bool]:
    """
    Translate dynamic text to Hindi ('hi') or Telugu ('te') using process-local caching.
    Returns (translated_text, was_cached).
    On LLM failure, returns canonical English text without caching failure.
    """
    lang_code = target_language.strip().lower()[:2]
    if lang_code == "en" or not text:
        return text, False

    lang_name = SUPPORTED_LANGUAGES.get(lang_code)
    if not lang_name:
        return text, False

    cache_key = _compute_cache_key(lang_code, text)

    # 1. Cache HIT
    if cache_key in _translation_cache:
        logger.debug("Translation cache HIT for key %s", cache_key[:10])
        return _translation_cache[cache_key], True

    # 2. Cache MISS -> Call Shared LLM Client
    logger.debug("Translation cache MISS for key %s; invoking shared LLM", cache_key[:10])
    translation_system_prompt = f"""You are a professional technical translator for Indian oil & infrastructure reports.
Translate the supplied project execution summary into {lang_name}.

Strict Rules:
- Preserve all activity IDs (e.g. ACT-101, A-1042), dates, numbers, percentages, and technical units EXACTLY.
- Do NOT alter any technical identifiers or numerical values.
- Do NOT add commentary, explanation, or preamble. Output ONLY the translated text.
"""
    try:
        client = _get_client()
        model = _get_model()
        response = _create_completion(
            client=client,
            model=model,
            messages=[
                {"role": "system", "content": translation_system_prompt},
                {"role": "user", "content": text},
            ],
            temperature=0.1,
        )
        translated = response.choices[0].message.content
        if translated and translated.strip():
            clean_translated = translated.strip()
            # Store in cache on success
            _translation_cache[cache_key] = clean_translated
            return clean_translated, False
    except Exception as e:
        logger.warning("Dynamic translation to %s failed (%s); returning canonical English", lang_name, e)

    # Fallback: Canonical English returned; failure is NOT cached
    return text, False


# =========================================================================
# 4. API Endpoints
# =========================================================================


@router.get("")
def get_execution_summary(
    period: str = Query(
        default="last_7_days",
        description="Aggregation period: 'last_7_days', 'this_month', or 'custom'",
    ),
    start_date: Optional[str] = Query(
        default=None,
        description="Start date (YYYY-MM-DD) for 'custom' period",
    ),
    end_date: Optional[str] = Query(
        default=None,
        description="End date (YYYY-MM-DD) for 'custom' period",
    ),
    discipline: str = Query(
        default="ALL",
        description="Discipline filter: 'ALL', 'CIVIL', 'PIPING', etc.",
    ),
    language: str = Query(
        default="en",
        description="Target language: 'en', 'hi', or 'te'",
    ),
    schedule_id: Optional[str] = Query(default=None, description="Required (or X-Schedule-ID); validated against the project"),
    schedule_context: ScheduleContext = Depends(gates.claim_review_schedule),
):
    """
    Phase 7 AI Execution Summary & Dynamic Translation. Superseded by
    routers/reports.py's /api/v1/reports/execution-summary (Feature 35),
    which the frontend actually calls -- this route stays live/scoped
    rather than removed, since it's still reachable, tested, and could be
    hit directly (ISS-05: ""no accessible production endpoint that can
    aggregate schedules incorrectly"").
    1. Compiles verified deterministic aggregate from database.
    2. Generates canonical English summary using shared LLM (with safe fallback).
    3. If language is 'hi' or 'te', performs dynamic translation with process-local caching.
    Restricted strictly to SUPERVISOR role.
    """
    target_lang = language.strip().lower()[:2] if language else "en"
    if target_lang not in SUPPORTED_LANGUAGES:
        target_lang = "en"

    resolved_schedule_id = schedule_context.schedule_id

    # Step 1: Deterministic aggregate
    aggregate = build_deterministic_aggregate(
        period=period,
        start_date=start_date,
        end_date=end_date,
        discipline=discipline,
        schedule_id=resolved_schedule_id,
    )

    # Step 2: Canonical English summary
    canonical_summary, generated_by = generate_llm_summary(aggregate)

    # Step 3: Dynamic translation (if non-English requested)
    cached = False
    if target_lang != "en":
        summary_text, cached = translate_dynamic_text(canonical_summary, target_lang)
    else:
        summary_text = canonical_summary

    return {
        "period": aggregate["period"],
        "discipline": aggregate["discipline"],
        "aggregate": aggregate,
        "canonical_summary": canonical_summary,
        "summary": summary_text,
        "language": target_lang,
        "cached": cached,
        "generated_by": generated_by,
    }
