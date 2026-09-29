"""
Protocols and Extension Contracts for Audit Dossier Foundation in SETUAI V7 Phase 13.
Allows future domains (Quality, Contractor, Institutional Memory, Supervising Agent)
to register section providers without modifying core dossier assembly logic.
"""

from __future__ import annotations

import uuid
from typing import Optional, Protocol, runtime_checkable

from backend.context.project import ProjectContext
from backend.dossier.schemas import ExtensionSection


@runtime_checkable
class DossierSectionProvider(Protocol):
    """
    Protocol for external domain providers contributing sections to the Audit Dossier.
    """

    @property
    def section_name(self) -> str:
        """Name of the section (e.g. 'quality', 'contractor', 'institutional_memory')."""
        ...

    def collect(
        self,
        context: ProjectContext,
        schedule_id: Optional[str] = None,
        activity_id: Optional[str] = None,
    ) -> ExtensionSection:
        """
        Collect section records strictly scoped to context.project_id.
        Must return an ExtensionSection with status AVAILABLE, PARTIAL, or NOT_AVAILABLE.
        Never fabricate data.
        """
        ...
