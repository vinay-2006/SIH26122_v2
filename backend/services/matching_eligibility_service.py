"""
SETUAI V7 — Phase 6: Activity Eligibility and State-Aware Candidate Selection Engine.
Consumes Phase 5 Canonical Execution State contracts to determine candidate eligibility
for execution report matching within strictly scoped Project + Schedule Version + Stage context.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from backend.shared.workflow_flags import with_workflow_flags
from backend.context.project import ProjectContext
from backend.context.schedule import ScheduleContext
from backend.repositories.stage_repo import ProjectStageRepository
from backend.schemas.stage import CanonicalExecutionState, WorkflowCondition
from backend.services.stage_service import StageService

logger = logging.getLogger(__name__)


# Standard explainable exclusion reasons
REASON_ELIGIBLE = "ELIGIBLE"
REASON_COMPLETED = "ACTIVITY_COMPLETED"
REASON_WRONG_PROJECT = "WRONG_PROJECT"
REASON_WRONG_SCHEDULE = "WRONG_SCHEDULE"
REASON_WRONG_STAGE = "WRONG_STAGE"
REASON_QUALITY_HOLD = "QUALITY_HOLD"
REASON_BLOCKED = "BLOCKED"
REASON_REOPEN_REQUIRED = "REOPEN_WORKFLOW_REQUIRED"
REASON_REWORK_REQUIRED = "REWORK_WORKFLOW_REQUIRED"
REASON_STAGE_COMPLETED = "STAGE_COMPLETED"
REASON_NO_ELIGIBLE_CANDIDATES = "NO_ELIGIBLE_CANDIDATE"


@dataclass
class ActivityEligibilityResult:
    """Deterministic result for single activity matching eligibility."""
    activity_id: str
    eligible: bool
    execution_state: str
    workflow_condition: str
    reason: str
    project_id: Optional[Union[str, uuid.UUID]] = None
    schedule_id: Optional[str] = None
    stage_id: Optional[Union[str, uuid.UUID]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "activity_id": self.activity_id,
            "eligible": self.eligible,
            "execution_state": self.execution_state,
            "workflow_condition": self.workflow_condition,
            "reason": self.reason,
            "project_id": str(self.project_id) if self.project_id else None,
            "schedule_id": self.schedule_id,
            "stage_id": str(self.stage_id) if self.stage_id else None,
        }


class MatchingEligibilityService:
    """
    Authoritative service for Phase 6 State-Aware Matching Eligibility.
    Ensures that field execution reports are matched only against activities
    that are strictly eligible within the scoped Project + Schedule Version + Stage context.
    """

    @classmethod
    def get_activity_eligibility(
        cls,
        activity_data: Dict[str, Any],
        *,
        expected_project_id: Optional[Union[str, uuid.UUID]] = None,
        expected_schedule_id: Optional[str] = None,
        expected_stage_id: Optional[Union[str, uuid.UUID]] = None,
        is_rework: bool = False,
    ) -> ActivityEligibilityResult:
        """
        Determines whether a single activity is eligible for normal or rework matching.

        Rules:
        1. Project Scoping: activity.project_id must match expected_project_id if provided.
        2. Schedule Scoping: activity.schedule_id must match expected_schedule_id if provided.
        3. Stage Scoping: activity.stage_id must match expected_stage_id if provided.
        4. Canonical Execution State:
           - NOT_STARTED -> eligible
           - IN_PROGRESS -> eligible
           - COMPLETED   -> NOT eligible (ACTIVITY_COMPLETED), unless authorized for rework (is_rework=True)
        5. Workflow Condition:
           - REOPEN_REQUESTED   -> NOT eligible (REOPEN_WORKFLOW_REQUIRED)
           - REWORK_IN_PROGRESS -> NOT eligible for normal matching; ELIGIBLE only if is_rework=True
           - QUALITY_HOLD       -> NOT eligible (QUALITY_HOLD)
           - BLOCKED            -> NOT eligible (BLOCKED)
           - NONE               -> eligible (if state allows)
        """
        activity_id = str(activity_data.get("activity_id") or "UNKNOWN")
        act_project_id = activity_data.get("project_id")
        act_schedule_id = activity_data.get("schedule_id")
        act_stage_id = activity_data.get("stage_id")

        # 1. Project Scoping Check
        if expected_project_id is not None and act_project_id is not None:
            if str(act_project_id).strip().lower() != str(expected_project_id).strip().lower():
                return ActivityEligibilityResult(
                    activity_id=activity_id,
                    eligible=False,
                    execution_state=CanonicalExecutionState.NOT_STARTED.value,
                    workflow_condition=WorkflowCondition.NONE.value,
                    reason=REASON_WRONG_PROJECT,
                    project_id=act_project_id,
                    schedule_id=act_schedule_id,
                    stage_id=act_stage_id,
                )

        # 2. Schedule Scoping Check
        if expected_schedule_id is not None and act_schedule_id is not None:
            if str(act_schedule_id).strip() != str(expected_schedule_id).strip():
                return ActivityEligibilityResult(
                    activity_id=activity_id,
                    eligible=False,
                    execution_state=CanonicalExecutionState.NOT_STARTED.value,
                    workflow_condition=WorkflowCondition.NONE.value,
                    reason=REASON_WRONG_SCHEDULE,
                    project_id=act_project_id,
                    schedule_id=act_schedule_id,
                    stage_id=act_stage_id,
                )

        # 3. Stage Scoping Check
        if expected_stage_id is not None and act_stage_id is not None:
            if str(act_stage_id).strip().lower() != str(expected_stage_id).strip().lower():
                return ActivityEligibilityResult(
                    activity_id=activity_id,
                    eligible=False,
                    execution_state=CanonicalExecutionState.NOT_STARTED.value,
                    workflow_condition=WorkflowCondition.NONE.value,
                    reason=REASON_WRONG_STAGE,
                    project_id=act_project_id,
                    schedule_id=act_schedule_id,
                    stage_id=act_stage_id,
                )

        # 4. Canonical Execution State (via Phase 5 Contract)
        execution_state = StageService.get_execution_state(activity_data)
        workflow_condition = StageService.get_workflow_condition(activity_data)

        # Invariant: COMPLETED activity is NOT eligible for normal matching
        # Exception: Under authorized rework (is_rework=True AND workflow_condition=REWORK_IN_PROGRESS)
        if execution_state == CanonicalExecutionState.COMPLETED.value:
            if is_rework and workflow_condition == WorkflowCondition.REWORK_IN_PROGRESS.value:
                return ActivityEligibilityResult(
                    activity_id=activity_id,
                    eligible=True,
                    execution_state=execution_state,
                    workflow_condition=workflow_condition,
                    reason=REASON_ELIGIBLE,
                    project_id=act_project_id,
                    schedule_id=act_schedule_id,
                    stage_id=act_stage_id,
                )
            return ActivityEligibilityResult(
                activity_id=activity_id,
                eligible=False,
                execution_state=execution_state,
                workflow_condition=workflow_condition,
                reason=REASON_COMPLETED,
                project_id=act_project_id,
                schedule_id=act_schedule_id,
                stage_id=act_stage_id,
            )

        # 5. Workflow Condition Exclusions
        if workflow_condition == WorkflowCondition.REOPEN_REQUESTED.value:
            return ActivityEligibilityResult(
                activity_id=activity_id,
                eligible=False,
                execution_state=execution_state,
                workflow_condition=workflow_condition,
                reason=REASON_REOPEN_REQUIRED,
                project_id=act_project_id,
                schedule_id=act_schedule_id,
                stage_id=act_stage_id,
            )

        if workflow_condition == WorkflowCondition.REWORK_IN_PROGRESS.value:
            if is_rework:
                return ActivityEligibilityResult(
                    activity_id=activity_id,
                    eligible=True,
                    execution_state=execution_state,
                    workflow_condition=workflow_condition,
                    reason=REASON_ELIGIBLE,
                    project_id=act_project_id,
                    schedule_id=act_schedule_id,
                    stage_id=act_stage_id,
                )
            return ActivityEligibilityResult(
                activity_id=activity_id,
                eligible=False,
                execution_state=execution_state,
                workflow_condition=workflow_condition,
                reason=REASON_REWORK_REQUIRED,
                project_id=act_project_id,
                schedule_id=act_schedule_id,
                stage_id=act_stage_id,
            )

        if workflow_condition == WorkflowCondition.QUALITY_HOLD.value:
            return ActivityEligibilityResult(
                activity_id=activity_id,
                eligible=False,
                execution_state=execution_state,
                workflow_condition=workflow_condition,
                reason=REASON_QUALITY_HOLD,
                project_id=act_project_id,
                schedule_id=act_schedule_id,
                stage_id=act_stage_id,
            )

        if workflow_condition == WorkflowCondition.BLOCKED.value:
            return ActivityEligibilityResult(
                activity_id=activity_id,
                eligible=False,
                execution_state=execution_state,
                workflow_condition=workflow_condition,
                reason=REASON_BLOCKED,
                project_id=act_project_id,
                schedule_id=act_schedule_id,
                stage_id=act_stage_id,
            )

        # Normal eligible candidate
        return ActivityEligibilityResult(
            activity_id=activity_id,
            eligible=True,
            execution_state=execution_state,
            workflow_condition=workflow_condition,
            reason=REASON_ELIGIBLE,
            project_id=act_project_id,
            schedule_id=act_schedule_id,
            stage_id=act_stage_id,
        )

    @classmethod
    def filter_eligible_activities(
        cls,
        activities: List[Dict[str, Any]],
        *,
        expected_project_id: Optional[Union[str, uuid.UUID]] = None,
        expected_schedule_id: Optional[str] = None,
        expected_stage_id: Optional[Union[str, uuid.UUID]] = None,
        is_rework: bool = False,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, ActivityEligibilityResult]]:
        """
        Filters a list of activities into eligible candidates and returns
        both the eligible list and a dictionary of exclusion explanations for all activities.
        """
        eligible_activities: List[Dict[str, Any]] = []
        audit_explanations: Dict[str, ActivityEligibilityResult] = {}

        for act in activities:
            act_id = str(act.get("activity_id"))
            eligibility = cls.get_activity_eligibility(
                act,
                expected_project_id=expected_project_id,
                expected_schedule_id=expected_schedule_id,
                expected_stage_id=expected_stage_id,
                is_rework=is_rework,
            )
            audit_explanations[act_id] = eligibility
            if eligibility.eligible:
                eligible_activities.append(act)

        return eligible_activities, audit_explanations

    @classmethod
    def get_eligible_activities(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        *,
        stage_id: Optional[Union[str, uuid.UUID]] = None,
        is_rework: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves all eligible activities directly from the database for the given context.
        Uses a single bulk query joining schedule_activities with approved_actuals.
        """
        project_id = context.project_id
        schedule_id = getattr(context, "schedule_id", None)

        query = """
            SELECT sa.activity_id, sa.schedule_id, sa.project_id, sa.stage_id,
                   sa.activity_name, sa.wbs_code, sa.discipline, sa.location,
                   sa.asset_tag, sa.planned_start, sa.planned_finish,
                   sa.planned_quantity, sa.uom, sa.baseline_pct_complete,
                   sa.weight_factor, sa.quality_gate_required,
                   aa.actual_pct_complete, aa.actual_start, aa.actual_finish,
                   aa.is_reopened,
                   (
                       SELECT ee.reopen_status FROM execution_events ee
                       WHERE ee.matched_activity_id = sa.activity_id AND ee.schedule_id = sa.schedule_id
                       ORDER BY ee.event_date DESC LIMIT 1
                   ) AS reopen_status
            FROM schedule_activities sa
            LEFT JOIN approved_actuals aa
                   ON sa.schedule_id = aa.schedule_id AND sa.activity_id = aa.activity_id
            WHERE sa.project_id = %(project_id)s
        """
        params: Dict[str, Any] = {"project_id": project_id}

        if schedule_id:
            query += " AND sa.schedule_id = %(schedule_id)s"
            params["schedule_id"] = schedule_id

        if stage_id:
            query += " AND sa.stage_id = %(stage_id)s"
            params["stage_id"] = str(stage_id)

        query += " ORDER BY sa.activity_id ASC;"

        with ProjectStageRepository.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(with_workflow_flags(query), params)
                all_acts = [dict(r) for r in cur.fetchall()]

        eligible, _ = cls.filter_eligible_activities(
            all_acts,
            expected_project_id=project_id,
            expected_schedule_id=schedule_id,
            expected_stage_id=stage_id,
            is_rework=is_rework,
        )
        return eligible

    @classmethod
    def get_activity_eligibility_by_id(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        activity_id: str,
        *,
        is_rework: bool = False,
    ) -> ActivityEligibilityResult:
        """
        Loads the activity execution data from the database using Phase 5 repository
        and returns its matching eligibility result.
        """
        data = ProjectStageRepository.get_activity_execution_data(context, activity_id)
        if not data:
            return ActivityEligibilityResult(
                activity_id=activity_id,
                eligible=False,
                execution_state=CanonicalExecutionState.NOT_STARTED.value,
                workflow_condition=WorkflowCondition.NONE.value,
                reason="ACTIVITY_NOT_FOUND",
                project_id=context.project_id,
                schedule_id=getattr(context, "schedule_id", None),
            )

        return cls.get_activity_eligibility(
            data,
            expected_project_id=context.project_id,
            expected_schedule_id=getattr(context, "schedule_id", None),
            is_rework=is_rework,
        )
