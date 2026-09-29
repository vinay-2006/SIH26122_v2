"""
Targeted Context Builder for SETUAI V7 Phase 12 Supervising Agent.
Aggregates relevant domain facts into a compact, evidence-preserving representation.
Enforces project isolation and token efficiency.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from backend.agents.schemas import InvocationMode
from backend.agents.tools import (
    get_active_schedule,
    get_activity_progress,
    get_activity_quality_status,
    get_activity_state,
    get_audit_verification,
    get_contractor_summaries,
    get_critical_and_blocked_activities,
    get_impact_summary,
    get_matching_conflicts,
    get_project_progress,
    get_project_status,
    get_quality_holds,
    get_recent_incidents,
    get_recent_matching_events,
    get_review_queue,
    get_stage_details,
    get_stage_progress,
    get_stages_summary,
    get_work_package_summaries,
    search_institutional_memory,
)
from backend.context.project import ProjectContext

logger = logging.getLogger(__name__)


class ContextBuilder:
    """
    Assembles domain intelligence into structured context payloads.
    Guarantees strict multi-tenant project isolation and avoids unbounded DB dumps.
    """

    @classmethod
    def build_briefing_context(cls, context: ProjectContext) -> Dict[str, Any]:
        """
        Builds a comprehensive yet compact project context for the supervisory briefing.
        """
        project = get_project_status(context)
        active_sched = get_active_schedule(context)
        progress = get_project_progress(context)
        stages = get_stages_summary(context)
        activities = get_critical_and_blocked_activities(context)
        quality_holds = get_quality_holds(context)
        contractors = get_contractor_summaries(context)
        review_queue = get_review_queue(context)
        recent_incidents = get_recent_incidents(context, limit=5)
        audit_verif = get_audit_verification(context)
        impact = get_impact_summary(context)

        # Retrieve relevant historical lessons using Phase 11
        # Query for general delays/holds relevant to current blocked items
        historical_memories = []
        if quality_holds or activities.get("blocked"):
            historical_memories = search_institutional_memory(
                context=context,
                query="quality hold delay inspection dispute",
                top_k=3,
            )

        return {
            "project": project,
            "active_schedule": active_sched,
            "overall_progress": progress,
            "stages": stages[:15],  # Cap to top stages
            "critical_activities": activities.get("delayed", [])[:10],
            "blocked_activities": activities.get("blocked", [])[:10],
            "rework_activities": activities.get("rework", [])[:10],
            "quality_holds": quality_holds[:10],
            "contractors": contractors[:10],
            "review_queue": {
                "total_claims": review_queue.get("total_pending_claims", 0),
                "total_reopens": review_queue.get("total_pending_reopens", 0),
                "pending_claims_sample": review_queue.get("claims", [])[:5],
                "pending_reopens_sample": review_queue.get("reopens", [])[:5],
            },
            "recent_incidents": recent_incidents,
            "relevant_historical_incidents": historical_memories,
            "schedule_impact": impact,
            "audit_verification": audit_verif,
        }

    @classmethod
    def build_query_context(
        cls,
        context: ProjectContext,
        query: str,
        mode: Optional[InvocationMode] = None,
        stage_id: Optional[str] = None,
        activity_id: Optional[str] = None,
        contractor_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Builds a focused context tailored to a specific user query and entity focus.
        """
        targeted_context: Dict[str, Any] = {
            "project": get_project_status(context),
            "query": query,
            "mode": mode.value if mode else InvocationMode.ON_DEMAND_BRIEFING.value,
        }

        # 1. Activity-specific analysis
        if activity_id:
            act_state = get_activity_state(context, activity_id)
            act_progress = get_activity_progress(context, activity_id)
            act_quality = get_activity_quality_status(context, activity_id)
            memories = search_institutional_memory(
                context=context, query=query, activity_id=activity_id, top_k=3
            )
            targeted_context["activity_details"] = act_state
            targeted_context["activity_progress"] = act_progress
            targeted_context["activity_quality"] = act_quality
            targeted_context["historical_references"] = memories
            return targeted_context

        # 2. Stage-specific analysis
        if stage_id:
            try:
                stg_uuid = uuid.UUID(stage_id)
                stg_details = get_stage_details(context, stg_uuid)
                stg_progress = get_stage_progress(context, stg_uuid)
                memories = search_institutional_memory(
                    context=context, query=query, stage_id=stage_id, top_k=3
                )
                targeted_context["stage_details"] = stg_details
                targeted_context["stage_progress"] = stg_progress
                targeted_context["historical_references"] = memories
                return targeted_context
            except Exception as e:
                logger.warning(f"[context_builder] Invalid stage_id {stage_id}: {e}")

        # 3. Incident Intelligence mode
        if mode == InvocationMode.INCIDENT_INTELLIGENCE:
            recent_incidents = get_recent_incidents(context, limit=10)
            historical_matches = search_institutional_memory(
                context=context, query=query, top_k=5
            )
            targeted_context["current_project_incidents"] = recent_incidents
            targeted_context["historical_institutional_lessons"] = historical_matches
            return targeted_context

        # 4. Review Queue mode
        if mode == InvocationMode.REVIEW_QUEUE:
            queue = get_review_queue(context, limit=20)
            conflicts = get_matching_conflicts(context)
            targeted_context["review_queue"] = queue
            targeted_context["matching_conflicts"] = conflicts
            return targeted_context

        # 5. Default: build balanced briefing context
        return cls.build_briefing_context(context)
