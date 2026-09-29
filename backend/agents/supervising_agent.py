"""
Supervising Agent Reasoning Core for SETUAI V7 Phase 12.
Orchestrates context assembly, prompt rendering, shared LLM execution,
robust JSON validation, and deterministic graceful degradation.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.agents.context_builder import ContextBuilder
from backend.agents.prompts import (
    SUPERVISING_AGENT_SYSTEM_PROMPT,
    format_briefing_prompt,
    format_query_prompt,
)
from backend.agents.schemas import (
    AgentFinding,
    AgentQueryRequest,
    AgentQueryResponse,
    AgentStatus,
    EvidenceReference,
    FindingCategory,
    FindingSeverity,
    SupervisoryBriefing,
)
from backend.context.project import ProjectContext
from backend.shared.llm_client import LLMClientError, call_llm

logger = logging.getLogger(__name__)


class SupervisingAgent:
    """
    Reasoning layer and intelligence orchestrator for V7.
    Strictly read-only; delegates authoritative calculations to domain engines.
    """

    @classmethod
    def generate_briefing(cls, context: ProjectContext) -> SupervisoryBriefing:
        """
        Generates a comprehensive project briefing.
        Falls back gracefully to a deterministic rule-based briefing if the LLM is unavailable.
        """
        raw_context = ContextBuilder.build_briefing_context(context)
        now_iso = datetime.now(timezone.utc).isoformat()

        # Attempt LLM synthesis
        user_prompt = format_briefing_prompt(raw_context)
        messages = [
            {"role": "system", "content": SUPERVISING_AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        llm_response_text: Optional[str] = None
        parsed_llm_data: Optional[Dict[str, Any]] = None

        try:
            llm_response_text = call_llm(
                messages=messages,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            if llm_response_text and llm_response_text.strip() != "{}":
                parsed_llm_data = json.loads(llm_response_text)
        except Exception as e:
            logger.warning(f"[supervising_agent] LLM briefing synthesis failed ({e}); falling back to deterministic mode")
            parsed_llm_data = None

        if parsed_llm_data and isinstance(parsed_llm_data, dict) and parsed_llm_data.get("summary"):
            # Healthy LLM response path
            findings = []
            for f in parsed_llm_data.get("findings", []):
                try:
                    ev_list = [
                        EvidenceReference(
                            entity_type=ev.get("entity_type", "UNKNOWN"),
                            entity_id=str(ev.get("entity_id", "")),
                            source_type=ev.get("source_type"),
                            reference_code=ev.get("reference_code"),
                            details=ev.get("details"),
                        )
                        for ev in f.get("evidence", [])
                    ]
                    cat_val = f.get("category", "STAGE_RISK")
                    if cat_val not in FindingCategory.__members__:
                        cat_val = FindingCategory.STAGE_RISK.value

                    sev_val = f.get("severity", "MEDIUM")
                    if sev_val not in FindingSeverity.__members__:
                        sev_val = FindingSeverity.MEDIUM.value

                    findings.append(
                        AgentFinding(
                            category=FindingCategory(cat_val),
                            severity=FindingSeverity(sev_val),
                            title=f.get("title", "Project Observation"),
                            description=f.get("description", ""),
                            why_it_matters=f.get("why_it_matters", ""),
                            evidence=ev_list,
                            affected_entity_type=f.get("affected_entity_type"),
                            affected_entity_id=f.get("affected_entity_id"),
                            recommended_action=f.get("recommended_action", "Review current project state."),
                        )
                    )
                except Exception as parse_err:
                    logger.warning(f"[supervising_agent] Skipped malformed finding: {parse_err}")

            return SupervisoryBriefing(
                project_id=str(context.project_id),
                generated_at=now_iso,
                agent_status=AgentStatus.HEALTHY,
                summary=parsed_llm_data.get("summary", "Supervisory briefing generated."),
                findings=findings,
                overall_progress=raw_context.get("overall_progress", {}),
                stage_summary=raw_context.get("stages", []),
                critical_activities=raw_context.get("critical_activities", []),
                blocked_activities=raw_context.get("blocked_activities", []),
                quality_holds=raw_context.get("quality_holds", []),
                contractor_issues=[],
                schedule_risks=[],
                relevant_historical_incidents=raw_context.get("relevant_historical_incidents", []),
                review_queue_summary=raw_context.get("review_queue", {}),
                recommended_reviews=parsed_llm_data.get("recommended_reviews", []),
                audit_verification=raw_context.get("audit_verification"),
            )

        # Deterministic Degraded Fallback Path
        return cls._build_degraded_briefing(context, raw_context, now_iso)

    @classmethod
    def _build_degraded_briefing(
        cls, context: ProjectContext, raw_context: Dict[str, Any], now_iso: str
    ) -> SupervisoryBriefing:
        """
        Constructs a complete, deterministic SupervisoryBriefing when the LLM is unavailable.
        Zero hallucinations; strictly reflects domain engine outputs.
        """
        progress_info = raw_context.get("overall_progress", {})
        progress_pct = progress_info.get("overall_progress_pct", progress_info.get("progress_pct", 0.0))
        quality_holds = raw_context.get("quality_holds", [])
        blocked_acts = raw_context.get("blocked_activities", [])
        rework_acts = raw_context.get("rework_activities", [])
        review_q = raw_context.get("review_queue", {})

        deterministic_findings: List[AgentFinding] = []
        recommended_reviews: List[str] = []

        # 1. Quality Hold Findings
        for qg in quality_holds:
            deterministic_findings.append(
                AgentFinding(
                    category=FindingCategory.QUALITY_HOLD,
                    severity=FindingSeverity.HIGH,
                    title=f"Quality Hold: {qg.get('gate_name')}",
                    description=f"Required quality gate '{qg.get('gate_name')}' ({qg.get('gate_type')}) is in status {qg.get('status')}.",
                    why_it_matters="Execution is gated; completion and subsequent stages cannot proceed until cleared.",
                    evidence=[
                        EvidenceReference(
                            entity_type="QUALITY_GATE",
                            entity_id=str(qg.get("quality_gate_id")),
                            reference_code=qg.get("gate_name"),
                            details=f"Gate type: {qg.get('gate_type')}, Status: {qg.get('status')}",
                        )
                    ],
                    affected_entity_type="STAGE" if qg.get("stage_id") else "ACTIVITY",
                    affected_entity_id=str(qg.get("stage_id") or qg.get("activity_id")),
                    recommended_action="Site Supervisor / Quality Inspector must inspect evidence and clear or reject hold.",
                )
            )
            recommended_reviews.append(f"Inspect Quality Gate {qg.get('gate_name')}")

        # 2. Blocked Activities Findings
        for act in blocked_acts:
            deterministic_findings.append(
                AgentFinding(
                    category=FindingCategory.DEPENDENCY_BLOCK,
                    severity=FindingSeverity.HIGH,
                    title=f"Activity Blocked: {act.get('activity_name')}",
                    description=f"Activity {act.get('activity_id')} is blocked under condition {act.get('workflow_condition')}.",
                    why_it_matters="Work cannot proceed in the field, delaying stage progress.",
                    evidence=[
                        EvidenceReference(
                            entity_type="ACTIVITY",
                            entity_id=str(act.get("activity_id")),
                            reference_code=act.get("activity_id"),
                            details=f"Condition: {act.get('workflow_condition')}",
                        )
                    ],
                    affected_entity_type="ACTIVITY",
                    affected_entity_id=str(act.get("activity_id")),
                    recommended_action="Review activity impediments and resolve predecessor or quality constraints.",
                )
            )
            recommended_reviews.append(f"Review Blocked Activity {act.get('activity_id')}")

        # 3. Rework Findings
        for act in rework_acts:
            deterministic_findings.append(
                AgentFinding(
                    category=FindingCategory.ACTIVITY_DELAY,
                    severity=FindingSeverity.MEDIUM,
                    title=f"Rework In Progress: {act.get('activity_name')}",
                    description=f"Activity {act.get('activity_id')} has an active reopen/rework status: {act.get('reopen_status')}.",
                    why_it_matters="Revised work requires re-inspection and updated actual approval.",
                    evidence=[
                        EvidenceReference(
                            entity_type="ACTIVITY",
                            entity_id=str(act.get("activity_id")),
                            reference_code=act.get("activity_id"),
                            details=f"Reopen status: {act.get('reopen_status')}",
                        )
                    ],
                    affected_entity_type="ACTIVITY",
                    affected_entity_id=str(act.get("activity_id")),
                    recommended_action="Inspect reworked field installation and submit revised actuals.",
                )
            )

        # 4. Review Queue Findings
        total_claims = review_q.get("total_claims", 0)
        total_reopens = review_q.get("total_reopens", 0)
        if total_claims > 0 or total_reopens > 0:
            deterministic_findings.append(
                AgentFinding(
                    category=FindingCategory.REVIEW_QUEUE,
                    severity=FindingSeverity.MEDIUM,
                    title=f"Review Queue: {total_claims} Claims, {total_reopens} Reopens Pending",
                    description=f"Supervisor review queue currently has {total_claims} unreviewed claims and {total_reopens} reopen requests.",
                    why_it_matters="Unreviewed claims prevent field progress from advancing to approved actuals.",
                    evidence=[
                        EvidenceReference(
                            entity_type="REVIEW_QUEUE",
                            entity_id=str(context.project_id),
                            details=f"{total_claims} claims, {total_reopens} reopens",
                        )
                    ],
                    affected_entity_type="PROJECT",
                    affected_entity_id=str(context.project_id),
                    recommended_action="Planner / Supervisor review and act upon pending queue items.",
                )
            )
            recommended_reviews.append("Clear Pending Review Queue Items")

        summary = (
            f"Supervisory Briefing (Deterministic Mode / Engine Standby): Overall project progress is currently "
            f"{progress_pct}%. There are {len(quality_holds)} active quality holds, {len(blocked_acts)} blocked activities, "
            f"and {total_claims + total_reopens} items in the supervisor review queue."
        )

        return SupervisoryBriefing(
            project_id=str(context.project_id),
            generated_at=now_iso,
            agent_status=AgentStatus.DEGRADED,
            summary=summary,
            findings=deterministic_findings,
            overall_progress=progress_info,
            stage_summary=raw_context.get("stages", []),
            critical_activities=raw_context.get("critical_activities", []),
            blocked_activities=blocked_acts,
            quality_holds=quality_holds,
            contractor_issues=[],
            schedule_risks=[],
            relevant_historical_incidents=raw_context.get("relevant_historical_incidents", []),
            review_queue_summary=review_q,
            recommended_reviews=recommended_reviews or ["Review project execution dashboard"],
            audit_verification=raw_context.get("audit_verification"),
        )

    @classmethod
    def answer_query(cls, context: ProjectContext, request: AgentQueryRequest) -> AgentQueryResponse:
        """
        Answers an on-demand supervisory query with evidence-backed reasoning.
        """
        raw_context = ContextBuilder.build_query_context(
            context=context,
            query=request.query,
            mode=request.mode,
            stage_id=request.stage_id,
            activity_id=request.activity_id,
            contractor_id=request.contractor_id,
        )
        now_iso = datetime.now(timezone.utc).isoformat()

        user_prompt = format_query_prompt(request.query, raw_context)
        messages = [
            {"role": "system", "content": SUPERVISING_AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        parsed_data = None
        try:
            llm_text = call_llm(
                messages=messages,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            if llm_text and llm_text.strip() != "{}":
                parsed_data = json.loads(llm_text)
        except Exception as e:
            logger.warning(f"[supervising_agent] LLM query synthesis failed ({e})")
            parsed_data = None

        if parsed_data and parsed_data.get("answer"):
            findings = []
            for f in parsed_data.get("findings", []):
                cat_val = f.get("category", "STAGE_RISK")
                if cat_val not in FindingCategory.__members__:
                    cat_val = FindingCategory.STAGE_RISK.value
                sev_val = f.get("severity", "MEDIUM")
                if sev_val not in FindingSeverity.__members__:
                    sev_val = FindingSeverity.MEDIUM.value

                findings.append(
                    AgentFinding(
                        category=FindingCategory(cat_val),
                        severity=FindingSeverity(sev_val),
                        title=f.get("title", "Observation"),
                        description=f.get("description", ""),
                        why_it_matters=f.get("why_it_matters", ""),
                        evidence=[
                            EvidenceReference(
                                entity_type=ev.get("entity_type", "UNKNOWN"),
                                entity_id=str(ev.get("entity_id", "")),
                                details=ev.get("details"),
                            )
                            for ev in f.get("evidence", [])
                        ],
                        recommended_action=f.get("recommended_action", "Review entity state."),
                    )
                )

            evidence_refs = [
                EvidenceReference(
                    entity_type=str(k),
                    entity_id=str(context.project_id),
                    details=f"Context domain: {k}",
                )
                for k in raw_context.keys()
            ]

            return AgentQueryResponse(
                project_id=str(context.project_id),
                query=request.query,
                generated_at=now_iso,
                agent_status=AgentStatus.HEALTHY,
                answer=parsed_data.get("answer", ""),
                findings=findings,
                evidence=evidence_refs,
                recommendations=parsed_data.get("recommendations", []),
                context_used={"keys": list(raw_context.keys())},
            )

        # Degraded fallback response
        fallback_answer = (
            f"Supervising Agent (Deterministic Fallback): Interpreting query '{request.query}'. "
            f"Project {context.project_id} context was retrieved. "
        )
        if "activity_details" in raw_context and raw_context["activity_details"]:
            act = raw_context["activity_details"]
            fallback_answer += (
                f"Activity {act.get('activity_id')} ('{act.get('activity_name')}') has canonical state "
                f"'{act.get('canonical_state')}', workflow condition '{act.get('workflow_condition')}', "
                f"and reported progress {act.get('actual_pct_complete')}%."
            )
        elif "stage_details" in raw_context and raw_context["stage_details"]:
            stg = raw_context["stage_details"]
            fallback_answer += (
                f"Stage {stg.get('stage_id')} ('{stg.get('stage_name')}') has "
                f"progress {stg.get('progress_info', {}).get('progress_pct')}%."
            )
        else:
            fallback_answer += "Detailed analysis is limited while the reasoning service operates in degraded mode."

        return AgentQueryResponse(
            project_id=str(context.project_id),
            query=request.query,
            generated_at=now_iso,
            agent_status=AgentStatus.DEGRADED,
            answer=fallback_answer,
            findings=[],
            evidence=[],
            recommendations=["Check the project execution dashboard or retry with full connectivity."],
            context_used={"keys": list(raw_context.keys())},
        )
