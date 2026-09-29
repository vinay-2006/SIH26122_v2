"""
Authoritative Weighted Progress Engine for SETUAI V7 Phase 8.
Provides deterministic, explainable, and multi-level progress rollups:
Activity Progress -> Stage Progress -> Schedule Progress -> Project Progress.

Guarantees:
1. Current Authoritative Approved Actual is used; historical revisions are never double-counted.
2. Reopened activities reflect only their current revision.
3. Quantity-based progress fallback when actual_pct_complete is absent.
4. Deterministic NULL and zero-weight handling (never NaN or division-by-zero).
5. Zero N+1 queries via bulk-loading progress repository.
6. Numerical progress is decoupled from Phase 5 stage lifecycle state.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple, Union

from fastapi import HTTPException, status

from backend.context.errors import raise_permission_denied, raise_resource_not_found
from backend.context.project import ProjectContext
from backend.context.schedule import ScheduleContext
from backend.rbac.permissions import Permission, has_permission
from backend.repositories.progress_repo import ProjectProgressRepository
from backend.repositories.stage_repo import ProjectStageRepository, StageNotFoundError
from backend.schemas.progress import (
    ActivityProgressItem,
    ActivityProgressResponse,
    ProgressBreakdownResponse,
    ProjectProgressResponse,
    ScheduleProgressResponse,
    ScheduleVersionProgressItem,
    StageProgressBreakdown,
)
from backend.schemas.stage import CanonicalExecutionState, WorkflowCondition
from backend.services.stage_service import StageService

logger = logging.getLogger(__name__)


class ProgressService:
    """
    Authoritative service implementing the V7 Progress Rollup Engine.
    """

    # =========================================================================
    # 1. DETERMINISTIC ACTIVITY PROGRESS ENGINE
    # =========================================================================

    @staticmethod
    def calculate_activity_progress(
        activity_data: Dict[str, Any],
    ) -> Tuple[float, str]:
        """
        Calculates the canonical percentage completion and calculation basis for a single activity.

        Priority:
            1. actual_pct_complete (explicit human-approved percentage)
            2. (actual_quantity / planned_quantity) * 100.0 (if quantity available and planned > 0)
            3. 100.0 if canonical execution state is COMPLETED
            4. 0.0 default

        Returns:
            Tuple of (clamped_progress_pct, calculation_basis_code)
        """
        raw_pct = activity_data.get("actual_pct_complete")
        if raw_pct is not None:
            try:
                pct = float(raw_pct)
                return round(max(0.0, min(100.0, pct)), 2), "ACTUAL_PCT_COMPLETE"
            except (ValueError, TypeError):
                pass

        act_qty = activity_data.get("actual_quantity")
        plan_qty = activity_data.get("planned_quantity")
        if act_qty is not None and plan_qty is not None:
            try:
                f_act = float(act_qty)
                f_plan = float(plan_qty)
                if f_plan > 0:
                    derived_pct = (f_act / f_plan) * 100.0
                    return round(max(0.0, min(100.0, derived_pct)), 2), "QUANTITY_DERIVED"
            except (ValueError, TypeError, ZeroDivisionError):
                pass

        canonical_state = StageService.get_execution_state(activity_data)
        if canonical_state == CanonicalExecutionState.COMPLETED.value:
            return 100.0, "STATE_COMPLETED"

        return 0.0, "DEFAULT_ZERO"

    @classmethod
    def get_activity_progress(
        cls,
        context: ScheduleContext,
        activity_id: str,
    ) -> Dict[str, Any]:
        """
        Retrieves authoritative progress for a single activity.
        Enforces ScheduleContext and VIEW_SCHEDULE permission.
        """
        if not has_permission(context.role, Permission.VIEW_SCHEDULE):
            raise_permission_denied(Permission.VIEW_SCHEDULE.value, context.role)

        act = ProjectProgressRepository.get_activity_with_actual(context, activity_id)
        if not act:
            raise_resource_not_found("Activity", activity_id)

        canonical_state = StageService.get_execution_state(act)
        workflow_cond = StageService.get_workflow_condition(act)
        progress_pct, basis = cls.calculate_activity_progress(act)

        return {
            "activity_id": act["activity_id"],
            "schedule_id": act["schedule_id"],
            "project_id": act["project_id"],
            "progress_pct": progress_pct,
            "canonical_state": canonical_state,
            "workflow_condition": workflow_cond,
            "is_reopened": bool(act.get("is_reopened") or False),
            "actual_pct_complete": act.get("actual_pct_complete"),
            "actual_quantity": act.get("actual_quantity"),
            "planned_quantity": act.get("planned_quantity"),
            "calculation_basis": basis,
        }

    # =========================================================================
    # 2. STAGE PROGRESS ENGINE
    # =========================================================================

    @classmethod
    def get_stage_progress(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        stage_id: uuid.UUID,
    ) -> Dict[str, Any]:
        """
        Calculates progress for a single stage within the scoped project and schedule.
        Combines activities using weight_factor weighting.
        """
        if not has_permission(context.role, Permission.VIEW_SCHEDULE):
            raise_permission_denied(Permission.VIEW_SCHEDULE.value, context.role)

        stage = ProjectStageRepository.get(context, stage_id)
        if not stage:
            raise_resource_not_found("Stage", str(stage_id))

        activities = ProjectProgressRepository.get_schedule_activities_with_actuals(
            context, stage_id=stage_id
        )

        breakdown = cls._calculate_stage_breakdown(stage, activities)
        return breakdown.model_dump()

    @classmethod
    def _calculate_stage_breakdown(
        cls,
        stage: Dict[str, Any],
        activities: List[Dict[str, Any]],
    ) -> StageProgressBreakdown:
        """
        Internal deterministic calculation of stage progress and explainable activity contributions.
        """
        total_weighted_points = 0.0
        total_weight = 0.0
        activity_items: List[ActivityProgressItem] = []
        completed_count = 0

        # Pass 1: Parse progress, states, and sum weights
        parsed_activities = []
        for act in activities:
            progress_pct, basis = cls.calculate_activity_progress(act)
            state = StageService.get_execution_state(act)
            cond = StageService.get_workflow_condition(act)

            if state == CanonicalExecutionState.COMPLETED.value:
                completed_count += 1

            raw_weight = act.get("weight_factor")
            if raw_weight is None:
                weight = 1.0
            else:
                try:
                    weight = float(raw_weight)
                    if weight < 0.0:
                        weight = 0.0
                except (ValueError, TypeError):
                    weight = 1.0

            total_weight += weight
            parsed_activities.append((act, progress_pct, state, cond, weight))

        # Pass 2: Calculate contributions
        for act, progress_pct, state, cond, weight in parsed_activities:
            total_weighted_points += weight * progress_pct

            # Activity contribution to this stage (percentage points)
            if total_weight > 0.0:
                contribution = round((weight * progress_pct) / total_weight, 2)
            else:
                contribution = 0.0

            activity_items.append(
                ActivityProgressItem(
                    activity_id=act["activity_id"],
                    activity_name=act["activity_name"],
                    stage_id=act.get("stage_id"),
                    canonical_state=state,
                    workflow_condition=cond,
                    is_reopened=bool(act.get("is_reopened") or False),
                    progress_pct=progress_pct,
                    actual_pct_complete=act.get("actual_pct_complete"),
                    actual_quantity=act.get("actual_quantity"),
                    planned_quantity=act.get("planned_quantity"),
                    weight_factor=weight,
                    weighted_contribution=contribution,
                )
            )

        if total_weight > 0.0:
            stage_progress = round(total_weighted_points / total_weight, 2)
        else:
            stage_progress = 0.0

        stage_progress = max(0.0, min(100.0, stage_progress))

        stage_weight_pct = stage.get("weight_pct")
        if stage_weight_pct is not None:
            try:
                stage_weight_pct = float(stage_weight_pct)
            except (ValueError, TypeError):
                stage_weight_pct = None

        return StageProgressBreakdown(
            stage_id=stage["stage_id"],
            stage_name=stage["stage_name"],
            sequence_order=stage.get("sequence_order") or 1,
            weight_pct=stage_weight_pct,
            progress_pct=stage_progress,
            weighted_contribution=0.0,  # Computed at schedule level
            activity_count=len(activities),
            completed_count=completed_count,
            activities=activity_items,
        )

    # =========================================================================
    # 3. SCHEDULE PROGRESS ENGINE
    # =========================================================================

    @classmethod
    def get_schedule_progress(
        cls,
        context: ScheduleContext,
    ) -> Dict[str, Any]:
        """
        Calculates authoritative progress for the entire schedule version.
        Uses stage weight_pct if defined; falls back to activity weight_factor rollup.
        Zero N+1 queries.
        """
        if not has_permission(context.role, Permission.VIEW_SCHEDULE):
            raise_permission_denied(Permission.VIEW_SCHEDULE.value, context.role)

        stages = ProjectProgressRepository.get_schedule_stages(context)
        all_activities = ProjectProgressRepository.get_schedule_activities_with_actuals(context)

        # Index activities by stage_id
        acts_by_stage: Dict[Optional[str], List[Dict[str, Any]]] = {}
        for act in all_activities:
            s_id = str(act["stage_id"]) if act.get("stage_id") else None
            acts_by_stage.setdefault(s_id, []).append(act)

        stage_breakdowns: List[StageProgressBreakdown] = []
        total_stage_weight = 0.0
        weighted_stage_progress_sum = 0.0
        has_positive_stage_weights = False

        for stg in stages:
            s_id_str = str(stg["stage_id"])
            stg_acts = acts_by_stage.get(s_id_str, [])
            stg_breakdown = cls._calculate_stage_breakdown(stg, stg_acts)

            stg_weight = stg_breakdown.weight_pct
            if stg_weight is not None and stg_weight > 0.0:
                has_positive_stage_weights = True
                total_stage_weight += stg_weight
                weighted_stage_progress_sum += stg_weight * stg_breakdown.progress_pct

            stage_breakdowns.append(stg_breakdown)

        # Activity execution state counters
        completed_count = 0
        in_progress_count = 0
        not_started_count = 0
        total_act_weight = 0.0
        total_act_weighted_pts = 0.0
        all_act_items: List[ActivityProgressItem] = []

        for act in all_activities:
            prog_pct, _ = cls.calculate_activity_progress(act)
            state = StageService.get_execution_state(act)
            cond = StageService.get_workflow_condition(act)

            if state == CanonicalExecutionState.COMPLETED.value:
                completed_count += 1
            elif state == CanonicalExecutionState.IN_PROGRESS.value:
                in_progress_count += 1
            else:
                not_started_count += 1

            raw_w = act.get("weight_factor")
            w = 1.0 if raw_w is None else max(0.0, float(raw_w or 1.0))
            total_act_weight += w
            total_act_weighted_pts += w * prog_pct

            all_act_items.append(
                ActivityProgressItem(
                    activity_id=act["activity_id"],
                    activity_name=act["activity_name"],
                    stage_id=act.get("stage_id"),
                    canonical_state=state,
                    workflow_condition=cond,
                    is_reopened=bool(act.get("is_reopened") or False),
                    progress_pct=prog_pct,
                    actual_pct_complete=act.get("actual_pct_complete"),
                    actual_quantity=act.get("actual_quantity"),
                    planned_quantity=act.get("planned_quantity"),
                    weight_factor=w,
                    weighted_contribution=0.0,
                )
            )

        # Determine overall schedule progress and basis
        if has_positive_stage_weights and total_stage_weight > 0.0:
            schedule_pct = round(weighted_stage_progress_sum / total_stage_weight, 2)
            basis = "STAGE_WEIGHTED"
            # Update stage weighted contributions to schedule
            for sb in stage_breakdowns:
                if sb.weight_pct and sb.weight_pct > 0.0:
                    sb.weighted_contribution = round(
                        (sb.weight_pct * sb.progress_pct) / total_stage_weight, 2
                    )
        elif len(all_activities) > 0 and total_act_weight > 0.0:
            schedule_pct = round(total_act_weighted_pts / total_act_weight, 2)
            basis = "ACTIVITY_WEIGHTED"
        else:
            schedule_pct = 0.0
            basis = "EMPTY_OR_ZERO_WEIGHT"

        schedule_pct = max(0.0, min(100.0, schedule_pct))

        res = ScheduleProgressResponse(
            schedule_id=context.schedule_id,
            project_id=context.project_id,
            progress_pct=schedule_pct,
            calculation_basis=basis,
            total_activities=len(all_activities),
            completed_activities=completed_count,
            in_progress_activities=in_progress_count,
            not_started_activities=not_started_count,
            stages=stage_breakdowns,
            activities=all_act_items,
        )
        return res.model_dump()

    # =========================================================================
    # 4. PROJECT PROGRESS ENGINE
    # =========================================================================

    @classmethod
    def get_project_progress(
        cls,
        context: ProjectContext,
        target_schedule_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Calculates project-level progress rollups.
        Enforces ProjectContext and independent calculation of each schedule version (V1 != V2).
        Never merges schedules together.
        """
        if not has_permission(context.role, Permission.VIEW_PROJECT):
            raise_permission_denied(Permission.VIEW_PROJECT.value, context.role)

        schedules = ProjectProgressRepository.get_project_schedules(context)
        if not schedules:
            return {
                "project_id": context.project_id,
                "project_name": "Unknown",
                "progress_pct": 0.0,
                "calculation_basis": "NO_SCHEDULES",
                "target_schedule_id": None,
                "schedules": [],
                "stages": [],
            }

        project_name = schedules[0].get("project_name") or "Project"
        schedule_items: List[ScheduleVersionProgressItem] = []
        selected_schedule_id = target_schedule_id

        # Calculate progress independently for each schedule version
        target_response: Optional[Dict[str, Any]] = None

        for sched in schedules:
            s_id = sched["schedule_id"]
            sched_ctx = ScheduleContext(
                project_context=context,
                schedule_id=s_id,
            )

            try:
                prog = cls.get_schedule_progress(sched_ctx)
            except Exception as e:
                logger.warning("Failed calculating progress for schedule %s: %s", s_id, e)
                prog = {
                    "progress_pct": 0.0,
                    "calculation_basis": "ERROR",
                    "total_activities": 0,
                    "completed_activities": 0,
                    "stages": [],
                }

            item = ScheduleVersionProgressItem(
                schedule_id=s_id,
                version_code=sched.get("version_code"),
                active=bool(sched.get("active") or False),
                progress_pct=prog["progress_pct"],
                calculation_basis=prog["calculation_basis"],
                total_activities=prog["total_activities"],
                completed_activities=prog["completed_activities"],
            )
            schedule_items.append(item)

            if target_schedule_id and s_id == target_schedule_id:
                target_response = prog
            elif not target_schedule_id and sched.get("active"):
                target_response = prog
                selected_schedule_id = s_id

        # If no active schedule matched and no target was given, pick the first schedule
        if not target_response and schedule_items:
            selected_schedule_id = schedule_items[0].schedule_id
            sched_ctx = ScheduleContext(
                project_context=context,
                schedule_id=selected_schedule_id,
            )
            target_response = cls.get_schedule_progress(sched_ctx)

        overall_pct = target_response["progress_pct"] if target_response else 0.0
        basis = target_response["calculation_basis"] if target_response else "EMPTY"
        stages = target_response.get("stages", []) if target_response else []

        res = ProjectProgressResponse(
            project_id=context.project_id,
            project_name=project_name,
            progress_pct=overall_pct,
            calculation_basis=basis,
            target_schedule_id=selected_schedule_id,
            schedules=schedule_items,
            stages=[StageProgressBreakdown(**s) for s in stages],
        )
        return res.model_dump()

    # =========================================================================
    # 5. HIERARCHICAL PROGRESS BREAKDOWN ENGINE
    # =========================================================================

    @classmethod
    def get_progress_breakdown(
        cls,
        context: ScheduleContext,
    ) -> Dict[str, Any]:
        """
        Produces the full hierarchical breakdown: Schedule -> Stages -> Activities
        and lists any unassigned activities.
        """
        if not has_permission(context.role, Permission.VIEW_SCHEDULE):
            raise_permission_denied(Permission.VIEW_SCHEDULE.value, context.role)

        schedule_prog = cls.get_schedule_progress(context)

        # Find unassigned activities (stage_id is None)
        unassigned_items = [
            act for act in schedule_prog.get("activities", [])
            if act.get("stage_id") is None
        ]

        res = ProgressBreakdownResponse(
            project_id=context.project_id,
            project_name="Project",
            schedule_id=context.schedule_id,
            overall_progress_pct=schedule_prog["progress_pct"],
            calculation_basis=schedule_prog["calculation_basis"],
            stages=[StageProgressBreakdown(**s) for s in schedule_prog.get("stages", [])],
            unassigned_activities=[ActivityProgressItem(**a) for a in unassigned_items],
        )
        return res.model_dump()
