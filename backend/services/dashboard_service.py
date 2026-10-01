"""
Project dashboard (SETUAI V7): project -> stage -> discipline progress straight from the database.

Actual progress is NOT stored anywhere but approved_actuals; this service only aggregates it, using the same
per-activity rule (ProgressService.calculate_activity_progress) and the same weight_factor weighting as the stage /
schedule progress engine, so a number on the dashboard can never disagree with /progress.

Planned progress is time-phased and derived (never stored): the share of each activity's planned window that has
elapsed at the schedule's data date (today when the schedule has none), weighted the same way.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from backend.context.errors import raise_permission_denied
from backend.context.schedule import ScheduleContext
from backend.rbac.permissions import Permission, has_permission
from backend.repositories.base import BaseRepository
from backend.repositories.progress_repo import ProjectProgressRepository
from backend.schemas.stage import CanonicalExecutionState, WorkflowCondition
from backend.services.progress_service import ProgressService
from backend.services.stage_service import StageService


def planned_pct(start: Optional[date], finish: Optional[date], as_of: date) -> float:
    """Linear share of the planned window elapsed at `as_of`, 0..100."""
    if start is None or finish is None:
        return 0.0
    if as_of < start:
        return 0.0
    if as_of >= finish:
        return 100.0
    return round(100.0 * ((as_of - start).days + 1) / ((finish - start).days + 1), 2)


def _weight(act: Dict[str, Any]) -> float:
    raw = act.get("weight_factor")
    try:
        return 1.0 if raw is None else max(0.0, float(raw))
    except (TypeError, ValueError):
        return 1.0


class _Acc:
    __slots__ = ("w", "actual", "planned", "n", "completed", "in_progress", "not_started", "blocked")

    def __init__(self) -> None:
        self.w = self.actual = self.planned = 0.0
        self.n = self.completed = self.in_progress = self.not_started = self.blocked = 0

    def add(self, w: float, actual: float, planned: float, state: str, cond: str) -> None:
        self.w += w
        self.actual += w * actual
        self.planned += w * planned
        self.n += 1
        if state == CanonicalExecutionState.COMPLETED.value:
            self.completed += 1
        elif state == CanonicalExecutionState.IN_PROGRESS.value:
            self.in_progress += 1
        else:
            self.not_started += 1
        if cond == WorkflowCondition.BLOCKED.value:
            self.blocked += 1

    def actual_pct(self) -> float:
        return round(self.actual / self.w, 2) if self.w > 0 else 0.0

    def planned_pct(self) -> float:
        return round(self.planned / self.w, 2) if self.w > 0 else 0.0


class DashboardService(BaseRepository):
    @classmethod
    def get_dashboard(cls, context: ScheduleContext) -> Dict[str, Any]:
        if not has_permission(context.role, Permission.VIEW_SCHEDULE):
            raise_permission_denied(Permission.VIEW_SCHEDULE.value, context.role)

        stages = ProjectProgressRepository.get_schedule_stages(context)
        activities = ProjectProgressRepository.get_schedule_activities_with_actuals(context)

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT p.project_id, p.project_code, p.project_name, p.lifecycle_status, p.location, p.project_type, "
                    "       p.planned_start, p.planned_finish, s.data_date "
                    "  FROM projects p JOIN schedules s ON s.project_id = p.project_id "
                    " WHERE p.project_id = %s AND s.schedule_id = %s",
                    (context.project_id, context.schedule_id),
                )
                proj = dict(cur.fetchone())
                cur.execute("SELECT code, name, sort_order FROM disciplines ORDER BY sort_order, code")
                disc_ref = {r["code"]: r for r in cur.fetchall()}
                cur.execute(
                    "SELECT stage_id, count(*) FILTER (WHERE status = 'ACTIVE')::int AS open_issues, count(*)::int AS issues "
                    "  FROM issues WHERE project_id = %s AND schedule_id = %s AND stage_id IS NOT NULL GROUP BY stage_id",
                    (context.project_id, context.schedule_id),
                )
                stage_issues = {str(r["stage_id"]): dict(r) for r in cur.fetchall()}
                cur.execute(
                    "SELECT category_code, count(*)::int AS n, count(*) FILTER (WHERE status = 'ACTIVE')::int AS open "
                    "  FROM issues WHERE project_id = %s AND schedule_id = %s GROUP BY category_code ORDER BY n DESC",
                    (context.project_id, context.schedule_id),
                )
                cat_rows = [dict(r) for r in cur.fetchall()]
                cur.execute(
                    "SELECT count(*)::int AS n FROM execution_events WHERE project_id = %s AND schedule_id = %s "
                    "AND status IN ('VALIDATED', 'REVIEW_REQUIRED', 'HOLD')",
                    (context.project_id, context.schedule_id),
                )
                pending_review = cur.fetchone()["n"]

        as_of: date = proj["data_date"] or date.today()
        overall = _Acc()
        by_stage: Dict[str, _Acc] = {}
        by_disc: Dict[str, _Acc] = {}
        for act in activities:
            actual, _ = ProgressService.calculate_activity_progress(act)
            plan = planned_pct(act.get("planned_start"), act.get("planned_finish"), as_of)
            state = StageService.get_execution_state(act)
            cond = StageService.get_workflow_condition(act)
            w = _weight(act)
            overall.add(w, actual, plan, state, cond)
            by_stage.setdefault(str(act.get("stage_id")), _Acc()).add(w, actual, plan, state, cond)
            by_disc.setdefault(act.get("discipline") or "UNSPECIFIED", _Acc()).add(w, actual, plan, state, cond)

        stage_rows: List[Dict[str, Any]] = []
        for stg in stages:
            acc = by_stage.get(str(stg["stage_id"]), _Acc())
            iss = stage_issues.get(str(stg["stage_id"]), {})
            stage_rows.append({
                "stage_id": stg["stage_id"], "stage_code": stg.get("stage_code"), "stage_name": stg["stage_name"],
                "sequence_order": stg.get("sequence_order") or 1, "weight_pct": stg.get("weight_pct"),
                "status": stg.get("status"), "planned_start": stg.get("planned_start"), "planned_finish": stg.get("planned_finish"),
                "actual_pct": acc.actual_pct(), "planned_pct": acc.planned_pct(),
                "variance_pct": round(acc.actual_pct() - acc.planned_pct(), 2),
                "activity_count": acc.n, "completed_count": acc.completed, "in_progress_count": acc.in_progress,
                "not_started_count": acc.not_started, "blocked_count": acc.blocked,
                "open_issue_count": iss.get("open_issues", 0), "issue_count": iss.get("issues", 0),
            })

        disc_rows = []
        for code, acc in by_disc.items():
            ref = disc_ref.get(code)
            disc_rows.append({
                "discipline": code, "discipline_name": ref["name"] if ref else code,
                "actual_pct": acc.actual_pct(), "planned_pct": acc.planned_pct(),
                "variance_pct": round(acc.actual_pct() - acc.planned_pct(), 2),
                "activity_count": acc.n, "completed_count": acc.completed, "in_progress_count": acc.in_progress,
                "not_started_count": acc.not_started, "blocked_count": acc.blocked,
                "_sort": ref["sort_order"] if ref else 10_000,
            })
        disc_rows.sort(key=lambda r: (r["_sort"], r["discipline"]))
        for r in disc_rows:
            del r["_sort"]

        return {
            "project_id": context.project_id,
            "schedule_id": context.schedule_id,
            "project_code": proj["project_code"],
            "project_name": proj["project_name"],
            "lifecycle_status": proj["lifecycle_status"],
            "location": proj["location"],
            "project_type": proj["project_type"],
            "planned_start": proj["planned_start"],
            "planned_finish": proj["planned_finish"],
            "as_of_date": as_of,
            "overall": {
                "actual_pct": overall.actual_pct(), "planned_pct": overall.planned_pct(),
                "variance_pct": round(overall.actual_pct() - overall.planned_pct(), 2),
                "activity_count": overall.n, "completed_count": overall.completed,
                "in_progress_count": overall.in_progress, "not_started_count": overall.not_started,
                "blocked_count": overall.blocked,
            },
            "stages": stage_rows,
            "disciplines": disc_rows,
            "issues": {
                "open": sum(c["open"] for c in cat_rows), "total": sum(c["n"] for c in cat_rows),
                "by_category": cat_rows,
            },
            "pending_review_count": pending_review,
        }
