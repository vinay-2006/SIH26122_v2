"""
Bulk Data Collectors for SETUAI V7 Phase 13 Audit Dossier.
Implements O(N + E) query architecture: one bulk read per table, assembled in memory.
Zero N+1 queries.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Set

from backend.shared.workflow_flags import with_workflow_flags
from backend.context.project import ProjectContext
from backend.context.schedule import ScheduleContext
from backend.dossier.schemas import (
    ActivitiesSection,
    ActivityItem,
    ApprovedActualItem,
    ApprovedActualsSection,
    EvidenceItem,
    ExecutionEvidenceSection,
    HumanDecisionItem,
    HumanDecisionsSection,
    ImpactSection,
    MatchingItem,
    MatchingSection,
    ProgressSection,
    ProjectSection,
    ReopenHistoryItem,
    ReopenHistorySection,
    ScheduleSection,
    SectionStatus,
    StageItem,
    StagesSection,
    ValidationItem,
    ValidationSection,
)
from backend.services.impact_service import ImpactService
from backend.services.progress_service import ProgressService
from backend.services.stage_service import StageService
from backend.shared.db import get_connection

logger = logging.getLogger(__name__)


class DossierBulkCollector:
    """
    Executes bulk reads across V7 domain tables and constructs normalized dossier sections.
    """

    # =========================================================================
    # 1. PROJECT SECTION
    # =========================================================================
    @classmethod
    def collect_project(cls, context: ProjectContext) -> ProjectSection:
        query = """
            SELECT project_id, project_code, project_name, status, created_at
            FROM projects
            WHERE project_id = %s;
        """
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, (context.project_id,))
                    row = cur.fetchone()
                    if not row:
                        return ProjectSection(
                            status=SectionStatus.NOT_AVAILABLE,
                            project_id=context.project_id,
                            project_code="UNKNOWN",
                            project_name="Unknown Project",
                            project_status="UNKNOWN",
                        )
                    d = dict(row)
                    return ProjectSection(
                        status=SectionStatus.AVAILABLE,
                        project_id=d["project_id"],
                        project_code=d["project_code"],
                        project_name=d["project_name"],
                        project_status=d.get("status") or "ACTIVE",
                        created_at=d.get("created_at"),
                    )
        except Exception as e:
            logger.warning("Error collecting project for dossier: %s", e)
            return ProjectSection(
                status=SectionStatus.NOT_AVAILABLE,
                project_id=context.project_id,
                project_code="UNKNOWN",
                project_name="Unknown Project",
                project_status="UNKNOWN",
            )

    # =========================================================================
    # 2. SCHEDULE SECTION
    # =========================================================================
    @classmethod
    def collect_schedule(
        cls,
        context: ProjectContext,
        schedule_id: Optional[str] = None,
    ) -> Optional[ScheduleSection]:
        if not schedule_id:
            return None

        query = """
            SELECT schedule_id, project_id, project_name, version_code, active,
                   supersedes_schedule_id, source_hash, version_metadata
            FROM schedules
            WHERE schedule_id = %s AND project_id = %s;
        """
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, (schedule_id, context.project_id))
                    row = cur.fetchone()
                    if not row:
                        return ScheduleSection(
                            status=SectionStatus.NOT_AVAILABLE,
                            schedule_id=schedule_id,
                            is_active=False,
                            metadata={},
                        )
                    d = dict(row)
                    return ScheduleSection(
                        status=SectionStatus.AVAILABLE,
                        schedule_id=d["schedule_id"],
                        version_code=d.get("version_code"),
                        is_active=bool(d.get("active") or False),
                        supersedes_schedule_id=d.get("supersedes_schedule_id"),
                        source_hash=d.get("source_hash"),
                        metadata=d.get("version_metadata") or {},
                    )
        except Exception as e:
            logger.warning("Error collecting schedule for dossier: %s", e)
            return ScheduleSection(
                status=SectionStatus.NOT_AVAILABLE,
                schedule_id=schedule_id,
                is_active=False,
                metadata={},
            )

    # =========================================================================
    # 3. STAGES SECTION
    # =========================================================================
    @classmethod
    def collect_stages(
        cls,
        context: ProjectContext,
        schedule_id: Optional[str] = None,
    ) -> StagesSection:
        query = """
            SELECT stage_id, project_id, stage_code, stage_name, status,
                   sequence_order, parent_stage_id, weight_pct
            FROM stages
            WHERE project_id = %s
            ORDER BY sequence_order ASC, stage_name ASC;
        """
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, (context.project_id,))
                    rows = cur.fetchall()
                    items = [
                        StageItem(
                            stage_id=r["stage_id"],
                            stage_code=r.get("stage_code"),
                            stage_name=r["stage_name"],
                            status=r.get("status"),
                            progress_pct=0.0,
                            sequence_order=r.get("sequence_order") or 1,
                            parent_stage_id=r.get("parent_stage_id"),
                        )
                        for r in rows
                    ]
                    return StagesSection(
                        status=SectionStatus.AVAILABLE if items else SectionStatus.PARTIAL,
                        stages=items,
                    )
        except Exception as e:
            logger.warning("Error collecting stages for dossier: %s", e)
            return StagesSection(status=SectionStatus.NOT_AVAILABLE, stages=[])

    # =========================================================================
    # 4. ACTIVITIES SECTION
    # =========================================================================
    @classmethod
    def collect_activities(
        cls,
        context: ProjectContext,
        schedule_id: Optional[str] = None,
        activity_id: Optional[str] = None,
    ) -> ActivitiesSection:
        query = """
            SELECT
                sa.activity_id, sa.schedule_id, sa.project_id, sa.stage_id,
                sa.activity_name, sa.wbs_code, sa.discipline, sa.location,
                sa.planned_start, sa.planned_finish, sa.planned_quantity,
                sa.weight_factor, sa.quality_gate_required,
                aa.actual_pct_complete, aa.actual_quantity, aa.actual_start,
                aa.actual_finish, aa.is_reopened
            FROM schedule_activities sa
            LEFT JOIN approved_actuals aa
                ON aa.schedule_id = sa.schedule_id
               AND aa.activity_id = sa.activity_id
            WHERE sa.project_id = %(project_id)s
        """
        params: Dict[str, Any] = {"project_id": context.project_id}
        if schedule_id:
            query += " AND sa.schedule_id = %(schedule_id)s"
            params["schedule_id"] = schedule_id
        if activity_id:
            query += " AND sa.activity_id = %(activity_id)s"
            params["activity_id"] = activity_id

        query = with_workflow_flags(query + " ORDER BY sa.activity_id ASC;")

        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    raw_activities = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.warning("Error bulk-loading activities: %s", e)
            raw_activities = []

        if not raw_activities:
            return ActivitiesSection(
                status=SectionStatus.AVAILABLE if activity_id is None else SectionStatus.NOT_AVAILABLE,
                activities=[],
            )

        items: List[ActivityItem] = []
        for act in raw_activities:
            prog_pct, _ = ProgressService.calculate_activity_progress(act)
            state = StageService.get_execution_state(act)
            cond = StageService.get_workflow_condition(act)

            raw_w = act.get("weight_factor")
            w = 1.0 if raw_w is None else max(0.0, float(raw_w or 1.0))

            items.append(
                ActivityItem(
                    activity_id=act["activity_id"],
                    activity_code=act.get("wbs_code"),
                    activity_name=act["activity_name"],
                    canonical_execution_state=state,
                    workflow_condition=cond,
                    progress_pct=prog_pct,
                    weight_factor=w,
                    stage_id=act.get("stage_id"),
                    is_reopened=bool(act.get("is_reopened") or False),
                )
            )

        return ActivitiesSection(status=SectionStatus.AVAILABLE, activities=items)

    # =========================================================================
    # 5. EXECUTION EVIDENCE SECTION
    # =========================================================================
    @classmethod
    def collect_evidence(
        cls,
        context: ProjectContext,
        schedule_id: Optional[str] = None,
        activity_id: Optional[str] = None,
    ) -> ExecutionEvidenceSection:
        """Bulk query joining execution_events, source_documents, and source_references."""
        query = """
            SELECT
                ee.event_id, ee.event_type, ee.event_date, ee.document_id,
                ee.raw_claim_text, ee.matched_activity_id, ee.claimed_pct,
                ee.claimed_quantity,
                sd.file_name AS document_name,
                sr.raw_snippet AS source_reference
            FROM execution_events ee
            LEFT JOIN source_documents sd ON sd.document_id = ee.document_id
            LEFT JOIN source_references sr ON sr.event_id = ee.event_id
            WHERE ee.project_id = %(project_id)s
        """
        params: Dict[str, Any] = {"project_id": context.project_id}

        if schedule_id:
            query += " AND ee.schedule_id = %(schedule_id)s"
            params["schedule_id"] = schedule_id
        if activity_id:
            query += " AND (ee.reported_activity_id = %(activity_id)s OR ee.matched_activity_id = %(activity_id)s)"
            params["activity_id"] = activity_id

        query += " ORDER BY ee.event_date DESC, ee.created_at DESC LIMIT 500;"

        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    rows = cur.fetchall()
                    items = [
                        EvidenceItem(
                            event_id=r["event_id"],
                            event_type=r.get("event_type"),
                            event_date=r.get("event_date"),
                            document_id=r.get("document_id"),
                            document_name=r.get("document_name"),
                            source_reference=r.get("source_reference"),
                            raw_claim_text=r["raw_claim_text"],
                            matched_activity_id=r.get("matched_activity_id"),
                            claimed_pct=r.get("claimed_pct"),
                            claimed_quantity=r.get("claimed_quantity"),
                        )
                        for r in rows
                    ]
                    return ExecutionEvidenceSection(
                        status=SectionStatus.AVAILABLE if items else SectionStatus.PARTIAL,
                        evidence=items,
                    )
        except Exception as e:
            logger.warning("Error collecting evidence for dossier: %s", e)
            return ExecutionEvidenceSection(status=SectionStatus.NOT_AVAILABLE, evidence=[])

    # =========================================================================
    # 6. MATCHING SECTION
    # =========================================================================
    @classmethod
    def collect_matching(
        cls,
        context: ProjectContext,
        schedule_id: Optional[str] = None,
        event_ids: Optional[List[str]] = None,
        activity_id: Optional[str] = None,
    ) -> MatchingSection:
        """Bulk query over candidate_matches."""
        query = """
            SELECT candidate_id, event_id, schedule_id, activity_id, rank_order,
                   match_tier, composite_confidence, semantic_score, fuzzy_score,
                   supporting_signals, disqualifying_signals
            FROM candidate_matches
            WHERE 1=1
        """
        params: Dict[str, Any] = {}

        if schedule_id:
            query += " AND schedule_id = %(schedule_id)s"
            params["schedule_id"] = schedule_id
        if activity_id:
            query += " AND activity_id = %(activity_id)s"
            params["activity_id"] = activity_id
        elif event_ids:
            query += " AND event_id = ANY(%(event_ids)s)"
            params["event_ids"] = event_ids
        else:
            query += " LIMIT 500"

        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    rows = cur.fetchall()
                    items = [
                        MatchingItem(
                            candidate_id=r["candidate_id"],
                            event_id=r["event_id"],
                            activity_id=r["activity_id"],
                            rank_order=r.get("rank_order") or 1,
                            match_tier=r.get("match_tier"),
                            composite_confidence=r.get("composite_confidence"),
                            semantic_score=r.get("semantic_score"),
                            fuzzy_score=r.get("fuzzy_score"),
                            supporting_signals=r.get("supporting_signals"),
                            disqualifying_signals=r.get("disqualifying_signals"),
                        )
                        for r in rows
                    ]
                    return MatchingSection(
                        status=SectionStatus.AVAILABLE if items else SectionStatus.PARTIAL,
                        candidates=items,
                    )
        except Exception as e:
            logger.warning("Error collecting matching decisions: %s", e)
            return MatchingSection(status=SectionStatus.NOT_AVAILABLE, candidates=[])

    # =========================================================================
    # 7. VALIDATION SECTION
    # =========================================================================
    @classmethod
    def collect_validation(
        cls,
        context: ProjectContext,
        schedule_id: Optional[str] = None,
        event_ids: Optional[List[str]] = None,
        activity_id: Optional[str] = None,
    ) -> ValidationSection:
        """Bulk query over validation_issues and conflict_records."""
        issues: List[ValidationItem] = []
        conflict_count = 0

        # Issues
        if event_ids:
            try:
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT issue_id, event_id, rule_code, severity, description
                            FROM validation_issues
                            WHERE event_id = ANY(%s)
                            ORDER BY issue_id ASC;
                            """,
                            (event_ids,),
                        )
                        issues = [
                            ValidationItem(
                                issue_id=r["issue_id"],
                                event_id=r["event_id"],
                                rule_code=r.get("rule_code"),
                                severity=r.get("severity"),
                                description=r["description"],
                            )
                            for r in cur.fetchall()
                        ]
            except Exception as e:
                logger.warning("Error collecting validation issues: %s", e)

        # Conflict count
        if schedule_id:
            try:
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        if activity_id:
                            cur.execute(
                                "SELECT count(*) FROM conflict_records WHERE schedule_id = %s AND activity_id = %s;",
                                (schedule_id, activity_id),
                            )
                        else:
                            cur.execute(
                                "SELECT count(*) FROM conflict_records WHERE schedule_id = %s;",
                                (schedule_id,),
                            )
                        row = cur.fetchone()
                        conflict_count = row[0] if row else 0
            except Exception as e:
                logger.warning("Error collecting conflicts: %s", e)

        return ValidationSection(
            status=SectionStatus.AVAILABLE,
            issues=issues,
            conflict_count=conflict_count,
        )

    # =========================================================================
    # 8. HUMAN DECISIONS SECTION
    # =========================================================================
    @classmethod
    def collect_decisions(
        cls,
        context: ProjectContext,
        event_ids: Optional[List[str]] = None,
        activity_id: Optional[str] = None,
    ) -> HumanDecisionsSection:
        query = """
            SELECT decision_id, event_id, selected_activity_id, action,
                   approved_pct, approved_qty, planner_id, justification, decided_at
            FROM planner_decisions
            WHERE 1=1
        """
        params: Dict[str, Any] = {}
        if activity_id:
            query += " AND selected_activity_id = %(activity_id)s"
            params["activity_id"] = activity_id
        elif event_ids:
            query += " AND event_id = ANY(%(event_ids)s)"
            params["event_ids"] = event_ids
        else:
            query += " LIMIT 200"

        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    rows = cur.fetchall()
                    items = [
                        HumanDecisionItem(
                            decision_id=r["decision_id"],
                            event_id=r["event_id"],
                            activity_id=r["selected_activity_id"],
                            action=r.get("action") or "APPROVE",
                            approved_pct=r.get("approved_pct"),
                            approved_qty=r.get("approved_qty"),
                            planner_id=r.get("planner_id"),
                            justification=r.get("justification") or "",
                            decided_at=r.get("decided_at"),
                        )
                        for r in rows
                    ]
                    return HumanDecisionsSection(
                        status=SectionStatus.AVAILABLE if items else SectionStatus.PARTIAL,
                        decisions=items,
                    )
        except Exception as e:
            logger.warning("Error collecting human decisions: %s", e)
            return HumanDecisionsSection(status=SectionStatus.NOT_AVAILABLE, decisions=[])

    # =========================================================================
    # 9. APPROVED ACTUALS SECTION
    # =========================================================================
    @classmethod
    def collect_approved_actuals(
        cls,
        context: ProjectContext,
        schedule_id: Optional[str] = None,
        activity_id: Optional[str] = None,
    ) -> ApprovedActualsSection:
        """
        Authoritative approved actuals bulk query.
        Current approved actual is taken directly from approved_actuals.
        """
        query = """
            SELECT actual_id, schedule_id, activity_id, project_id,
                   actual_start, actual_finish, actual_pct_complete,
                   actual_quantity, decision_id, is_reopened, created_at
            FROM approved_actuals
            WHERE project_id = %(project_id)s
        """
        params: Dict[str, Any] = {"project_id": context.project_id}

        if schedule_id:
            query += " AND schedule_id = %(schedule_id)s"
            params["schedule_id"] = schedule_id
        if activity_id:
            query += " AND activity_id = %(activity_id)s"
            params["activity_id"] = activity_id

        query += " ORDER BY created_at DESC;"

        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    rows = cur.fetchall()
                    items = [
                        ApprovedActualItem(
                            actual_id=r["actual_id"],
                            activity_id=r["activity_id"],
                            schedule_id=r["schedule_id"],
                            actual_start=r.get("actual_start"),
                            actual_finish=r.get("actual_finish"),
                            actual_pct_complete=r.get("actual_pct_complete"),
                            actual_quantity=r.get("actual_quantity"),
                            decision_id=r["decision_id"],
                            is_reopened=bool(r.get("is_reopened") or False),
                            created_at=r.get("created_at"),
                        )
                        for r in rows
                    ]
                    return ApprovedActualsSection(
                        status=SectionStatus.AVAILABLE if items else SectionStatus.PARTIAL,
                        actuals=items,
                    )
        except Exception as e:
            logger.warning("Error collecting approved actuals: %s", e)
            return ApprovedActualsSection(status=SectionStatus.NOT_AVAILABLE, actuals=[])

    # =========================================================================
    # 10. REOPEN HISTORY SECTION
    # =========================================================================
    @classmethod
    def collect_reopen_history(
        cls,
        context: ProjectContext,
        schedule_id: Optional[str] = None,
        activity_id: Optional[str] = None,
        activities: Optional[List[ActivityItem]] = None,
    ) -> ReopenHistorySection:
        """
        Bulk query retrieving reopen and actual revision audit history.
        Preserves original actual vs revision actual distinction.
        """
        query = """
            SELECT log_id, entity_id, action, actor_id, before_state, after_state, timestamp
            FROM audit_logs
            WHERE project_id = %(project_id)s
              AND action IN ('REQUEST_REOPEN', 'DECIDE_REOPEN', 'RECORD_ACTUAL_REVISION')
        """
        params: Dict[str, Any] = {"project_id": context.project_id}

        if activity_id:
            query += " AND entity_id = %(activity_id)s"
            params["activity_id"] = activity_id

        query += " ORDER BY log_id ASC;"

        logs_by_act: Dict[str, List[Dict[str, Any]]] = {}
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    for r in cur.fetchall():
                        d = dict(r)
                        act_key = str(d["entity_id"])
                        logs_by_act.setdefault(act_key, []).append(d)
        except Exception as e:
            logger.warning("Error collecting reopen history logs: %s", e)

        # Assemble per-activity reopen records
        records: List[ReopenHistoryItem] = []
        target_acts = [activity_id] if activity_id else list(logs_by_act.keys())
        if activities and not target_acts:
            target_acts = [a.activity_id for a in activities if a.is_reopened]

        for act_id in target_acts:
            act_logs = logs_by_act.get(act_id, [])
            records.append(
                ReopenHistoryItem(
                    activity_id=act_id,
                    reopen_status="REOPENED" if act_logs else "NONE",
                    workflow_condition="REWORK_IN_PROGRESS" if act_logs else "STANDARD",
                    historical_revisions_count=len(act_logs),
                    current_actual=None,
                    historical_snapshots=act_logs,
                )
            )

        return ReopenHistorySection(
            status=SectionStatus.AVAILABLE if records else SectionStatus.PARTIAL,
            records=records,
        )

    # =========================================================================
    # 11. PROGRESS SECTION
    # =========================================================================
    @classmethod
    def collect_progress(
        cls,
        context: ProjectContext,
        schedule_id: Optional[str] = None,
    ) -> ProgressSection:
        """Reuses authoritative ProgressService. Zero duplicate calculation."""
        try:
            proj_prog = ProgressService.get_project_progress(context, target_schedule_id=schedule_id)
            sched_pct = 0.0
            basis = proj_prog.get("calculation_basis", "PROJECT_ROLLUP")

            if schedule_id:
                sched_ctx = ScheduleContext(project_context=context, schedule_id=schedule_id)
                sched_prog = ProgressService.get_schedule_progress(sched_ctx)
                sched_pct = sched_prog.get("progress_pct", 0.0)
                basis = sched_prog.get("calculation_basis", basis)

            return ProgressSection(
                status=SectionStatus.AVAILABLE,
                project_progress_pct=proj_prog.get("progress_pct", 0.0),
                schedule_progress_pct=sched_pct,
                calculation_basis=basis,
                breakdowns=proj_prog,
            )
        except Exception as e:
            logger.warning("Error collecting progress for dossier: %s", e)
            return ProgressSection(
                status=SectionStatus.NOT_AVAILABLE,
                project_progress_pct=0.0,
                schedule_progress_pct=0.0,
                calculation_basis="ERROR",
                breakdowns={},
            )

    # =========================================================================
    # 12. IMPACT SECTION
    # =========================================================================
    @classmethod
    def collect_impact(
        cls,
        context: ProjectContext,
        schedule_id: Optional[str] = None,
    ) -> ImpactSection:
        """Reuses ImpactService. Zero duplicate calculation."""
        if not schedule_id:
            return ImpactSection(status=SectionStatus.PARTIAL, scenarios=[])

        try:
            sched_ctx = ScheduleContext(project_context=context, schedule_id=schedule_id)
            scenarios = ImpactService.list_scenarios(sched_ctx)
            return ImpactSection(
                status=SectionStatus.AVAILABLE if scenarios else SectionStatus.PARTIAL,
                scenarios=[s.model_dump() for s in scenarios],
            )
        except Exception as e:
            logger.warning("Error collecting impact scenarios for dossier: %s", e)
            return ImpactSection(status=SectionStatus.NOT_AVAILABLE, scenarios=[])
