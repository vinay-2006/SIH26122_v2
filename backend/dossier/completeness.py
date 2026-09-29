"""
Deterministic Completeness Evaluator for SETUAI V7 Phase 13 Audit Dossier.
Inspects all assembled sections and extension points to calculate transparent completeness metrics.
"""

from __future__ import annotations

from typing import Dict, List
from backend.dossier.schemas import (
    DossierCompleteness,
    ExtensionSection,
    SectionStatus,
)


class CompletenessEvaluator:
    """
    Evaluates section statuses across core and extension modules.
    Never classifies a dossier as fully AVAILABLE if required core sections are missing or broken.
    """

    CORE_SECTIONS = [
        "project",
        "schedule",
        "stages",
        "activities",
        "execution_evidence",
        "matching",
        "validation",
        "human_decisions",
        "approved_actuals",
        "reopen_history",
        "progress",
        "impact",
        "audit_chain",
    ]

    EXTENSION_SECTIONS = [
        "quality",
        "contractor",
        "work_package",
        "institutional_memory",
        "agent_briefing",
        "governance",
    ]

    @classmethod
    def evaluate(
        cls,
        section_statuses: Dict[str, SectionStatus],
        extensions: Dict[str, ExtensionSection],
        is_activity_scoped: bool = False,
    ) -> DossierCompleteness:
        """
        Calculates complete_sections, partial_sections, missing_sections, and overall_status.
        """
        complete: List[str] = []
        partial: List[str] = []
        missing: List[str] = []

        # 1. Evaluate Core Sections
        for sec_name in cls.CORE_SECTIONS:
            # If activity-scoped, schedule may be optional or inherited
            status = section_statuses.get(sec_name, SectionStatus.NOT_AVAILABLE)
            if status == SectionStatus.AVAILABLE:
                complete.append(sec_name)
            elif status == SectionStatus.PARTIAL:
                partial.append(sec_name)
            elif status == SectionStatus.NOT_AVAILABLE:
                missing.append(sec_name)
            # NOT_APPLICABLE sections are omitted from missing

        # 2. Evaluate Extension Sections
        for ext_name in cls.EXTENSION_SECTIONS:
            ext = extensions.get(ext_name)
            ext_status = ext.status if ext else SectionStatus.NOT_AVAILABLE
            if ext_status == SectionStatus.AVAILABLE:
                complete.append(f"extension_{ext_name}")
            elif ext_status == SectionStatus.PARTIAL:
                partial.append(f"extension_{ext_name}")
            else:
                missing.append(f"extension_{ext_name}")

        total = len(complete) + len(partial) + len(missing)

        # 3. Determine Overall Status
        # If any core section is missing:
        core_missing = [s for s in missing if not s.startswith("extension_")]
        core_partial = [s for s in partial if not s.startswith("extension_")]

        if core_missing:
            # If all core sections are missing: NOT_AVAILABLE; else PARTIAL
            if len(core_missing) >= len(cls.CORE_SECTIONS) - 1:
                overall = SectionStatus.NOT_AVAILABLE
            else:
                overall = SectionStatus.PARTIAL
        elif core_partial or any(m.startswith("extension_") for m in missing):
            overall = SectionStatus.PARTIAL
        else:
            overall = SectionStatus.AVAILABLE

        return DossierCompleteness(
            overall_status=overall,
            complete_sections=complete,
            partial_sections=partial,
            missing_sections=missing,
            total_sections=total,
            completed_count=len(complete),
        )
