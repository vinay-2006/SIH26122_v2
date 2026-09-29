"""
Project- and Schedule-Scoped Stage Repository for SETUAI V7.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Union
import psycopg

from backend.context.project import ProjectContext
from backend.context.schedule import ScheduleContext
from backend.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class StageAlreadyExistsError(Exception):
    """Raised when a stage with the given stage_code already exists in the project/schedule."""


class StageNotFoundError(Exception):
    """Raised when a requested stage is not found in the authorized context."""


class InvalidStageRelationshipError(Exception):
    """Raised when a parent or predecessor stage belongs to a different project or schedule."""


class ProjectStageRepository(BaseRepository):
    """
    Authoritative repository for stage metadata, hierarchy, activities, and gates.
    Strictly scoped to project_id and schedule_id.
    """

    @classmethod
    def create(
        cls,
        context: ScheduleContext,
        stage_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Creates a new stage strictly under the authenticated project and schedule version.
        Validates stage_code uniqueness and boundary integrity of parent/predecessor stages.
        """
        project_id = context.project_id
        schedule_id = context.schedule_id
        stage_code = stage_data.get("stage_code")
        parent_stage_id = stage_data.get("parent_stage_id")
        gating_predecessor_stage_id = stage_data.get("gating_predecessor_stage_id")

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                # 1. Uniqueness check for stage_code within project and schedule
                if stage_code:
                    cur.execute(
                        """
                        SELECT stage_id FROM stages
                        WHERE project_id = %s AND schedule_id = %s AND stage_code = %s;
                        """,
                        (project_id, schedule_id, stage_code),
                    )
                    if cur.fetchone():
                        raise StageAlreadyExistsError(
                            f"Stage with code '{stage_code}' already exists in schedule '{schedule_id}'"
                        )

                # 2. Validate parent_stage_id boundary (must be in same project and schedule)
                if parent_stage_id:
                    cur.execute(
                        """
                        SELECT stage_id FROM stages
                        WHERE stage_id = %s AND project_id = %s AND schedule_id = %s;
                        """,
                        (parent_stage_id, project_id, schedule_id),
                    )
                    if not cur.fetchone():
                        raise InvalidStageRelationshipError(
                            f"Parent stage '{parent_stage_id}' does not exist in authorized schedule '{schedule_id}'"
                        )

                # 3. Validate gating_predecessor_stage_id boundary
                if gating_predecessor_stage_id:
                    cur.execute(
                        """
                        SELECT stage_id FROM stages
                        WHERE stage_id = %s AND project_id = %s AND schedule_id = %s;
                        """,
                        (gating_predecessor_stage_id, project_id, schedule_id),
                    )
                    if not cur.fetchone():
                        raise InvalidStageRelationshipError(
                            f"Gating predecessor stage '{gating_predecessor_stage_id}' does not exist in schedule '{schedule_id}'"
                        )

                # 4. Insert stage
                stage_id = stage_data.get("stage_id") or uuid.uuid4()
                completion_rule = stage_data.get("completion_rule") or {}
                if isinstance(completion_rule, dict):
                    completion_rule_json = json.dumps(completion_rule)
                else:
                    completion_rule_json = str(completion_rule)

                cur.execute(
                    """
                    INSERT INTO stages (
                        stage_id, project_id, schedule_id, parent_stage_id, stage_code,
                        stage_name, sequence_order, weight_pct, status, planned_start,
                        planned_finish, contract_milestone_date, gating_predecessor_stage_id,
                        completion_rule, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now(), now()
                    )
                    RETURNING stage_id, project_id, schedule_id, parent_stage_id, stage_code,
                              stage_name, sequence_order, weight_pct, status, planned_start,
                              planned_finish, contract_milestone_date, gating_predecessor_stage_id,
                              completion_rule, created_at, updated_at;
                    """,
                    (
                        stage_id,
                        project_id,
                        schedule_id,
                        parent_stage_id,
                        stage_code,
                        stage_data["stage_name"],
                        stage_data.get("sequence_order", 1),
                        stage_data.get("weight_pct"),
                        stage_data.get("status", "NOT_STARTED"),
                        stage_data.get("planned_start"),
                        stage_data.get("planned_finish"),
                        stage_data.get("contract_milestone_date"),
                        gating_predecessor_stage_id,
                        completion_rule_json,
                    ),
                )
                row = cur.fetchone()
                conn.commit()
                return dict(row)

    @classmethod
    def get(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        stage_id: uuid.UUID,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves a stage by ID, scoped strictly to the authorized project_id.
        If ScheduleContext is passed, also enforces schedule_id boundary.
        """
        project_id = context.project_id
        schedule_id = getattr(context, "schedule_id", None)

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                if schedule_id:
                    cur.execute(
                        """
                        SELECT stage_id, project_id, schedule_id, parent_stage_id, stage_code,
                               stage_name, sequence_order, weight_pct, status, planned_start,
                               planned_finish, contract_milestone_date, gating_predecessor_stage_id,
                               completion_rule, created_at, updated_at
                        FROM stages
                        WHERE stage_id = %s AND project_id = %s AND schedule_id = %s;
                        """,
                        (stage_id, project_id, schedule_id),
                    )
                else:
                    cur.execute(
                        """
                        SELECT stage_id, project_id, schedule_id, parent_stage_id, stage_code,
                               stage_name, sequence_order, weight_pct, status, planned_start,
                               planned_finish, contract_milestone_date, gating_predecessor_stage_id,
                               completion_rule, created_at, updated_at
                        FROM stages
                        WHERE stage_id = %s AND project_id = %s;
                        """,
                        (stage_id, project_id),
                    )
                row = cur.fetchone()
                return dict(row) if row else None

    @classmethod
    def list(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        parent_stage_id: Optional[uuid.UUID] = None,
    ) -> List[Dict[str, Any]]:
        """
        Lists stages for the project and schedule version.
        """
        project_id = context.project_id
        schedule_id = getattr(context, "schedule_id", None)

        query = """
            SELECT stage_id, project_id, schedule_id, parent_stage_id, stage_code,
                   stage_name, sequence_order, weight_pct, status, planned_start,
                   planned_finish, contract_milestone_date, gating_predecessor_stage_id,
                   completion_rule, created_at, updated_at
            FROM stages
            WHERE project_id = %(project_id)s
        """
        params: Dict[str, Any] = {"project_id": project_id}

        if schedule_id:
            query += " AND schedule_id = %(schedule_id)s"
            params["schedule_id"] = schedule_id
        if parent_stage_id is not None:
            query += " AND parent_stage_id = %(parent_stage_id)s"
            params["parent_stage_id"] = parent_stage_id

        query += " ORDER BY sequence_order ASC, created_at ASC;"

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return [dict(r) for r in cur.fetchall()]

    @classmethod
    def get_tree(
        cls,
        context: Union[ScheduleContext, ProjectContext],
    ) -> List[Dict[str, Any]]:
        """
        Fetches all stages for the project/schedule and constructs a recursive tree.
        """
        stages = cls.list(context)
        nodes: Dict[str, Dict[str, Any]] = {
            str(s["stage_id"]): dict(s, children=[]) for s in stages
        }
        root_nodes: List[Dict[str, Any]] = []

        for s in stages:
            s_id = str(s["stage_id"])
            parent_id = str(s["parent_stage_id"]) if s.get("parent_stage_id") else None
            if parent_id and parent_id in nodes:
                nodes[parent_id]["children"].append(nodes[s_id])
            else:
                root_nodes.append(nodes[s_id])

        return root_nodes

    @classmethod
    def update(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        stage_id: uuid.UUID,
        updates: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Updates stage fields safely within the authorized project and schedule context.
        """
        project_id = context.project_id
        schedule_id = getattr(context, "schedule_id", None)

        allowed_fields = [
            "stage_code", "stage_name", "parent_stage_id", "sequence_order",
            "weight_pct", "status", "planned_start", "planned_finish",
            "contract_milestone_date", "gating_predecessor_stage_id", "completion_rule"
        ]

        set_clauses = []
        params: Dict[str, Any] = {
            "stage_id": stage_id,
            "project_id": project_id,
        }
        if schedule_id:
            params["schedule_id"] = schedule_id

        for k, v in updates.items():
            if k in allowed_fields and v is not None:
                if k == "completion_rule":
                    set_clauses.append(f"{k} = %({k})s::jsonb")
                    params[k] = json.dumps(v) if isinstance(v, dict) else str(v)
                else:
                    set_clauses.append(f"{k} = %({k})s")
                    params[k] = v

        if not set_clauses:
            return cls.get(context, stage_id)

        set_clauses.append("updated_at = now()")
        where_clause = "WHERE stage_id = %(stage_id)s AND project_id = %(project_id)s"
        if schedule_id:
            where_clause += " AND schedule_id = %(schedule_id)s"

        query = f"""
            UPDATE stages
            SET {', '.join(set_clauses)}
            {where_clause}
            RETURNING stage_id, project_id, schedule_id, parent_stage_id, stage_code,
                      stage_name, sequence_order, weight_pct, status, planned_start,
                      planned_finish, contract_milestone_date, gating_predecessor_stage_id,
                      completion_rule, created_at, updated_at;
        """

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                conn.commit()
                return dict(row) if row else None

    @classmethod
    def get_stage_activities_with_actuals(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        stage_id: uuid.UUID,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves all activities assigned to this stage, joined with their latest approved actuals.
        """
        project_id = context.project_id
        schedule_id = getattr(context, "schedule_id", None)

        query = """
            SELECT sa.activity_id, sa.schedule_id, sa.project_id, sa.stage_id, sa.activity_name,
                   sa.wbs_code, sa.planned_start, sa.planned_finish, sa.weight_factor,
                   sa.quality_gate_required,
                   aa.actual_id, aa.actual_start, aa.actual_finish, aa.actual_pct_complete,
                   aa.actual_quantity, aa.is_reopened,
                   (
                       SELECT ee.reopen_status FROM execution_events ee
                       WHERE ee.matched_activity_id = sa.activity_id AND ee.schedule_id = sa.schedule_id
                       ORDER BY ee.event_date DESC LIMIT 1
                   ) AS reopen_status
            FROM schedule_activities sa
            LEFT JOIN approved_actuals aa
                   ON sa.schedule_id = aa.schedule_id AND sa.activity_id = aa.activity_id
            WHERE sa.project_id = %(project_id)s
              AND sa.stage_id = %(stage_id)s
        """
        params: Dict[str, Any] = {"project_id": project_id, "stage_id": stage_id}

        if schedule_id:
            query += " AND sa.schedule_id = %(schedule_id)s"
            params["schedule_id"] = schedule_id

        query += " ORDER BY sa.activity_id ASC;"

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return [dict(r) for r in cur.fetchall()]

    @classmethod
    def get_stage_gates(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        stage_id: uuid.UUID,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves all quality gates directly attached to the stage or to its assigned activities.
        """
        project_id = context.project_id
        schedule_id = getattr(context, "schedule_id", None)

        query = """
            SELECT qg.quality_gate_id, qg.project_id, qg.stage_id, qg.schedule_id,
                   qg.activity_id, qg.gate_type, qg.gate_name, qg.required, qg.status,
                   qg.due_date, qg.remarks
            FROM quality_gates qg
            WHERE qg.project_id = %(project_id)s
              AND (
                  qg.stage_id = %(stage_id)s
                  OR (
                      qg.activity_id IN (
                          SELECT sa.activity_id FROM schedule_activities sa
                          WHERE sa.project_id = %(project_id)s AND sa.stage_id = %(stage_id)s
                      )
                  )
              )
        """
        params: Dict[str, Any] = {"project_id": project_id, "stage_id": stage_id}
        if schedule_id:
            query += " AND (qg.schedule_id = %(schedule_id)s OR qg.schedule_id IS NULL)"
            params["schedule_id"] = schedule_id

        query += " ORDER BY qg.created_at ASC;"

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return [dict(r) for r in cur.fetchall()]

    @classmethod
    def get_activity_execution_data(
        cls,
        context: Union[ScheduleContext, ProjectContext],
        activity_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves a single activity and its actuals execution data.
        """
        project_id = context.project_id
        schedule_id = getattr(context, "schedule_id", None)

        query = """
            SELECT sa.activity_id, sa.schedule_id, sa.project_id, sa.stage_id, sa.activity_name,
                   sa.wbs_code, sa.planned_start, sa.planned_finish, sa.weight_factor,
                   sa.quality_gate_required,
                   aa.actual_id, aa.actual_start, aa.actual_finish, aa.actual_pct_complete,
                   aa.actual_quantity, aa.is_reopened,
                   (
                       SELECT ee.reopen_status FROM execution_events ee
                       WHERE ee.matched_activity_id = sa.activity_id AND ee.schedule_id = sa.schedule_id
                       ORDER BY ee.event_date DESC LIMIT 1
                   ) AS reopen_status
            FROM schedule_activities sa
            LEFT JOIN approved_actuals aa
                   ON sa.schedule_id = aa.schedule_id AND sa.activity_id = aa.activity_id
            WHERE sa.project_id = %(project_id)s
              AND sa.activity_id = %(activity_id)s
        """
        params: Dict[str, Any] = {"project_id": project_id, "activity_id": activity_id}
        if schedule_id:
            query += " AND sa.schedule_id = %(schedule_id)s"
            params["schedule_id"] = schedule_id

        with cls.rls_connection(context.user_id) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                return dict(row) if row else None
