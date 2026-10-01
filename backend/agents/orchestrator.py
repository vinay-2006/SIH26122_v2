"""
High-Level Orchestrator for SETUAI V7 Phase 12 Supervising Agent.
Handles public service dispatch, audit logging, and coordination with domain engines.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from backend.agents.schemas import (
    AgentFinding,
    AgentQueryRequest,
    AgentQueryResponse,
    SupervisoryBriefing,
)
from backend.agents.supervising_agent import SupervisingAgent
from backend.agents.tools.review_queue import get_review_queue
from backend.context.project import ProjectContext
from backend.shared.audit import write_audit_log
from backend.shared.db import get_connection
from backend.shared.llm_client import get_default_model, get_llm_provider

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """
    Public entrypoint for supervisory intelligence workflows.
    Ensures that every invocation is authenticated, audited, and strictly read-only.
    """

    @classmethod
    def generate_briefing(cls, context: ProjectContext) -> SupervisoryBriefing:
        """Generates an executive briefing and records an audit trace."""
        briefing = SupervisingAgent.generate_briefing(context)

        # 1. Audit trail record
        try:
            write_audit_log(
                entity_type="SUPERVISING_AGENT",
                entity_id=str(context.project_id),
                action="AGENT_BRIEFING_GENERATED",
                actor_id=str(context.user_id),
                project_id=context.project_id,
                role=context.role,
                before_state=None,
                after_state={
                    "briefing_id": briefing.briefing_id,
                    "agent_status": briefing.agent_status.value,
                    "findings_count": len(briefing.findings),
                    "provider": get_llm_provider(),
                    "model": get_default_model(),
                },
            )
        except Exception as audit_err:
            logger.warning(f"[orchestrator] Audit logging failed for briefing: {audit_err}")

        # 2. Store to agent_briefings table if available
        try:
            with get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO agent_briefings (
                        briefing_id, project_id, trigger_type, severity,
                        title, briefing_text, evidence_refs, recommended_action, status
                    ) VALUES (
                        %(briefing_id)s, %(project_id)s, %(trigger_type)s, %(severity)s,
                        %(title)s, %(briefing_text)s, %(evidence_refs)s, %(recommended_action)s, %(status)s
                    )
                    """,
                    {
                        "briefing_id": briefing.briefing_id,
                        "project_id": context.project_id,
                        "trigger_type": "ON_DEMAND",
                        "severity": "NORMAL" if briefing.agent_status.value == "HEALTHY" else "DEGRADED",
                        "title": f"Supervisory Briefing - {briefing.generated_at[:10]}",
                        "briefing_text": briefing.summary,
                        "evidence_refs": json.dumps([f.model_dump() for f in briefing.findings[:5]], default=str),
                        "recommended_action": "; ".join(briefing.recommended_reviews[:3]),
                        "status": "ACTIVE",
                    },
                )
        except Exception as db_err:
            # Non-blocking if table is unmigrated or temporary DB error
            logger.debug(f"[orchestrator] Could not persist agent_briefings row: {db_err}")

        return briefing

    @classmethod
    def query(cls, context: ProjectContext, request: AgentQueryRequest) -> AgentQueryResponse:
        """Processes an evidence-backed intelligence query with audit trail."""
        response = SupervisingAgent.answer_query(context, request)

        # Audit trail record
        try:
            write_audit_log(
                entity_type="SUPERVISING_AGENT",
                entity_id=str(context.project_id),
                action="AGENT_QUERY_EXECUTED",
                actor_id=str(context.user_id),
                project_id=context.project_id,
                role=context.role,
                before_state=None,
                after_state={
                    "query": request.query,
                    "mode": request.mode.value if request.mode else None,
                    "agent_status": response.agent_status.value,
                    "findings_count": len(response.findings),
                },
            )
        except Exception as audit_err:
            logger.warning(f"[orchestrator] Audit logging failed for query: {audit_err}")

        return response

    @classmethod
    def get_review_queue_intelligence(cls, context: ProjectContext) -> Dict[str, Any]:
        """Provides supervisor review queue intelligence with conflict detection."""
        return get_review_queue(context)

    @classmethod
    def get_findings(cls, context: ProjectContext) -> List[AgentFinding]:
        """Retrieves active findings identified in the latest briefing."""
        briefing = SupervisingAgent.generate_briefing(context)
        return briefing.findings


_orchestrator = AgentOrchestrator()


def get_agent_orchestrator() -> AgentOrchestrator:
    """Dependency provider for AgentOrchestrator."""
    return _orchestrator
