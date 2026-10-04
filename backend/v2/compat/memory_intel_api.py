"""Institutional memory that works for the project, on v2 data (all read-only except the capture of a lesson from a resolved issue):
  * GET  memory/radar           lessons that apply to the work under way or about to start (backend/v2/memory_radar.py)
  * GET  memory/insights        what the organisation has learned: lessons by category, typical delay, recurrence, and how many resolved issues became lessons
  * GET  memory/capture-queue   resolved issues that have not become a lesson yet (Supervisor)
  * POST memory/capture/{id}    turn one of them into a lesson (Supervisor; the v2 issue domain does the write and the audit)
The search and for-issue endpoints of the original memory feature are unchanged (issues_api.py)."""
from __future__ import annotations

import threading
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from .. import memory_radar, permissions as P
from ..domain import issues as di
from ..domain.common import SUP, actor_tx
from ..errors import ApiError
from . import schedule_api
from .context import Ctx, path_ctx

router = APIRouter(prefix="/api/v1/projects/{project_id}/memory", tags=["legacy-contract: memory intelligence"])
_ENC_LOCK = threading.Lock()
_ENCODER = None
_ENC_FAILED = False


def _encoder():
    """the sentence-embedding model, loaded once; None when it is unavailable (the radar then uses shared terms only)"""
    global _ENCODER, _ENC_FAILED
    if _ENCODER is not None or _ENC_FAILED:
        return _ENCODER
    with _ENC_LOCK:
        if _ENCODER is None and not _ENC_FAILED:
            try:
                from backend.memory.adapters import SentenceTransformersAdapter
                e = SentenceTransformersAdapter()
                if e.is_available():
                    _ENCODER = e
                else:
                    _ENC_FAILED = True
            except Exception:
                _ENC_FAILED = True
    return _ENCODER


def lessons_of(c, ctx: Ctx) -> List[Dict[str, Any]]:
    rows = c.execute(
        "select m.memory_id, m.project_id, m.title, m.narrative, m.root_cause, m.corrective_action, m.lessons_learned, m.outcome, m.category_code, m.discipline_code, m.delay_days, p.project_name "
        "from institutional_memory m join projects p on p.project_id = m.project_id where m.project_id = %s or m.visibility = 'ORGANISATION' order by m.recorded_at desc limit 300", (ctx.project_id,)).fetchall()
    return [{"memory_id": r["memory_id"], "title": r["title"], "narrative": r["narrative"], "root_cause": r["root_cause"], "corrective_action": r["corrective_action"],
             "lessons_learned": r["lessons_learned"], "outcome": r["outcome"], "category": r["category_code"], "discipline": r["discipline_code"],
             "delay_days": float(r["delay_days"]) if r["delay_days"] is not None else None, "project_name": r["project_name"], "own_project": r["project_id"] == ctx.project_id} for r in rows]


def compute_radar(ctx: Ctx, horizon_days: int) -> Dict[str, Any]:
    version = ctx.version_id or ctx.active_version_id
    if version is None:
        raise ApiError(409, "NO_ACTIVE_SCHEDULE", "The project has no active schedule: a Project Manager must activate one first")
    with actor_tx(ctx.actor, readonly=True) as c:
        status = c.execute("select lifecycle_status from projects where project_id = %s", (ctx.project_id,)).fetchone()["lifecycle_status"]
        acts = schedule_api.activities(c, ctx, version)
        lessons = lessons_of(c, ctx)
    work = [{"activity_id": a["activity_id"], "name": a["activity_name"], "stage": a["stage_name"], "discipline": a["discipline"], "state": a["execution_state"],
             "planned_start": date.fromisoformat(a["planned_start"][:10]) if a["planned_start"] else None, "planned_finish": date.fromisoformat(a["planned_finish"][:10]) if a["planned_finish"] else None}
            for a in acts]
    starts = [w["planned_start"] for w in work if w["planned_start"]]
    if status == "COMPLETED":
        return {"project_status": status, "reference_date": None, "horizon_days": horizon_days, "activities_in_window": 0, "activities_with_lessons": 0, "lessons_considered": len(lessons), "items": [],
                "retrieval_mode": "none", "note": "This project is complete: nothing is upcoming. Its lessons feed the organisation's memory for other projects."}
    ref = date.today() if status == "ONGOING" else (min(starts) if starts else date.today())
    enc = _encoder()
    cache: Dict[Any, Any] = {}

    def vec(key, text):
        if key not in cache:
            cache[key] = enc.encode_text(text)
        return cache[key]

    def similarity(query: str, lesson: Dict[str, Any]) -> Optional[float]:
        if enc is None:
            return None
        q = vec(("q", query), query)
        l = vec(("l", lesson["memory_id"]), " ".join(filter(None, [lesson["title"], lesson.get("root_cause"), lesson.get("lessons_learned")])))
        return enc.compute_similarity(q, l) if q and l else None

    out = memory_radar.build(work, lessons, reference=ref, horizon_days=horizon_days, similarity=similarity if enc else None)
    out.update(project_status=status, retrieval_mode="shared terms + semantic similarity" if enc else "shared terms",
               note="Upcoming work is measured from the project's first planned start (it has not started yet)." if status == "UPCOMING" else None)
    return out


@router.get("/radar")
def radar(horizon_days: int = Query(180, ge=7, le=730), ctx: Ctx = Depends(path_ctx(P.VIEW_ROOT_CAUSES))):
    return compute_radar(ctx, horizon_days)


@router.get("/insights")
def insights(ctx: Ctx = Depends(path_ctx(P.VIEW_ROOT_CAUSES))):
    with actor_tx(ctx.actor, readonly=True) as c:
        cats = c.execute(
            "select m.category_code as code, coalesce(ic.name, m.category_code) as name, count(*) as lessons, count(distinct m.project_id) as projects, "
            "percentile_cont(0.5) within group (order by m.delay_days) as median_delay, max(m.delay_days) as max_delay from institutional_memory m "
            "left join issue_categories ic on ic.code = m.category_code where m.project_id = %s or m.visibility = 'ORGANISATION' group by 1, 2 order by count(*) desc, 1", (ctx.project_id,)).fetchall()
        own = c.execute("select count(*) n from institutional_memory where project_id = %s", (ctx.project_id,)).fetchone()["n"]
        total = c.execute("select count(*) n from institutional_memory where project_id = %s or visibility = 'ORGANISATION'", (ctx.project_id,)).fetchone()["n"]
        cap = c.execute("select count(*) filter (where i.status = 'RESOLVED') resolved, count(*) filter (where i.status = 'RESOLVED' and exists (select 1 from institutional_memory m where m.issue_id = i.issue_id)) captured "
                        "from issues i where i.project_id = %s", (ctx.project_id,)).fetchone()
    return {"lessons": {"visible": total, "own_project": own, "from_other_projects": total - own},
            "capture": {"resolved_issues": cap["resolved"], "captured": cap["captured"], "waiting": cap["resolved"] - cap["captured"],
                        "rate_pct": round(100.0 * cap["captured"] / cap["resolved"], 1) if cap["resolved"] else None},
            "categories": [{"category_code": r["code"], "category_name": r["name"], "lessons": r["lessons"], "projects": r["projects"],
                            "median_delay_days": float(r["median_delay"]) if r["median_delay"] is not None else None,
                            "max_delay_days": float(r["max_delay"]) if r["max_delay"] is not None else None, "recurring": r["lessons"] >= 2 or r["projects"] >= 2} for r in cats]}


@router.get("/capture-queue")
def capture_queue(ctx: Ctx = Depends(path_ctx(P.RESOLVE_ISSUE))):
    version = ctx.version_id or ctx.active_version_id
    with actor_tx(ctx.actor, readonly=True) as c:
        rows = c.execute(
            "select i.issue_id, i.title, i.category_code, i.severity, i.description, i.resolution_notes, i.impact_days_actual, i.resolved_at, ba.external_activity_id as activity "
            "from issues i left join baseline_activities ba on ba.activity_uid = i.activity_uid and ba.version_id = %s where i.project_id = %s and i.status = 'RESOLVED' "
            "and not exists (select 1 from institutional_memory m where m.issue_id = i.issue_id) order by i.resolved_at desc nulls last limit 100", (version, ctx.project_id)).fetchall()
    return {"items": [{"issue_id": str(r["issue_id"]), "title": r["title"], "category_code": r["category_code"], "severity": r["severity"], "description": r["description"],
                       "resolution_notes": r["resolution_notes"], "impact_days_actual": float(r["impact_days_actual"]) if r["impact_days_actual"] is not None else None,
                       "resolved_at": r["resolved_at"].isoformat() if r["resolved_at"] else None, "activity_id": r["activity"]} for r in rows]}


class CaptureIn(BaseModel):
    lessons_learned: str = Field(min_length=5, max_length=4000)
    outcome: Optional[str] = Field(default=None, max_length=2000)
    share_with_organisation: bool = False


@router.post("/capture/{issue_id}", status_code=201)
def capture(issue_id: str, body: CaptureIn, ctx: Ctx = Depends(path_ctx(P.RESOLVE_ISSUE, writable=True))):
    import uuid
    if ctx.access.role != SUP:
        raise ApiError(403, "PERMISSION_DENIED", "Only a Supervisor records institutional memory")
    try:
        iid = uuid.UUID(issue_id)
    except ValueError:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "Issue not found")
    with actor_tx(ctx.actor, readonly=True) as c:
        row = c.execute("select resolution_notes, status from issues where project_id = %s and issue_id = %s", (ctx.project_id, iid)).fetchone()
        done = c.execute("select 1 from institutional_memory where project_id = %s and issue_id = %s", (ctx.project_id, iid)).fetchone() if row else None
    if row is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "Issue not found")
    if done:
        raise ApiError(409, "ALREADY_CAPTURED", "This issue is already a lesson")
    out = di.promote_to_memory(ctx.actor, iid, lessons_learned=body.lessons_learned, corrective_action=row["resolution_notes"], outcome=body.outcome,
                               visibility="ORGANISATION" if body.share_with_organisation else "PROJECT")
    return {"memory_id": str(out["memory_id"])}
