"""
Interfaces and Protocols for Institutional Memory Retrieval in SETUAI V7 Phase 11.
Enforces decoupling: future domain implementations register against these contracts.
"""

from __future__ import annotations

from typing import List, Optional, Protocol, runtime_checkable

from backend.context.project import ProjectContext
from backend.memory.schemas import MemoryFilter, MemoryRecord


@runtime_checkable
class MemoryProvider(Protocol):
    """
    Protocol for sources of retrievable institutional memory records.
    Implementations must enforce strict project isolation using the provided ProjectContext.
    """

    def get_candidates(
        self,
        context: ProjectContext,
        filters: Optional[MemoryFilter] = None,
    ) -> List[MemoryRecord]:
        """
        Retrieve candidate memory records belonging strictly to context.project_id
        matching the provided metadata filters.
        """
        ...


@runtime_checkable
class SemanticEncoder(Protocol):
    """
    Protocol for optional semantic embedding adapters.
    """

    def is_available(self) -> bool:
        """Returns True if the semantic model is loaded and ready for inference."""
        ...

    def encode_text(self, text: str) -> Optional[List[float]]:
        """Encode arbitrary text into a normalized embedding vector."""
        ...

    def compute_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Compute cosine similarity between two normalized vectors."""
        ...
