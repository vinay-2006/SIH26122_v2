"""
Audit Dossier Foundation for SETUAI V7 Phase 13.
Provides deterministic, export-ready audit dossiers and cryptographic audit verification.
"""

from backend.dossier.schemas import (
    AuditChainSection,
    AuditChainVerificationResult,
    AuditDossier,
    DossierCompleteness,
    DossierScope,
    ExtensionSection,
    SectionStatus,
)
from backend.dossier.interfaces import DossierSectionProvider
from backend.dossier.service import (
    DossierService,
    build_activity_dossier,
    build_project_dossier,
    build_schedule_dossier,
    verify_audit_chain,
)
from backend.dossier.audit_verifier import AuditVerifier

__all__ = [
    "AuditDossier",
    "SectionStatus",
    "DossierScope",
    "DossierCompleteness",
    "AuditChainSection",
    "AuditChainVerificationResult",
    "ExtensionSection",
    "DossierSectionProvider",
    "DossierService",
    "AuditVerifier",
    "build_project_dossier",
    "build_schedule_dossier",
    "build_activity_dossier",
    "verify_audit_chain",
]
