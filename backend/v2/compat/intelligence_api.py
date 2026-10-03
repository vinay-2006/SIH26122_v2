"""The Dashboard, forecast, silent-activity alerts, delay reasons, historical memory, AI Execution Summary, translation and CSV export on v2 data.

The ORIGINAL query/aggregation functions run unchanged over the legacy read model (migration 0016): query_dashboard_summary, query_delay_reason_aggregates,
query_institutional_memory, query_forecast, build_deterministic_aggregate, generate_llm_summary (LLM when configured, deterministic template otherwise), translate_dynamic_text,
format_csv_rows. Everything is read-only; the project dashboard re-uses the original aggregation helpers on v2's ledger-derived progress."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from .. import permissions as P
from ..domain.common import PM, SUP, actor_tx
from ..errors import ApiError, forbidden
from . import schedule_api
from .checks_api import legacy_view_mode
from .context import Ctx, legacy_ctx, path_ctx

router = APIRouter(tags=["legacy-contract: dashboard & reports"])
_NARRATIVES: Dict[str, str] = {}              # process-local cache of LLM narratives, keyed by the exact aggregate (a cached text is never served for different numbers)


def _http(e) -> ApiError:
    return ApiError(e.status_code, "REQUEST_REFUSED", e.detail if isinstance(e.detail, str) else str(e.detail))


def _view(ctx: Ctx):
    version = ctx.version_id or ctx.active_version_id
    if version is None:
        raise ApiError(409, "NO_ACTIVE_SCHEDULE", "The project has no active schedule: a Project Manager must activate one first")
    return version


def _clean(obj):
    return json.loads(json.dumps(obj, default=str))


def _run(ctx: Ctx, fn):
    """call an original read-only query function with a connection that sees the demo-vocabulary read model of this project / schedule version"""
    from fastapi import HTTPException
    version = _view(ctx)
    with actor_tx(ctx.actor, readonly=True) as c:
        with legacy_view_mode(c, ctx.project_id, version):
            try:
                return _clean(fn(c, str(version)))
            except HTTPException as e:
                raise _http(e)


@router.get("/api/v1/dashboard/summary")
def dashboard_summary(ctx: Ctx = Depends(legacy_ctx(P.REVIEW_CLAIMS))):
    from backend.routers.dashboard import query_dashboard_summary
    return _run(ctx, lambda c, v: query_dashboard_summary(conn=c, schedule_id=v))


@router.get("/api/v1/dashboard/delay-reasons")
def delay_reasons(ctx: Ctx = Depends(legacy_ctx(P.REVIEW_CLAIMS))):
    from backend.routers.dashboard import query_delay_reason_aggregates
    return _run(ctx, lambda c, v: query_delay_reason_aggregates(conn=c, schedule_id=v))


@router.get("/api/v1/dashboard/institutional-memory")
def historical_memory(discipline: Optional[str] = None, ctx: Ctx = Depends(legacy_ctx(P.REVIEW_CLAIMS))):
    """planned vs actual duration of this project's activities (the viewed schedule version; progress history is per activity identity, so versions do not double count)"""
    from backend.routers.dashboard import query_institutional_memory
    return _run(ctx, lambda c, v: query_institutional_memory(discipline=discipline, conn=c, project_id=str(ctx.project_id)))


@router.get("/api/v1/dashboard/forecast")
def forecast(activity_id: Optional[str] = None, discipline: Optional[str] = None, ctx: Ctx = Depends(legacy_ctx(P.REVIEW_CLAIMS))):
    """lightweight historical-ratio forecast: actual / planned duration of the project's finished activities of the same discipline applied to the target"""
    from backend.routers.dashboard import query_forecast
    return _run(ctx, lambda c, v: query_forecast(activity_id=activity_id, discipline=discipline, conn=c, project_id=str(ctx.project_id), schedule_id=v))


@router.get("/api/v1/alerts/silent-activities")
@router.get("/api/v1/claims/silent-activities")
@router.get("/api/v1/claims/checks/silent-activities")
@router.get("/api/v1/dashboard/silent-activities")
def silent_activities(ctx: Ctx = Depends(legacy_ctx(P.REVIEW_CLAIMS))):
    """activities whose planned start has passed, that are not complete, and that nobody reported on for 3 days (the original alert rule)"""
    sql = """
        SELECT sa.activity_id, sa.schedule_id, sa.activity_name, sa.discipline, sa.location, sa.planned_start, sa.planned_finish, sa.baseline_pct_complete
          FROM schedule_activities sa
          LEFT JOIN approved_actuals aa ON sa.schedule_id = aa.schedule_id AND sa.activity_id = aa.activity_id
         WHERE sa.planned_start <= CURRENT_DATE
           AND (aa.actual_pct_complete IS NULL OR aa.actual_pct_complete < 100.0)
           AND NOT EXISTS (SELECT 1 FROM execution_events ee WHERE ee.schedule_id = sa.schedule_id AND ee.matched_activity_id = sa.activity_id
                              AND ee.created_at >= CURRENT_DATE - INTERVAL '3 days')
           AND sa.schedule_id = %s AND sa.project_id = %s
         ORDER BY sa.planned_start ASC"""
    return _run(ctx, lambda c, v: {"silent_activities": [dict(r) for r in c.execute(sql, (v, str(ctx.project_id))).fetchall()]})


# ---------------------------------------------------------------------------------------------------------------------------------- project dashboard
@router.get("/api/v1/projects/{project_id}/schedules/{schedule_id}/dashboard")
def project_dashboard(ctx: Ctx = Depends(path_ctx(P.VIEW_DASHBOARD))):
    """project -> stage -> discipline progress straight from the ledgers (actual) and time-phased from the planned dates (planned), with the original aggregation helpers"""
    from backend.services.dashboard_service import _Acc, planned_pct
    version = _view(ctx)
    with actor_tx(ctx.actor, readonly=True) as c:
        acts = schedule_api.activities(c, ctx, version)
        stages = schedule_api.stages(c, ctx, version)
        proj = c.execute("select p.project_code, p.project_name, p.lifecycle_status, p.location, p.project_type, p.planned_start, p.planned_finish, v.data_date from projects p "
                         "join schedule_versions v on v.project_id = p.project_id where p.project_id = %s and v.version_id = %s", (ctx.project_id, version)).fetchone()
        disc_ref = {r["code"]: r for r in c.execute("select code, name, sort_order from disciplines").fetchall()}
        cats = c.execute("select category_code, count(*)::int n, count(*) filter (where status = 'ACTIVE')::int open from issues where project_id = %s group by category_code order by n desc", (ctx.project_id,)).fetchall()
        stage_issues = {str(r["stage_wbs_uid"]): r for r in c.execute("select stage_wbs_uid, count(*)::int issues, count(*) filter (where status = 'ACTIVE')::int open_issues from issues where project_id = %s "
                                                                      "and stage_wbs_uid is not null group by stage_wbs_uid", (ctx.project_id,)).fetchall()}
        pending = 0
        if ctx.access.role == SUP:        # a count of open claims is an aggregate; claim CONTENT is never part of this payload
            pending = c.execute("select count(*) n from execution_events where project_id = %s and status in ('EXTRACTED','MATCHED','VALIDATED','DISPUTED')", (ctx.project_id,)).fetchone()["n"]
        elif ctx.access.role == PM:
            pending = c.execute("select count(*) n from execution_events where project_id = %s and status in ('EXTRACTED','MATCHED','VALIDATED','DISPUTED')", (ctx.project_id,)).fetchone()["n"]
    as_of = proj["data_date"] or date.today()
    overall, by_stage, by_disc = _Acc(), {}, {}
    for a in acts:
        plan = planned_pct(_d(a["planned_start"]), _d(a["planned_finish"]), as_of)
        w = a["weight"] or 0.0
        cond = "BLOCKED" if a["_blocked"] else "NONE"
        overall.add(w, a["_pct"], plan, a["execution_state"] if a["execution_state"] in ("NOT_STARTED", "IN_PROGRESS", "COMPLETED") else "COMPLETED", cond)
        by_stage.setdefault(a["stage_id"], _Acc()).add(w, a["_pct"], plan, a["execution_state"] if a["execution_state"] in ("NOT_STARTED", "IN_PROGRESS", "COMPLETED") else "COMPLETED", cond)
        by_disc.setdefault(a["discipline"] or "UNSPECIFIED", _Acc()).add(w, a["_pct"], plan, a["execution_state"] if a["execution_state"] in ("NOT_STARTED", "IN_PROGRESS", "COMPLETED") else "COMPLETED", cond)

    def row(acc):
        return {"actual_pct": acc.actual_pct(), "planned_pct": acc.planned_pct(), "variance_pct": round(acc.actual_pct() - acc.planned_pct(), 2), "activity_count": acc.n,
                "completed_count": acc.completed, "in_progress_count": acc.in_progress, "not_started_count": acc.not_started, "blocked_count": acc.blocked}
    stage_rows = []
    for s in stages:
        acc = by_stage.get(s["stage_id"], _Acc())
        iss = stage_issues.get(s["stage_id"], {})
        stage_rows.append({"stage_id": s["stage_id"], "stage_code": s["stage_code"], "stage_name": s["stage_name"], "sequence_order": s["sequence_order"], "weight_pct": s["weight_pct"],
                           "status": s["status"], "planned_start": s["planned_start"], "planned_finish": s["planned_finish"], **row(acc),
                           "open_issue_count": iss.get("open_issues", 0), "issue_count": iss.get("issues", 0)})
    disc_rows = sorted(({"discipline": code, "discipline_name": (disc_ref.get(code) or {}).get("name", code), **row(acc), "_s": (disc_ref.get(code) or {}).get("sort_order", 10_000)}
                        for code, acc in by_disc.items()), key=lambda r: (r["_s"], r["discipline"]))
    for r in disc_rows:
        r.pop("_s")
    return _clean({"project_id": str(ctx.project_id), "schedule_id": str(version), "project_code": proj["project_code"], "project_name": proj["project_name"],
                   "lifecycle_status": proj["lifecycle_status"], "location": proj["location"], "project_type": proj["project_type"], "planned_start": proj["planned_start"],
                   "planned_finish": proj["planned_finish"], "as_of_date": as_of, "overall": row(overall), "stages": stage_rows, "disciplines": disc_rows,
                   "issues": {"open": sum(r["open"] for r in cats), "total": sum(r["n"] for r in cats), "by_category": [dict(r) for r in cats]}, "pending_review_count": pending})


def _d(v):
    return date.fromisoformat(v[:10]) if isinstance(v, str) and v else v


# ---------------------------------------------------------------------------------------------------------------------------------- execution summary + translation
@router.get("/api/v1/reports/execution-summary")
def execution_summary(start: Optional[str] = None, end: Optional[str] = None, discipline: Optional[str] = None, language: str = "en", ctx: Ctx = Depends(legacy_ctx(P.REVIEW_CLAIMS))):
    """AI Execution Summary: a deterministic aggregate of verified facts; only those numbers are given to the language model (when one is configured) to phrase,
    otherwise the deterministic template is returned. `language` translates for display; the canonical English text is never altered."""
    from backend.routers import reports as rp
    from backend.routers.summary import SUPPORTED_LANGUAGES, build_deterministic_aggregate, generate_llm_summary, translate_dynamic_text
    try:
        end_d = date.fromisoformat(end) if end else date.today()
        start_d = date.fromisoformat(start) if start else end_d - timedelta(days=7)
    except ValueError:
        raise ApiError(400, "BAD_DATE", "start/end must be YYYY-MM-DD")
    if start_d > end_d:
        raise ApiError(400, "BAD_PERIOD", "start must be on or before end")
    disc = (discipline or "all").strip()

    def build(c, v):
        agg = build_deterministic_aggregate(period="custom", start_date=start_d.isoformat(), end_date=end_d.isoformat(), discipline="ALL" if disc.lower() == "all" else disc, conn=c, schedule_id=v)
        q = ("SELECT COUNT(*) AS n FROM execution_events WHERE event_date >= %s AND event_date <= %s AND status IN ('VALIDATED','REVIEW_REQUIRED','HOLD') AND COALESCE(priority_score, 0) >= %s"
             + (" AND UPPER(TRIM(discipline)) = %s" if agg["discipline"] != "ALL" else ""))
        params = [start_d.isoformat(), end_d.isoformat(), rp.ESCALATION_SCORE] + ([agg["discipline"]] if agg["discipline"] != "ALL" else [])
        return {"aggregate": agg, "escalations": int(c.execute(q, tuple(params)).fetchone()["n"])}
    got = _run(ctx, build)
    agg = got["aggregate"]
    key = hashlib.sha256((str(ctx.project_id) + start_d.isoformat() + end_d.isoformat() + agg["discipline"] + rp._aggregate_hash(agg)).encode()).hexdigest()
    canonical = _NARRATIVES.get(key)
    cached = canonical is not None
    generated_by = "cache"
    if canonical is None:
        canonical, generated_by = generate_llm_summary(agg)
        if generated_by == "llm":
            _NARRATIVES[key] = canonical
    lang = (language or "en").strip().lower()[:2]
    if lang not in SUPPORTED_LANGUAGES:
        lang = "en"
    text, translated = canonical, False
    if lang != "en":
        text, _ = translate_dynamic_text(canonical, lang)
        translated = text != canonical
    metrics, highlights = rp._metrics_and_highlights(agg, got["escalations"])
    return _clean({"reporting_period": {"start_date": start_d.isoformat(), "end_date": end_d.isoformat()}, "discipline": None if agg["discipline"] == "ALL" else agg["discipline"],
                   "summary_text": text, "canonical_summary": canonical, "language": lang, "translated": translated, "cached": cached, "generated_by": generated_by, "metrics": metrics,
                   "key_highlights": highlights, "aggregate": agg, "generated_at": datetime.now(timezone.utc).isoformat()})


class TranslateIn(BaseModel):
    texts: List[str] = Field(max_length=40)
    target_language: str


@router.post("/api/v1/reports/translate")
def translate(body: TranslateIn, ctx: Ctx = Depends(legacy_ctx(P.VIEW_PROJECT))):
    """presentation-layer translation of generated text; any failure returns the original text, so translation never blocks review"""
    from backend.routers.summary import SUPPORTED_LANGUAGES, translate_dynamic_text
    lang = (body.target_language or "en").strip().lower()[:2]
    out = []
    for t in body.texts:
        try:
            text, _ = translate_dynamic_text(t, lang) if lang in SUPPORTED_LANGUAGES else (t, False)
        except Exception:                                             # noqa: BLE001
            text = t
        out.append(text)
    return {"target_language": lang, "texts": out, "translated": [o != t for o, t in zip(out, body.texts)]}


# ---------------------------------------------------------------------------------------------------------------------------------- CSV export
@router.get("/api/v1/export/csv")
def export_csv(ctx: Ctx = Depends(legacy_ctx(P.VIEW_DASHBOARD))):
    """the approved actuals of the viewed schedule version in the canonical 5-column CSV (no claim text: Supervisors and Project Managers may export; engineers may not)"""
    if ctx.access.role not in (SUP, PM):
        raise forbidden("Role may not export approved actuals", "PERMISSION_DENIED")
    from backend.routers.export import format_csv_rows, query_approved_actuals_for_export
    rows = _run(ctx, lambda c, v: query_approved_actuals_for_export(conn=c, schedule_id=v, project_id=str(ctx.project_id)))
    return Response(content=format_csv_rows(rows), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="approved_actuals.csv"'})


# ---------------------------------------------------------------------------------------------------------------------------------- activity directory + history
@router.get("/api/v1/activities")
def list_activities(search: Optional[str] = None, discipline: Optional[str] = None, location: Optional[str] = None, wbs_code: Optional[str] = None,
                    execution_state: Optional[str] = None, is_critical: Optional[str] = None, float_range: Optional[str] = None, has_changes: Optional[bool] = None,
                    change_recency: Optional[str] = None, page: int = 1, page_size: int = 25, sort_by: str = "activity_id", sort_order: str = "asc",
                    ctx: Ctx = Depends(legacy_ctx(P.REVIEW_CLAIMS))):
    """the activity directory with canonical execution state and the original filters (the original listing query on the read model)"""
    from backend.routers.activities import query_activities
    page, page_size = max(1, page), max(1, min(page_size, 500))
    return _run(ctx, lambda c, v: query_activities(schedule_id=v, search=search, discipline=discipline, location=location, wbs_code=wbs_code, execution_state=execution_state,
                                                   is_critical=is_critical, float_range=float_range, has_changes=has_changes, change_recency=change_recency, page=page,
                                                   page_size=page_size, sort_by=sort_by, sort_order=sort_order, conn=c))


@router.get("/api/v1/activities/{activity_id}/history")
def activity_history(activity_id: str, ctx: Ctx = Depends(legacy_ctx(P.REVIEW_CLAIMS))):
    """chronological history of one activity: claims, decisions, conflicts, approved actuals (the original timeline builder on the read model)"""
    from backend.routers.activities import query_activity_history
    return _run(ctx, lambda c, v: query_activity_history(activity_id=activity_id, schedule_id=v, conn=c))
