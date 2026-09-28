"""
Stage Domain and Execution State Service for SETUAI V7.
Provides deterministic canonical execution-state calculation, stage-state resolution,
stage-progress aggregation, dependency and quality gate validation, and completion rules.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Union

from fastapi import HTTPException, status

from backend.context.errors import raise_permission_denied, raise_resource_not_found
from backend.context.project import ProjectContext
from backend.context.schedule import ScheduleContext
from backend.rbac.permissions import Permission, has_permission
from backend.repositories.audit_repo import ProjectAuditRepository
from backend.repositories.stage_repo import (
    InvalidStageRelationshipError,
    ProjectStageRepository,
    StageAlreadyExistsError,
    StageNotFoundError,
)
from backend.schemas.stage import (
    CanonicalExecutionState,
    StageCreate,
    StageUpdate,
    WorkflowCondition,
)

logger = logging.getLogger(__name__)


class StageService:
    """
    Authoritative service implementing the Stage and Execution State Engine for V7.
    """

    # =========================================================================
    # 1. CANONICAL EXECUTION STATE & WORKFLOW CONDITION ENGINES (PURE / DETERMINISTIC)
    # =========================================================================

    @staticmethod
    def get_execution_state(
        activity_data: Optional[Dict[str, Any]] = None,
        *,
        actual_pct_complete: Optional[float] = None,
        actual_start: Optional[Any] = None,
    ) -> str:
        """
        Calculates the canonical 3-state execution state deterministically.

        Canonical Rule:
            1. actual_pct_complete >= 100 -> COMPLETED
            2. actual_start IS NOT NULL   -> IN_PROGRESS
            3. else                       -> NOT_STARTED

        Never uses LLMs, probabilistic models, or UI overrides.
        """
        pct = actual_pct_complete
        start = actual_start

        if activity_data:
            if pct is None:
                pct = activity_data.get("actual_pct_complete")
            if start is None:
                start = activity_data.get("actual_start")

        # 1. actual_pct_complete >= 100 -> COMPLETED
        if pct is not None:
            try:
                if float(pct) >= 100.0:
                    return CanonicalExecutionState.COMPLETED.value
            except (ValueError, TypeError):
                pass

        # 2. actual_start IS NOT NULL -> IN_PROGRESS
        if start is not None and str(start).strip() != "":
            return CanonicalExecutionState.IN_PROGRESS.value

        # 3. else -> NOT_STARTED
        return CanonicalExecutionState.NOT_STARTED.value

    @staticmethod
    def get_workflow_condition(
        activity_data: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Resolves operational workflow conditions independently of execution state.

        Conditions:
            - REOPEN_REQUESTED (reopen_status == 'REQUESTED')
            - REWORK_IN_PROGRESS (is_reopened == TRUE or reopen_status == 'APPROVED')
            - QUALITY_HOLD (unresolved quality gates or hold flag)
            - BLOCKED (blocked flag or impediment)
            - NONE
        """
        if not activity_data:
            return WorkflowCondition.NONE.value

        reopen_status = activity_data.get("reopen_status")
        is_reopened = activity_data.get("is_reopened", False)
        status_field = activity_data.get("status")

        if reopen_status == "REQUESTED":
            return WorkflowCondition.REOPEN_REQUESTED.value
        if is_reopened or reopen_status == "APPROVED":
            return WorkflowCondition.REWORK_IN_PROGRESS.value
        if status_field == "QUALITY_HOLD":
            return WorkflowCondition.QUALITY_HOLD.value
        if status_field == "BLOCKED":
            return WorkflowCondition.BLOCKED.value

        return WorkflowCondition.NONE.value

    # =========================================================================
    # 2. STAGE CRUD & LIFECYCLE
    # =========================================================================

    @classmethod
    def create_stage(
        cls,
        context: ScheduleContext,
        payload: StageCreate,
    ) -> Dict[str, Any]:
        """
        Creates a new stage within the project and schedule version.
        Requires MANAGE_SCHEDULE permission.
        """
        if not has_permission(context.role, Permission.MANAGE_SCHEDULE):
            raise_permission_denied(Permission.MANAGE_SCHEDULE.value, context.role)

        stage_dict = payload.model_dump(exclude_unset=True)
        try:
            created = ProjectStageRepository.create(context, stage_dict)
        except StageAlreadyExistsError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
        except InvalidStageRelationshipError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

        ProjectAuditRepository.log(
            context=context.project_context if hasattr(context, "project_context") else context,
            action="STAGE_CREATED",
            entity_type="STAGE",
            entity_id=str(created["stage_id"]),
            schedule_id=getattr(context, "schedule_id", None),
            new_state={
                "stage_name": created["stage_name"],
                "stage_code": created.get("stage_code"),
                "parent_stage_id": str(created["parent_stage_id"]) if created.get("parent_stage_id") else None,
            },
        )
        return created

    @classmethod
    def get_stage(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        stage_id: uuid.UUID,
    ) -> Dict[str, Any]:
        """
        Retrieves a stage by ID within the authorized project context.
        """
        stage = ProjectStageRepository.get(context, stage_id)
        if not stage:
            raise_resource_not_found("Stage", str(stage_id))
        return stage

    @classmethod
    def list_stages(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        parent_stage_id: Optional[uuid.UUID] = None,
    ) -> List[Dict[str, Any]]:
        """
        Lists stages belonging to the authorized project/schedule context.
        """
        return ProjectStageRepository.list(context, parent_stage_id=parent_stage_id)

    @classmethod
    def get_stage_tree(
        cls,
        context: Union[ScheduleContext, ProjectContext],
    ) -> List[Dict[str, Any]]:
        """
        Retrieves the hierarchical stage tree for the authorized project/schedule context.
        """
        return ProjectStageRepository.get_tree(context)

    @classmethod
    def update_stage(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        stage_id: uuid.UUID,
        payload: StageUpdate,
    ) -> Dict[str, Any]:
        """
        Updates stage metadata. Requires MANAGE_SCHEDULE permission.
        """
        if not has_permission(context.role, Permission.MANAGE_SCHEDULE):
            raise_permission_denied(Permission.MANAGE_SCHEDULE.value, context.role)

        existing = ProjectStageRepository.get(context, stage_id)
        if not existing:
            raise_resource_not_found("Stage", str(stage_id))

        updates = payload.model_dump(exclude_unset=True)
        updated = ProjectStageRepository.update(context, stage_id, updates)
        if not updated:
            raise_resource_not_found("Stage", str(stage_id))

        ProjectAuditRepository.log(
            context=context.project_context if hasattr(context, "project_context") else context,
            action="STAGE_UPDATED",
            entity_type="STAGE",
            entity_id=str(stage_id),
            schedule_id=getattr(context, "schedule_id", None),
            new_state={"updated_fields": list(updates.keys())},
        )
        return updated


    # =========================================================================
    # 3. STAGE EXECUTION STATE, PROGRESS, AND COMPLETION ENGINE
    # =========================================================================

    @classmethod
    def calculate_stage_state(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        stage_id: uuid.UUID,
    ) -> Dict[str, Any]:
        """
        Calculates the canonical stage state and activity execution summary.

        Rules:
            1. If stage has no activities -> returns stored status or NOT_STARTED
            2. If all required activities are COMPLETED:
               - Evaluates completion constraints (gates, predecessors, blockers)
               - If all cleared -> stage computed state is COMPLETED
               - If gates/predecessors/blockers unresolved -> stage computed state is IN_PROGRESS
            3. If some activities are IN_PROGRESS or COMPLETED -> stage computed state is IN_PROGRESS
            4. If all activities are NOT_STARTED -> stage computed state is NOT_STARTED
        """
        stage = cls.get_stage(context, stage_id)
        activities = ProjectStageRepository.get_stage_activities_with_actuals(context, stage_id)

        activity_summaries: List[Dict[str, Any]] = []
        completed_count = 0
        in_progress_count = 0
        not_started_count = 0
        stage_workflow_condition = WorkflowCondition.NONE.value

        for act in activities:
            state = cls.get_execution_state(act)
            condition = cls.get_workflow_condition(act)

            if state == CanonicalExecutionState.COMPLETED.value:
                completed_count += 1
            elif state == CanonicalExecutionState.IN_PROGRESS.value:
                in_progress_count += 1
            else:
                not_started_count += 1

            if condition != WorkflowCondition.NONE.value and stage_workflow_condition == WorkflowCondition.NONE.value:
                stage_workflow_condition = condition

            activity_summaries.append({
                "activity_id": act["activity_id"],
                "activity_name": act["activity_name"],
                "canonical_state": state,
                "workflow_condition": condition,
                "actual_pct_complete": act.get("actual_pct_complete"),
                "actual_start": act.get("actual_start"),
                "actual_finish": act.get("actual_finish"),
                "weight_factor": act.get("weight_factor") or 1.0,
                "quality_gate_required": act.get("quality_gate_required", False),
                "is_reopened": bool(act.get("is_reopened") or False),
            })

        total = len(activities)

        if total == 0:
            computed_state = stage.get("status") or CanonicalExecutionState.NOT_STARTED.value
        elif completed_count == total:
            # Check gates, predecessors, blockers
            completion_check = cls.is_stage_complete(context, stage_id)
            if completion_check["is_complete"]:
                computed_state = CanonicalExecutionState.COMPLETED.value
            else:
                computed_state = CanonicalExecutionState.IN_PROGRESS.value
                if completion_check["has_blockers"] and stage_workflow_condition == WorkflowCondition.NONE.value:
                    stage_workflow_condition = WorkflowCondition.BLOCKED.value
        elif in_progress_count > 0 or completed_count > 0:
            computed_state = CanonicalExecutionState.IN_PROGRESS.value
        else:
            computed_state = CanonicalExecutionState.NOT_STARTED.value

        return {
            "stage_id": stage_id,
            "stage_name": stage["stage_name"],
            "computed_state": computed_state,
            "stored_status": stage.get("status", "NOT_STARTED"),
            "workflow_condition": stage_workflow_condition,
            "total_activities": total,
            "completed_activities": completed_count,
            "in_progress_activities": in_progress_count,
            "not_started_activities": not_started_count,
            "activities": activity_summaries,
        }

    @classmethod
    def calculate_stage_progress(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        stage_id: uuid.UUID,
    ) -> Dict[str, Any]:
        """
        Calculates the aggregate progress percentage for a stage.

        Formula:
            Progress = Sum(weight_factor_i * actual_pct_i) / Sum(weight_factor_i)
            If sum of weights == 0 or unweighted: arithmetic average of actual_pct_i.
            If activity has no actual_pct, COMPLETED implies 100.0, else 0.0.
        """
        stage = cls.get_stage(context, stage_id)
        activities = ProjectStageRepository.get_stage_activities_with_actuals(context, stage_id)

        if not activities:
            return {
                "stage_id": stage_id,
                "stage_name": stage["stage_name"],
                "progress_pct": 0.0,
                "calculation_basis": "NO_ACTIVITIES",
                "activity_count": 0,
                "activities": [],
            }

        total_weighted_pct = 0.0
        total_weight = 0.0
        activity_summaries: List[Dict[str, Any]] = []

        for act in activities:
            state = cls.get_execution_state(act)
            condition = cls.get_workflow_condition(act)
            weight = float(act.get("weight_factor") or 1.0)
            if weight <= 0:
                weight = 1.0

            raw_pct = act.get("actual_pct_complete")
            if raw_pct is not None:
                try:
                    pct = float(raw_pct)
                except (ValueError, TypeError):
                    pct = 0.0
            elif state == CanonicalExecutionState.COMPLETED.value:
                pct = 100.0
            else:
                pct = 0.0

            pct = max(0.0, min(100.0, pct))
            total_weighted_pct += weight * pct
            total_weight += weight

            activity_summaries.append({
                "activity_id": act["activity_id"],
                "activity_name": act["activity_name"],
                "canonical_state": state,
                "workflow_condition": condition,
                "actual_pct_complete": pct,
                "actual_start": act.get("actual_start"),
                "actual_finish": act.get("actual_finish"),
                "weight_factor": weight,
                "quality_gate_required": act.get("quality_gate_required", False),
                "is_reopened": bool(act.get("is_reopened") or False),
            })

        overall_pct = (total_weighted_pct / total_weight) if total_weight > 0 else 0.0
        overall_pct = round(max(0.0, min(100.0, overall_pct)), 2)

        return {
            "stage_id": stage_id,
            "stage_name": stage["stage_name"],
            "progress_pct": overall_pct,
            "calculation_basis": "WEIGHTED_FACTOR",
            "activity_count": len(activities),
            "activities": activity_summaries,
        }

    @classmethod
    def check_stage_dependencies(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        stage_id: uuid.UUID,
    ) -> Dict[str, Any]:
        """
        Validates predecessor stage requirements.
        If gating_predecessor_stage_id is set, verifies that the predecessor stage is COMPLETED.
        """
        stage = cls.get_stage(context, stage_id)
        pred_id = stage.get("gating_predecessor_stage_id")

        if not pred_id:
            return {
                "stage_id": stage_id,
                "gating_predecessor_stage_id": None,
                "predecessor_name": None,
                "predecessor_status": None,
                "is_satisfied": True,
                "blocking_reason": None,
            }

        pred_stage = ProjectStageRepository.get(context, pred_id)
        if not pred_stage:
            return {
                "stage_id": stage_id,
                "gating_predecessor_stage_id": pred_id,
                "predecessor_name": None,
                "predecessor_status": None,
                "is_satisfied": False,
                "blocking_reason": f"Gating predecessor stage '{pred_id}' could not be found",
            }

        pred_status = pred_stage.get("status")
        is_satisfied = (pred_status == CanonicalExecutionState.COMPLETED.value)
        blocking_reason = None
        if not is_satisfied:
            blocking_reason = (
                f"Gating predecessor stage '{pred_stage['stage_name']}' is not completed (current status: '{pred_status}')"
            )

        return {
            "stage_id": stage_id,
            "gating_predecessor_stage_id": pred_id,
            "predecessor_name": pred_stage["stage_name"],
            "predecessor_status": pred_status,
            "is_satisfied": is_satisfied,
            "blocking_reason": blocking_reason,
        }

    @classmethod
    def check_stage_gates(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        stage_id: uuid.UUID,
    ) -> Dict[str, Any]:
        """
        Checks quality gates associated with this stage and its activities.
        All required gates must be in ('PASSED', 'WAIVED').
        """
        cls.get_stage(context, stage_id)
        gates = ProjectStageRepository.get_stage_gates(context, stage_id)

        passed_count = 0
        pending_count = 0
        failed_count = 0
        all_cleared = True
        gate_summaries: List[Dict[str, Any]] = []

        for g in gates:
            status = g.get("status", "PENDING")
            required = g.get("required", True)

            if status in ("PASSED", "WAIVED"):
                passed_count += 1
            elif status == "FAILED":
                failed_count += 1
                if required:
                    all_cleared = False
            else:
                pending_count += 1
                if required:
                    all_cleared = False

            gate_summaries.append({
                "quality_gate_id": g["quality_gate_id"],
                "gate_name": g["gate_name"],
                "gate_type": g["gate_type"],
                "required": required,
                "status": status,
                "activity_id": g.get("activity_id"),
            })

        return {
            "stage_id": stage_id,
            "total_gates": len(gates),
            "passed_gates": passed_count,
            "pending_gates": pending_count,
            "failed_gates": failed_count,
            "all_cleared": all_cleared,
            "gates": gate_summaries,
        }

    @classmethod
    def is_stage_complete(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        stage_id: uuid.UUID,
    ) -> Dict[str, Any]:
        """
        Authoritative stage completion rule check:
            A stage is complete ONLY when:
                1. All required activities are COMPLETED
                2. Predecessor stages are SATISFIED
                3. Required quality gates are CLEARED
                4. No active blocking conditions exist
        """
        stage = cls.get_stage(context, stage_id)
        activities = ProjectStageRepository.get_stage_activities_with_actuals(context, stage_id)

        blocking_conditions: List[str] = []

        # 1. Activities check
        total_acts = len(activities)
        completed_acts = 0
        for act in activities:
            st = cls.get_execution_state(act)
            if st == CanonicalExecutionState.COMPLETED.value:
                completed_acts += 1
            cond = cls.get_workflow_condition(act)
            if cond in (WorkflowCondition.BLOCKED.value, WorkflowCondition.QUALITY_HOLD.value):
                blocking_conditions.append(f"Activity '{act['activity_id']}' in workflow hold: {cond}")

        activities_completed = (total_acts > 0 and completed_acts == total_acts)
        if total_acts > 0 and not activities_completed:
            blocking_conditions.append(f"{total_acts - completed_acts} of {total_acts} activities are incomplete")
        elif total_acts == 0:
            activities_completed = False
            blocking_conditions.append("Stage contains zero assigned activities")

        # 2. Dependency check
        dep_check = cls.check_stage_dependencies(context, stage_id)
        dependencies_satisfied = dep_check["is_satisfied"]
        if not dependencies_satisfied and dep_check.get("blocking_reason"):
            blocking_conditions.append(dep_check["blocking_reason"])

        # 3. Gate check
        gate_check = cls.check_stage_gates(context, stage_id)
        gates_cleared = gate_check["all_cleared"]
        if not gates_cleared:
            blocking_conditions.append(
                f"Quality gates unresolved: {gate_check['pending_gates']} pending, {gate_check['failed_gates']} failed"
            )

        # 4. Stage-level stored status blocker
        if stage.get("status") in ("BLOCKED", "ON_HOLD"):
            blocking_conditions.append(f"Stage status is set to '{stage['status']}'")

        is_complete = (
            activities_completed
            and dependencies_satisfied
            and gates_cleared
            and (len(blocking_conditions) == 0)
        )

        return {
            "stage_id": stage_id,
            "stage_name": stage["stage_name"],
            "is_complete": is_complete,
            "activities_completed": activities_completed,
            "dependencies_satisfied": dependencies_satisfied,
            "gates_cleared": gates_cleared,
            "has_blockers": len(blocking_conditions) > 0,
            "blocking_conditions": blocking_conditions,
        }

    # =========================================================================
    # 4. STABLE SERVICE INTERFACES FOR PHASE 6 (STATE-AWARE MATCHING)
    # =========================================================================

    @classmethod
    def get_activity_execution_context(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        activity_id: str,
    ) -> Dict[str, Any]:
        """
        Authoritative contract consumed by Phase 6 (State-Aware Matching) to determine
        candidate eligibility without exposing internal database implementation.
        """
        data = ProjectStageRepository.get_activity_execution_data(context, activity_id)
        if not data:
            raise_resource_not_found("Activity", activity_id)

        canonical_state = cls.get_execution_state(data)
        workflow_cond = cls.get_workflow_condition(data)

        return {
            "activity_id": activity_id,
            "schedule_id": data["schedule_id"],
            "project_id": data["project_id"],
            "stage_id": data.get("stage_id"),
            "canonical_execution_state": canonical_state,
            "workflow_condition": workflow_cond,
            "actual_start": data.get("actual_start"),
            "actual_finish": data.get("actual_finish"),
            "actual_pct_complete": data.get("actual_pct_complete"),
            "quality_gate_required": data.get("quality_gate_required", False),
            "is_reopened": bool(data.get("is_reopened") or False),
            "reopen_status": data.get("reopen_status"),
        }
