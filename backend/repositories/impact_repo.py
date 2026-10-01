"""
Compound Impact Repository for SETUAI V7 Phase 10.
Enforces project and schedule isolation, bulk graph loading without N+1 queries,
and persistence of reproducible impact scenarios.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional, Tuple, Union

from backend.shared.workflow_flags import with_workflow_flags
from backend.context.project import ProjectContext
from backend.context.schedule import ScheduleContext
from backend.repositories.base import BaseRepository


def _json_serial(obj: Any) -> Any:
    """JSON serializer for objects not serializable by default json code."""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    raise TypeError(f"Type {type(obj)} not serializable")


class ProjectImpactRepository(BaseRepository):
    """
    Project- and schedule-scoped repository for dependency graph and impact scenarios.
    """

    @classmethod
    def get_schedule_graph_data(
        cls,
        context: ScheduleContext,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Bulk-loads all activities, dependencies, and stages for the scoped project and schedule.
        Returns (activities, dependencies, stages) in 3 queries without N+1 roundtrips.
        """
        project_id = context.project_id
        schedule_id = context.schedule_id

        # 1. Activities with stages and approved actuals
        act_query = """
            SELECT sa.activity_id, sa.schedule_id, sa.project_id, sa.stage_id,
                   sa.activity_name, sa.wbs_code, sa.discipline, sa.location,
                   sa.planned_start, sa.planned_finish, sa.planned_quantity,
                   sa.total_float, sa.is_critical, sa.weight_factor, sa.quality_gate_required,
                   stg.stage_name, stg.sequence_order AS stage_sequence_order,
                   aa.actual_start, aa.actual_finish, aa.actual_pct_complete,
                   aa.actual_quantity, aa.is_reopened,
                   (
                       SELECT ee.reopen_status FROM execution_events ee
                       WHERE ee.matched_activity_id = sa.activity_id AND ee.schedule_id = sa.schedule_id
                       ORDER BY ee.event_date DESC LIMIT 1
                   ) AS reopen_status
            FROM schedule_activities sa
            LEFT JOIN stages stg ON sa.stage_id = stg.stage_id AND sa.project_id = stg.project_id
            LEFT JOIN approved_actuals aa ON sa.schedule_id = aa.schedule_id AND sa.activity_id = aa.activity_id
            WHERE sa.project_id = %(project_id)s
              AND sa.schedule_id = %(schedule_id)s
            ORDER BY sa.activity_id ASC;
        """

        # 2. Dependencies
        dep_query = """
            SELECT sd.dependency_id, sd.schedule_id, sd.predecessor_activity_id,
                   sd.successor_activity_id, sd.relationship_type, sd.lag_days
            FROM schedule_dependencies sd
            WHERE sd.schedule_id = %(schedule_id)s;
        """

        # 3. Stages
        stg_query = """
            SELECT stage_id, project_id, schedule_id, stage_code, stage_name,
                   sequence_order, weight_pct, status, planned_start, planned_finish
            FROM stages
            WHERE project_id = %(project_id)s
              AND schedule_id = %(schedule_id)s
            ORDER BY sequence_order ASC;
        """

        params = {"project_id": project_id, "schedule_id": schedule_id}

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(with_workflow_flags(act_query), params)
                activities = [dict(r) for r in cur.fetchall()]

                cur.execute(dep_query, params)
                dependencies = [dict(r) for r in cur.fetchall()]

                cur.execute(stg_query, params)
                stages = [dict(r) for r in cur.fetchall()]

                return activities, dependencies, stages

    @classmethod
    def create_scenario(
        cls,
        context: ScheduleContext,
        name: str,
        inputs: Dict[str, Any],
        results: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Persists a evaluated reproducible impact scenario in impact_scenarios.
        """
        scenario_id = uuid.uuid4()
        inputs_json = json.dumps(inputs, default=_json_serial)
        results_json = json.dumps(results, default=_json_serial)

        query = """
            INSERT INTO impact_scenarios (
                scenario_id, project_id, schedule_id, name, created_by, inputs, results
            ) VALUES (
                %(scenario_id)s, %(project_id)s, %(schedule_id)s, %(name)s, %(created_by)s,
                %(inputs)s::jsonb, %(results)s::jsonb
            )
            RETURNING scenario_id, project_id, schedule_id, name, created_by, created_at, inputs, results;
        """
        params = {
            "scenario_id": scenario_id,
            "project_id": context.project_id,
            "schedule_id": context.schedule_id,
            "name": name,
            "created_by": context.user_id,
            "inputs": inputs_json,
            "results": results_json,
        }

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                return dict(row)

    @classmethod
    def get_scenario(
        cls,
        context: ScheduleContext,
        scenario_id: uuid.UUID,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves a single persisted impact scenario strictly scoped by project and schedule.
        """
        query = """
            SELECT scenario_id, project_id, schedule_id, name, created_by, created_at, inputs, results
            FROM impact_scenarios
            WHERE scenario_id = %(scenario_id)s
              AND project_id = %(project_id)s
              AND schedule_id = %(schedule_id)s;
        """
        params = {
            "scenario_id": scenario_id,
            "project_id": context.project_id,
            "schedule_id": context.schedule_id,
        }

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                return dict(row) if row else None

    @classmethod
    def list_scenarios(
        cls,
        context: ScheduleContext,
    ) -> List[Dict[str, Any]]:
        """
        Lists all persisted impact scenarios for the scoped project and schedule version.
        """
        query = """
            SELECT scenario_id, project_id, schedule_id, name, created_by, created_at,
                   jsonb_array_length(inputs->'seed_activities') AS seed_count,
                   jsonb_array_length(results->'affected_activities') AS affected_count,
                   (results->>'schedule_impact_days')::int AS schedule_impact_days,
                   results->>'severity' AS severity
            FROM impact_scenarios
            WHERE project_id = %(project_id)s
              AND schedule_id = %(schedule_id)s
            ORDER BY created_at DESC;
        """
        params = {
            "project_id": context.project_id,
            "schedule_id": context.schedule_id,
        }

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return [dict(r) for r in cur.fetchall()]
