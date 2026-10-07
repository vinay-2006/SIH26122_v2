"""
Authoritative Audit Dossier Assembly Service for SETUAI V7 Phase 13.
Provides deterministic, read-only aggregation across execution evidence, matching,
validation, decisions, actuals, progress, impact, and cryptographic audit verification.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Union

from fastapi import HTTPException, status

from backend.context.errors import raise_invalid_project_context, raise_permission_denied
from backend.context.project import ProjectContext
from backend.context.schedule import ScheduleContext
from backend.dossier.audit_verifier import AuditVerifier
from backend.dossier.collectors import DossierBulkCollector
from backend.dossier.completeness import CompletenessEvaluator
from backend.dossier.interfaces import DossierSectionProvider
from backend.dossier.schemas import (
    AuditChainSection,
    AuditChainVerificationResult,
    AuditDossier,
    DossierScope,
    ExtensionSection,
    SectionStatus,
)
from backend.rbac.permissions import Permission, has_permission

logger = logging.getLogger(__name__)


class DossierService:
    """
    Authoritative service assembling structured, export-ready Audit Dossiers.
    Enforces project and schedule isolation and provides extensible provider slots.
    """

    _extension_providers: Dict[str, DossierSectionProvider] = {}

    @classmethod
    def register_section_provider(cls, provider: DossierSectionProvider) -> None:
        """Register future domain provider (e.g. Member 2 Quality / Contractor / Memory)."""
        cls._extension_providers[provider.section_name] = provider
        logger.info("Registered dossier extension provider: %s", provider.section_name)

    @classmethod
    def _collect_extensions(
        cls,
        context: ProjectContext,
        schedule_id: Optional[str] = None,
        activity_id: Optional[str] = None,
    ) -> Dict[str, ExtensionSection]:
        """
        Gathers extension sections. For registered providers, delegates collection.
        For unregistered future domains, explicitly reports status NOT_AVAILABLE with empty records.
        Never fabricates mock data.
        """
        standard_extensions = [
            "quality",
            "contractor",
            "work_package",
            "institutional_memory",
            "agent_briefing",
            "governance",
        ]
        results: Dict[str, ExtensionSection] = {}

        for ext_name in standard_extensions:
            if ext_name in cls._extension_providers:
                try:
                    results[ext_name] = cls._extension_providers[ext_name].collect(
                        context=context,
                        schedule_id=schedule_id,
                        activity_id=activity_id,
                    )
                except Exception as e:
                    logger.warning("Error collecting extension '%s': %s", ext_name, e)
                    results[ext_name] = ExtensionSection(
                        status=SectionStatus.NOT_AVAILABLE,
                        provider=ext_name,
                        records=[],
                    )
            else:
                results[ext_name] = ExtensionSection(
                    status=SectionStatus.NOT_AVAILABLE,
                    provider=None,
                    records=[],
                )

        return results

    # =========================================================================
    # 1. PROJECT DOSSIER ASSEMBLY
    # =========================================================================
    @classmethod
    def build_project_dossier(
        cls,
        context: ProjectContext,
        schedule_id: Optional[str] = None,
    ) -> AuditDossier:
        """
        Assembles comprehensive project-level audit dossier.
        Requires VIEW_PROJECT or VIEW_AUDIT permission.
        """
        if not (has_permission(context.role, Permission.VIEW_PROJECT) or has_permission(context.role, Permission.VIEW_AUDIT)):
            raise_permission_denied(Permission.VIEW_PROJECT.value, context.role)

        dossier_id = f"DOSSIER-PRJ-{context.project_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

        # 1. Bulk Collect Core Sections
        proj_sec = DossierBulkCollector.collect_project(context)
        sched_sec = DossierBulkCollector.collect_schedule(context, schedule_id)
        stages_sec = DossierBulkCollector.collect_stages(context, schedule_id)
        act_sec = DossierBulkCollector.collect_activities(context, schedule_id)
        ev_sec = DossierBulkCollector.collect_evidence(context, schedule_id)

        # Event IDs for downstream correlation
        event_ids = [e.event_id for e in ev_sec.evidence] if ev_sec.evidence else []

        match_sec = DossierBulkCollector.collect_matching(context, schedule_id, event_ids)
        val_sec = DossierBulkCollector.collect_validation(context, schedule_id, event_ids)
        dec_sec = DossierBulkCollector.collect_decisions(context, event_ids)
        actuals_sec = DossierBulkCollector.collect_approved_actuals(context, schedule_id)
        reopen_sec = DossierBulkCollector.collect_reopen_history(context, schedule_id, activities=act_sec.activities)
        prog_sec = DossierBulkCollector.collect_progress(context, schedule_id)
        impact_sec = DossierBulkCollector.collect_impact(context, schedule_id)

        # 2. Cryptographic Audit Chain Verification
        audit_logs = AuditVerifier.fetch_audit_logs(context, schedule_id=schedule_id)
        verification_res = AuditVerifier.verify_project_chain(context)
        audit_sec = AuditChainSection(
            status=SectionStatus.AVAILABLE if verification_res.status == "VALID" else (
                SectionStatus.PARTIAL if verification_res.status in ("EMPTY", "LEGACY_ONLY") else SectionStatus.NOT_AVAILABLE
            ),
            verification=verification_res,
            recent_logs=audit_logs[-20:] if audit_logs else [],
        )

        # 3. Future Extension Points
        extensions = cls._collect_extensions(context, schedule_id)

        # 4. Deterministic Completeness Evaluation
        section_statuses = {
            "project": proj_sec.status,
            "schedule": sched_sec.status if sched_sec else SectionStatus.NOT_APPLICABLE,
            "stages": stages_sec.status,
            "activities": act_sec.status,
            "execution_evidence": ev_sec.status,
            "matching": match_sec.status,
            "validation": val_sec.status,
            "human_decisions": dec_sec.status,
            "approved_actuals": actuals_sec.status,
            "reopen_history": reopen_sec.status,
            "progress": prog_sec.status,
            "impact": impact_sec.status,
            "audit_chain": audit_sec.status,
        }
        completeness = CompletenessEvaluator.evaluate(section_statuses, extensions)

        return AuditDossier(
            dossier_id=dossier_id,
            project_id=context.project_id,
            schedule_id=schedule_id,
            generated_at=datetime.utcnow(),
            scope=DossierScope.PROJECT if not schedule_id else DossierScope.SCHEDULE,
            project=proj_sec,
            schedule=sched_sec,
            stages=stages_sec,
            activities=act_sec,
            execution_evidence=ev_sec,
            matching=match_sec,
            validation=val_sec,
            human_decisions=dec_sec,
            approved_actuals=actuals_sec,
            reopen_history=reopen_sec,
            progress=prog_sec,
            impact=impact_sec,
            audit_chain=audit_sec,
            extensions=extensions,
            completeness=completeness,
        )

    # =========================================================================
    # 2. SCHEDULE DOSSIER ASSEMBLY
    # =========================================================================
    @classmethod
    def build_schedule_dossier(
        cls,
        context: ScheduleContext,
    ) -> AuditDossier:
        """
        Assembles schedule-specific audit dossier.
        Enforces ScheduleContext ownership.
        """
        return cls.build_project_dossier(
            context=context.project_context,
            schedule_id=context.schedule_id,
        )

    # =========================================================================
    # 3. ACTIVITY DOSSIER ASSEMBLY
    # =========================================================================
    @classmethod
    def build_activity_dossier(
        cls,
        context: ScheduleContext,
        activity_id: str,
    ) -> AuditDossier:
        """
        Assembles deep audit dossier focused on a single activity.
        Traces evidence -> match -> validation -> decision -> actual -> reopen -> progress -> impact.
        """
        if not (has_permission(context.role, Permission.VIEW_SCHEDULE) or has_permission(context.role, Permission.VIEW_AUDIT)):
            raise_permission_denied(Permission.VIEW_SCHEDULE.value, context.role)

        project_ctx = context.project_context
        schedule_id = context.schedule_id
        dossier_id = f"DOSSIER-ACT-{schedule_id}-{activity_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

        # 1. Single Activity Bulk Collect
        proj_sec = DossierBulkCollector.collect_project(project_ctx)
        sched_sec = DossierBulkCollector.collect_schedule(project_ctx, schedule_id)
        stages_sec = DossierBulkCollector.collect_stages(project_ctx, schedule_id)
        act_sec = DossierBulkCollector.collect_activities(project_ctx, schedule_id, activity_id=activity_id)

        if not act_sec.activities:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Activity '{activity_id}' not found in schedule '{schedule_id}'.",
            )

        ev_sec = DossierBulkCollector.collect_evidence(project_ctx, schedule_id, activity_id=activity_id)
        event_ids = [e.event_id for e in ev_sec.evidence] if ev_sec.evidence else []

        match_sec = DossierBulkCollector.collect_matching(project_ctx, schedule_id, event_ids, activity_id=activity_id)
        val_sec = DossierBulkCollector.collect_validation(project_ctx, schedule_id, event_ids, activity_id=activity_id)
        dec_sec = DossierBulkCollector.collect_decisions(project_ctx, event_ids, activity_id=activity_id)
        actuals_sec = DossierBulkCollector.collect_approved_actuals(project_ctx, schedule_id, activity_id=activity_id)
        reopen_sec = DossierBulkCollector.collect_reopen_history(project_ctx, schedule_id, activity_id=activity_id)
        prog_sec = DossierBulkCollector.collect_progress(project_ctx, schedule_id)
        impact_sec = DossierBulkCollector.collect_impact(project_ctx, schedule_id)

        # 2. Activity-specific Audit Verification
        audit_logs = AuditVerifier.fetch_audit_logs(project_ctx, schedule_id=schedule_id, entity_id=activity_id)
        verification_res = AuditVerifier.verify_project_chain(project_ctx)
        audit_sec = AuditChainSection(
            status=SectionStatus.AVAILABLE if verification_res.status == "VALID" else (
                SectionStatus.PARTIAL if verification_res.status in ("EMPTY", "LEGACY_ONLY") else SectionStatus.NOT_AVAILABLE
            ),
            verification=verification_res,
            recent_logs=audit_logs,
        )

        # 3. Extensions
        extensions = cls._collect_extensions(project_ctx, schedule_id, activity_id=activity_id)

        # 4. Completeness
        section_statuses = {
            "project": proj_sec.status,
            "schedule": sched_sec.status if sched_sec else SectionStatus.AVAILABLE,
            "stages": stages_sec.status,
            "activities": act_sec.status,
            "execution_evidence": ev_sec.status,
            "matching": match_sec.status,
            "validation": val_sec.status,
            "human_decisions": dec_sec.status,
            "approved_actuals": actuals_sec.status,
            "reopen_history": reopen_sec.status,
            "progress": prog_sec.status,
            "impact": impact_sec.status,
            "audit_chain": audit_sec.status,
        }
        completeness = CompletenessEvaluator.evaluate(section_statuses, extensions, is_activity_scoped=True)

        return AuditDossier(
            dossier_id=dossier_id,
            project_id=project_ctx.project_id,
            schedule_id=schedule_id,
            activity_id=activity_id,
            generated_at=datetime.utcnow(),
            scope=DossierScope.ACTIVITY,
            project=proj_sec,
            schedule=sched_sec,
            stages=stages_sec,
            activities=act_sec,
            execution_evidence=ev_sec,
            matching=match_sec,
            validation=val_sec,
            human_decisions=dec_sec,
            approved_actuals=actuals_sec,
            reopen_history=reopen_sec,
            progress=prog_sec,
            impact=impact_sec,
            audit_chain=audit_sec,
            extensions=extensions,
            completeness=completeness,
        )

    # =========================================================================
    # 4. CRYPTOGRAPHIC VERIFICATION API
    # =========================================================================
    @classmethod
    def verify_audit_chain(
        cls,
        context: ProjectContext,
        schedule_id: Optional[str] = None,
    ) -> AuditChainVerificationResult:
        """
        Standalone cryptographic audit verification over project logs.
        Requires VIEW_AUDIT or VIEW_PROJECT permission.
        """
        if not (has_permission(context.role, Permission.VIEW_AUDIT) or has_permission(context.role, Permission.VIEW_PROJECT)):
            raise_permission_denied(Permission.VIEW_AUDIT.value, context.role)

        return AuditVerifier.verify_project_chain(context, schedule_id=schedule_id)


# ============================================================================
# STABLE CONVENIENCE FUNCTIONS FOR MEMBER 2 CONSUMPTION
# ============================================================================

def build_project_dossier(context: ProjectContext, schedule_id: Optional[str] = None) -> AuditDossier:
    return DossierService.build_project_dossier(context, schedule_id=schedule_id)


def build_schedule_dossier(context: ScheduleContext) -> AuditDossier:
    return DossierService.build_schedule_dossier(context)


def build_activity_dossier(context: ScheduleContext, activity_id: str) -> AuditDossier:
    return DossierService.build_activity_dossier(context, activity_id)


def verify_audit_chain(context: ProjectContext, schedule_id: Optional[str] = None) -> AuditChainVerificationResult:
    return DossierService.verify_audit_chain(context, schedule_id=schedule_id)
