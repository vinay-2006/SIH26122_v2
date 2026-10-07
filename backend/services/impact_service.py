"""
Compound Impact Intelligence Engine for SETUAI V7 Phase 10.
Provides pure, deterministic schedule delay simulation, multi-predecessor constraint evaluation,
lag/lead handling, float absorption, execution-state gating, cycle detection, and stage/project rollups.
"""

from __future__ import annotations

import collections
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import HTTPException, status

from backend.context.errors import raise_permission_denied, raise_resource_not_found
from backend.context.schedule import ScheduleContext
from backend.rbac.permissions import Permission, has_permission
from backend.repositories.impact_repo import ProjectImpactRepository
from backend.schemas.impact import (
    ActivityImpactItem,
    CreateScenarioRequest,
    FloatAnalysisSummary,
    ImpactScenarioResult,
    ImpactScenarioSummary,
    ImpactSeverity,
    SeedActivityInput,
    StageImpactItem,
)
from backend.schemas.stage import CanonicalExecutionState, WorkflowCondition
from backend.services.stage_service import StageService
from backend.shared.impact import (
    ConstraintEvaluation,
    compare_constraints,
    evaluate_constraint,
    parse_date,
    select_controlling_constraint,
)

logger = logging.getLogger(__name__)


class ImpactService:
    """
    Authoritative service implementing the V7 Compound Impact Engine.
    """

    # =========================================================================
    # 1. CYCLE DETECTION (DETERMINISTIC)
    # =========================================================================

    @staticmethod
    def detect_graph_cycle(
        activities: List[Dict[str, Any]],
        dependencies: List[Dict[str, Any]],
    ) -> Optional[List[str]]:
        """
        Detects directed cycles in the dependency graph using depth-first search with color tagging.
        Returns the cycle node sequence if a cycle is found, or None if the graph is a valid DAG.
        """
        adj: Dict[str, List[str]] = {act["activity_id"]: [] for act in activities}
        for dep in dependencies:
            p_id = dep["predecessor_activity_id"]
            s_id = dep["successor_activity_id"]
            if p_id in adj and s_id in adj:
                adj[p_id].append(s_id)

        # 0 = unvisited, 1 = visiting (in recursion stack), 2 = visited
        color: Dict[str, int] = {k: 0 for k in adj}
        parent: Dict[str, Optional[str]] = {k: None for k in adj}

        for node in sorted(adj.keys()):
            if color[node] != 0:
                continue

            stack = [node]
            while stack:
                curr = stack[-1]
                if color[curr] == 0:
                    color[curr] = 1
                    for neighbor in sorted(adj.get(curr, [])):
                        if color[neighbor] == 1:
                            # Cycle detected! Reconstruct path
                            cycle = [neighbor, curr]
                            p = parent.get(curr)
                            while p and p != neighbor:
                                cycle.append(p)
                                p = parent.get(p)
                            cycle.append(neighbor)
                            cycle.reverse()
                            return cycle
                        elif color[neighbor] == 0:
                            parent[neighbor] = curr
                            stack.append(neighbor)
                else:
                    color[curr] = 2
                    stack.pop()

        return None

    # =========================================================================
    # 2. SEVERITY CLASSIFICATION (DETERMINISTIC)
    # =========================================================================

    @staticmethod
    def classify_severity(
        schedule_impact_days: int,
        affected_activities: List[ActivityImpactItem],
    ) -> ImpactSeverity:
        """
        Deterministically classifies the severity of a scenario:
        - NONE: 0 residual days delay
        - LOW: 1-3 days residual delay, non-critical
        - MEDIUM: 4-7 days residual delay, non-critical
        - HIGH: > 7 days residual delay, or 1-5 days on critical activity
        - CRITICAL: > 5 days on critical activity
        """
        if schedule_impact_days <= 0:
            return ImpactSeverity.NONE

        has_critical_impact = any(
            a.is_critical and (a.residual_delay_days or 0) > 0
            for a in affected_activities
        )
        max_critical_delay = max(
            [a.residual_delay_days or 0 for a in affected_activities if a.is_critical],
            default=0,
        )

        if has_critical_impact:
            if max_critical_delay > 5 or schedule_impact_days > 14:
                return ImpactSeverity.CRITICAL
            return ImpactSeverity.HIGH

        if schedule_impact_days > 7:
            return ImpactSeverity.HIGH
        elif schedule_impact_days >= 4:
            return ImpactSeverity.MEDIUM
        else:
            return ImpactSeverity.LOW

    # =========================================================================
    # 3. COMPOUND PROPAGATION ENGINE
    # =========================================================================

    @classmethod
    def evaluate_compound_impact(
        cls,
        context: ScheduleContext,
        seed_activities: List[SeedActivityInput],
        scenario_name: str = "Compound Impact Preview",
    ) -> ImpactScenarioResult:
        """
        Executes deterministic multi-hop compound impact propagation.
        Zero N+1 queries.
        """
        if not has_permission(context.role, Permission.VIEW_SCHEDULE):
            raise_permission_denied(Permission.VIEW_SCHEDULE.value, context.role)

        if not seed_activities:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one seed activity must be provided for impact simulation.",
            )

        # 1. Bulk-load schedule graph
        activities_raw, dependencies_raw, stages_raw = ProjectImpactRepository.get_schedule_graph_data(context)
        activities_map: Dict[str, Dict[str, Any]] = {a["activity_id"]: a for a in activities_raw}
        stages_map: Dict[str, Dict[str, Any]] = {str(s["stage_id"]): s for s in stages_raw}

        # 2. Validate seeds
        for seed in seed_activities:
            if seed.activity_id not in activities_map:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Seed activity '{seed.activity_id}' not found in schedule '{context.schedule_id}'.",
                )
            if seed.delay_days < 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"delay_days for seed '{seed.activity_id}' must be non-negative.",
                )

        # 3. Cycle Detection
        cycle_path = cls.detect_graph_cycle(activities_raw, dependencies_raw)
        if cycle_path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"IMPACT_GRAPH_CYCLE: Dependency cycle detected in schedule: {' -> '.join(cycle_path)}",
            )

        # 4. Build in-memory graph
        outgoing: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
        incoming: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)

        for dep in dependencies_raw:
            p_id = dep["predecessor_activity_id"]
            s_id = dep["successor_activity_id"]
            if p_id in activities_map and s_id in activities_map:
                outgoing[p_id].append(dep)
                incoming[s_id].append(dep)

        # 5. Frontier Propagation
        # Seed activities initialization
        propagated_residual_delays: Dict[str, int] = {}
        causal_paths: Dict[str, List[str]] = {}
        propagation_depths: Dict[str, int] = {}

        # Frontier queue: (activity_id, current_residual_delay, depth)
        queue: collections.deque[str] = collections.deque()

        for seed in seed_activities:
            act_id = seed.activity_id
            s_act = activities_map[act_id]
            s_float = s_act.get("total_float")
            # Calculate seed initial residual delay after its own float
            if s_float is not None:
                float_val = max(0.0, float(s_float))
                absorbed = int(min(seed.delay_days, float_val))
                residual = int(max(0, seed.delay_days - float_val))
            else:
                absorbed = 0
                residual = seed.delay_days

            propagated_residual_delays[act_id] = residual
            causal_paths[act_id] = [act_id]
            propagation_depths[act_id] = 0
            queue.append(act_id)

        evaluated_successors: Dict[str, ActivityImpactItem] = {}

        while queue:
            curr_id = queue.popleft()
            curr_residual = propagated_residual_delays.get(curr_id, 0)
            curr_depth = propagation_depths.get(curr_id, 0)
            curr_path = causal_paths.get(curr_id, [curr_id])

            # If current activity has no outgoing residual delay, it cannot push successors
            if curr_residual <= 0 and curr_id not in [s.activity_id for s in seed_activities]:
                continue

            for edge in outgoing.get(curr_id, []):
                succ_id = edge["successor_activity_id"]
                if succ_id in [s.activity_id for s in seed_activities]:
                    # Do not overwrite seed activities with downstream loops
                    continue

                succ_data = activities_map[succ_id]
                succ_state = StageService.get_execution_state(succ_data)
                succ_workflow = StageService.get_workflow_condition(succ_data)

                # Execution State Gating:
                # COMPLETED successor that is NOT in rework/reopen cannot be delayed into future
                is_completed = (
                    succ_state == CanonicalExecutionState.COMPLETED.value
                    and succ_workflow not in (
                        WorkflowCondition.REWORK_IN_PROGRESS.value,
                        WorkflowCondition.REOPEN_REQUESTED.value,
                    )
                )

                if is_completed:
                    if succ_id not in evaluated_successors:
                        b_start = parse_date(succ_data.get("planned_start"))
                        b_finish = parse_date(succ_data.get("planned_finish"))
                        evaluated_successors[succ_id] = ActivityImpactItem(
                            activity_id=succ_id,
                            activity_name=succ_data["activity_name"],
                            stage_id=succ_data.get("stage_id"),
                            stage_name=succ_data.get("stage_name"),
                            is_critical=bool(succ_data.get("is_critical") or False),
                            canonical_state=succ_state,
                            workflow_condition=succ_workflow,
                            baseline_start=b_start,
                            baseline_finish=b_finish,
                            shifted_start=b_start,
                            shifted_finish=b_finish,
                            gross_delay_days=0,
                            total_float=float(succ_data["total_float"]) if succ_data.get("total_float") is not None else None,
                            float_status="KNOWN" if succ_data.get("total_float") is not None else "UNKNOWN",
                            absorbed_delay_days=0,
                            residual_delay_days=0,
                            controlling_predecessor=curr_id,
                            controlling_relationship=edge.get("relationship_type") or "FS",
                            lag_days=float(edge.get("lag_days") or 0.0),
                            propagation_depth=curr_depth + 1,
                            causal_path=curr_path + [succ_id],
                            classification="ALREADY_COMPLETED",
                            explanation=f"Activity '{succ_id}' is already COMPLETED; downstream delay is halted.",
                        )
                    continue

                # Gather and evaluate all active incoming constraints for succ_id
                constraints: List[ConstraintEvaluation] = []
                for in_edge in incoming.get(succ_id, []):
                    pred_id = in_edge["predecessor_activity_id"]
                    pred_data = activities_map[pred_id]
                    pred_sim_delay = propagated_residual_delays.get(pred_id, 0)

                    c_eval = evaluate_constraint(
                        predecessor_id=pred_id,
                        predecessor_start=parse_date(pred_data.get("planned_start")),
                        predecessor_finish=parse_date(pred_data.get("planned_finish")),
                        successor_id=succ_id,
                        successor_planned_start=parse_date(succ_data.get("planned_start")),
                        successor_planned_finish=parse_date(succ_data.get("planned_finish")),
                        relationship_type=in_edge.get("relationship_type") or "FS",
                        lag_days=float(in_edge.get("lag_days") or 0.0),
                        is_target_predecessor=(pred_id in [s.activity_id for s in seed_activities]),
                        simulated_delay_days=pred_sim_delay,
                    )
                    constraints.append(c_eval)

                controlling = select_controlling_constraint(constraints)
                if not controlling:
                    continue

                b_start = parse_date(succ_data.get("planned_start"))
                b_finish = parse_date(succ_data.get("planned_finish"))
                raw_float = succ_data.get("total_float")

                if controlling.required_successor_start is not None and b_start is not None:
                    gross_delay = max(0, (controlling.required_successor_start - b_start).days)
                else:
                    gross_delay = 0

                shifted_start = (b_start + timedelta(days=gross_delay)) if b_start else None
                shifted_finish = (b_finish + timedelta(days=gross_delay)) if b_finish else None

                # Float analysis
                if raw_float is not None:
                    float_val = float(raw_float)
                    float_status_val = "KNOWN"
                    absorbed = int(min(gross_delay, max(0.0, float_val)))
                    residual = int(max(0, gross_delay - max(0.0, float_val)))
                else:
                    float_status_val = "UNKNOWN"
                    absorbed = None
                    residual = gross_delay  # Conservative propagation when float is unknown

                # Classification & Explanation
                if gross_delay == 0:
                    classification = "NO_IMPACT"
                    explanation = f"Zero required schedule shift from controlling predecessor '{controlling.predecessor_activity_id}'."
                elif float_status_val == "KNOWN" and residual == 0:
                    classification = "ABSORBED_BY_FLOAT"
                    explanation = (
                        f"Propagated delay of {gross_delay} days fully absorbed by available float "
                        f"({raw_float} days) from predecessor '{controlling.predecessor_activity_id}'."
                    )
                elif bool(succ_data.get("is_critical")):
                    classification = "CRITICAL_PATH_SLIP"
                    explanation = (
                        f"Critical activity impacted by {gross_delay} days from controlling predecessor "
                        f"'{controlling.predecessor_activity_id}'. Residual slip: {residual} days."
                    )
                else:
                    classification = "FLOAT_EXHAUSTED"
                    explanation = (
                        f"Delay of {gross_delay} days exhausted {absorbed or 0} days float from predecessor "
                        f"'{controlling.predecessor_activity_id}'. Residual slip: {residual} days."
                    )

                new_path = (causal_paths.get(controlling.predecessor_activity_id) or [controlling.predecessor_activity_id]) + [succ_id]

                item = ActivityImpactItem(
                    activity_id=succ_id,
                    activity_name=succ_data["activity_name"],
                    stage_id=succ_data.get("stage_id"),
                    stage_name=succ_data.get("stage_name"),
                    is_critical=bool(succ_data.get("is_critical") or False),
                    canonical_state=succ_state,
                    workflow_condition=succ_workflow,
                    baseline_start=b_start,
                    baseline_finish=b_finish,
                    shifted_start=shifted_start,
                    shifted_finish=shifted_finish,
                    gross_delay_days=gross_delay,
                    total_float=float(raw_float) if raw_float is not None else None,
                    float_status=float_status_val,
                    absorbed_delay_days=absorbed,
                    residual_delay_days=residual,
                    controlling_predecessor=controlling.predecessor_activity_id,
                    controlling_relationship=controlling.relationship_type,
                    lag_days=controlling.lag_days,
                    propagation_depth=curr_depth + 1,
                    causal_path=new_path,
                    classification=classification,
                    explanation=explanation,
                )

                # Deduplication / Strongest Path Retention
                existing_item = evaluated_successors.get(succ_id)
                if existing_item is None or (residual is not None and residual > (existing_item.residual_delay_days or 0)):
                    evaluated_successors[succ_id] = item
                    propagated_residual_delays[succ_id] = residual if residual is not None else 0
                    causal_paths[succ_id] = new_path
                    propagation_depths[succ_id] = curr_depth + 1
                    queue.append(succ_id)

        # Sort affected activities by propagation depth and ID
        affected_list = list(evaluated_successors.values())
        affected_list.sort(key=lambda x: (x.propagation_depth, x.activity_id))

        # 6. Stage-level aggregation
        stage_map_items: Dict[uuid.UUID, List[ActivityImpactItem]] = collections.defaultdict(list)
        for aff in affected_list:
            if aff.stage_id:
                stage_map_items[aff.stage_id].append(aff)

        affected_stages: List[StageImpactItem] = []
        for s_id, s_acts in stage_map_items.items():
            s_name = s_acts[0].stage_name or f"Stage {s_id}"
            max_delay = max([a.residual_delay_days or 0 for a in s_acts], default=0)
            if max_delay > 0:
                progression = f"Stage progression delayed by up to {max_delay} days."
            else:
                progression = "Stage schedule absorbed without milestone slip."

            affected_stages.append(
                StageImpactItem(
                    stage_id=s_id,
                    stage_name=s_name,
                    affected_activities_count=len(s_acts),
                    max_stage_delay_days=max_delay,
                    stage_progression_impact=progression,
                )
            )

        # 7. Float Analysis Summary
        known_float = [a for a in affected_list if a.float_status == "KNOWN"]
        unknown_float = [a for a in affected_list if a.float_status == "UNKNOWN"]
        total_absorbed = sum([a.absorbed_delay_days or 0 for a in known_float])
        crit_delays = sum([1 for a in affected_list if a.is_critical and (a.residual_delay_days or 0) > 0])

        float_summary = FloatAnalysisSummary(
            total_activities_evaluated=len(affected_list),
            activities_with_known_float=len(known_float),
            activities_with_unknown_float=len(unknown_float),
            total_float_absorbed_days=total_absorbed,
            critical_path_delays_count=crit_delays,
        )

        # 8. Overall Schedule & Project Impact
        # Max residual delay across affected activities and seeds
        seed_critical_delays = [
            seed.delay_days for seed in seed_activities
            if activities_map[seed.activity_id].get("is_critical")
            or activities_map[seed.activity_id].get("total_float") == 0
        ]
        affected_residuals = [a.residual_delay_days or 0 for a in affected_list]
        schedule_impact = max(affected_residuals + seed_critical_delays, default=0)

        # Severity
        severity = cls.classify_severity(schedule_impact, affected_list)

        return ImpactScenarioResult(
            scenario_id=None,
            project_id=context.project_id,
            schedule_id=context.schedule_id,
            name=scenario_name,
            seed_activities=seed_activities,
            affected_activities=affected_list,
            affected_stages=affected_stages,
            float_analysis=float_summary,
            schedule_impact_days=schedule_impact,
            project_completion_impact_days=schedule_impact,
            severity=severity,
            has_cycle=False,
            cycle_path=None,
            calculated_at=datetime.utcnow(),
            algorithm_version="V7_COMPOUND_A1",
        )

    # =========================================================================
    # 4. SCENARIO PERSISTENCE & MANAGEMENT
    # =========================================================================

    @classmethod
    def create_and_save_scenario(
        cls,
        context: ScheduleContext,
        payload: CreateScenarioRequest,
    ) -> ImpactScenarioResult:
        """
        Evaluates compound impact and commits the reproducible scenario to impact_scenarios.
        Requires MANAGE_SCHEDULE permission.
        """
        if not has_permission(context.role, Permission.MANAGE_SCHEDULE):
            raise_permission_denied(Permission.MANAGE_SCHEDULE.value, context.role)

        result = cls.evaluate_compound_impact(
            context,
            seed_activities=payload.seed_activities,
            scenario_name=payload.name,
        )

        inputs_payload = {
            "name": payload.name,
            "description": payload.description,
            "seed_activities": [s.model_dump() for s in payload.seed_activities],
        }
        results_payload = result.model_dump(exclude={"scenario_id"})

        persisted = ProjectImpactRepository.create_scenario(
            context,
            name=payload.name,
            inputs=inputs_payload,
            results=results_payload,
        )

        result.scenario_id = persisted["scenario_id"]
        return result

    @classmethod
    def get_scenario(
        cls,
        context: ScheduleContext,
        scenario_id: uuid.UUID,
    ) -> ImpactScenarioResult:
        """
        Retrieves a persisted scenario by ID strictly scoped to project and schedule.
        """
        if not has_permission(context.role, Permission.VIEW_SCHEDULE):
            raise_permission_denied(Permission.VIEW_SCHEDULE.value, context.role)

        raw = ProjectImpactRepository.get_scenario(context, scenario_id)
        if not raw:
            raise_resource_not_found("ImpactScenario", str(scenario_id))

        results_data = raw["results"]
        results_data["scenario_id"] = raw["scenario_id"]
        return ImpactScenarioResult(**results_data)

    @classmethod
    def list_scenarios(
        cls,
        context: ScheduleContext,
    ) -> List[ImpactScenarioSummary]:
        """
        Lists summary metadata of all saved scenarios for this project and schedule.
        """
        if not has_permission(context.role, Permission.VIEW_SCHEDULE):
            raise_permission_denied(Permission.VIEW_SCHEDULE.value, context.role)

        rows = ProjectImpactRepository.list_scenarios(context)
        return [
            ImpactScenarioSummary(
                scenario_id=r["scenario_id"],
                project_id=r["project_id"],
                schedule_id=r["schedule_id"],
                name=r["name"],
                created_by=r.get("created_by"),
                created_at=r["created_at"],
                seed_count=r.get("seed_count") or 0,
                affected_count=r.get("affected_count") or 0,
                schedule_impact_days=r.get("schedule_impact_days") or 0,
                severity=r.get("severity") or "NONE",
            )
            for r in rows
        ]

    @classmethod
    def get_single_activity_preview(
        cls,
        context: ScheduleContext,
        activity_id: str,
        delay_days: int,
    ) -> Dict[str, Any]:
        """
        Convenience endpoint for single-activity preview, maintaining backward compatibility.
        """
        seed = SeedActivityInput(activity_id=activity_id, delay_days=delay_days, reason="Single Activity Simulation")
        res = cls.evaluate_compound_impact(context, [seed], scenario_name=f"Preview {activity_id} +{delay_days}d")
        return res.model_dump()

    WATCH_REASONS = {
        "BLOCKED": "Activity is blocked by an open blocker",
        "QUALITY_HOLD": "Activity or a predecessor is on a quality hold",
        "REOPEN_REQUESTED": "A reopen request is awaiting decision",
        "REWORK_IN_PROGRESS": "Approved rework is in progress",
    }

    @classmethod
    def get_watchlist(cls, context: ScheduleContext, sensitivity_days: int = 1) -> List[Dict[str, Any]]:
        """
        Impact watch list: every activity whose derived workflow condition is not NONE (blocked / quality hold /
        reopen / rework), with its real downstream reach if it slips `sensitivity_days`. The slip is a fixed
        sensitivity probe, NOT a forecast: the list ranks how far a problem would propagate through the stored
        network. Deterministic, read-only, project- and schedule-scoped.
        """
        if not has_permission(context.role, Permission.VIEW_SCHEDULE):
            raise_permission_denied(Permission.VIEW_SCHEDULE.value, context.role)
        from backend.services.progress_service import ProgressService

        rank = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
        out: List[Dict[str, Any]] = []
        for a in ProgressService.get_schedule_progress(context).get("activities", []):
            cond = a.get("workflow_condition") or "NONE"
            if cond == "NONE":
                continue
            res = cls.evaluate_compound_impact(
                context, [SeedActivityInput(activity_id=a["activity_id"], delay_days=sensitivity_days, reason=cond)],
                scenario_name=f"Watch {a['activity_id']}",
            )
            stages: Dict[str, Dict[str, Any]] = {}
            for x in res.affected_activities:
                if x.stage_id:
                    st = stages.setdefault(str(x.stage_id), {"stage_id": str(x.stage_id), "stage_name": x.stage_name, "count": 0})
                    st["count"] += 1
            severity = res.severity.value if hasattr(res.severity, "value") else str(res.severity)
            out.append({
                "activity_id": a["activity_id"],
                "activity_name": a["activity_name"],
                "stage_id": str(a["stage_id"]) if a.get("stage_id") else None,
                "workflow_condition": cond,
                "severity": severity,
                "sensitivity_days": sensitivity_days,
                "direct_successor_count": sum(1 for x in res.affected_activities if x.propagation_depth == 1),
                "total_downstream_count": len(res.affected_activities),
                "critical_downstream_count": sum(1 for x in res.affected_activities if x.is_critical),
                "stages": list(stages.values()),
                "project_completion_impact_days": res.project_completion_impact_days,
                "primary_reason": cls.WATCH_REASONS.get(cond, cond),
            })
        out.sort(key=lambda r: (-rank.get(r["severity"], 0), -r["total_downstream_count"], r["activity_id"]))
        return out
