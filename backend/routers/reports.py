"""
PRD v6 Feature 35 -- AI Execution Summary at the PRD-specified path:

    GET /api/v1/reports/execution-summary?start=YYYY-MM-DD&end=YYYY-MM-DD&discipline=...

Aggregation is deterministic (routers/summary.py::build_deterministic_aggregate); only
those aggregate numbers are given to the LLM, which is told to phrase them, not compute
or add any. The narrative is cached per exact (period_start, period_end, discipline) in
`execution_summaries`, guarded by a hash of the aggregate so a cached narrative is never
served after the underlying numbers change. If the LLM is unavailable a deterministic
template summary is returned (and never cached). `language` (en/hi/te) translates for
display at runtime; the canonical stored English text is never overwritten.

Supervisor only.
"""
import hashlib
import json
import logging
import uuid
from datetime import date, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.routers.summary import (
    SUPPORTED_LANGUAGES,
    build_deterministic_aggregate,
    generate_llm_summary,
    translate_dynamic_text,
)
from backend.shared.auth import get_current_user
from backend.shared.db import get_connection
from backend.context import gates
from backend.context.schedule import ScheduleContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])

ESCALATION_SCORE = 100.0  # same threshold the review queue UI uses for its escalation badge


def _aggregate_hash(aggregate: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(aggregate, sort_keys=True, default=str).encode()).hexdigest()


def _read_cache(period_start: str, period_end: str, discipline: str, agg_hash: str) -> Optional[str]:
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT summary_text, aggregate_hash FROM execution_summaries
                WHERE period_start = %s AND period_end = %s AND COALESCE(discipline, 'ALL') = %s
                """,
                (period_start, period_end, discipline),
            ).fetchone()
        if row and row["aggregate_hash"] == agg_hash:
            return row["summary_text"]
    except Exception as e:  # cache is an optimisation, never a blocker
        logger.warning("execution_summaries cache read failed: %s", e)
    return None


def _write_cache(period_start: str, period_end: str, discipline: str, text: str, agg_hash: str) -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO execution_summaries
                    (summary_id, period_start, period_end, discipline, summary_text, aggregate_hash)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (period_start, period_end, COALESCE(discipline, 'ALL'))
                DO UPDATE SET summary_text = EXCLUDED.summary_text,
                              aggregate_hash = EXCLUDED.aggregate_hash,
                              generated_at = now()
                """,
                (str(uuid.uuid4()), period_start, period_end, discipline, text, agg_hash),
            )
            conn.commit()
    except Exception as e:
        logger.warning("execution_summaries cache write failed: %s", e)


def _escalations(period_start: str, period_end: str, discipline: str) -> int:
    q = """
        SELECT COUNT(*) AS n FROM execution_events
        WHERE event_date >= %s AND event_date <= %s
          AND status IN ('VALIDATED', 'REVIEW_REQUIRED', 'HOLD')
          AND COALESCE(priority_score, 0) >= %s
    """
    params: list = [period_start, period_end, ESCALATION_SCORE]
    if discipline != "ALL":
        q += " AND UPPER(TRIM(discipline)) = %s"
        params.append(discipline)
    with get_connection() as conn:
        return int(conn.execute(q, tuple(params)).fetchone()["n"])


def _metrics_and_highlights(agg: Dict[str, Any], escalations: int) -> tuple[dict, list]:
    by_status = agg["claims"]["by_status"]
    total = agg["claims"]["total_claims"]
    approved = by_status.get("APPROVED", 0) + by_status.get("EDITED", 0)
    decided = approved + by_status.get("REJECTED", 0)
    open_conflicts = agg["conflicts"]["by_status"].get("OPEN", 0)
    resolved_conflicts = agg["conflicts"]["by_status"].get("RESOLVED", 0)
    reasons = sorted(agg["delays"]["reasons"].items(), key=lambda kv: (-kv[1], kv[0]))
    metrics = {
        "total_claims_processed": total,
        "approval_rate_pct": round(100.0 * approved / decided, 1) if decided else None,
        "open_conflicts_count": open_conflicts,
        "high_priority_escalations": escalations,
        "top_delay_drivers": [{"reason": r, "count": c} for r, c in reasons[:5]],
        "activities_with_events": agg["claims"].get("activities_with_events", 0),
        "conflicts_opened": agg["conflicts"]["total_conflicts"],
        "conflicts_resolved": resolved_conflicts,
        "claims_by_status": by_status,
    }
    acts = agg["activities"]
    highlights = [
        f"{total} claim(s) in period; {approved} approved/edited, {by_status.get('REJECTED', 0)} rejected, "
        f"{by_status.get('HOLD', 0)} on hold.",
        f"{agg['approved_progress']['activities_with_actuals']} activit(ies) with approved actuals "
        f"(average approved completion {agg['approved_progress']['avg_approved_pct']}%).",
        f"{open_conflicts} open conflict(s), {resolved_conflicts} resolved; "
        f"{agg['validation_issues']['total_issues']} validation issue(s).",
        f"Schedule state: {acts['completed']} completed, {acts['in_progress']} in progress, "
        f"{acts['not_started']} not started of {acts['total']}.",
    ]
    if reasons:
        highlights.append("Reported delay drivers: " + ", ".join(f"{r} ({c})" for r, c in reasons[:3]) + ".")
    return metrics, highlights


@router.get("/execution-summary")
def execution_summary(
    start: Optional[str] = Query(default=None, description="YYYY-MM-DD; default 7 days before end"),
    end: Optional[str] = Query(default=None, description="YYYY-MM-DD; default today"),
    discipline: Optional[str] = Query(default=None, description="discipline, or all/omitted for all"),
    language: str = Query(default="en", description="display language: en, hi, te"),
    schedule_id: Optional[str] = Query(default=None, description="Required (or X-Schedule-ID); validated against the project"),
    schedule_context: ScheduleContext = Depends(gates.claim_review_schedule),
):
    try:
        end_d = date.fromisoformat(end) if end else date.today()
        start_d = date.fromisoformat(start) if start else end_d - timedelta(days=7)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="start/end must be YYYY-MM-DD")
    if start_d > end_d:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="start must be on or before end")

    resolved_schedule_id = schedule_context.schedule_id
    disc = (discipline or "all").strip()
    aggregate = build_deterministic_aggregate(
        period="custom", start_date=start_d.isoformat(), end_date=end_d.isoformat(),
        discipline="ALL" if disc.lower() == "all" else disc,
        schedule_id=resolved_schedule_id,
    )
    disc_key = aggregate["discipline"]
    agg_hash = _aggregate_hash(aggregate)

    canonical = _read_cache(start_d.isoformat(), end_d.isoformat(), disc_key, agg_hash)
    cached = canonical is not None
    generated_by = "cache"
    if canonical is None:
        canonical, generated_by = generate_llm_summary(aggregate)
        if generated_by == "llm":
            _write_cache(start_d.isoformat(), end_d.isoformat(), disc_key, canonical, agg_hash)

    lang = (language or "en").strip().lower()[:2]
    if lang not in SUPPORTED_LANGUAGES:
        lang = "en"
    text = canonical
    translated = False
    if lang != "en":
        text, _ = translate_dynamic_text(canonical, lang)  # falls back to canonical English on failure
        translated = text != canonical

    metrics, highlights = _metrics_and_highlights(
        aggregate, _escalations(start_d.isoformat(), end_d.isoformat(), disc_key)
    )
    from datetime import datetime, timezone

    return {
        "reporting_period": {"start_date": start_d.isoformat(), "end_date": end_d.isoformat()},
        "discipline": None if disc_key == "ALL" else disc_key,
        "summary_text": text,
        "canonical_summary": canonical,
        "language": lang,
        "translated": translated,
        "cached": cached,
        "generated_by": generated_by,
        "metrics": metrics,
        "key_highlights": highlights,
        "aggregate": aggregate,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Feature 6: runtime translation of generated content (presentation layer only)
# ---------------------------------------------------------------------------
from typing import List  # noqa: E402

from pydantic import BaseModel, Field  # noqa: E402


class TranslateRequest(BaseModel):
    texts: List[str] = Field(max_length=40)
    target_language: str  # en / hi / te


@router.post("/translate")
def translate(req: TranslateRequest, current_user=Depends(get_current_user)):
    """
    Translate generated/runtime text (clarification questions, Ask Why, evidence and validation
    explanations) for display. Canonical stored text is never modified. Any failure returns the
    original text unchanged -- translation must never block review.
    """
    lang = (req.target_language or "en").strip().lower()[:2]
    out = []
    for t in req.texts:
        try:
            text, _ = translate_dynamic_text(t, lang) if lang in SUPPORTED_LANGUAGES else (t, False)
        except Exception as e:  # defensive: translate_dynamic_text already falls back
            logger.warning("translate failed: %s", e)
            text = t
        out.append(text)
    return {"target_language": lang, "texts": out, "translated": [o != t for o, t in zip(out, req.texts)]}
