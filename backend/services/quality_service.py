"""
Service layer for Phase 10 Quality, ITP, and Hold Points domain.
Enforces business rules, project isolation, RBAC, and audit logging.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional
from backend.audit_context import AuditContext
from backend.context.errors import raise_permission_denied, raise_resource_not_found
from backend.context.project import ProjectContext
from backend.rbac.permissions import Permission, has_permission
from backend.repositories.activity_repo import ProjectActivityRepository
from backend.repositories.contractor_repo import ProjectContractorRepository
from backend.repositories.itp_repo import ITPRepository
from backend.repositories.quality_repo import QualityEvidenceRepository, QualityGateRepository
from backend.repositories.work_package_repo import ProjectWorkPackageRepository
from backend.schemas.quality import (
    ITPCreate,
    QualityEvidenceCreate,
    QualityGateCreate,
)


class QualityService:
    """Business logic service for Quality, ITP, Evidence, and Hold Points."""

    # ---------------------------------------------------------
    # ITP Operations
    # ---------------------------------------------------------

    @classmethod
    def create_itp(cls, context: ProjectContext, payload: ITPCreate) -> Dict[str, Any]:
        """Creates a project-scoped Inspection & Test Plan."""
        if not (
            has_permission(context.role, Permission.MANAGE_QUALITY)
            or has_permission(context.role, Permission.APPROVE_QUALITY)
        ):
            raise_permission_denied(Permission.MANAGE_QUALITY.value, context.role)

        # Validate cross-project references
        if payload.contractor_id:
            contractor = ProjectContractorRepository.get(context, payload.contractor_id)
            if not contractor:
                raise_resource_not_found("Contractor", str(payload.contractor_id))

        if payload.work_package_id:
            wp = ProjectWorkPackageRepository.get(context, payload.work_package_id)
            if not wp:
                raise_resource_not_found("WorkPackage", str(payload.work_package_id))

        itp = ITPRepository.create(
            user_id=context.user_id,
            project_id=context.project_id,
            title=payload.title,
            description=payload.description,
            schedule_id=payload.schedule_id,
            stage_id=payload.stage_id,
            discipline=payload.discipline,
            responsible_party=payload.responsible_party,
            contractor_id=payload.contractor_id,
            work_package_id=payload.work_package_id,
            status=payload.status,
        )

        AuditContext.from_context(
            context=context,
            action="CREATE_ITP",
            entity_type="ITP",
            entity_id=str(itp["itp_id"]),
            schedule_id=payload.schedule_id,
            new_state=itp,
        ).persist(context)

        return itp

    @classmethod
    def get_itp(cls, context: ProjectContext, itp_id: uuid.UUID) -> Dict[str, Any]:
        """Gets an ITP by ID within the project scope."""
        if not has_permission(context.role, Permission.VIEW_QUALITY):
            raise_permission_denied(Permission.VIEW_QUALITY.value, context.role)

        itp = ITPRepository.get_by_id(context.user_id, itp_id, context.project_id)
        if not itp:
            raise_resource_not_found("ITP", str(itp_id))
        return itp

    @classmethod
    def list_itps(cls, context: ProjectContext) -> List[Dict[str, Any]]:
        """Lists all ITPs for the current project."""
        if not has_permission(context.role, Permission.VIEW_QUALITY):
            raise_permission_denied(Permission.VIEW_QUALITY.value, context.role)

        return ITPRepository.list_by_project(context.user_id, context.project_id)

    # ---------------------------------------------------------
    # Quality Checkpoints / Gates Operations
    # ---------------------------------------------------------

    @classmethod
    def create_quality_gate(
        cls, context: ProjectContext, payload: QualityGateCreate
    ) -> Dict[str, Any]:
        """Creates a quality gate / checkpoint."""
        if not (
            has_permission(context.role, Permission.MANAGE_QUALITY)
            or has_permission(context.role, Permission.APPROVE_QUALITY)
        ):
            raise_permission_denied(Permission.MANAGE_QUALITY.value, context.role)

        # Validate cross-project references
        if payload.itp_id:
            itp = ITPRepository.get_by_id(context.user_id, payload.itp_id, context.project_id)
            if not itp:
                raise_resource_not_found("ITP", str(payload.itp_id))

        if payload.contractor_id:
            contractor = ProjectContractorRepository.get(context, payload.contractor_id)
            if not contractor:
                raise_resource_not_found("Contractor", str(payload.contractor_id))

        if payload.work_package_id:
            wp = ProjectWorkPackageRepository.get(context, payload.work_package_id)
            if not wp:
                raise_resource_not_found("WorkPackage", str(payload.work_package_id))

        if payload.activity_id:
            activity = ProjectActivityRepository.get(context, payload.activity_id)
            if not activity:
                raise_resource_not_found("Activity", payload.activity_id)

        gate = QualityGateRepository.create(
            user_id=context.user_id,
            project_id=context.project_id,
            gate_name=payload.gate_name,
            gate_type=payload.gate_type,
            checkpoint_category=payload.checkpoint_category,
            stage_id=payload.stage_id,
            schedule_id=payload.schedule_id,
            activity_id=payload.activity_id,
            itp_id=payload.itp_id,
            contractor_id=payload.contractor_id,
            work_package_id=payload.work_package_id,
            required=payload.required,
            due_date=payload.due_date,
            remarks=payload.remarks,
        )

        AuditContext.from_context(
            context=context,
            action="CREATE_QUALITY_GATE",
            entity_type="QUALITY_GATE",
            entity_id=str(gate["quality_gate_id"]),
            schedule_id=payload.schedule_id,
            new_state=gate,
        ).persist(context)

        return gate

    @classmethod
    def get_quality_gate(
        cls, context: ProjectContext, quality_gate_id: uuid.UUID
    ) -> Dict[str, Any]:
        """Gets a quality gate by ID."""
        if not has_permission(context.role, Permission.VIEW_QUALITY):
            raise_permission_denied(Permission.VIEW_QUALITY.value, context.role)

        gate = QualityGateRepository.get_by_id(context.user_id, quality_gate_id, context.project_id)
        if not gate:
            raise_resource_not_found("QualityGate", str(quality_gate_id))
        return gate

    @classmethod
    def list_activity_quality_gates(
        cls, context: ProjectContext, activity_id: str
    ) -> List[Dict[str, Any]]:
        """Lists all quality gates attached to a specific activity."""
        if not has_permission(context.role, Permission.VIEW_QUALITY):
            raise_permission_denied(Permission.VIEW_QUALITY.value, context.role)

        # Ensure activity belongs to project
        activity = ProjectActivityRepository.get(context, activity_id)
        if not activity:
            raise_resource_not_found("Activity", activity_id)

        return QualityGateRepository.list_by_activity(context.user_id, context.project_id, activity_id)

    # ---------------------------------------------------------
    # Evidence & Gate State Transitions
    # ---------------------------------------------------------

    @classmethod
    def submit_quality_evidence(
        cls,
        context: ProjectContext,
        quality_gate_id: uuid.UUID,
        payload: QualityEvidenceCreate,
    ) -> Dict[str, Any]:
        """Submits quality evidence for a checkpoint."""
        if not (
            has_permission(context.role, Permission.MANAGE_QUALITY)
            or has_permission(context.role, Permission.APPROVE_QUALITY)
            or has_permission(context.role, Permission.CREATE_EXECUTION_EVENT)
        ):
            raise_permission_denied(Permission.MANAGE_QUALITY.value, context.role)

        gate = QualityGateRepository.get_by_id(context.user_id, quality_gate_id, context.project_id)
        if not gate:
            raise_resource_not_found("QualityGate", str(quality_gate_id))

        evidence = QualityEvidenceRepository.create(
            user_id=context.user_id,
            quality_gate_id=quality_gate_id,
            source_document_id=payload.source_document_id,
            evidence_type=payload.evidence_type,
            result=payload.result,
            inspector_name=payload.inspector_name,
            inspection_date=payload.inspection_date,
            evidence_hash=payload.evidence_hash,
            metadata=payload.metadata,
        )

        # If gate is currently PENDING, transition it to SUBMITTED
        old_state = dict(gate)
        if gate["status"] == "PENDING":
            gate = QualityGateRepository.update_status(
                user_id=context.user_id,
                quality_gate_id=quality_gate_id,
                project_id=context.project_id,
                status="SUBMITTED",
            ) or gate

        AuditContext.from_context(
            context=context,
            action="SUBMIT_QUALITY_EVIDENCE",
            entity_type="QUALITY_EVIDENCE",
            entity_id=str(evidence["quality_evidence_id"]),
            schedule_id=gate.get("schedule_id"),
            old_state=old_state,
            new_state=evidence,
        ).persist(context)

        return evidence

    @classmethod
    def pass_quality_gate(
        cls, context: ProjectContext, quality_gate_id: uuid.UUID, remarks: Optional[str] = None
    ) -> Dict[str, Any]:
        """Approves / passes a quality gate."""
        if not (
            has_permission(context.role, Permission.APPROVE_QUALITY)
            or has_permission(context.role, Permission.MANAGE_QUALITY)
        ):
            raise_permission_denied(Permission.APPROVE_QUALITY.value, context.role)

        gate = QualityGateRepository.get_by_id(context.user_id, quality_gate_id, context.project_id)
        if not gate:
            raise_resource_not_found("QualityGate", str(quality_gate_id))

        old_state = dict(gate)
        updated = QualityGateRepository.update_status(
            user_id=context.user_id,
            quality_gate_id=quality_gate_id,
            project_id=context.project_id,
            status="PASSED",
            remarks=remarks,
        )

        AuditContext.from_context(
            context=context,
            action="PASS_QUALITY_GATE",
            entity_type="QUALITY_GATE",
            entity_id=str(quality_gate_id),
            schedule_id=gate.get("schedule_id"),
            old_state=old_state,
            new_state=updated,
        ).persist(context)

        return updated

    @classmethod
    def fail_quality_gate(
        cls, context: ProjectContext, quality_gate_id: uuid.UUID, remarks: Optional[str] = None
    ) -> Dict[str, Any]:
        """Fails a quality gate."""
        if not (
            has_permission(context.role, Permission.APPROVE_QUALITY)
            or has_permission(context.role, Permission.MANAGE_QUALITY)
        ):
            raise_permission_denied(Permission.APPROVE_QUALITY.value, context.role)

        gate = QualityGateRepository.get_by_id(context.user_id, quality_gate_id, context.project_id)
        if not gate:
            raise_resource_not_found("QualityGate", str(quality_gate_id))

        old_state = dict(gate)
        updated = QualityGateRepository.update_status(
            user_id=context.user_id,
            quality_gate_id=quality_gate_id,
            project_id=context.project_id,
            status="FAILED",
            remarks=remarks,
        )

        AuditContext.from_context(
            context=context,
            action="FAIL_QUALITY_GATE",
            entity_type="QUALITY_GATE",
            entity_id=str(quality_gate_id),
            schedule_id=gate.get("schedule_id"),
            old_state=old_state,
            new_state=updated,
        ).persist(context)

        return updated

    @classmethod
    def waive_quality_gate(
        cls,
        context: ProjectContext,
        quality_gate_id: uuid.UUID,
        waiver_reason: str,
        remarks: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Waives a quality gate (requires WAIVE_QUALITY or MANAGE_QUALITY permission)."""
        if not (
            has_permission(context.role, Permission.WAIVE_QUALITY)
            or has_permission(context.role, Permission.MANAGE_QUALITY)
        ):
            raise_permission_denied(Permission.WAIVE_QUALITY.value, context.role)

        gate = QualityGateRepository.get_by_id(context.user_id, quality_gate_id, context.project_id)
        if not gate:
            raise_resource_not_found("QualityGate", str(quality_gate_id))

        old_state = dict(gate)
        updated = QualityGateRepository.update_status(
            user_id=context.user_id,
            quality_gate_id=quality_gate_id,
            project_id=context.project_id,
            status="WAIVED",
            remarks=remarks,
            waiver_reason=waiver_reason,
        )

        AuditContext.from_context(
            context=context,
            action="WAIVE_QUALITY_GATE",
            entity_type="QUALITY_GATE",
            entity_id=str(quality_gate_id),
            schedule_id=gate.get("schedule_id"),
            old_state=old_state,
            new_state=updated,
        ).persist(context)

        return updated

    # ---------------------------------------------------------
    # Quality Status & Eligibility Evaluation Engines
    # (Contract for Member 1 Integration)
    # ---------------------------------------------------------

    @classmethod
    def get_quality_status(cls, context: ProjectContext, activity_id: str) -> Dict[str, Any]:
        """
        Evaluates the full quality status and execution eligibility for an activity.
        This method exposes the stable Member 1 Integration Contract.
        """
        if not has_permission(context.role, Permission.VIEW_QUALITY):
            raise_permission_denied(Permission.VIEW_QUALITY.value, context.role)

        activity = ProjectActivityRepository.get(context, activity_id)
        if not activity:
            raise_resource_not_found("Activity", activity_id)

        gates = QualityGateRepository.list_by_activity(context.user_id, context.project_id, activity_id)
        
        # Calculate gate metrics
        total_gates = len(gates)
        passed_gates = sum(1 for g in gates if g["status"] == "PASSED")
        pending_gates = sum(1 for g in gates if g["status"] in ("PENDING", "SUBMITTED"))
        failed_gates = sum(1 for g in gates if g["status"] == "FAILED")
        waived_gates = sum(1 for g in gates if g["status"] == "WAIVED")

        # Check active hold points (category HOLD and not resolved)
        has_active_hold_point = any(
            g.get("checkpoint_category") == "HOLD" and g["status"] not in ("PASSED", "WAIVED")
            for g in gates
        )

        quality_gate_required = bool(activity.get("quality_gate_required")) or (total_gates > 0)

        # Deterministic Eligibility Rules
        if not quality_gate_required:
            is_eligible = True
            canonical_status = "NOT_REQUIRED"
            blocking_reason = None
        else:
            if failed_gates > 0:
                is_eligible = False
                canonical_status = "FAILED"
                blocking_reason = f"Activity has {failed_gates} failed quality gate(s)"
            elif has_active_hold_point:
                is_eligible = False
                canonical_status = "PENDING"
                blocking_reason = "Activity has 1 or more active hold point(s) pending clearance"
            elif pending_gates > 0:
                is_eligible = False
                has_submitted = any(g["status"] == "SUBMITTED" for g in gates)
                canonical_status = "SUBMITTED" if has_submitted else "PENDING"
                blocking_reason = f"Activity has {pending_gates} pending/submitted quality gate(s)"
            elif (passed_gates + waived_gates) == total_gates:
                is_eligible = True
                canonical_status = "PASSED" if passed_gates > 0 else "WAIVED"
                blocking_reason = None
            else:
                is_eligible = False
                canonical_status = "PENDING"
                blocking_reason = "Quality requirement unfulfilled"

        return {
            "activity_id": activity_id,
            "quality_gate_required": quality_gate_required,
            "is_eligible": is_eligible,
            "status": canonical_status,
            "total_gates": total_gates,
            "passed_gates": passed_gates,
            "pending_gates": pending_gates,
            "failed_gates": failed_gates,
            "waived_gates": waived_gates,
            "has_active_hold_point": has_active_hold_point,
            "blocking_reason": blocking_reason,
            "gates": gates,
        }

    @classmethod
    def is_quality_eligible(cls, context: ProjectContext, activity_id: str) -> bool:
        """
        Returns boolean indicating whether an activity is quality-eligible to proceed.
        Helper integration function for Member 1's claim execution engine.
        """
        status_info = cls.get_quality_status(context, activity_id)
        return status_info["is_eligible"]
